"""Shared helpers for Psyche Claude Code hooks.

Hooks must never break the user's session: every entry point swallows all
exceptions and exits 0. Debug output goes to ~/.psyche/memzero_hook.log.
"""
import json
import os
import sys

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)
os.environ.setdefault("PSYCHE_NONINTERACTIVE", "1")

LOG_PATH = os.path.expanduser("~/.psyche/memzero_hook.log")


def recursion_guard():
    """Exits immediately when running inside a headless claude spawned by a
    hook (PSYCHE_MEM_HOOK=1), so extraction can't trigger hooks recursively."""
    if os.environ.get("PSYCHE_MEM_HOOK") == "1":
        sys.exit(0)


def log(msg: str):
    try:
        with open(LOG_PATH, "a") as f:
            f.write(msg.rstrip() + "\n")
    except Exception:
        pass


def read_payload() -> dict:
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


def cwd_from_payload(payload) -> str | None:
    return payload.get("cwd") or payload.get("workspace") or None


MEM_LEDGER_PATH = os.path.expanduser("~/.psyche/mem_ledger.jsonl")


def append_ledger(event: str, session_id: str, count: int, chars: int, path: str = None):
    """Appends one JSON line: {ts, event, session_id, count, chars}.
    Swallows all errors (hooks must never break)."""
    from datetime import datetime, timezone
    try:
        with open(path or MEM_LEDGER_PATH, "a") as f:
            f.write(json.dumps({
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": event,
                "session_id": session_id,
                "count": count,
                "chars": chars,
            }) + "\n")
    except Exception:
        pass


def ledger_path(session_id: str) -> str:
    safe = "".join(c for c in (session_id or "unknown") if c.isalnum() or c in "-_")
    return f"/tmp/psyche_mem_ledger_{safe}.json"


def read_ledger(session_id: str) -> set:
    try:
        with open(ledger_path(session_id)) as f:
            return set(json.load(f))
    except Exception:
        return set()


def write_ledger(session_id: str, ids: set):
    try:
        with open(ledger_path(session_id), "w") as f:
            json.dump(sorted(ids), f)
    except Exception:
        pass
