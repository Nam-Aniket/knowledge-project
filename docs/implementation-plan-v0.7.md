# Psyche v0.7 — Implementation Plan: Host-Agent Guidance Protocol + Cache-Aware Injection

Audience: coding agents (Claude Code, Codex/GPT, Antigravity/Gemini), possibly cold.
Consistency across agents is the top requirement: follow the spec literally.

---

## Why (diagnosis)

**Problem 1 — guidance degrades to retrieval-only when no chat model is configured, and the degradation is a dead end.**

The setup wizard and `LLMClient.__init__` couple chat to the embedding provider. With `LLM_PROVIDER=local` (the user's actual config: local ONNX embeddings, `CHAT_MODEL=none`), `LLMClient` hard-codes `self.chat_model = "none"` (`llm_client.py:173-175`). `generate_guidance_brief` then short-circuits to `retrieval_only_brief` (`guidance.py:192-193`), returning a plan with `actions: []`. Verified real-world failure: Antigravity called `generate_guidance`, received the action-less brief, and synthesized a plan *outside* Psyche — so nothing materialized into goals/experiments and the check-in loop never engaged.

Fix has two parts: (a) **host agent as synthesizer** — Psyche returns a synthesis pack + a `submit_guidance_plan` tool that runs the existing parser/materialization; (b) **`CHAT_PROVIDER`** decoupling so keyless-terminal users can pair local embeddings with an Ollama/Gemini/OpenAI chat model.

**Problem 2 — Psyche's memory injections are cache-hostile, and it can't measure it.**

Cache prefix matches byte-for-byte from the first changed byte; a single early change re-prices everything after it at full rate. The session-start block is ordered `updated_at DESC` (`memzero.py:692`) and embeds per-fact dates (`memzero.py:460`), so any touched fact reorders/changes the stable prefix. Psyche can't see provider cache metrics but can measure its own injection stability. Make injections deterministic; add a cache-exposure metric to the ledger.

**All-models requirement:** the cache-friendliness work is provider-agnostic by mechanism. Where numbers appear (discount rate, TTL, usage field names), use a **per-provider constants table** keyed off `CHAT_PROVIDER`, never hardcode Anthropic values.

A live logging proxy (`psyche cache-audit`) is **deferred** — new operational surface (MITM on the provider API); the injection-stability metric gives most of the signal at zero new surface.

---

## Global conventions (ALL agents MUST follow)

- **Repo:** `/Users/aniketnamjoshi/knowledge-project` (absolute paths; cwd resets between shell calls).
- **Python:** `/Users/aniketnamjoshi/knowledge-project/.venv/bin/python`
- **Test command (end of EVERY step, from repo root):** `TESTING=true .venv/bin/python -m unittest discover tests`. Baseline **77 green**. Every step ends green.
- **Branch:** `feat/v0.7-byo-guidance-and-cache` (already cut from `feat/v0.6-guidance-and-memory`).
- **Commits:** one per step, `v0.7 step N: <summary>`, only after suite green.
- **Style:** match existing modules (4-space, snake_case, `rich` CLI, docstrings). **No new third-party deps.** **No schema migration** — `SCHEMA_VERSION` stays 3. Never touch the usearch index or base RAG tools.
- LLM access via `LLMClient` only. Treat `chat_model == "none"` OR `provider == "none"` as "no chat".
- `submit_guidance_plan` MUST reuse `plan_schema.parse_plan_response` + `guidance.coerce_plan` + `guidance.materialize_plan` — no new parser.
- Throwaway DB for manual checks: `DATABASE_PATH=/tmp/psyche_v07_stepN.db`; never mutate `~/.psyche/knowledge.db`.

---

## Dependency graph

- Step 0 (push/PR v0.6) — done.
- Step 1 (branch) — done.
- Steps 2, 3, 4 — parallelizable after Step 1.
- Step 5 — after 4.
- Step 6 — after {2, 4}.
- Step 7 — last.

---

## Step 2 — Synthesis pack + `submit_guidance_plan` (primary fix)

**Files:** `guidance.py`, `mcp_server.py`, `tests/test_guidance.py` (or new test file).

1. Extract the shared retrieval/context-gathering block (`guidance.py:195-303`) into `_gather_guidance_context(goal_text, domain, db_path, llm) -> dict` (returns retrieved_knowledge, graph_context, known_facts, active_goals, active_experiments, personal_rules, diagnostic_questions, available_metrics). Both `generate_guidance_brief` and the new `build_synthesis_pack` call it. Behavior-preserving.
2. New `build_synthesis_pack(goal_text, domain, db_path, llm) -> dict`: returns `{"mode":"synthesis_pack","domain","goal","instruction":<imperative telling the host agent to synthesize and call submit_guidance_plan>,"schema":PLAN_SCHEMA_DESCRIPTION,"context":{...}}`. No LLM call.
3. `generate_guidance_tool`: when no chat model, return `json.dumps(build_synthesis_pack(...), indent=2)` instead of retrieval-only. Chat-model path unchanged. Keep `retrieval_only_brief`/`format_brief_for_display` for CLI.
4. New `submit_guidance_plan_tool(plan_json, topic=None, apply=True) -> str`: topic-aware db_path; parse (string→`parse_plan_response`, dict→`coerce_plan`) using submitted `goal`/`domain`; on failure return `{"error":...,"schema":...}` and do NOT materialize; stamp `synthesized_by="host-agent"` (LLM path gets `"psyche-llm"`); dedup against an active goal with same title+domain created <10 min ago (return `{"status":"duplicate","goal_id":...}`); if apply, `materialize_plan` and return `{"status":"materialized",...}`; if not, return validated plan JSON (preview).
5. MCP: register `submit_guidance_plan` tool (adjacent to `checkin_plan` ~`mcp_server.py:540`) + dispatch branch (~`mcp_server.py:776`).

**Tests:** pack has schema+context; tool returns pack without chat; submit materializes goal+experiments sharing plan_id; submit rejects garbage (no goal created); submit dedup (second = duplicate, count 1).
**Acceptance:** 5 tests pass; suite green (≈82).

---

## Step 3 — Decouple chat from embeddings via `CHAT_PROVIDER`

**Files:** `llm_client.py`, `.env.example`, `tests/test_llm_client.py`.

1. `LLMClient.__init__`: `chat_provider = os.getenv("CHAT_PROVIDER","").lower() or self.provider`. Store `self.chat_provider`. Resolve `self.chat_model` by chat_provider (none/local/offline→"none"; ollama→CHAT_MODEL or "llama3"; openai→CHAT_MODEL or "gpt-4o-mini" + require key; gemini→CHAT_MODEL or "gemini-1.5-flash" + require key). **Compat:** when `chat_provider == self.provider` (default), resolved chat_model MUST equal current behavior, including `local→"none"`.
2. `generate_completion`: route by `self.chat_provider` (keep the `chat_model=="none"` raise first).
3. Wizard: for choices 4/5, optional prompt to pair a chat model; write `CHAT_PROVIDER` + key/model. Choices 1-3 unchanged.
4. `.env.example` + docs: document `CHAT_PROVIDER` (optional, defaults to LLM_PROVIDER).

**Tests:** default → local stays chat_model "none"; CHAT_PROVIDER=ollama decouples and dispatches ollama; CHAT_PROVIDER=openai w/o key raises. (Construct LLMClient non-interactively.)
**Acceptance:** 3 tests pass; existing gemini/openai default path unchanged; suite green.

---

## Step 4 — Make injections cache-stable

**Files:** `memzero.py`, `hooks/psyche_session_start.py`, `hooks/_hook_common.py`, tests.

1. `standing_fact_rows`: add `stable: bool=False`. When True, `ORDER BY (project IS NULL), id ASC`. session-start calls `stable=True`.
2. `format_facts`: add `include_date: bool=True`. When False, omit the `(…date)` suffix. session-start calls `include_date=False`.
3. `psyche_session_start.py`: use `stable=True`, `include_date=False`; constant header. Output = pure function of fact set + project.
4. `_hook_common.py`: add `stable_block_hash(text) -> str` = `hashlib.sha1(text.encode()).hexdigest()[:12]`.

**Tests:** stable order is id ASC (unaffected by touch); no-date format deterministic across touch; session-start block identical across a fact-touch (no new facts).
**Acceptance:** prompt-submit untouched; suite green.

---

## Step 5 — Cache-exposure metric in the ledger (PER-PROVIDER)

**Files:** `hooks/_hook_common.py`, `hooks/psyche_session_start.py`, `memzero.py`, `mem_cli.py`, tests.

1. `append_ledger`: optional `block_hash=None` field, included only when provided.
2. session-start: compute `h = stable_block_hash(text)`, pass `block_hash=h`.
3. `ledger_summary`: add `session_start_count`, `distinct_session_blocks`, `session_block_changes` (=max(0,distinct-1)), `prompt_submit_count`, `prompt_submit_facts`. Back-compat: lines without block_hash never raise.
4. **Per-provider discount table** in memzero (or a small constants module): `CACHE_DISCOUNT = {"anthropic":0.9,"openai":0.5,"gemini":0.75,"ollama":0.0,"local":0.0,"none":0.0}` and a `cache_ttl_seconds` note. `ledger_summary` includes an `estimated_savings` field computed with the discount for the configured `CHAT_PROVIDER` (fallback anthropic), CLEARLY labeled "modeled estimate".
5. `mem_cli.py` stats: one "Cache exposure" line — block changes across sessions + modeled estimate (labeled), naming the provider used.

**Tests:** counts block changes (distinct=2 → changes=1); legacy ledger (no block_hash) summarizes without error.
**Acceptance:** suite green.

---

## Step 6 — Protocol block + connect.py guidance

**Files:** `docs/memory-protocol.md`, `connect.py` (likely no code change), `tests/test_connect.py`.

Append to `docs/memory-protocol.md` (after the first `---`, so `_get_protocol_block` includes it):
- **"Guidance synthesis (no chat model)":** if `generate_guidance` returns `mode: synthesis_pack`, YOU synthesize the plan JSON per `schema` and call `submit_guidance_plan`. Don't improvise outside Psyche.
- **"Placement (cache-friendliness)":** treat injected memory as append-only; never edit facts into earlier context — it breaks the host's prompt cache and re-prices the prefix.

**Tests:** protocol block contains `submit_guidance_plan` and `append-only`. Update any verbatim-block assertion.
**Acceptance:** suite green.

---

## Step 7 — Version single-source, changelog, commit teaching docs

**Files:** `pyproject.toml`, `package.json`, `README.md`, `mcp_server.py` (`serverInfo.version`→0.7.0 via a `__version__` constant), `CHANGELOG.md`, `git add docs/teaching/`.

1. Single-source `__version__ = "0.7.0"` in `mcp_server.py`; reference it in serverInfo. (pyproject/package.json/README badge are manual mirrors → 0.7.0.)
2. CHANGELOG `[0.7.0]` entry: synthesis-pack + submit_guidance_plan; CHAT_PROVIDER; cache-stable injections + per-provider cache-exposure metric; protocol placement guidance.
3. Commit both teaching docs under `docs/teaching/`.

**Acceptance:** version 0.7.0 everywhere; teaching docs tracked; suite green.

---

## Cross-cutting acceptance

- Suite green after every step (final ≈ 77 + ~14).
- No-chat protocol end-to-end: `generate_guidance` → `mode:synthesis_pack`; valid plan → `submit_guidance_plan` materializes goal+experiments with `synthesized_by:"host-agent"`; `list_goals_and_experiments` shows them; `checkin_plan` engages.
- `LLM_PROVIDER=local` + `CHAT_PROVIDER=ollama` → real plan from terminal.
- `psyche mem stats` reports `session_block_changes` + modeled estimate (labeled, per-provider).
- `psyche connect <client>` block mentions `submit_guidance_plan` + append-only.
- `SCHEMA_VERSION == 3`; usearch index + base RAG untouched.

---

## Trade-offs

1. BYO-model/synthesis-pack first; CHAT_PROVIDER second — the production failure was agent-side.
2. CHAT_PROVIDER defaults to LLM_PROVIDER — zero-config-breaking.
3. Live cache-audit proxy deferred — new operational surface; ledger metric gives most signal.
4. No schema migration — removes the riskiest change class.
5. Stable session-start order by id ASC — cache stability needs a pure function of the fact set.
6. Per-provider constants (discount/TTL/usage fields) — keeps a single codebase honest across Claude/GPT/Gemini.
</content>
