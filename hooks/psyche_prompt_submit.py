"""UserPromptSubmit hook: inject facts relevant to the prompt (~2.5 KB cap, gated).

Also captures explicit "remember: <fact>" prompts verbatim — instant storage
with no LLM required. Skips trivial prompts and facts already injected this
session.
"""
import re
import sys
import _hook_common as hc


def main():
    payload = hc.read_payload()
    session_id = payload.get("session_id", "")
    prompt = (payload.get("prompt") or "").strip()

    m = re.match(r"(?is)^\s*(?:please\s+)?remember\s*[:,-]\s*(.+)$", prompt)
    if m:
        fact = " ".join(m.group(1).split())
        import memzero
        category = "preference" if re.search(r"(?i)\b(prefer|always|never|don'?t)\b", fact) else "fact"
        result = memzero.add_memory(fact, category=category, agent_id="claude-code", run_id=session_id)
        if result["duplicate_of"] is not None:
            print(f"(Psyche memory: already stored as fact #{result['duplicate_of']}.)")
        else:
            print(f"(Psyche memory: stored fact #{result['id']} — \"{result['fact']}\". It will be recalled in future sessions across Claude Code, Codex, and Antigravity.)")
        hc.log(f"prompt_submit {session_id}: remember-capture #{result['id']}")
        return

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
    hc.recursion_guard()
    try:
        main()
    except Exception as e:
        hc.log(f"prompt_submit error: {e}")
    sys.exit(0)
