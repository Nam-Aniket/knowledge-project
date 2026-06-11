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
