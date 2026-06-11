"""PreCompact/SessionEnd hook: extract durable atomic facts from the transcript.

Write-only — injects nothing. No-ops when Psyche has no chat model configured
(CHAT_MODEL=none); the near-duplicate guard makes PreCompact + SessionEnd
double-firing safe.
"""
import json
import os
import sys
import _hook_common as hc

MAX_TRANSCRIPT_CHARS = 12000


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


def main():
    payload = hc.read_payload()
    session_id = payload.get("session_id", "")
    path = payload.get("transcript_path", "")
    if not path or not os.path.exists(path):
        return
    import memzero
    text = transcript_text(path)
    stored = memzero.extract_and_store(text, agent_id="claude-code", run_id=session_id)
    hc.log(f"extract {session_id} ({payload.get('hook_event_name')}): stored {len(stored)} facts")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        hc.log(f"extract error: {e}")
    sys.exit(0)
