"""PreCompact/SessionEnd hook: extract durable atomic facts from the transcript.

Write-only — injects nothing. Uses Psyche's chat model when configured; when
CHAT_MODEL=none, falls back to headless `claude -p --model haiku` (requires a
one-time `claude /login`). No-ops silently when neither is available. The
near-duplicate guard makes PreCompact + SessionEnd double-firing safe.
"""
import json
import os
import shutil
import subprocess
import sys
import _hook_common as hc

MAX_TRANSCRIPT_CHARS = 12000


class _ClaudeCLIChat:
    """LLM shim: embeddings delegate to Psyche's local model; completions go
    through the claude CLI in headless mode on the user's subscription."""
    chat_model = "claude-haiku-cli"

    def __init__(self, base_llm, cli_path):
        self._base = base_llm
        self._cli = cli_path
        self.provider = base_llm.provider

    def get_embedding(self, text):
        return self._base.get_embedding(text)

    def generate_completion(self, system_instruction, prompt):
        env = dict(os.environ, PSYCHE_MEM_HOOK="1")
        result = subprocess.run(
            [self._cli, "-p", "--model", "haiku", "--max-turns", "1"],
            input=f"{system_instruction}\n\n---\nTRANSCRIPT:\n{prompt}",
            capture_output=True, text=True, timeout=100, env=env, cwd="/tmp",
        )
        out = (result.stdout or "").strip()
        if result.returncode != 0 or not out or "login" in out.lower()[:60]:
            raise RuntimeError(f"claude CLI extraction failed: {(result.stderr or out)[:200]}")
        return out


def _resolve_llm():
    from llm_client import LLMClient
    llm = LLMClient()
    if getattr(llm, "chat_model", "none") != "none":
        return llm
    cli_path = shutil.which("claude") or "/opt/homebrew/bin/claude"
    if os.path.exists(cli_path):
        return _ClaudeCLIChat(llm, cli_path)
    return llm


def transcript_text(path: str) -> str:
    parts = []
    with open(path) as f:
        for line in f:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("type") not in ("user", "assistant"):
                continue
            message = entry.get("message") or {}
            role = message.get("role", entry["type"])
            content = message.get("content")
            if isinstance(content, str):
                texts = [content]
            elif isinstance(content, list):
                texts = [b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text"]
            else:
                texts = []
            text = "\n".join(t for t in texts if t).strip()
            if text:
                parts.append(f"{role}: {text}")
    return "\n\n".join(parts)[-MAX_TRANSCRIPT_CHARS:]


def count_turns(path: str) -> int:
    """Cheap count of assistant entries in the transcript — a monotonic activity
    proxy for the Stop-hook gate (exact turn semantics don't matter here)."""
    n = 0
    try:
        with open(path) as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") == "assistant":
                    n += 1
    except Exception:
        return 0
    return n


def extract_facts(payload, *, source) -> int:
    """Resolve LLM, build transcript, extract+store durable facts. Returns the
    count stored. Shared by the SessionEnd/PreCompact path and the Stop hook.
    The near-duplicate guard in extract_and_store makes repeated calls safe."""
    path = payload.get("transcript_path", "")
    session_id = payload.get("session_id", "")
    if not path or not os.path.exists(path):
        return 0
    import memzero
    text = transcript_text(path)
    llm = _resolve_llm()
    project = memzero.project_key_for(hc.cwd_from_payload(payload))
    stored = memzero.extract_and_store(text, agent_id="claude-code",
                                       run_id=session_id, project=project, llm=llm)
    hc.log(f"extract {session_id} ({source}) via {getattr(llm, 'chat_model', '?')}: stored {len(stored)} facts")
    return len(stored)


import re as _re

_CORRECTION_RE = _re.compile(
    r"(?i)\b(no,|that'?s (wrong|not right)|don'?t|undo|revert|stop|actually)\b"
)
_CLEAN_END_RE = _re.compile(
    r"(?i)^(thanks|thank you|perfect|works|great|ship it|done|looks good)[^a-z]{0,10}$"
)
_CORRECTION_NEG_TOKENS = {"no,", "that's wrong", "that's not right", "don't", "undo",
                           "revert", "stop", "actually"}


def _proxy_hints(text: str) -> dict:
    """Cheap Python-only signals from the transcript text."""
    # Split into rough user turns (lines starting with "user:")
    lines = text.splitlines()
    user_lines = [l for l in lines if l.lower().startswith("user:")]
    correction_count = sum(1 for l in user_lines if _CORRECTION_RE.search(l))
    # Clean-end: last 1-2 user turns short + affirmative, no correction tokens
    last_user = [l[5:].strip() for l in user_lines[-2:] if l[5:].strip()]
    clean_end = False
    if last_user:
        last = last_user[-1]
        if len(last) <= 40 and _CLEAN_END_RE.match(last) and not _CORRECTION_RE.search(last):
            clean_end = True
    return {
        "correction_count": correction_count,
        "clean_end": clean_end,
        "user_turn_count": len(user_lines),
    }


_OUTCOME_SYSTEM = (
    "You classify whether injected memories helped in this coding session. "
    "Return ONLY a JSON object (no markdown): "
    '{"outcome":"good|bad|neutral","confidence":0.0-1.0,"signals":["<brief reason>"]}. '
    "good = memories were relevant and session succeeded cleanly; "
    "bad = memories caused confusion or the session had repeated corrections/errors; "
    "neutral = unclear or not enough signal. "
    "Anchor your answer on the proxy hints provided."
)


def _classify_outcome(llm, text: str, hints: dict) -> dict | None:
    """One cheap chat call → outcome dict or None on failure."""
    try:
        prompt = (
            f"PROXY HINTS: corrections={hints['correction_count']}, "
            f"clean_end={hints['clean_end']}, user_turns={hints['user_turn_count']}\n\n"
            f"TRANSCRIPT (last 4000 chars):\n{text[-4000:]}"
        )
        raw = llm.generate_completion(_OUTCOME_SYSTEM, prompt)
        raw = _re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
        obj = json.loads(raw)
        if obj.get("outcome") not in ("good", "bad", "neutral"):
            return None
        return obj
    except Exception:
        return None


def main():
    payload = hc.read_payload()
    session_id = payload.get("session_id", "")
    path = payload.get("transcript_path", "")
    if not path or not os.path.exists(path):
        return
    extract_facts(payload, source=payload.get("hook_event_name") or "session")

    # Outcome classifier — never breaks the hook. Final verdict only runs here
    # (SessionEnd/PreCompact), never mid-session from the Stop hook.
    try:
        text = transcript_text(path)
        llm = _resolve_llm()
        injected_ids = hc.read_injected_ids(session_id)
        if injected_ids and getattr(llm, "chat_model", "none") != "none":
            hints = _proxy_hints(text)
            result = _classify_outcome(llm, text, hints)
            if result is not None:
                outcome = result.get("outcome", "neutral")
                confidence = float(result.get("confidence", 0.5))
                memzero.record_outcome(
                    memory_ids=list(injected_ids),
                    outcome=outcome,
                    confidence=confidence,
                    source="transcript",
                    session_id=session_id,
                )
                hc.log(
                    f"outcome {session_id}: {outcome} (conf={confidence:.2f}, "
                    f"ids={sorted(injected_ids)}, signals={result.get('signals', [])})"
                )
    except Exception:
        pass


if __name__ == "__main__":
    hc.recursion_guard()
    try:
        main()
    except Exception as e:
        hc.log(f"extract error: {e}")
    sys.exit(0)
