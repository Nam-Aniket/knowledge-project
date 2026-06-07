#!/usr/bin/env python3
"""
Psyche Guidance Engine
─────────────────────
Connects knowledge retrieval to structured decision-making.
Supports the loop: goal → diagnose → retrieve → brief → experiment → review → rule.
"""
import os
import sys
import json
import argparse
from datetime import datetime, timezone, timedelta

# Ensure current directory is in path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from db import (
    get_connection, resolve_db_path, init_db,
    add_goal, get_goals, update_goal,
    add_experiment, get_experiments, update_experiment,
    add_metric_log, get_metric_logs,
    add_review, get_reviews,
    add_rule, get_rules, update_rule,
)
from llm_client import LLMClient
from rich.console import Console
from rich.table import Table
from rich.markdown import Markdown
from rich.panel import Panel

console = Console()
err_console = Console(stderr=True)

# ─── Domain Pack Management ───────────────────────────────────────────────────

DOMAINS_DIR = os.path.expanduser("~/.psyche/domains")

def _get_seed_pack_paths():
    """Returns paths to the default seed packs bundled with Psyche."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    packs_dir = os.path.join(base_dir, "psyche", "domain_packs")
    if not os.path.exists(packs_dir):
        # Fallback if running from a different root
        packs_dir = os.path.join(base_dir, "domain_packs")
    
    if os.path.exists(packs_dir):
        return [os.path.join(packs_dir, f) for f in os.listdir(packs_dir) if f.endswith(".yaml") or f.endswith(".json")]
    return []

def ensure_domain_packs():
    """Creates the domains directory and seeds default packs if they don't exist."""
    import shutil
    os.makedirs(DOMAINS_DIR, exist_ok=True)
    seed_paths = _get_seed_pack_paths()
    for src in seed_paths:
        filename = os.path.basename(src)
        dst = os.path.join(DOMAINS_DIR, filename)
        if not os.path.exists(dst):
            shutil.copy2(src, dst)



def load_domain_pack(domain):
    """Loads a domain question pack from ~/.psyche/domains/. Falls back to general if not found."""
    ensure_domain_packs()

    # Try YAML first, then JSON
    yaml_path = os.path.join(DOMAINS_DIR, f"{domain}.yaml")
    json_path = os.path.join(DOMAINS_DIR, f"{domain}.json")

    pack_path = None
    if os.path.exists(yaml_path):
        pack_path = yaml_path
    elif os.path.exists(json_path):
        pack_path = json_path
    elif domain != "general":
        # Fall back to general
        return load_domain_pack("general")
    else:
        # Return built-in general pack
        return SEED_PACKS["general"]

    with open(pack_path, "r", encoding="utf-8") as f:
        if pack_path.endswith(".yaml"):
            try:
                import yaml
                return yaml.safe_load(f)
            except ImportError:
                pass
        return json.load(f)


def _get_all_packs():
    ensure_domain_packs()
    packs = {}
    for f in os.listdir(DOMAINS_DIR):
        if f.endswith(".yaml") or f.endswith(".json"):
            name = f.split(".")[0]
            try:
                packs[name] = load_domain_pack(name)
            except Exception:
                pass
    return packs


def detect_domain(query_text):
    """Detects the most relevant domain for a query by keyword matching against domain search_terms."""
    query_lower = query_text.lower()
    best_domain = "general"
    best_score = 0

    packs = _get_all_packs()
    for domain_name, pack in packs.items():
        if domain_name == "general":
            continue
        score = 0
        for term in pack.get("search_terms", []):
            if term.lower() in query_lower:
                score += 1
        if score > best_score:
            best_score = score
            best_domain = domain_name

    return best_domain


# ─── Guidance Brief Generation ────────────────────────────────────────────────

GUIDANCE_SYSTEM_INSTRUCTION = """\
You are Psyche, a knowledge-grounded personal improvement advisor. Your role is to produce structured, actionable guidance briefs.

RULES:
1. Ground every recommendation in the RETRIEVED KNOWLEDGE provided. Cite sources and locations.
2. Never give vague motivational advice. Be specific and actionable.
3. Every recommendation must connect to a measurable metric or observable feedback signal.
4. If the retrieved knowledge is insufficient, say so explicitly in missing_information.
5. If there is no strong retrieved principle found, explicitly state: "No strong retrieved principle found." Do not force a connection.
6. Output valid JSON matching the schema exactly. No markdown wrapping, no extra text.

OUTPUT SCHEMA:
{
  "domain": "<string>",
  "stage": "<exploring|planning|executing|reviewing>",
  "goal": "<one-line goal statement>",
  "missing_information": ["<what you'd need to know to give better advice>"],
  "relevant_principles": [
    {"principle": "<key insight>", "source": "<Title, Location>"}
  ],
  "key_assumptions": ["<assumptions the recommendation depends on>"],
  "risks_and_traps": ["<common mistakes or failure modes>"],
  "suggested_metrics": [
    {"name": "<metric_name>", "type": "<objective|subjective>", "unit": "<unit>"}
  ],
  "next_action": "<the single smallest next step>",
  "success_condition": "<how to know it's working>",
  "failure_condition": "<when to stop or change approach>",
  "review_date": "<YYYY-MM-DD, typically 1-2 weeks out>",
  "rule_suggestions": ["<personal rules to consider adopting>"]
}
"""


def generate_guidance_brief(goal_text, domain, db_path, llm):
    """Generates a structured guidance brief by retrieving knowledge and using LLM synthesis."""
    from query import perform_hybrid_search, format_context, retrieve_concept_context
    from db import get_all_embeddings_only, search_vector_vec
    import numpy as np

    pack = load_domain_pack(domain)
    conn = get_connection(db_path)

    try:
        # Gather context: existing rules, goals, experiments
        existing_rules = get_rules(conn, domain=domain)
        active_goals = get_goals(conn, domain=domain, status='active')
        active_experiments = get_experiments(conn, status='active')
    finally:
        conn.close()

    # Retrieve knowledge using existing hybrid search pipeline
    records = []
    if llm.provider != "none":
        conn = get_connection(db_path)
        try:
            records = get_all_embeddings_only(conn)
        finally:
            conn.close()

    chunk_ids = np.array([r["chunk_id"] for r in records if r["embedding"] is not None], dtype=np.int32)
    valid_embeddings = [r["embedding"] for r in records if r["embedding"] is not None]
    if valid_embeddings:
        embeddings_matrix = np.vstack(valid_embeddings)
    else:
        embeddings_matrix = np.array([], dtype=np.float32)

    # Load usearch index if available
    usearch_index = None
    if llm.provider != "none":
        try:
            from usearch.index import Index
            index_path = db_path.replace(".db", "") + ".usearch"
            if os.path.exists(index_path) and embeddings_matrix.size > 0:
                dim = embeddings_matrix.shape[1]
                usearch_index = Index(ndim=dim, metric="cosine")
                usearch_index.load(index_path)
        except Exception:
            usearch_index = None

    # Build search query from goal + domain search terms
    search_query = goal_text
    search_terms = pack.get("search_terms", [])
    if search_terms:
        # Add a few relevant domain terms to enrich the query
        search_query += " " + " ".join(search_terms[:3])

    similarities = perform_hybrid_search(
        db_path=db_path,
        query_text=search_query,
        chunk_ids=chunk_ids,
        embeddings_matrix=embeddings_matrix,
        llm=llm,
        usearch_index=usearch_index,
        limit=8
    )
    context_str = format_context(similarities, top_n=8)

    # Retrieve concept graph context
    conn = get_connection(db_path)
    try:
        graph_context = retrieve_concept_context(conn, goal_text)
    finally:
        conn.close()

    # Build the prompt
    rules_text = ""
    if existing_rules:
        rules_text = "\n### PERSONAL RULES (learned from past experience):\n"
        for r in existing_rules:
            rules_text += f"- [{r['confidence']}] {r['rule_text']} (source: {r['source'] or 'manual'})\n"

    goals_text = ""
    if active_goals:
        goals_text = "\n### ACTIVE GOALS:\n"
        for g in active_goals:
            goals_text += f"- [{g['domain']}] {g['title']} (stage: {g['stage']})\n"

    experiments_text = ""
    if active_experiments:
        experiments_text = "\n### ACTIVE EXPERIMENTS:\n"
        for e in active_experiments:
            experiments_text += f"- {e['title']} (metric: {e['metric_name'] or 'none'}, review: {e['review_date'] or 'unset'})\n"

    questions_text = "\n### DIAGNOSTIC QUESTIONS TO CONSIDER:\n"
    for q in pack.get("diagnostic_questions", []):
        questions_text += f"- {q}\n"

    metrics_text = ""
    domain_metrics = pack.get("metrics", [])
    if domain_metrics:
        metrics_text = "\n### AVAILABLE METRICS FOR THIS DOMAIN:\n"
        for m in domain_metrics:
            metrics_text += f"- {m['name']} ({m['type']}, {m['unit']})\n"

    full_context = ""
    if graph_context:
        full_context += f"{graph_context}\n\n---\n\n"
    full_context += f"### RETRIEVED KNOWLEDGE:\n{context_str}"

    review_date = (datetime.now(timezone.utc) + timedelta(days=14)).strftime("%Y-%m-%d")

    prompt = (
        f"Domain: {domain}\n"
        f"User's Goal/Problem: {goal_text}\n"
        f"Default review date: {review_date}\n"
        f"{questions_text}"
        f"{metrics_text}"
        f"{rules_text}"
        f"{goals_text}"
        f"{experiments_text}"
        f"\n---\n\n"
        f"{full_context}\n\n"
        f"Based on the above retrieved knowledge and context, generate a structured guidance brief as JSON."
    )

    raw_response = llm.generate_completion(GUIDANCE_SYSTEM_INSTRUCTION, prompt)

    # Parse the JSON response
    try:
        cleaned = raw_response.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()
        brief = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        import re
        
        # Regex-based fallback template parsing
        def extract(pattern, text, default=""):
            m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            return m.group(1).strip() if m else default
            
        def extract_list(pattern, text):
            block = extract(pattern, text)
            if not block: return []
            return [line.strip("- *").strip() for line in block.split("\n") if line.strip("- *")]

        # Attempt to salvage the most important parts into a brief structure
        brief = {
            "domain": domain,
            "stage": "exploring",
            "goal": goal_text,
            "next_action": extract(r"(?:next\s*action|action|step)[^:]*:?\s*([^\n]+)", cleaned, "Review the raw response below."),
            "success_condition": extract(r"success[^:]*:?\s*([^\n]+)", cleaned, ""),
            "failure_condition": extract(r"failure[^:]*:?\s*([^\n]+)", cleaned, ""),
            "review_date": extract(r"review[^:]*:?\s*([\d-]+)", cleaned, review_date),
            "relevant_principles": [{"principle": p, "source": "Retrieved Context"} for p in extract_list(r"principles?[\s\"*]*:?[\s\[]*\n(.*?)(?:\"?\s*,|\n\n|\Z)", cleaned) if p],
            "raw_response": raw_response,
            "parse_error": "Could not strictly parse LLM response as JSON. Showing best-effort extraction. Raw response is available below."
        }

    return brief


def format_brief_for_display(brief):
    """Renders a guidance brief as rich-formatted terminal output."""
    lines = []
    lines.append(f"[bold cyan]Domain:[/bold cyan] {brief.get('domain', 'unknown')}")
    lines.append(f"[bold cyan]Stage:[/bold cyan] {brief.get('stage', 'unknown')}")
    lines.append(f"[bold cyan]Goal:[/bold cyan] {brief.get('goal', '')}")

    if brief.get("parse_error"):
        lines.append(f"\n[bold yellow]⚠ Parse Warning:[/bold yellow] {brief['parse_error']}")
        if brief.get("raw_response"):
            lines.append(f"\n[dim]{brief['raw_response']}[/dim]")
        return "\n".join(lines)

    missing = brief.get("missing_information", [])
    if missing:
        lines.append("\n[bold yellow]❓ Missing Information:[/bold yellow]")
        for item in missing:
            lines.append(f"  • {item}")

    principles = brief.get("relevant_principles", [])
    if principles:
        lines.append("\n[bold green]📚 Relevant Principles:[/bold green]")
        for p in principles:
            lines.append(f"  • {p.get('principle', '')} [dim]— {p.get('source', '')}[/dim]")

    assumptions = brief.get("key_assumptions", [])
    if assumptions:
        lines.append("\n[bold blue]🔑 Key Assumptions:[/bold blue]")
        for a in assumptions:
            lines.append(f"  • {a}")

    risks = brief.get("risks_and_traps", [])
    if risks:
        lines.append("\n[bold red]⚠ Risks & Traps:[/bold red]")
        for r in risks:
            lines.append(f"  • {r}")

    metrics = brief.get("suggested_metrics", [])
    if metrics:
        lines.append("\n[bold magenta]📊 Suggested Metrics:[/bold magenta]")
        for m in metrics:
            lines.append(f"  • {m.get('name', '')} ({m.get('type', '')}, {m.get('unit', '')})")

    if brief.get("next_action"):
        lines.append(f"\n[bold green]▶ Next Action:[/bold green] {brief['next_action']}")

    if brief.get("success_condition"):
        lines.append(f"[bold green]✓ Success:[/bold green] {brief['success_condition']}")

    if brief.get("failure_condition"):
        lines.append(f"[bold red]✗ Failure/Kill:[/bold red] {brief['failure_condition']}")

    if brief.get("review_date"):
        lines.append(f"[bold cyan]📅 Review Date:[/bold cyan] {brief['review_date']}")

    rule_suggestions = brief.get("rule_suggestions", [])
    if rule_suggestions:
        lines.append("\n[bold yellow]📝 Suggested Rules:[/bold yellow]")
        for r in rule_suggestions:
            lines.append(f"  • {r}")

    return "\n".join(lines)


# ─── CLI Subcommands ──────────────────────────────────────────────────────────

def _resolve_db():
    """Resolves the database path from environment, matching existing CLI pattern."""
    db_path = resolve_db_path(os.getenv("DATABASE_PATH", "knowledge.db"))
    if not os.path.exists(db_path):
        init_db(db_path)
    else:
        # Ensure new tables exist on older databases
        init_db(db_path)
    return db_path


def main():
    """CLI handler for 'psyche guide' — generates a guidance brief."""
    parser = argparse.ArgumentParser(description="Generate a structured guidance brief from a goal or problem.")
    parser.add_argument("goal", nargs="?", help="The goal or problem to get guidance on.")
    parser.add_argument("--domain", "-d", help="Domain (e.g., business, health, wealth, career, happiness). Auto-detected if omitted.")
    parser.add_argument("--db-path", help="Database file path override.")
    args = parser.parse_args()

    if not args.goal:
        console.print("[bold red]Error:[/bold red] Please provide a goal or problem.")
        console.print("Usage: psyche guide \"Your goal or problem here\"")
        sys.exit(1)

    db_path = resolve_db_path(args.db_path or os.getenv("DATABASE_PATH", "knowledge.db"))
    if not os.path.exists(db_path):
        init_db(db_path)
    else:
        init_db(db_path)

    # Auto-detect domain if not provided
    domain = args.domain or detect_domain(args.goal)

    try:
        llm = LLMClient()
    except Exception as e:
        err_console.print(f"[bold red]Error initializing LLM client:[/bold red] {e}")
        sys.exit(1)

    if llm.chat_model == "none" or llm.provider == "none":
        console.print("[bold yellow]⚠ Guidance brief generation requires an LLM (chat model).[/bold yellow]")
        console.print("[dim]You can still use: psyche goal, psyche experiment, psyche log-metric, psyche review, psyche rules[/dim]")
        sys.exit(1)

    console.print(f"\n[bold green]🧭 Generating Guidance Brief[/bold green]")
    console.print(f"[dim]Domain: {domain} | Goal: {args.goal}[/dim]\n")

    with console.status("[bold cyan]Retrieving knowledge and generating brief..."):
        brief = generate_guidance_brief(args.goal, domain, db_path, llm)

    output = format_brief_for_display(brief)
    console.print(Panel(output, title="[bold]Guidance Brief[/bold]", border_style="cyan", padding=(1, 2)))
    console.print("")


def goal_main():
    """CLI handler for 'psyche goal' — manage goals."""
    parser = argparse.ArgumentParser(description="Manage personal goals.")
    sub = parser.add_subparsers(dest="action")

    # goal add
    add_p = sub.add_parser("add", help="Add a new goal.")
    add_p.add_argument("title", help="Goal title/description.")
    add_p.add_argument("--domain", "-d", default="general", help="Domain (default: general).")
    add_p.add_argument("--stage", "-s", default="exploring", help="Stage (default: exploring).")
    add_p.add_argument("--description", help="Longer description.")
    add_p.add_argument("--db-path", help="Database path override.")

    # goal list
    list_p = sub.add_parser("list", help="List goals.")
    list_p.add_argument("--domain", "-d", help="Filter by domain.")
    list_p.add_argument("--all", action="store_true", help="Include completed/abandoned goals.")
    list_p.add_argument("--db-path", help="Database path override.")

    # goal update
    update_p = sub.add_parser("update", help="Update a goal.")
    update_p.add_argument("id", type=int, help="Goal ID.")
    update_p.add_argument("--status", choices=["active", "paused", "completed", "abandoned"])
    update_p.add_argument("--stage", choices=["exploring", "planning", "executing", "reviewing"])
    update_p.add_argument("--title")
    update_p.add_argument("--db-path", help="Database path override.")

    args = parser.parse_args()
    db_path = _resolve_db()
    if args.action and hasattr(args, 'db_path') and args.db_path:
        db_path = resolve_db_path(args.db_path)
        init_db(db_path)

    conn = get_connection(db_path)
    try:
        if args.action == "add":
            goal_id = add_goal(conn, args.domain, args.title, args.description, args.stage)
            console.print(f"[bold green]✓ Goal #{goal_id} created:[/bold green] {args.title} [dim]({args.domain}, {args.stage})[/dim]")
        elif args.action == "list":
            status = None if args.all else "active"
            goals = get_goals(conn, domain=args.domain, status=status)
            if not goals:
                console.print("[dim]No goals found.[/dim]")
                return
            table = Table(title="Goals", show_header=True, header_style="bold cyan")
            table.add_column("ID", style="dim", width=4)
            table.add_column("Domain", width=10)
            table.add_column("Stage", width=10)
            table.add_column("Title")
            table.add_column("Status", width=10)
            for g in goals:
                status_style = "green" if g["status"] == "active" else "dim"
                table.add_row(str(g["id"]), g["domain"], g["stage"], g["title"], f"[{status_style}]{g['status']}[/{status_style}]")
            console.print(table)
        elif args.action == "update":
            updates = {}
            if args.status:
                updates["status"] = args.status
            if args.stage:
                updates["stage"] = args.stage
            if args.title:
                updates["title"] = args.title
            if updates:
                update_goal(conn, args.id, **updates)
                console.print(f"[bold green]✓ Goal #{args.id} updated.[/bold green]")
            else:
                console.print("[dim]No updates specified.[/dim]")
        else:
            parser.print_help()
    finally:
        conn.close()


def experiment_main():
    """CLI handler for 'psyche experiment' — manage experiments."""
    parser = argparse.ArgumentParser(description="Manage experiments.")
    sub = parser.add_subparsers(dest="action")

    # experiment add
    add_p = sub.add_parser("add", help="Create a new experiment.")
    add_p.add_argument("title", help="Experiment title.")
    add_p.add_argument("--goal", type=int, help="Goal ID to link to.")
    add_p.add_argument("--metric", help="Primary metric to track.")
    add_p.add_argument("--hypothesis", help="What you expect to happen.")
    add_p.add_argument("--success", help="Success condition.")
    add_p.add_argument("--failure", help="Failure/kill condition.")
    add_p.add_argument("--review", help="Review date (YYYY-MM-DD).")
    add_p.add_argument("--db-path", help="Database path override.")

    # experiment list
    list_p = sub.add_parser("list", help="List experiments.")
    list_p.add_argument("--goal", type=int, help="Filter by goal ID.")
    list_p.add_argument("--all", action="store_true", help="Include completed experiments.")
    list_p.add_argument("--db-path", help="Database path override.")

    # experiment complete
    complete_p = sub.add_parser("complete", help="Mark an experiment as completed.")
    complete_p.add_argument("id", type=int, help="Experiment ID.")
    complete_p.add_argument("--outcome", help="What happened.")
    complete_p.add_argument("--status", choices=["completed", "failed", "abandoned"], default="completed")
    complete_p.add_argument("--db-path", help="Database path override.")

    args = parser.parse_args()
    db_path = _resolve_db()
    if args.action and hasattr(args, 'db_path') and args.db_path:
        db_path = resolve_db_path(args.db_path)
        init_db(db_path)

    conn = get_connection(db_path)
    try:
        if args.action == "add":
            exp_id = add_experiment(
                conn, args.goal, args.title,
                hypothesis=args.hypothesis, metric_name=args.metric,
                success_condition=args.success, failure_condition=args.failure,
                review_date=args.review
            )
            console.print(f"[bold green]✓ Experiment #{exp_id} created:[/bold green] {args.title}")
        elif args.action == "list":
            status = None if args.all else "active"
            experiments = get_experiments(conn, goal_id=args.goal, status=status)
            if not experiments:
                console.print("[dim]No experiments found.[/dim]")
                return
            table = Table(title="Experiments", show_header=True, header_style="bold cyan")
            table.add_column("ID", style="dim", width=4)
            table.add_column("Goal", width=5)
            table.add_column("Title")
            table.add_column("Metric", width=15)
            table.add_column("Review", width=12)
            table.add_column("Status", width=10)
            for e in experiments:
                status_style = "green" if e["status"] == "active" else "dim"
                table.add_row(
                    str(e["id"]), str(e["goal_id"] or "-"), e["title"],
                    e["metric_name"] or "-", e["review_date"] or "-",
                    f"[{status_style}]{e['status']}[/{status_style}]"
                )
            console.print(table)
        elif args.action == "complete":
            update_experiment(conn, args.id, status=args.status, outcome=args.outcome)
            console.print(f"[bold green]✓ Experiment #{args.id} marked as {args.status}.[/bold green]")
        else:
            parser.print_help()
    finally:
        conn.close()


def log_metric_main():
    """CLI handler for 'psyche log-metric' — log a metric data point."""
    parser = argparse.ArgumentParser(description="Log a metric measurement.")
    parser.add_argument("metric_name", help="Name of the metric (e.g., reply_rate, mood, weight).")
    parser.add_argument("value", type=float, help="The metric value.")
    parser.add_argument("--unit", help="Unit of measurement.")
    parser.add_argument("--experiment", type=int, help="Experiment ID to link to.")
    parser.add_argument("--goal", type=int, help="Goal ID to link to.")
    parser.add_argument("--note", help="Optional note about this measurement.")
    parser.add_argument("--db-path", help="Database path override.")
    args = parser.parse_args()

    db_path = _resolve_db()
    if args.db_path:
        db_path = resolve_db_path(args.db_path)
        init_db(db_path)

    conn = get_connection(db_path)
    try:
        log_id = add_metric_log(
            conn, args.metric_name, args.value,
            unit=args.unit, note=args.note,
            experiment_id=args.experiment, goal_id=args.goal
        )
        console.print(f"[bold green]✓ Metric logged:[/bold green] {args.metric_name} = {args.value} {args.unit or ''} [dim](ID: {log_id})[/dim]")
    finally:
        conn.close()


def review_main():
    """CLI handler for 'psyche review' — manage reviews."""
    parser = argparse.ArgumentParser(description="Manage reviews and reflections.")
    sub = parser.add_subparsers(dest="action")

    # review add
    add_p = sub.add_parser("add", help="Write a review.")
    add_p.add_argument("--experiment", type=int, help="Experiment ID.")
    add_p.add_argument("--goal", type=int, help="Goal ID.")
    add_p.add_argument("--happened", required=True, help="What happened.")
    add_p.add_argument("--worked", help="What worked.")
    add_p.add_argument("--didnt", help="What didn't work.")
    add_p.add_argument("--lesson", help="Key lesson learned.")
    add_p.add_argument("--next", help="Next action.")
    add_p.add_argument("--db-path", help="Database path override.")

    # review list
    list_p = sub.add_parser("list", help="List reviews.")
    list_p.add_argument("--goal", type=int, help="Filter by goal ID.")
    list_p.add_argument("--experiment", type=int, help="Filter by experiment ID.")
    list_p.add_argument("--db-path", help="Database path override.")

    args = parser.parse_args()
    db_path = _resolve_db()
    if args.action and hasattr(args, 'db_path') and args.db_path:
        db_path = resolve_db_path(args.db_path)
        init_db(db_path)

    conn = get_connection(db_path)
    try:
        if args.action == "add":
            review_id = add_review(
                conn, args.happened,
                what_worked=args.worked, what_didnt=args.didnt,
                lesson=args.lesson, next_action=args.next,
                experiment_id=args.experiment, goal_id=args.goal
            )
            console.print(f"[bold green]✓ Review #{review_id} saved.[/bold green]")

            # If a lesson was provided, suggest saving it as a rule
            if args.lesson:
                console.print(f"\n[bold yellow]💡 Tip:[/bold yellow] Save this lesson as a personal rule:")
                domain = "general"
                if args.goal:
                    goals = get_goals(conn, status=None)
                    for g in goals:
                        if g["id"] == args.goal:
                            domain = g["domain"]
                            break
                console.print(f"  psyche rules add \"{args.lesson}\" --domain {domain} --source review:{review_id}")
        elif args.action == "list":
            reviews = get_reviews(conn, goal_id=args.goal, experiment_id=args.experiment)
            if not reviews:
                console.print("[dim]No reviews found.[/dim]")
                return
            for r in reviews:
                console.print(Panel(
                    f"[bold]What happened:[/bold] {r['what_happened'] or '-'}\n"
                    f"[bold green]Worked:[/bold green] {r['what_worked'] or '-'}\n"
                    f"[bold red]Didn't work:[/bold red] {r['what_didnt'] or '-'}\n"
                    f"[bold yellow]Lesson:[/bold yellow] {r['lesson'] or '-'}\n"
                    f"[bold cyan]Next action:[/bold cyan] {r['next_action'] or '-'}",
                    title=f"Review #{r['id']} [dim]({r['created_at'][:10]})[/dim]",
                    border_style="yellow"
                ))
        else:
            parser.print_help()
    finally:
        conn.close()


def rules_main():
    """CLI handler for 'psyche rules' — manage personal rules."""
    parser = argparse.ArgumentParser(description="Manage personal rules and learnings.")
    sub = parser.add_subparsers(dest="action")

    # rules add
    add_p = sub.add_parser("add", help="Add a personal rule.")
    add_p.add_argument("rule", help="The rule text.")
    add_p.add_argument("--domain", "-d", default="general", help="Domain (default: general).")
    add_p.add_argument("--source", help="Source of this rule (e.g., review:1, book:Title).")
    add_p.add_argument("--confidence", choices=["tentative", "tested", "proven"], default="tentative")
    add_p.add_argument("--db-path", help="Database path override.")

    # rules list
    list_p = sub.add_parser("list", help="List personal rules.")
    list_p.add_argument("--domain", "-d", help="Filter by domain.")
    list_p.add_argument("--all", action="store_true", help="Include inactive rules.")
    list_p.add_argument("--db-path", help="Database path override.")

    # rules update
    update_p = sub.add_parser("update", help="Update a rule.")
    update_p.add_argument("id", type=int, help="Rule ID.")
    update_p.add_argument("--confidence", choices=["tentative", "tested", "proven"])
    update_p.add_argument("--active", choices=["true", "false"])
    update_p.add_argument("--text", help="Updated rule text.")
    update_p.add_argument("--db-path", help="Database path override.")

    args = parser.parse_args()
    db_path = _resolve_db()
    if args.action and hasattr(args, 'db_path') and args.db_path:
        db_path = resolve_db_path(args.db_path)
        init_db(db_path)

    conn = get_connection(db_path)
    try:
        if args.action == "add":
            rule_id = add_rule(conn, args.domain, args.rule, source=args.source, confidence=args.confidence)
            console.print(f"[bold green]✓ Rule #{rule_id} saved:[/bold green] {args.rule} [dim]({args.domain}, {args.confidence})[/dim]")
        elif args.action == "list":
            active = not args.all
            rules = get_rules(conn, domain=args.domain, active=active)
            if not rules:
                console.print("[dim]No rules found.[/dim]")
                return
            table = Table(title="Personal Rules", show_header=True, header_style="bold cyan")
            table.add_column("ID", style="dim", width=4)
            table.add_column("Domain", width=10)
            table.add_column("Rule")
            table.add_column("Confidence", width=10)
            table.add_column("Source", width=15)
            for r in rules:
                conf_style = {"tentative": "yellow", "tested": "cyan", "proven": "green"}.get(r["confidence"], "dim")
                table.add_row(str(r["id"]), r["domain"], r["rule_text"], f"[{conf_style}]{r['confidence']}[/{conf_style}]", r["source"] or "-")
            console.print(table)
        elif args.action == "update":
            updates = {}
            if args.confidence:
                updates["confidence"] = args.confidence
            if args.active is not None:
                updates["active"] = 1 if args.active == "true" else 0
            if args.text:
                updates["rule_text"] = args.text
            if updates:
                update_rule(conn, args.id, **updates)
                console.print(f"[bold green]✓ Rule #{args.id} updated.[/bold green]")
            else:
                console.print("[dim]No updates specified.[/dim]")
        else:
            parser.print_help()
    finally:
        conn.close()


# ─── MCP Tool Handlers ───────────────────────────────────────────────────────

def generate_guidance_tool(goal_text, domain=None, topic=None):
    """MCP tool handler for generating a guidance brief."""
    db_path = resolve_db_path(os.getenv("DATABASE_PATH", "knowledge.db"))
    if topic:
        db_path = resolve_db_path(f"topic_{topic}.db")

    if not os.path.exists(db_path):
        init_db(db_path)
    else:
        init_db(db_path)

    if not domain:
        domain = detect_domain(goal_text)

    try:
        llm = LLMClient()
        if llm.chat_model == "none" or llm.provider == "none":
            return "Error: Guidance brief generation requires an LLM (chat model). Configure one with 'psyche setup'."
    except Exception as e:
        return f"Error initializing LLM client: {e}"

    try:
        brief = generate_guidance_brief(goal_text, domain, db_path, llm)
        return json.dumps(brief, indent=2)
    except Exception as e:
        return f"Error generating guidance brief: {e}"


def list_goals_experiments_tool(domain=None, topic=None):
    """MCP tool handler for listing active goals and experiments."""
    db_path = resolve_db_path(os.getenv("DATABASE_PATH", "knowledge.db"))
    if topic:
        db_path = resolve_db_path(f"topic_{topic}.db")

    if not os.path.exists(db_path):
        return "No goals or experiments found. Database does not exist yet."

    init_db(db_path)
    conn = get_connection(db_path)
    try:
        goals = get_goals(conn, domain=domain, status='active')
        experiments = get_experiments(conn, status='active')
        rules = get_rules(conn, domain=domain)

        output = ""
        if goals:
            output += "### ACTIVE GOALS:\n"
            for g in goals:
                output += f"- [#{g['id']}] [{g['domain']}] {g['title']} (stage: {g['stage']})\n"
        else:
            output += "No active goals.\n"

        if experiments:
            output += "\n### ACTIVE EXPERIMENTS:\n"
            for e in experiments:
                output += f"- [#{e['id']}] {e['title']} (metric: {e['metric_name'] or 'none'}, review: {e['review_date'] or 'unset'})\n"

        if rules:
            output += "\n### PERSONAL RULES:\n"
            for r in rules:
                output += f"- [{r['confidence']}] {r['rule_text']}\n"

        return output.strip() or "No active goals, experiments, or rules found."
    except Exception as e:
        return f"Error: {e}"
    finally:
        conn.close()
