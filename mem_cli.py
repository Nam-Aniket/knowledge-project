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


def _print_ledger_section():
    """Token ledger summary (populated by the hook injection ledger)."""
    if not hasattr(memzero, "ledger_summary"):
        return
    summary = memzero.ledger_summary()
    if summary["total_injections"] == 0:
        return
    console.print(
        f"\n[bold]Token ledger[/bold] — injections: {summary['total_injections']}, "
        f"facts injected: {summary['total_facts']}, "
        f"tokens injected: ~{summary['tokens_injected']} (estimate, ~chars/4), "
        f"re-derivation avoided: ~{summary['tokens_injected']} tokens"
    )


if __name__ == "__main__":
    main()
