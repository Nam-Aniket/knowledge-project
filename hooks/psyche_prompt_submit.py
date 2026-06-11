"""UserPromptSubmit hook: inject facts relevant to the prompt (~2.5 KB cap, gated).

Skips trivial prompts and facts already injected this session.
"""
import sys
import _hook_common as hc


def main():
    payload = hc.read_payload()
    session_id = payload.get("session_id", "")
    prompt = (payload.get("prompt") or "").strip()
    if len(prompt) < 30 or prompt.startswith("/") or prompt.startswith("#"):
        return
    import memzero
    results = memzero.search_memories(prompt, top=6)
    seen = hc.read_ledger(session_id)
    fresh = [r for r in results if r["id"] not in seen]
    if not fresh:
        return
    print("Relevant facts from Psyche memory:")
    print(memzero.format_facts(fresh, max_chars=2500))
    hc.write_ledger(session_id, seen | {r["id"] for r in fresh})
    hc.log(f"prompt_submit {session_id}: injected {len(fresh)} facts")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        hc.log(f"prompt_submit error: {e}")
    sys.exit(0)
