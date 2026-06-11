"""SessionStart hook: inject standing preference/decision/lesson facts (~1.5 KB cap)."""
import sys
import _hook_common as hc


def main():
    payload = hc.read_payload()
    session_id = payload.get("session_id", "")
    import memzero
    rows = memzero.standing_fact_rows(top=12)
    if not rows:
        return
    text = memzero.format_facts(rows, max_chars=1500)
    print("Known durable facts about this user/project (Psyche memory):")
    print(text)
    hc.write_ledger(session_id, hc.read_ledger(session_id) | {r["id"] for r in rows})
    hc.log(f"session_start {session_id}: injected {len(rows)} facts")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        hc.log(f"session_start error: {e}")
    sys.exit(0)
