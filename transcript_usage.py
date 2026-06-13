"""Read Claude Code transcript JSONL files to extract real cache usage metrics.

All functions swallow errors and never raise — callers (hooks, CLI) must not crash.
Stdlib only: os, re, json, glob.
"""
import glob
import json
import os
import re

_DEFAULT_PROJECTS_ROOT = os.path.expanduser("~/.claude/projects")

_ZEROS = {
    "turns": 0,
    "input_uncached": 0,
    "cache_creation": 0,
    "cache_read": 0,
    "output": 0,
    "model": None,
}


def slugify_cwd(cwd: str) -> str:
    """Convert an absolute path to a Claude Code project slug.

    Mirrors Claude Code's own slug rule: replace every non-alphanumeric
    character with a hyphen.  Example:
        /Users/aniketnamjoshi/knowledge-project
        -> -Users-aniketnamjoshi-knowledge-project
    """
    return re.sub(r"[^a-zA-Z0-9]", "-", os.path.abspath(cwd))


def transcript_path(
    session_id: str,
    cwd: str | None = None,
    projects_root: str = _DEFAULT_PROJECTS_ROOT,
) -> str | None:
    """Return the path to a session transcript JSONL, or None if not found.

    Strategy:
    1. If cwd is given, try <projects_root>/<slug>/<session_id>.jsonl directly.
    2. Fall back to globbing <projects_root>/*/<session_id>.jsonl (first hit).

    Never returns a path under a <session_id>/ subdirectory (subagent transcripts).
    """
    try:
        if cwd is not None:
            slug = slugify_cwd(cwd)
            candidate = os.path.join(projects_root, slug, session_id + ".jsonl")
            if os.path.isfile(candidate):
                return candidate
        # Fall back: search all project directories
        pattern = os.path.join(projects_root, "*", session_id + ".jsonl")
        hits = glob.glob(pattern)
        if hits:
            return hits[0]
    except Exception:
        pass
    return None


def parse_transcript_usage(path: str) -> dict:
    """Stream a transcript JSONL and sum token usage from assistant turns.

    Skips sidechain turns (isSidechain == True) and non-assistant lines.
    Deduplicates by uuid (last-wins) before summing.
    Returns a dict with keys: turns, input_uncached, cache_creation,
    cache_read, output, model.  On any failure returns all-zeros dict.
    """
    try:
        seen: dict[str, dict] = {}  # uuid -> obj, last-wins dedup
        with open(path) as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") != "assistant":
                    continue
                if obj.get("isSidechain") is True:
                    continue
                uid = obj.get("uuid")
                if uid is not None:
                    seen[uid] = obj
                else:
                    # No uuid — treat as unique; accumulate inline
                    seen[id(obj)] = obj

        turns = 0
        input_uncached = cache_creation = cache_read = output = 0
        model = None
        for obj in seen.values():
            usage = obj.get("message", {}).get("usage", {})
            if not usage:
                continue
            turns += 1
            input_uncached += usage.get("input_tokens", 0)
            cache_creation += usage.get("cache_creation_input_tokens", 0)
            cache_read += usage.get("cache_read_input_tokens", 0)
            output += usage.get("output_tokens", 0)
            m = obj.get("message", {}).get("model")
            if m:
                model = m

        return {
            "turns": turns,
            "input_uncached": input_uncached,
            "cache_creation": cache_creation,
            "cache_read": cache_read,
            "output": output,
            "model": model,
        }
    except Exception:
        return dict(_ZEROS)


def count_tokens(text: str, provider: str = "anthropic") -> tuple[int, bool]:
    """Estimate token count for text.

    Returns (tokens, is_exact).  is_exact is always False — even when tiktoken
    is available it uses cl100k_base, not Anthropic's tokenizer, so the result
    is always approximate.  Falls back to len(text)//4 when tiktoken is absent.
    """
    try:
        import tiktoken  # noqa: PLC0415
        enc = tiktoken.get_encoding("cl100k_base")
        return (len(enc.encode(text)), False)
    except ImportError:
        return (len(text) // 4, False)
