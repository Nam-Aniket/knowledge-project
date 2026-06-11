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
    get_connection, resolve_db_path, init_db, index_path_for,
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

# Cache of parsed domain packs keyed by pack file path → (mtime, parsed_dict).
# Avoids re-reading/re-parsing YAML/JSON on every guidance call.
_PACK_CACHE = {}

# Whether ensure_domain_packs has already run its seed-copy loop this process.
_PACKS_SEEDED = False

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
    """Creates the domains directory and seeds default packs if they don't exist.

    The seed-copy loop only runs once per process; subsequent calls are no-ops.
    """
    global _PACKS_SEEDED
    if _PACKS_SEEDED:
        return
    import shutil
    os.makedirs(DOMAINS_DIR, exist_ok=True)
    seed_paths = _get_seed_pack_paths()
    for src in seed_paths:
        filename = os.path.basename(src)
        dst = os.path.join(DOMAINS_DIR, filename)
        if not os.path.exists(dst):
            shutil.copy2(src, dst)
    _PACKS_SEEDED = True


def _load_pack_file(pack_path):
    """Reads and parses a pack file, caching by path and re-parsing only when
    the file's mtime changes."""
    try:
        mtime = os.path.getmtime(pack_path)
    except OSError:
        mtime = None

    cached = _PACK_CACHE.get(pack_path)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    with open(pack_path, "r", encoding="utf-8") as f:
        if pack_path.endswith(".yaml"):
            try:
                import yaml
                parsed = yaml.safe_load(f)
            except ImportError:
                f.seek(0)
                parsed = json.load(f)
        else:
            parsed = json.load(f)

    _PACK_CACHE[pack_path] = (mtime, parsed)
    return parsed


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

    return _load_pack_file(pack_path)


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

from plan_schema import PLAN_SCHEMA_DESCRIPTION, parse_plan_response, coerce_plan, empty_plan

GUIDANCE_SYSTEM_INSTRUCTION = f"""\
You are Psyche, a knowledge-grounded planning engine. You turn a goal into an
ACTIONABLE PLAN the user can execute this week — never vague advice.

RULES:
1. Ground the diagnosis and principles in RETRIEVED KNOWLEDGE and KNOWN FACTS
   provided below. Cite sources (Title, Location) for principles.
2. Produce 2-5 concrete actions the USER performs themselves. Each action must be
   small enough to finish within its time_estimate, have an observable
   success_criterion, a horizon (today|this_week|this_month), and a
   due_offset_days. Attach a metric when the action's progress is measurable.
3. Pick first_action_index: the single smallest action to start with now.
4. If retrieved knowledge is thin, still produce actions, but keep diagnosis honest.
5. Output ONLY valid JSON matching the schema. No markdown, no commentary.

SCHEMA:
{PLAN_SCHEMA_DESCRIPTION}
"""


def generate_guidance_brief(goal_text, domain, db_path, llm):
    """Generates a schema-validated ACTION PLAN by retrieving knowledge and
    using LLM synthesis with retry-once strict parsing. Falls back to a
    retrieval-only brief (actions: []) when no chat model is configured."""
    from query import perform_hybrid_search, format_context, retrieve_concept_context
    from db import get_all_embeddings_only, search_vector_vec
    import numpy as np

    if getattr(llm, "chat_model", "none") == "none" or getattr(llm, "provider", "none") == "none":
        return retrieval_only_brief(goal_text, domain, db_path, llm)

    pack = load_domain_pack(domain)
    conn = get_connection(db_path)

    try:
        # Gather context: existing rules, goals, experiments
        existing_rules = get_rules(conn, domain=domain)
        active_goals = get_goals(conn, domain=domain, status='active')
        active_experiments = get_experiments(conn, status='active')
    finally:
        conn.close()

    # Atomic-memory context: known durable facts about the user.
    import memzero
    facts = memzero.search_memories(goal_text, top=6, db_path=db_path, llm=llm)
    facts_text = memzero.format_facts(facts, max_chars=1500) if facts else ""

    # Retrieve knowledge using existing hybrid search pipeline.
    # Try loading the usearch index FIRST; the full embeddings matrix is only a
    # numpy fallback used when the index is absent.
    records = []
    chunk_ids = np.array([], dtype=np.int32)
    embeddings_matrix = np.array([], dtype=np.float32)
    usearch_index = None
    if llm.provider != "none":
        index_path = index_path_for(db_path)
        try:
            from usearch.index import Index
            if os.path.exists(index_path):
                usearch_index = Index.restore(index_path)
        except Exception:
            usearch_index = None

        if usearch_index is None:
            # No index: fall back to loading all embeddings into a numpy matrix.
            conn = get_connection(db_path)
            try:
                records = get_all_embeddings_only(conn)
            finally:
                conn.close()
            chunk_ids = np.array([r["chunk_id"] for r in records if r["embedding"] is not None], dtype=np.int32)
            valid_embeddings = [r["embedding"] for r in records if r["embedding"] is not None]
            if valid_embeddings:
                embeddings_matrix = np.vstack(valid_embeddings)

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

    facts_block = ""
    if facts_text:
        facts_block = f"\n### KNOWN FACTS ABOUT THE USER:\n{facts_text}\n"

    prompt = (
        f"Domain: {domain}\n"
        f"User's Goal/Problem: {goal_text}\n"
        f"Default review date: {review_date}\n"
        f"{questions_text}"
        f"{metrics_text}"
        f"{rules_text}"
        f"{goals_text}"
        f"{experiments_text}"
        f"{facts_block}"
        f"\n---\n\n"
        f"{full_context}\n\n"
        f"Based on the above retrieved knowledge and context, generate an actionable plan as JSON."
    )

    raw = llm.generate_completion(GUIDANCE_SYSTEM_INSTRUCTION, prompt)
    plan, reason = parse_plan_response(raw, goal_text, domain)
    if plan is None or not plan.get("actions"):
        retry_prompt = prompt + (
            "\n\nYour previous reply was not valid or had no actions. "
            "Reply again with ONLY the JSON object, 2-5 concrete actions, no prose."
        )
        raw = llm.generate_completion(GUIDANCE_SYSTEM_INSTRUCTION, retry_prompt)
        plan, reason = parse_plan_response(raw, goal_text, domain)
    if plan is None:
        plan = empty_plan(goal_text, domain)
        plan["diagnosis"] = "Could not generate a structured plan from the model."
    else:
        plan = coerce_plan(plan, goal_text, domain)
    return plan


def retrieval_only_brief(goal_text, domain, db_path, llm=None):
    """Builds an action-less brief from local records when no chat model is
    configured: top rules, matching open experiments, relevant atomic facts,
    and top retrieved principles. Returns a plan dict (actions: []) plus
    'open_experiments'/'facts' renderings. Never calls llm.generate_completion."""
    from query import perform_hybrid_search, format_context
    import numpy as np
    import memzero

    conn = get_connection(db_path)
    try:
        existing_rules = get_rules(conn, domain=domain)
        active_experiments = get_experiments(conn, status='active')
    finally:
        conn.close()

    plan = empty_plan(goal_text, domain)
    plan["diagnosis"] = "Retrieval-only brief (no chat model configured)."
    plan["rule_suggestions"] = [r["rule_text"] for r in existing_rules][:8]
    plan["open_experiments"] = [
        {"id": e["id"], "title": e["title"], "review_date": e.get("review_date")}
        for e in active_experiments
    ][:8]

    if llm is not None and getattr(llm, "provider", "none") != "none":
        usearch_index = None
        try:
            from usearch.index import Index
            index_path = index_path_for(db_path)
            if os.path.exists(index_path):
                usearch_index = Index.restore(index_path)
        except Exception:
            usearch_index = None
        try:
            similarities = perform_hybrid_search(
                db_path=db_path,
                query_text=goal_text,
                chunk_ids=np.array([], dtype=np.int32),
                embeddings_matrix=np.array([], dtype=np.float32),
                llm=llm,
                usearch_index=usearch_index,
                limit=5,
            )
            plan["relevant_principles"] = [
                {"principle": (chunk.get("text") or "")[:200],
                 "source": f"{chunk.get('source_title', 'Unknown')}, {chunk.get('location') or 'n/a'}"}
                for chunk, _score in similarities[:5]
            ]
        except Exception:
            pass
        plan["facts"] = memzero.search_memories(goal_text, top=6, db_path=db_path, llm=llm)
    else:
        plan["facts"] = []

    return plan


def format_brief_for_display(brief):
    """Renders an actionable plan as rich-formatted terminal output."""
    lines = []
    lines.append(f"[bold cyan]Domain:[/bold cyan] {brief.get('domain', 'unknown')}")
    lines.append(f"[bold cyan]Goal:[/bold cyan] {brief.get('goal', '')}")

    if brief.get("diagnosis"):
        lines.append(f"\n[bold blue]🩺 Diagnosis:[/bold blue] {brief['diagnosis']}")

    actions = brief.get("actions", [])
    if actions:
        lines.append("\n[bold green]✅ Actions:[/bold green]")
        first = brief.get("first_action_index", 0)
        for i, a in enumerate(actions):
            marker = " [bold yellow]▶ START HERE[/bold yellow]" if i == first else ""
            metric = a.get("metric") or {}
            metric_str = f" · metric: {metric['name']} ({metric.get('unit','')})" if metric.get("name") else ""
            lines.append(
                f"  {i + 1}. {a.get('action', '')}{marker}\n"
                f"     [dim]~{a.get('time_estimate_min', '?')}min · {a.get('horizon', '')} · "
                f"due in {a.get('due_offset_days', '?')}d · success: {a.get('success_criterion', '')}{metric_str}[/dim]"
            )

    principles = brief.get("relevant_principles", [])
    if principles:
        lines.append("\n[bold green]📚 Principles:[/bold green]")
        for p in principles:
            lines.append(f"  • {p.get('principle', '')} [dim]— {p.get('source', '')}[/dim]")

    rule_suggestions = brief.get("rule_suggestions", [])
    if rule_suggestions:
        lines.append("\n[bold yellow]📝 Suggested Rules:[/bold yellow]")
        for r in rule_suggestions:
            lines.append(f"  • {r}")

    facts = brief.get("facts", [])
    if facts:
        lines.append("\n[bold magenta]🧠 Facts considered:[/bold magenta]")
        for f in facts:
            fact_text = f.get("fact", "") if isinstance(f, dict) else str(f)
            lines.append(f"  • {fact_text}")

    open_experiments = brief.get("open_experiments", [])
    if open_experiments:
        lines.append("\n[bold blue]🧪 Open Experiments:[/bold blue]")
        for e in open_experiments:
            lines.append(f"  • [#{e.get('id')}] {e.get('title', '')} [dim](review: {e.get('review_date') or 'unset'})[/dim]")

    if brief.get("review_in_days"):
        lines.append(f"\n[bold cyan]📅 Review in:[/bold cyan] {brief['review_in_days']} days")

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

    console.print(f"\n[bold green]🧭 Generating Guidance Brief[/bold green]")
    console.print(f"[dim]Domain: {domain} | Goal: {args.goal}[/dim]\n")

    with console.status("[bold cyan]Retrieving knowledge and generating brief..."):
        brief = generate_guidance_brief(args.goal, domain, db_path, llm)

    no_chat = llm.chat_model == "none" or llm.provider == "none"
    if no_chat and brief.get("actions") == []:
        console.print("[bold yellow]Retrieval-only brief (no chat model configured)[/bold yellow]")

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
