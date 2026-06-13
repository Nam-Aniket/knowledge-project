#!/usr/bin/env python3
"""CLI for Psyche's atomic memory store: psyche mem <subcommand>."""
import argparse
import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from rich.console import Console
from rich.table import Table

import memzero
from db import resolve_db_path

console = Console()


def _db(args):
    return resolve_db_path(args.db_path or os.getenv("DATABASE_PATH", "knowledge.db"))


def _print_facts(rows):
    if not rows:
        console.print("[dim]No facts found.[/dim]")
        return
    table = Table(show_header=True, header_style="bold cyan")
    for col in ("id", "fact", "category", "project", "hits", "updated"):
        table.add_column(col)
    for r in rows:
        table.add_row(
            str(r["id"]), r["fact"], r.get("category") or "-",
            r.get("project") or "(global)",
            str(r.get("retrieval_count", 0)), (r.get("updated_at") or "")[:10],
        )
    console.print(table)


def main():
    parser = argparse.ArgumentParser(prog="psyche mem", description="Manage atomic memory facts.")
    sub = parser.add_subparsers(dest="action", required=True)

    p_list = sub.add_parser("list", help="List live facts (newest first).")
    p_list.add_argument("--limit", type=int, default=50)
    p_list.add_argument("--project")
    p_list.add_argument("--category")
    p_list.add_argument("--all", action="store_true", help="Include superseded facts.")
    p_list.add_argument("--db-path")

    p_search = sub.add_parser("search", help="Hybrid search over facts.")
    p_search.add_argument("query")
    p_search.add_argument("--top", type=int, default=8)
    p_search.add_argument("--project")
    p_search.add_argument("--db-path")

    p_add = sub.add_parser("add", help="Store a fact.")
    p_add.add_argument("fact")
    p_add.add_argument("--category")
    p_add.add_argument("--entities", help="Comma-separated entity names.")
    p_add.add_argument("--project")
    p_add.add_argument("--db-path")

    p_del = sub.add_parser("delete", help="Delete a fact by id.")
    p_del.add_argument("id", type=int)
    p_del.add_argument("--db-path")

    p_prune = sub.add_parser("prune", help="Delete never-retrieved facts older than N weeks.")
    p_prune.add_argument("--stale", type=int, default=8, metavar="WEEKS")
    p_prune.add_argument("--yes", action="store_true", help="Skip confirmation.")
    p_prune.add_argument("--db-path")

    p_stats = sub.add_parser("stats", help="Memory store statistics.")
    p_stats.add_argument("--db-path")

    p_forget = sub.add_parser("forget", help="Soft-retire memories matching a query, then optionally hard-delete.")
    p_forget.add_argument("query", help="Search query to find memories to forget.")
    p_forget.add_argument("--db-path")

    p_review = sub.add_parser("review", help="List currently soft-retired (hidden) memories.")
    p_review.add_argument("--db-path")

    p_unforget = sub.add_parser("unforget", help="Restore a soft-retired memory by id.")
    p_unforget.add_argument("id", type=int, help="Memory id to restore.")
    p_unforget.add_argument("--db-path")

    args = parser.parse_args()
    db_path = _db(args)

    if args.action == "list":
        _print_facts(memzero.list_memories(
            limit=args.limit, project=args.project, category=args.category,
            include_superseded=args.all, db_path=db_path,
        ))
    elif args.action == "search":
        results = memzero.search_memories(args.query, top=args.top, project=args.project, db_path=db_path)
        if results:
            for r in results:
                cat = f" ({r['category']})" if r.get("category") else ""
                console.print(f"  • [#{r['id']}] {r['fact']}{cat}")
        else:
            console.print("[dim]No relevant facts found.[/dim]")
    elif args.action == "add":
        entities = [e.strip() for e in (args.entities or "").split(",") if e.strip()] or None
        result = memzero.add_memory(args.fact, category=args.category, entities=entities,
                                    project=args.project, db_path=db_path)
        if result["duplicate_of"] is not None:
            console.print(f"[yellow]Skipped: near-duplicate of fact #{result['duplicate_of']}.[/yellow]")
        else:
            console.print(f"[green]Stored fact #{result['id']}.[/green]")
            if result.get("superseded"):
                console.print(f"[dim]Superseded older fact #{result['superseded']}.[/dim]")
    elif args.action == "delete":
        ok = memzero.delete_memory(args.id, db_path=db_path)
        console.print("[green]Deleted.[/green]" if ok else "[red]Fact not found.[/red]")
    elif args.action == "prune":
        candidates = memzero.prune_stale(weeks=args.stale, dry_run=True, db_path=db_path)
        if not candidates:
            console.print("[dim]Nothing to prune.[/dim]")
            return
        console.print(f"{len(candidates)} never-retrieved facts older than {args.stale} weeks: {candidates}")
        if not args.yes:
            from rich.prompt import Confirm
            if not Confirm.ask("Delete them?"):
                console.print("[dim]Aborted.[/dim]")
                return
        deleted = memzero.prune_stale(weeks=args.stale, dry_run=False, db_path=db_path)
        console.print(f"[green]Pruned {len(deleted)} facts.[/green]")
    elif args.action == "stats":
        s = memzero.stats(db_path=db_path)
        console.print(f"[bold]Total live facts:[/bold] {s['total']}")
        if s["by_category"]:
            console.print("[bold]By category:[/bold] " + ", ".join(f"{k}: {v}" for k, v in s["by_category"].items()))
        if s["by_project"]:
            console.print("[bold]By project:[/bold] " + ", ".join(f"{k}: {v}" for k, v in s["by_project"].items()))
        console.print(f"[bold]Total retrievals:[/bold] {s['total_retrievals']} · [bold]Never retrieved:[/bold] {s['never_retrieved']}")
        _print_ledger_section()
    elif args.action == "forget":
        result = memzero.forget_memory(query=args.query, db_path=db_path)
        candidates = result.get("candidates", [])
        retired = result.get("retired", [])
        if not candidates:
            console.print("[dim]No memories found matching that query.[/dim]")
            return
        table = Table(show_header=True, header_style="bold cyan")
        for col in ("id", "fact", "category", "score"):
            table.add_column(col)
        for c in candidates:
            table.add_row(str(c["id"]), c["fact"], c.get("category") or "-",
                          f"{c.get('score', 0.0):.3f}")
        console.print(table)
        console.print(f"[yellow]{len(retired)} memory/memories soft-retired (hidden from injection).[/yellow]")
        console.print(f"[dim]To undo: psyche mem unforget <id>[/dim]")
        from rich.prompt import Confirm
        if Confirm.ask(f"Permanently delete these {len(retired)} memory/memories? (No = keep them retired/hidden)"):
            hard_result = memzero.forget_memory(ids=retired, confirm=True, hard=True, db_path=db_path)
            deleted = hard_result.get("deleted", [])
            console.print(f"[red]Permanently deleted {len(deleted)} memory/memories.[/red]")
        else:
            console.print(f"[dim]Memories remain retired (hidden). Use 'psyche mem unforget <id>' to restore.[/dim]")
    elif args.action == "review":
        from db import get_connection
        from db import resolve_db_path as _resolve
        resolved = _resolve(db_path)
        conn = get_connection(resolved)
        try:
            rows = conn.execute(
                "SELECT id, fact, category, project, retrieval_count, updated_at, retired_at "
                "FROM atomic_memories WHERE retired_at IS NOT NULL ORDER BY retired_at DESC LIMIT 100"
            ).fetchall()
        except Exception:
            rows = []
        finally:
            conn.close()
        if not rows:
            console.print("[dim]No retired memories.[/dim]")
            return
        table = Table(show_header=True, header_style="bold yellow")
        for col in ("id", "fact", "category", "project", "hits", "updated", "retired_at"):
            table.add_column(col)
        for r in rows:
            table.add_row(
                str(r[0]), r[1], r[2] or "-", r[3] or "(global)",
                str(r[4] or 0), (r[5] or "")[:10], (r[6] or "")[:10],
            )
        console.print(table)
        console.print(f"[dim]{len(rows)} retired memory/memories. Use 'psyche mem unforget <id>' to restore.[/dim]")
    elif args.action == "unforget":
        result = memzero.unforget(ids=[args.id], db_path=db_path)
        if result["unretired"]:
            console.print(f"[green]Restored memory #{args.id}.[/green]")
        else:
            console.print(f"[red]Memory #{args.id} not found or not retired.[/red]")


def _print_ledger_section():
    """Token ledger summary (populated by the hook injection ledger)."""
    if not hasattr(memzero, "ledger_summary"):
        return
    summary = memzero.ledger_summary(with_transcripts=True)
    if summary["total_injections"] == 0:
        return
    console.print(
        f"\n[bold]Token ledger[/bold] — injections: {summary['total_injections']}, "
        f"facts injected: {summary['total_facts']}, "
        f"tokens injected (approx, ~chars/4): ~{summary['tokens_injected']}, "
        f"re-derivation avoided: ~{summary['tokens_injected']} tokens"
    )
    console.print(
        f"[bold]Cache exposure[/bold] — "
        f"block changes within project: {summary.get('session_block_changes', 0)} across "
        f"{summary.get('session_start_count', 0)} sessions (lower is better) · "
        f"prompt facts: {summary.get('prompt_submit_facts', 0)} over "
        f"{summary.get('prompt_submit_count', 0)} turns"
    )
    measured_sessions = summary.get("measured_sessions", 0)
    session_start_count = summary.get("session_start_count", 0)
    if measured_sessions > 0:
        cache_read_share = summary.get("cache_read_share", 0)
        coverage_note = ""
        measured_coverage = summary.get("measured_coverage", 1.0)
        if 0 < measured_coverage < 1:
            coverage_note = f" ({measured_sessions}/{session_start_count} sessions had transcripts)"
        console.print(
            f"[bold]Measured cache (Claude Code)[/bold] — "
            f"{cache_read_share:.0%} of input tokens served from prompt cache "
            f"across {measured_sessions} sessions "
            f"(measured from transcripts; reflects Claude Code's whole cached prefix, "
            f"not Psyche alone){coverage_note}"
        )
    warm_sessions = summary.get("warm_sessions", 0)
    if warm_sessions > 0:
        block_tokens = summary.get("block_tokens", 0)
        psyche_avoided_tokens = summary.get("psyche_avoided_tokens", 0)
        console.print(
            f"[bold]Psyche cost-avoidance[/bold] — "
            f"stable block ~{block_tokens} tok kept warm at 0.1x on {warm_sessions} warm sessions; "
            f"vs a cache-busting injector (re-write at 1.25x), "
            f"avoided ~{psyche_avoided_tokens} input-token-equivalents "
            f"(modeled, block-attributable)"
        )


if __name__ == "__main__":
    main()
