# Psyche v0.6 — Implementation Plan: Guidance Redesign + Memory Productization

Status: ready to execute. Audience: coding agents (Claude Code, Codex/GPT, Antigravity/Gemini), possibly cold, in separate sessions. Consistency across agents is the top requirement: follow the spec literally.

---

## Why (diagnosis of the current guidance layer)

Read `guidance.py` before starting. The user complaint — *"idea generation and implementation doesn't really work — make it generate workable stuff I can implement into my life"* — traces to concrete defects:

1. **Output is advice-shaped, not action-shaped.** The schema (`GUIDANCE_SYSTEM_INSTRUCTION`, `guidance.py:173-192`) is dominated by `relevant_principles`, `key_assumptions`, `risks_and_traps`, `rule_suggestions`. There is exactly **one** `next_action` string (`:187`) and no list of concrete, time-bound, verifiable actions. The result reads like a consultant memo, not a to-do list.

2. **Brittle regex fallback corrupts output silently.** When JSON parsing fails (`:330-355`), the code regex-scrapes `next_action`/`success`/`failure` out of freeform prose and stuffs a `parse_error` + `raw_response` into the brief. There is **no retry**; a single malformed LLM response degrades to a near-empty brief. Modern models follow strict-JSON instructions if you *ask once more on failure* — the retry is missing.

3. **The brief is a dead end — no link to the records the user already has.** `generate_guidance_brief` returns a dict that is only *printed* (`format_brief_for_display`, `:360`) or JSON-dumped by the MCP tool (`generate_guidance_tool`, `:826`). Nothing is written to `goals`, `experiments`, `metric_logs`, or `rules`. The tables exist (db.py:194-271) and have full CRUD, but the brief never creates a single record. There is no path from "here's a plan" to "this plan is now tracked."

4. **No follow-through loop.** There is no "check in on my plan" entry point. Reviews exist as manual CRUD (`review_main`, `:658`) but nothing pulls open experiments + recent metric logs + prior reviews together, asks what happened, and adjusts. Without this, plans are generated and forgotten — the core of "implement into my life."

5. **Everything dies when `chat_model == "none"`.** Both the CLI (`:465-468`) and MCP tool (`:820-821`) hard-exit with an error when no chat model is configured. A local-fastembed user (the default `provider="local"` sets `chat_model="none"`, llm_client.py:173-175) gets *nothing* — not even a retrieval-only brief from their own rules/experiments/knowledge.

6. **No atomic-memory integration.** `memzero.search_memories` exists and returns durable user facts, but `generate_guidance_brief` never calls it. Briefs ignore everything the user has told the system about themselves.

The redesign in Phase A fixes 1-6. Phase B productizes the memory layer (onboarding, project scoping, CLI, ledger, superseding). The two phases are independent after Step 1; parallelization is marked per step.

---

## Global conventions (ALL agents MUST follow)

**Repo path:** `/Users/aniketnamjoshi/knowledge-project` (absolute paths only; cwd resets between shell calls).

**Venv python:** `/Users/aniketnamjoshi/knowledge-project/.venv/bin/python`

**Test command (canonical — run from repo root at the end of every step):**
```
cd /Users/aniketnamjoshi/knowledge-project && TESTING=true .venv/bin/python -m unittest discover tests
```
Baseline is **51 tests green**. Every step must end with the full suite green (new tests added by a step count toward the new total). If a step leaves the suite red, the step is not done.

**Branch:** Create `feat/v0.6-guidance-and-memory` cut from `feat/atomic-memory` as the very first action of Step 0, then commit each step onto it.
```
cd /Users/aniketnamjoshi/knowledge-project && git checkout feat/atomic-memory && git checkout -b feat/v0.6-guidance-and-memory
```
If the branch already exists, `git checkout feat/v0.6-guidance-and-memory` and continue.

**Commit style:** one commit per step. Imperative, scoped:
`v0.6 step N: <short summary>` (e.g. `v0.6 step 3: add actionable-plan JSON schema + retry parser`). Commit only after the suite is green.

**Code style rules:**
- Match the existing module you are editing (4-space indent, `snake_case`, rich for CLI output, docstrings as in db.py/guidance.py).
- **No new third-party dependencies.** Allowed stdlib + already-imported: `json`, `re`, `os`, `sys`, `sqlite3`, `argparse`, `datetime`, `subprocess`, `shutil`, `numpy`, `rich`, `yaml` (optional-import pattern as in guidance.py:91), `tomllib` (stdlib, read-only TOML — Python 3.11+; this repo runs 3.14). For **writing** TOML in Step B1, do **not** add `tomli-w`; hand-emit the minimal MCP table as text (spec given in that step).
- **Never touch the chunks usearch index** (`index_path_for`, `build_or_update_usearch_index`) or the base RAG tools (`search_knowledge`, `retrieve_graph`, `record_interaction`, `write_memory_core`, `append_memory_archival`). The `.mem.usearch` index is fair game per its module (`memzero.py`).
- **All schema changes go through the MIGRATIONS framework** in `db.py`: add the new columns inside a new migration callable, append `(3, _migrate_v3_...)` to `MIGRATIONS` (db.py:51), bump `SCHEMA_VERSION` to `3` (db.py:8), AND add the same columns to the relevant `CREATE TABLE` / a post-create `ALTER` in `init_db` so fresh DBs and migrated DBs converge. Follow the existing `_create_atomic_memory_tables` + `_migrate_v2_atomic_memories` pattern (db.py:11-51). There is exactly **one** schema bump in this whole plan (Step 2, v2→v3); all new columns ride in it.
- Use `try: ALTER TABLE ... except sqlite3.OperationalError: pass` for additive columns on existing tables (mirror db.py:106-109).
- LLM calls use the existing `LLMClient` interface only: `llm.provider`, `llm.chat_model`, `llm.get_embedding(text)`, `llm.generate_completion(system_instruction, prompt)` (llm_client.py:157-225). Treat `chat_model == "none"` OR `provider == "none"` as "no chat".

**Execution discipline:** Do **not** announce, ask for clarification, or propose alternatives. Execute the step exactly as written. If you are genuinely blocked (a precondition is false, a file is missing, a verify command cannot pass for reasons outside the step), **stop and report** the specific blocker — do not improvise a different design.

**Verify-command convention:** every step lists runnable commands with expected output. Run them from `/Users/aniketnamjoshi/knowledge-project`. Use a throwaway DB via `DATABASE_PATH=/tmp/psyche_v06_stepN.db` for manual checks so you never mutate the user's real `~/.psyche/knowledge.db`. Delete `/tmp/psyche_v06_*.db*` when done.

---

## Dependency graph / parallelization

- **Step 0** (branch) — first, blocking.
- **Step 1** (plan schema + parser, pure module `plan_schema.py`) — no deps. Blocks 3, 4.
- **Step 2** (schema v3 migration: all new columns at once) — depends on Step 0 only. Blocks 5, B2, B5, B3, B4-stats.
- **Step 3** (actionable brief generation) — depends on 1, 2.
- **Step 4** (materialize brief → goal+experiment+metrics records) — depends on 3.
- **Step 5** (check-in follow-through loop) — depends on 2, 4.
- **Phase B is independent of Phase A after Step 2.** B1 (`connect`) depends only on Step 0. B2/B5 (project column, superseding, retrieval-count) depend on Step 2. B3 (`mem` CLI) depends on B2. B4 (ledger) depends on Step 0; its `stats` reporting depends on B2's retrieval-count.
- **Parallelizable pairs** (different agents, separate sessions): {Step 3+4+5 chain} ‖ {B1} ‖ {B2→B3, B5} ‖ {B4}. The only shared file with contention is `db.py` (Step 2 owns the single migration; B2/B5 add columns *inside Step 2's migration* — so **Step 2 must merge before B2/B5 start**). `mcp_server.py`, `cli.py`, and `guidance.py` are touched by multiple steps; serialize commits touching the same file or rebase.
- **Step 6** (integration: README + CHANGELOG + final suite) — last.

---

## Step 0 — Cut the working branch

**Files:** none (git only).

**Do:**
```
cd /Users/aniketnamjoshi/knowledge-project && git checkout feat/atomic-memory && git checkout -b feat/v0.6-guidance-and-memory
```

**Acceptance:**
```
cd /Users/aniketnamjoshi/knowledge-project && git rev-parse --abbrev-ref HEAD
```
Expected output: `feat/v0.6-guidance-and-memory`

Then confirm baseline:
```
cd /Users/aniketnamjoshi/knowledge-project && TESTING=true .venv/bin/python -m unittest discover tests 2>&1 | tail -3
```
Expected: a line `OK` and `Ran 51 tests`.

**Out of scope:** any code change.

---

## Step 1 — Actionable-plan JSON schema + strict parser with retry

**Goal:** a standalone, dependency-free module defining the new action-shaped schema, a strict validator, and a `parse_or_retry` helper. No LLM provider logic here — pure data + validation, so it is trivially unit-testable.

**File to create:** `/Users/aniketnamjoshi/knowledge-project/plan_schema.py`

**Spec — exact public surface:**

```python
"""Actionable guidance-plan schema, validation, and resilient JSON parsing.

The v0.6 guidance layer produces an ACTION PLAN, not advice. This module owns
the schema contract shared by generation (guidance.py) and materialization
(records written to goals/experiments/metric_logs).
"""

# The canonical schema, embedded verbatim into the LLM system prompt.
PLAN_SCHEMA_DESCRIPTION: str  # human/LLM-facing schema text (see below)

VALID_HORIZONS = {"today", "this_week", "this_month"}  # action.horizon values

def empty_plan(goal_text: str, domain: str) -> dict:
    """Returns a minimal valid plan dict (no actions) for graceful-degradation
    and parse-failure fallback paths."""

def validate_plan(obj) -> tuple[bool, str]:
    """Returns (True, "") if obj matches the plan schema, else (False, reason).
    Strict on required keys/types; tolerant of extra keys (ignored downstream)."""

def coerce_plan(obj, goal_text: str, domain: str) -> dict:
    """Best-effort normalize a loosely-valid object into a schema-conformant
    plan: fills missing optional lists with [], clamps actions to <= 5, drops
    malformed action entries, defaults horizon to 'this_week'. Raises ValueError
    only if obj is not a dict."""

def parse_plan_response(raw: str, goal_text: str, domain: str) -> tuple[dict | None, str]:
    """Strips ``` fences, json.loads, validates. Returns (plan, "") on success
    or (None, reason) on failure — caller decides whether to retry."""
```

**The plan schema (use exactly these keys; put this object in `PLAN_SCHEMA_DESCRIPTION` as the LLM-facing contract):**

```json
{
  "domain": "<string>",
  "goal": "<one-line restatement of the user's goal>",
  "diagnosis": "<2-3 sentence read of the situation, grounded in retrieved knowledge/facts>",
  "actions": [
    {
      "action": "<concrete imperative step the user does themselves>",
      "horizon": "today|this_week|this_month",
      "time_estimate_min": <integer minutes>,
      "success_criterion": "<observable, checkable outcome>",
      "due_offset_days": <integer days from today>,
      "metric": {"name": "<snake_case>", "type": "objective|subjective", "unit": "<unit>"}
    }
  ],
  "first_action_index": <integer index into actions of the single thing to do first>,
  "relevant_principles": [{"principle": "<insight>", "source": "<Title, Location>"}],
  "rule_suggestions": ["<personal rule to consider adopting>"],
  "review_in_days": <integer, default 7>
}
```

Rules to encode in `validate_plan`:
- Required top-level keys: `domain`, `goal`, `actions`, `first_action_index`, `review_in_days`. (Others optional, default to `[]`/`""`/`7` via `coerce_plan`.)
- `actions` must be a non-empty list of dicts each having `action` (non-empty str), `horizon` ∈ `VALID_HORIZONS`, `time_estimate_min` (int ≥ 1), `success_criterion` (non-empty str), `due_offset_days` (int ≥ 0). `metric` is optional but if present must have `name`,`type`,`unit`.
- `first_action_index` int in `range(len(actions))`.
- `coerce_plan` clamps `actions` to first 5, drops entries failing the per-action check, and if all are dropped raises nothing — returns a plan with `actions: []` (caller treats empty-actions as a soft failure to trigger retry).

**Tests to add:** `/Users/aniketnamjoshi/knowledge-project/tests/test_plan_schema.py`
- `test_valid_plan_passes` — a hand-built conformant dict passes `validate_plan`.
- `test_missing_actions_fails` — dict without `actions` returns `(False, ...)`.
- `test_bad_horizon_dropped_by_coerce` — action with `horizon="someday"` is dropped by `coerce_plan`.
- `test_parse_strips_fences` — ` ```json\n{...}\n``` ` parses to a valid plan.
- `test_parse_garbage_returns_none` — `parse_plan_response("not json", ...)` returns `(None, reason)`.
- `test_empty_plan_is_valid` — `validate_plan(empty_plan("g","general"))` returns `(True, "")` only if you define empty_plan with `actions:[]` AND you special-case: empty_plan is allowed to have empty actions (used for degradation). **Resolve this** by having `validate_plan` accept empty `actions` ONLY when an internal flag is set; simpler: make `empty_plan` valid by giving it `actions: []` and have `validate_plan` treat empty actions as valid-but-empty `(True,"")`, while the *generation caller* separately checks `len(plan["actions"]) > 0` to decide retry. Document this in the docstring.

**Acceptance:**
```
cd /Users/aniketnamjoshi/knowledge-project && TESTING=true .venv/bin/python -m unittest tests.test_plan_schema 2>&1 | tail -2
```
Expected: `OK`, with the new tests counted.
Full suite green (now 51 + new):
```
cd /Users/aniketnamjoshi/knowledge-project && TESTING=true .venv/bin/python -m unittest discover tests 2>&1 | tail -2
```

**Out of scope:** any LLM call, any DB write, any change to `guidance.py`.

---

## Step 2 — Schema v3 migration (ALL new columns, one bump)

**Goal:** the single schema change in this plan. Adds every new column needed by Phase A Step 4/5 and Phase B (project scoping, superseding already exists as a column, retrieval-count). One migration, one `SCHEMA_VERSION` bump.

**File to modify:** `/Users/aniketnamjoshi/knowledge-project/db.py`

**Changes:**

1. Add columns to `atomic_memories` (project scoping + retrieval ranking). `superseded_by` already exists (db.py:25) — do **not** re-add it. New columns:
   - `project TEXT` (NULL = global fact)
   - `retrieval_count INTEGER NOT NULL DEFAULT 0`
   - `last_retrieved_at TEXT`

2. Add a column to `experiments` linking back to the originating plan/brief (used by check-in to group an action set):
   - `plan_id TEXT` (a generated plan group id; NULL for manually-created experiments)

   And to `goals`:
   - `plan_id TEXT`

**Implementation pattern (mirror existing v2 code):**

In `db.py`:
- Write `def _migrate_v3_actionable_and_project(conn):` that runs additive `ALTER TABLE` statements, each wrapped in `try/except sqlite3.OperationalError: pass`:
  ```python
  def _migrate_v3_actionable_and_project(conn):
      cur = conn.cursor()
      for ddl in (
          "ALTER TABLE atomic_memories ADD COLUMN project TEXT",
          "ALTER TABLE atomic_memories ADD COLUMN retrieval_count INTEGER NOT NULL DEFAULT 0",
          "ALTER TABLE atomic_memories ADD COLUMN last_retrieved_at TEXT",
          "ALTER TABLE goals ADD COLUMN plan_id TEXT",
          "ALTER TABLE experiments ADD COLUMN plan_id TEXT",
      ):
          try:
              cur.execute(ddl)
          except sqlite3.OperationalError:
              pass
  ```
- Append to `MIGRATIONS`: `MIGRATIONS = [(2, _migrate_v2_atomic_memories), (3, _migrate_v3_actionable_and_project)]`
- Bump `SCHEMA_VERSION = 3`.
- In `init_db`, after `_create_atomic_memory_tables(conn)` and after the `goals`/`experiments` CREATEs, call `_migrate_v3_actionable_and_project(conn)` so **fresh** DBs also get the columns (the CREATE statements are not edited; the ALTERs are idempotent via the try/except). Place this call *before* `_run_migrations(conn)`.

**Acceptance:**
```
cd /Users/aniketnamjoshi/knowledge-project && rm -f /tmp/psyche_v06_step2.db* && DATABASE_PATH=/tmp/psyche_v06_step2.db .venv/bin/python -c "
import db; db.init_db('/tmp/psyche_v06_step2.db')
c=db.get_connection(db.resolve_db_path('/tmp/psyche_v06_step2.db'))
cols=lambda t:[r[1] for r in c.execute(f'PRAGMA table_info({t})')]
assert 'project' in cols('atomic_memories'), cols('atomic_memories')
assert 'retrieval_count' in cols('atomic_memories')
assert 'last_retrieved_at' in cols('atomic_memories')
assert 'plan_id' in cols('experiments')
assert 'plan_id' in cols('goals')
assert db.get_metadata(c,'schema_version')=='3'
print('OK v3')"
```
Expected: `OK v3`.

Add a migration test to `tests/test_migrations.py`: `test_v3_columns_present_on_fresh_db` asserting the five columns + `schema_version == '3'`. Then full suite:
```
cd /Users/aniketnamjoshi/knowledge-project && TESTING=true .venv/bin/python -m unittest discover tests 2>&1 | tail -2
```
Expected: `OK`.

**Out of scope:** writing/reading the new columns from memzero/guidance (later steps). No data backfill (defaults handle it).

---

## Step 3 — Actionable brief generation (replace regex extraction; add retry, memory context, graceful degradation)

**Goal:** rewrite `generate_guidance_brief` to produce a schema-validated **action plan** using `plan_schema`, with retry-once, atomic-memory context, and a retrieval-only fallback when no chat model.

**Files to modify:** `/Users/aniketnamjoshi/knowledge-project/guidance.py`

**Changes:**

1. **New system prompt.** Replace `GUIDANCE_SYSTEM_INSTRUCTION` (guidance.py:162-193) with an action-oriented prompt. Exact text:
```
You are Psyche, a knowledge-grounded planning engine. You turn a goal into an
ACTIONABLE PLAN the user can execute this week — never vague advice.

RULES:
1. Ground the diagnosis and principles in RETRIEVED KNOWLEDGE and KNOWN FACTS
   provided below. Cite sources (Title, Location) for principles.
2. Produce 2-5 concrete actions the USER performs themselves. Each action must be
   small enough to finish within its time_estimate, have an observable
   success_criterion, a horizon (today|this_week|this_month), and a
   due_offset_days. Attach a metric when the action's progress is measurable.
3. Pick first_action_index: the single smallest action to start with now.
4. If retrieved knowledge is thin, still produce actions, but keep diagnosis honest.
5. Output ONLY valid JSON matching the schema. No markdown, no commentary.

SCHEMA:
<PLAN_SCHEMA_DESCRIPTION inserted here>
```
Insert `plan_schema.PLAN_SCHEMA_DESCRIPTION` where indicated.

2. **Add memory context.** Inside `generate_guidance_brief`, after retrieving rules/goals/experiments and before building the prompt, call:
   ```python
   import memzero
   facts = memzero.search_memories(goal_text, top=6, db_path=db_path, llm=llm)
   facts_text = memzero.format_facts(facts, max_chars=1500) if facts else ""
   ```
   Add a `### KNOWN FACTS ABOUT THE USER:` block to the prompt when `facts_text` is non-empty. (memzero.search_memories already returns `[]` gracefully on weak matches / missing tables.)

3. **Retry-once strict parsing.** Replace the JSON-parse + regex-fallback block (guidance.py:319-355) with:
   ```python
   from plan_schema import parse_plan_response, coerce_plan, empty_plan
   raw = llm.generate_completion(GUIDANCE_SYSTEM_INSTRUCTION, prompt)
   plan, reason = parse_plan_response(raw, goal_text, domain)
   if plan is None or not plan.get("actions"):
       retry_prompt = prompt + (
           "\n\nYour previous reply was not valid or had no actions. "
           "Reply again with ONLY the JSON object, 2-5 concrete actions, no prose."
       )
       raw = llm.generate_completion(GUIDANCE_SYSTEM_INSTRUCTION, retry_prompt)
       plan, reason = parse_plan_response(raw, goal_text, domain)
   if plan is None:
       plan = empty_plan(goal_text, domain)
       plan["diagnosis"] = "Could not generate a structured plan from the model."
   else:
       plan = coerce_plan(plan, goal_text, domain)
   return plan
   ```
   **Delete** the regex `extract`/`extract_list` fallback entirely. No `raw_response`/`parse_error` keys remain in the happy path.

4. **Graceful degradation (no chat model).** Add a new function:
   ```python
   def retrieval_only_brief(goal_text, domain, db_path):
       """Builds an action-less brief from local records when no chat model is
       configured: top rules, matching open experiments, relevant atomic facts,
       and top retrieved principles. Returns a plan dict (actions: []) plus a
       'principles'/'open_experiments'/'facts' rendering."""
   ```
   It must NOT call `llm.generate_completion`. It MAY embed for retrieval if `llm.provider != "none"` (use the existing hybrid-search block guarded by `llm.provider != "none"`, mirroring guidance.py:220-256). Populate `plan = empty_plan(goal_text, domain)`, set `plan["relevant_principles"]` from retrieved chunks, and attach `plan["rule_suggestions"]` from existing rules, `plan["open_experiments"]` (list of `{id,title,review_date}`), `plan["facts"]` from `memzero.search_memories`.

5. **Wire the no-chat path.** In `generate_guidance_brief`, at the top, if `llm.chat_model == "none" or llm.provider == "none"`, `return retrieval_only_brief(...)`.

6. **Update the CLI `main()`** (guidance.py:437-478): remove the hard exit at `:465-468`. Instead, always call `generate_guidance_brief`; when the returned plan has `actions == []` and no chat model, print a banner "Retrieval-only brief (no chat model configured)" then render. 

7. **Update `generate_guidance_tool`** (guidance.py:804-829): remove the hard error at `:820-821`; return `json.dumps(brief, indent=2)` for both chat and no-chat paths.

8. **Rewrite `format_brief_for_display`** (guidance.py:360-421) to render the new schema: Diagnosis, then a numbered Actions list (action · ~Nmin · due in D days · success: …, marking `first_action_index` with `▶ START HERE`), then Principles, Suggested Rules, Review-in. Keep rich markup style. Add a "Facts considered" section when present.

**Tests to add/update:** `tests/test_guidance.py`
- Update any test asserting old keys (`next_action`, `parse_error`) — search the file first.
- `test_brief_retry_on_bad_json` — mock `llm.generate_completion` to return garbage on first call and valid plan JSON on second; assert the returned plan has actions and `generate_completion` was called twice.
- `test_brief_validates_actions` — mock returns a valid plan; assert every action has `horizon ∈ VALID_HORIZONS` and an int `due_offset_days`.
- `test_retrieval_only_when_no_chat` — construct a fake LLM with `chat_model="none", provider="none"`; assert `generate_guidance_brief` returns a dict with `actions == []` and does NOT raise, and that `generate_completion` is never called (use a Mock that raises if called).

Use the existing test pattern (tempfile DB, `unittest.mock`). For mocking the LLM, pass a `mock.Mock()` with `.provider`, `.chat_model`, `.get_embedding`, `.generate_completion` set, matching how `generate_guidance_brief(goal, domain, db_path, llm)` receives `llm`.

**Acceptance:**
```
cd /Users/aniketnamjoshi/knowledge-project && TESTING=true .venv/bin/python -m unittest tests.test_guidance 2>&1 | tail -2
```
Expected: `OK`. Full suite green.

Manual smoke (no chat model → no crash):
```
cd /Users/aniketnamjoshi/knowledge-project && rm -f /tmp/psyche_v06_step3.db* && LLM_PROVIDER=local DATABASE_PATH=/tmp/psyche_v06_step3.db .venv/bin/python -c "
import guidance, db
db.init_db('/tmp/psyche_v06_step3.db')
class L: provider='none'; chat_model='none'
b=guidance.generate_guidance_brief('save more money','wealth','/tmp/psyche_v06_step3.db', L())
assert isinstance(b,dict) and b.get('actions')==[]; print('OK degrade')"
```
Expected: `OK degrade`.

**Out of scope:** writing any goal/experiment/metric records (Step 4); the check-in loop (Step 5).

---

## Step 4 — Materialize a brief into goal + experiment + metric records

**Goal:** make briefs *implementable* by persisting them. Add a function + flag so a generated plan can be auto-created as a goal, one experiment per action, and metric definitions, all tagged with a shared `plan_id`.

**Files to modify:** `/Users/aniketnamjoshi/knowledge-project/guidance.py`, `/Users/aniketnamjoshi/knowledge-project/mcp_server.py`

**Changes:**

1. **New function in `guidance.py`:**
   ```python
   def materialize_plan(plan: dict, db_path: str) -> dict:
       """Persists a plan: creates one goal (from plan['goal']/domain), and one
       experiment per action (title=action, success_condition=success_criterion,
       review_date=today+due_offset_days, metric_name from action.metric.name).
       All records share a generated plan_id (e.g. 'plan_' + short uuid/time hex).
       Returns {'plan_id', 'goal_id', 'experiment_ids': [...]}."""
   ```
   - Generate `plan_id` as `"plan_" + datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')`.
   - Insert goal via `add_goal(conn, domain, plan['goal'], description=plan.get('diagnosis'), stage='planning')`, then `UPDATE goals SET plan_id=? WHERE id=?` (the CRUD helpers don't accept plan_id; set it with a direct UPDATE — do NOT extend `add_goal`'s signature to keep the change surgical; a follow-up direct `conn.execute` is fine).
   - For each action: compute `review_date = (today + due_offset_days).strftime('%Y-%m-%d')`; `add_experiment(conn, goal_id, action['action'], hypothesis=plan.get('diagnosis'), metric_name=(action.get('metric') or {}).get('name'), success_condition=action['success_criterion'], review_date=review_date)`; then `UPDATE experiments SET plan_id=? WHERE id=?`.
   - Do not log metric values (none exist yet); metric *definitions* live on the experiment's `metric_name`. (No separate metric table needed — `metric_logs` is for data points.)
   - Use one connection, commit once, close in `finally`.

2. **CLI flag.** In `guidance.main()` add `--apply` (store_true). After rendering the brief, if `--apply` and the plan has actions, call `materialize_plan` and print the created goal/experiment ids with a hint: `Check in later with: psyche checkin <goal_id>` (the checkin command lands in Step 5; printing the hint now is fine).

3. **MCP: extend `generate_guidance_tool`.** Add param `apply: bool = False`. When `apply` and plan has actions, call `materialize_plan` and append a `_materialized` key (`{plan_id, goal_id, experiment_ids}`) to the returned JSON. Update the MCP `tools/list` schema for `generate_guidance` (mcp_server.py:476-496) to add `"apply": {"type":"boolean","description":"If true, create goal+experiment records from the plan.","default": false}`, and the `tools/call` dispatch (mcp_server.py:714-727) to read and pass `arguments.get("apply", False)`.

**Tests to add:** `tests/test_guidance.py`
- `test_materialize_creates_records` — build a valid plan with 3 actions, call `materialize_plan`, assert: one goal exists with that `plan_id`; three experiments exist with the same `plan_id`; experiment `success_condition` and `metric_name` match the actions; `review_date` is a valid `YYYY-MM-DD`.

**Acceptance:**
```
cd /Users/aniketnamjoshi/knowledge-project && rm -f /tmp/psyche_v06_step4.db* && DATABASE_PATH=/tmp/psyche_v06_step4.db .venv/bin/python -c "
import guidance, db
p='/tmp/psyche_v06_step4.db'; db.init_db(p)
plan={'domain':'wealth','goal':'Save \$2k','diagnosis':'d','actions':[
 {'action':'Cancel 2 subscriptions','horizon':'this_week','time_estimate_min':20,'success_criterion':'2 cancelled','due_offset_days':3,'metric':{'name':'subs_cancelled','type':'objective','unit':'count'}}],
 'first_action_index':0,'relevant_principles':[],'rule_suggestions':[],'review_in_days':7}
r=guidance.materialize_plan(plan,p)
c=db.get_connection(db.resolve_db_path(p))
g=c.execute('SELECT COUNT(*) FROM goals WHERE plan_id=?',(r['plan_id'],)).fetchone()[0]
e=c.execute('SELECT COUNT(*) FROM experiments WHERE plan_id=?',(r['plan_id'],)).fetchone()[0]
assert g==1 and e==1, (g,e); print('OK materialize', r['plan_id'])"
```
Expected: `OK materialize plan_...`. Full suite green.

**Out of scope:** check-in/review loop (Step 5); storing decisions as atomic facts (Step 5).

---

## Step 5 — Check-in follow-through loop (`psyche checkin` + MCP tool)

**Goal:** the loop that makes plans stick. Given a goal/plan, pull its open experiments + recent metric logs + prior reviews, ask the LLM (or, no-chat, a structured form) "what happened / what's next," log a review per experiment, optionally adjust review dates, and store key decisions as atomic facts.

**Files to modify:** `/Users/aniketnamjoshi/knowledge-project/guidance.py`, `/Users/aniketnamjoshi/knowledge-project/cli.py`, `/Users/aniketnamjoshi/knowledge-project/mcp_server.py`

**Changes:**

1. **Core function in `guidance.py`:**
   ```python
   def checkin_plan(goal_id: int, update_text: str, db_path: str, llm) -> dict:
       """Follow-through on an active plan.
       1. Load goal, its open experiments (status='active'), recent metric_logs,
          and prior reviews for that goal.
       2. If a chat model is available, ask it to: assess progress per experiment,
          decide for each {keep, complete, adjust} with a one-line reason and an
          optional new next-action, and surface 1-3 key decisions worth
          remembering. Use a strict-JSON contract (reuse the parse-retry pattern
          from plan_schema-style parsing; a small local schema is fine).
       3. Without a chat model: record the user's free-text update as a single
          review on the goal; no LLM assessment.
       4. Write one review per experiment (add_review) capturing what_happened +
          next_action. For experiments the model marks 'complete', call
          update_experiment(status='completed', outcome=...).
       5. Store each key decision as an atomic fact via
          memzero.add_memory(fact, category='decision', agent_id='psyche-checkin',
          db_path=db_path, llm=llm).
       Returns {'reviews': [ids], 'completed': [exp_ids], 'facts_stored': [ids],
                'summary': '<short text>'}."""
   ```
   Reuse existing helpers: `get_goals(conn, status=None)` to find the goal, `get_experiments(conn, goal_id=goal_id, status='active')`, `get_metric_logs(conn, goal_id=goal_id, limit=20)`, `get_reviews(conn, goal_id=goal_id, limit=5)`, `add_review`, `update_experiment`. Single connection, commit, `finally: close`.

   Define a small local strict-JSON contract for the LLM (not in `plan_schema.py` — it is check-in specific):
   ```json
   {"summary":"<2 sentences>",
    "experiment_updates":[{"experiment_id":<int>,"decision":"keep|complete|adjust","reason":"<one line>","next_action":"<optional>","new_review_in_days":<optional int>}],
    "key_decisions":["<durable decision worth remembering>"]}
   ```
   Parse with the same fence-strip + json.loads + retry-once approach; on total failure, fall back to the no-chat path (log the raw `update_text` as one review).

2. **New CLI entry `checkin_main()` in `guidance.py`:**
   - `argparse`: positional `goal_id` (int); `--update`/`-u` text (the user's "what happened"); `--db-path`. If `--update` omitted, read from stdin or prompt via rich.
   - Build `LLMClient()`, call `checkin_plan`, render a summary panel: completed experiments, new reviews, decisions stored.

3. **Wire CLI dispatch.** In `cli.py`:
   - Add `checkin` to the usage string (`cli.py:32`) and the available-commands list (`cli.py:88`).
   - Add branch:
     ```python
     elif subcommand == "checkin":
         import guidance
         guidance.checkin_main()
     ```

4. **MCP tool `checkin_plan`.** Add to `tools/list` (after `list_goals_and_experiments`) and `tools/call`:
   - name `checkin_plan`, inputSchema: `goal_id` (integer, required), `update` (string, required: "what happened since last time"), `topic` (string, optional).
   - Dispatch builds `LLMClient()` / resolves db via `resolve_topic_db_path`, calls a thin `guidance.checkin_tool(goal_id, update, topic)` wrapper (add it next to `generate_guidance_tool`) returning `json.dumps(result, indent=2)`.

**Tests to add:** `tests/test_guidance.py`
- `test_checkin_no_chat_logs_review` — fake LLM `chat_model='none'`; create a goal + one active experiment; `checkin_plan(goal_id, "I cancelled one sub", ...)`; assert exactly one review row exists for the goal and `generate_completion` was never called.
- `test_checkin_chat_completes_and_stores_decision` — mock `generate_completion` to return valid check-in JSON marking the experiment `complete` and one `key_decisions` entry; assert the experiment status becomes `completed`, a review is written, and an atomic memory row with `category='decision'` exists. (Mock `llm.get_embedding` to return a small fixed vector so `memzero.add_memory` works; or set `provider='none'` on the fact-store path — but then embedding is skipped and the fact still stores verbatim per memzero.add_memory, which is acceptable; choose provider with a real-ish embedding to also exercise the index path, your call, but keep it deterministic.)

**Acceptance:**
```
cd /Users/aniketnamjoshi/knowledge-project && TESTING=true .venv/bin/python -m unittest tests.test_guidance 2>&1 | tail -2
```
Expected: `OK`. Full suite green.

CLI surface check:
```
cd /Users/aniketnamjoshi/knowledge-project && .venv/bin/python cli.py 2>&1 | grep -o checkin | head -1
```
Expected: `checkin`.

**Out of scope:** any Phase B work.

---

## Step B1 — `psyche connect <client>` one-command onboarding

**Goal:** idempotent onboarding that wires Psyche's MCP server + memory protocol into each client's config, with backup + merge + `--dry-run`.

**Files to create:** `/Users/aniketnamjoshi/knowledge-project/connect.py`
**Files to modify:** `/Users/aniketnamjoshi/knowledge-project/cli.py`

**Shared facts the writer needs:**
- MCP launch command: `<repo>/.venv/bin/python <repo>/cli.py start-mcp` (cwd = repo). Server name key: `psyche`.
- Repo dir resolved as `os.path.dirname(os.path.abspath(__file__))` inside `connect.py`.
- Protocol prose to append: read the canonical block from `docs/memory-protocol.md` (already in repo). For AGENTS.md/GEMINI.md, append a short pointer block delimited by markers so re-runs are idempotent.

**Spec — `connect.py`:**
```python
def connect(client: str, dry_run: bool = False) -> list[str]:
    """Wires Psyche into the given client. client in {'claude-code','codex','gemini','antigravity'}
    ('antigravity' is an alias for 'gemini'). Returns a list of human-readable
    actions taken (or would-be-taken when dry_run). Idempotent."""
```

Per-client behavior:

- **claude-code** → `~/.claude/settings.json` (JSON). Load (or `{}`), back up to `settings.json.psyche-bak` if it exists and no backup yet, ensure `mcpServers.psyche = {"command": "<venv python>", "args": ["<repo>/cli.py","start-mcp"]}` merged WITHOUT clobbering other servers/keys. Also ensure the three hooks are registered if a `hooks` section convention exists — **but** the existing hooks live in `<repo>/hooks/`; registering them is client-specific and brittle, so for v0.6 limit claude-code wiring to the `mcpServers.psyche` entry and print a one-line note that hooks can be enabled separately. (Keep scope tight; do not auto-edit hook wiring.)

- **codex** → `~/.codex/config.toml`. Read with `tomllib` if present. Ensure an MCP server entry exists. Since we may not add `tomli-w`, **append** (never rewrite) a clearly-delimited block to the file if the `psyche` entry is absent:
  ```toml
  # >>> psyche (managed) >>>
  [mcp_servers.psyche]
  command = "<venv python>"
  args = ["<repo>/cli.py", "start-mcp"]
  # <<< psyche (managed) <<<
  ```
  Idempotency: skip if the `# >>> psyche (managed) >>>` marker already in the file. Back up `config.toml` → `config.toml.psyche-bak` once. Also append the protocol block to `~/.codex/AGENTS.md` between `<!-- psyche:start -->` / `<!-- psyche:end -->` markers (skip if markers present).

- **gemini / antigravity** → `~/.gemini/config/mcp_config.json` (JSON; create parent dirs). Merge `mcpServers.psyche` like claude-code. Append protocol pointer to `~/.gemini/GEMINI.md` between the same `<!-- psyche:start/end -->` markers.

Common helpers: `_backup_once(path)`, `_merge_json_mcp(path, entry)`, `_append_marked_block(path, start_marker, end_marker, block)`. All respect `dry_run` (compute + return the action string, write nothing).

**CLI wiring (`cli.py`):** add `connect` to usage + commands list and:
```python
elif subcommand == "connect":
    import connect, argparse
    ap = argparse.ArgumentParser(prog="psyche connect")
    ap.add_argument("client", choices=["claude-code","codex","gemini","antigravity"])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    for line in connect.connect(a.client, dry_run=a.dry_run):
        print(line)
```

**Tests to add:** `tests/test_connect.py`
- Use `tempfile`/monkeypatch HOME (set `os.environ['HOME']` to a tmp dir in setUp; connect.py must derive all paths from `os.path.expanduser`, so this isolates it).
- `test_claude_code_creates_mcp_entry` — run `connect('claude-code')`, assert `~/.claude/settings.json` has `mcpServers.psyche.command` ending in `.venv/bin/python` (or at least the args contain `start-mcp`).
- `test_idempotent` — run twice; assert the `psyche` entry appears exactly once and existing unrelated keys survive.
- `test_dry_run_writes_nothing` — `connect('codex', dry_run=True)`; assert the config file does not exist / is unchanged, and a non-empty action list is returned.
- `test_codex_marker_idempotent` — run codex twice; assert the `# >>> psyche (managed) >>>` marker count == 1.

**Acceptance:**
```
cd /Users/aniketnamjoshi/knowledge-project && HOME=/tmp/psyche_v06_home .venv/bin/python cli.py connect claude-code --dry-run 2>&1 | head -3
```
Expected: lines describing the would-be edit, file unchanged.
```
cd /Users/aniketnamjoshi/knowledge-project && TESTING=true .venv/bin/python -m unittest tests.test_connect 2>&1 | tail -2
```
Expected: `OK`. Full suite green. Clean up `/tmp/psyche_v06_home`.

**Out of scope:** auto-enabling Claude Code hooks; verifying the MCP server actually boots in the client.

---

## Step B2 — Project-scoped facts (write + retrieval)

**Goal:** scope atomic facts to the current project, with project facts boosted at retrieval and global facts always included. Columns already added in Step 2.

**Files to modify:** `/Users/aniketnamjoshi/knowledge-project/memzero.py`, `/Users/aniketnamjoshi/knowledge-project/hooks/_hook_common.py`, the three hook entry points, `/Users/aniketnamjoshi/knowledge-project/mcp_server.py`.

**Changes:**

1. **Project key helper** in `memzero.py`:
   ```python
   def project_key_for(cwd: str | None) -> str | None:
       """Returns a stable project key for cwd: the git toplevel basename if cwd
       is in a git repo, else the cwd basename. None when cwd is falsy."""
   ```
   Implement with `subprocess.run(['git','-C',cwd,'rev-parse','--show-toplevel'], ...)`; on success use `os.path.basename(toplevel)`, else `os.path.basename(os.path.abspath(cwd))`. Swallow all errors → fall back to basename.

2. **`add_memory` gains `project: str | None = None`** — stored into the new `project` column. Update the INSERT (memzero.py:185-189) to include `project`. Keep backward-compatible default `None` (global fact).

3. **`search_memories` gains `project: str | None = None`** — change the final SELECT (memzero.py:336-345) to return rows where `project = ? OR project IS NULL` when a project is given (else unchanged), and **boost** project matches in the RRF/ordering: after building `scores`, add a small bonus to ids whose row has `project == <current>`. Simplest: fetch `project` in the SELECT, then re-sort results so same-project facts rank above globals at equal score (stable boost, e.g. add `0.01` to score for project rows before final sort). Keep returning the existing dict shape plus an optional `project` key.

4. **Increment retrieval count.** In `search_memories`, after computing the final `results`, `UPDATE atomic_memories SET retrieval_count = retrieval_count + 1, last_retrieved_at = ? WHERE id IN (...)` for the returned ids (single statement, same connection before close). This powers `mem prune --stale` (B3) and future ranking.

5. **Hooks pass cwd.** In `_hook_common.py` add `def cwd_from_payload(payload) -> str | None: return payload.get('cwd') or payload.get('workspace') or None`. In `psyche_session_start.py`, `psyche_prompt_submit.py`, `psyche_extract.py`: derive `project = memzero.project_key_for(hc.cwd_from_payload(payload))` and pass `project=project` to `add_memory`, and `project=project` to `search_memories` / `standing_fact_rows`. For `standing_fact_rows`, add a `project` param mirroring `search_memories` (project OR NULL, project boosted) — keep it optional/back-compatible.

6. **MCP `add_memory` / `search_memories` tools** (mcp_server.py:741-770): accept an optional `project` argument (add to inputSchema and dispatch). Default `None`.

**Tests to add:** `tests/test_memory.py`
- `test_project_fact_scoping` — add a global fact and a project='alpha' fact; `search_memories(query, project='alpha')` returns both; `search_memories(query, project='beta')` returns only the global one.
- `test_project_boost_orders_first` — with two equally-relevant facts (one project='alpha', one global), project fact ranks first when `project='alpha'`.
- `test_retrieval_count_increments` — after a search returning a fact, its `retrieval_count >= 1`.
- `test_project_key_for_basename` — `project_key_for('/tmp/some/dir')` returns `'dir'` (in a non-git tmp dir).

**Acceptance:**
```
cd /Users/aniketnamjoshi/knowledge-project && TESTING=true .venv/bin/python -m unittest tests.test_memory 2>&1 | tail -2
```
Expected: `OK`. Full suite green.

**Out of scope:** the `mem` CLI (B3); ledger (B4).

---

## Step B3 — `psyche mem` CLI (list/search/add/delete/prune/stats)

**Goal:** a first-class CLI for the atomic memory store.

**Files to create:** `/Users/aniketnamjoshi/knowledge-project/mem_cli.py`
**Files to modify:** `/Users/aniketnamjoshi/knowledge-project/cli.py`, and add list/count helpers to `memzero.py` as needed.

**Subcommands (argparse subparsers, rich output, `--db-path` on each, resolve via `resolve_db_path`):**
- `list [--limit N] [--project P] [--category C] [--all]` — list live facts (superseded_by IS NULL by default; `--all` includes superseded). Add `memzero.list_memories(limit=50, project=None, category=None, include_superseded=False, db_path=None)` returning rows with id/fact/category/project/retrieval_count/updated_at.
- `search <query> [--top N] [--project P]` — calls `memzero.search_memories`, prints bullets.
- `add <fact> [--category C] [--entities a,b] [--project P]` — calls `memzero.add_memory`; prints stored id or duplicate note.
- `delete <id>` — `memzero.delete_memory`.
- `prune [--stale N] [--yes]` — delete facts with `retrieval_count == 0` AND `updated_at` older than N weeks (default 8). Without `--yes`, list candidates and confirm interactively (rich `Confirm.ask`). Add `memzero.prune_stale(weeks=8, dry_run=False, db_path=None) -> list[int]` (returns deleted/candidate ids). It must remove from the `.mem.usearch` index too — reuse `memzero._remove_from_mem_index` and the FTS delete (mirror `delete_memory`, memzero.py:427-440).
- `stats` — print: total live facts, count by category, count by project (top 5), total `retrieval_count` sum, facts never retrieved. Add `memzero.stats(db_path=None) -> dict`. (Token-savings reporting comes from the ledger in B4 and is appended to this `stats` view there.)

**CLI wiring (`cli.py`):** add `mem` to usage + commands; branch:
```python
elif subcommand == "mem":
    import mem_cli
    mem_cli.main()
```

**Tests to add:** `tests/test_memory.py`
- `test_list_memories_filters` — add facts in two projects/categories; assert `list_memories(project=..., category=...)` filters correctly.
- `test_prune_stale_removes_unretrieved_old` — insert a fact with `retrieval_count=0` and an old `updated_at`; `prune_stale(weeks=0)` returns its id and it's gone afterward; a retrieved/recent fact survives.
- `test_stats_shape` — `stats()` returns dict with keys `total`, `by_category`, `never_retrieved`.

**Acceptance:**
```
cd /Users/aniketnamjoshi/knowledge-project && rm -f /tmp/psyche_v06_b3.db* && DATABASE_PATH=/tmp/psyche_v06_b3.db .venv/bin/python cli.py mem add "User prefers tabs over spaces" --category preference 2>&1 | tail -1
cd /Users/aniketnamjoshi/knowledge-project && DATABASE_PATH=/tmp/psyche_v06_b3.db .venv/bin/python cli.py mem stats 2>&1 | tail -3
```
Expected: a "stored" line, then stats showing total ≥ 1.
```
cd /Users/aniketnamjoshi/knowledge-project && TESTING=true .venv/bin/python -m unittest tests.test_memory 2>&1 | tail -2
```
Expected: `OK`. Full suite green.

**Out of scope:** ledger/token math (B4).

---

## Step B4 — Token-savings ledger

**Goal:** record every hook injection to a JSONL ledger and report savings in `psyche mem stats`.

**Files to modify:** `/Users/aniketnamjoshi/knowledge-project/hooks/_hook_common.py`, `psyche_session_start.py`, `psyche_prompt_submit.py`, `/Users/aniketnamjoshi/knowledge-project/mem_cli.py`.

**Changes:**

1. **Append helper** in `_hook_common.py`:
   ```python
   MEM_LEDGER_PATH = os.path.expanduser("~/.psyche/mem_ledger.jsonl")
   def append_ledger(event: str, session_id: str, count: int, chars: int):
       """Appends one JSON line: {ts, event, session_id, count, chars}.
       Swallows all errors (hooks must never break)."""
   ```
   (`event` is `"session_start"` or `"prompt_submit"`.)

2. **Call it** in `psyche_session_start.py` (after computing injected `text`: `hc.append_ledger("session_start", session_id, len(rows), len(text))`) and in `psyche_prompt_submit.py` for both the relevant-facts injection (count=`len(fresh)`, chars=`len(formatted)`) and optionally the remember-capture (count=1, chars=len(fact)). Do NOT log when nothing is injected.

3. **`mem stats` reads the ledger.** Add `mem_cli` logic (or `memzero.ledger_summary(path=MEM_LEDGER_PATH) -> dict`) computing: total injections, total facts injected, total chars injected, estimated tokens injected (`chars / 4`, integer), and an "estimated re-derivation avoided" heuristic = tokens_injected (a fact injected is a fact the agent didn't have to re-derive). Print these under a "Token ledger" section in `mem stats`. Keep the heuristic explicitly labeled "estimate (~chars/4)".

**Tests to add:** `tests/test_memory.py`
- `test_ledger_append_and_summary` — point `MEM_LEDGER_PATH` at a tmp file (monkeypatch), append two events, assert `ledger_summary` returns `total_injections == 2` and `tokens_injected == sum(chars)//4`.
- Make the function take an explicit `path` param so tests don't touch `~/.psyche`.

**Acceptance:**
```
cd /Users/aniketnamjoshi/knowledge-project && TESTING=true .venv/bin/python -m unittest tests.test_memory 2>&1 | tail -2
```
Expected: `OK`. Manual:
```
cd /Users/aniketnamjoshi/knowledge-project && .venv/bin/python -c "
import tempfile, os, _hook_common as hc" 2>/dev/null || true
```
(Primary verification is the unit test; the hook path is exercised by tests, not a live session.) Full suite green.

**Out of scope:** changing injection caps or extraction logic.

---

## Step B5 — Contradiction superseding + retrieval-ranking use

**Goal:** at `add_memory` time, when the most-similar live fact is a *near* (not duplicate) match in `[0.80, 0.95)`, mark the old fact `superseded_by = <new id>` (no LLM call). `superseded_by` is already excluded from retrieval/standing facts (memzero.py:339, :473), so this immediately resolves contradictions at write time.

**Files to modify:** `/Users/aniketnamjoshi/knowledge-project/memzero.py`.

**Changes:**

1. Add a constant: `SUPERSEDE_LOW = 0.80` (reuse `DUP_SIMILARITY = 0.95` as the upper bound).

2. New helper:
   ```python
   def _find_supersede_candidate(db_path, vector) -> tuple[int, float] | None:
       """Returns (id, similarity) of the top live fact whose similarity to vector
       is in [SUPERSEDE_LOW, DUP_SIMILARITY), else None."""
   ```
   Mirror `_find_duplicate` (memzero.py:135-160) but with the band check; require the candidate row to be live (`superseded_by IS NULL`).

3. In `add_memory`, after the dup guard returns no duplicate and the new row is inserted (so we have `memory_id`), look up the supersede candidate and, if found, `UPDATE atomic_memories SET superseded_by = ?, updated_at = ? WHERE id = ? AND superseded_by IS NULL` with `(memory_id, now, candidate_id)`. Include `superseded` info in the returned dict: add key `"superseded"` = `candidate_id or None`. Do this within the same connection/commit used for the insert (move the supersede UPDATE before `conn.commit()` if structurally clean, else a second small commit is fine).

4. Ensure the **search SELECT** already excludes superseded (it does, memzero.py:339) — no change. Ensure `standing_fact_rows` excludes superseded (it does, memzero.py:473) — no change.

5. (Ranking use) When two non-superseded facts tie in search score, prefer the one with higher `retrieval_count`, then more recent `updated_at`. Add this as a final tiebreak in `search_memories`'s ordering (cheap, uses the column from B2). Keep it minimal.

**Tests to add:** `tests/test_memory.py`
- `test_supersede_marks_old_fact` — with a deterministic embedding stub (or by directly inserting two facts and invoking the band logic), add fact A, then add a near-variant A′ whose similarity to A is in `[0.80,0.95)`; assert A's `superseded_by == A′.id` and that `search_memories` returns A′ but not A. To make similarity deterministic in a test, pass a fake `llm` whose `get_embedding` returns controlled vectors (e.g., A=[1,0,...], A′=[0.9,0.43,...] normalized to land in-band) — document the chosen vectors in the test.
- `test_near_duplicate_above_095_is_skipped_not_superseded` — similarity ≥ 0.95 still returns `duplicate_of` and does NOT create a new row (existing behavior preserved).

**Acceptance:**
```
cd /Users/aniketnamjoshi/knowledge-project && TESTING=true .venv/bin/python -m unittest tests.test_memory 2>&1 | tail -2
```
Expected: `OK`. Full suite green.

**Out of scope:** LLM-based contradiction reasoning; updating `update_memory` superseding semantics.

---

## Step 6 — Integration: docs, changelog, final suite, commit

**Goal:** finalize the release.

**Files to modify:** `/Users/aniketnamjoshi/knowledge-project/README.md`, `/Users/aniketnamjoshi/knowledge-project/CHANGELOG.md` (create if absent), `/Users/aniketnamjoshi/knowledge-project/docs/memory-protocol.md` (add project-scoping + connect note if relevant), and a version string bump where one exists (search for `0.2.0` in `mcp_server.py:278` — update `serverInfo.version` to `0.6.0`).

**Do:**
1. **README:** add a "What's new in 0.6" section documenting: actionable guidance plans (`psyche guide "<goal>" --apply`), the check-in loop (`psyche checkin <goal_id> -u "..."`), `psyche connect <client>`, `psyche mem` subcommands, project-scoped memory, and the token ledger in `psyche mem stats`. Update the command list in usage docs to include `connect`, `checkin`, `mem`.
2. **CHANGELOG.md:** add a `## 0.6.0 — <date>` entry summarizing Phase A (guidance redesign: action plans, strict JSON + retry, materialize to records, check-in loop, graceful degradation, atomic-memory context) and Phase B (connect, project-scoped facts, mem CLI, token ledger, contradiction superseding). Today's date: 2026-06-11.
3. **Version bump:** `mcp_server.py` `serverInfo.version` → `"0.6.0"`.
4. **Full suite:**
   ```
   cd /Users/aniketnamjoshi/knowledge-project && TESTING=true .venv/bin/python -m unittest discover tests 2>&1 | tail -3
   ```
   Expected: `OK`, with the new total test count (51 + all added tests).
5. **Commit:** `v0.6 step 6: docs, changelog, version bump for 0.6.0`.

**Acceptance:**
```
cd /Users/aniketnamjoshi/knowledge-project && grep -c "0.6.0" CHANGELOG.md && grep -c "0.6.0" mcp_server.py
```
Expected: each ≥ 1. Full suite `OK`.

**Out of scope:** merging the branch / tagging a release (leave for the user).

---

## Cross-cutting acceptance (run after all steps)

```
cd /Users/aniketnamjoshi/knowledge-project && TESTING=true .venv/bin/python -m unittest discover tests 2>&1 | tail -3
```
Must print `OK`. Then spot-check the four new CLI verbs respond without traceback:
```
cd /Users/aniketnamjoshi/knowledge-project && for c in connect checkin mem guide; do .venv/bin/python cli.py $c --help >/dev/null 2>&1 && echo "$c ok" || echo "$c FAIL"; done
```
Expected: `connect ok`, `checkin ok`, `mem ok`, `guide ok` (note: `guide`/`checkin` may print usage rather than `--help`; "ok" = no crash). Clean up `/tmp/psyche_v06_*`.

---

## Key files (absolute paths)

- `/Users/aniketnamjoshi/knowledge-project/guidance.py` — Phase A centerpiece (Steps 3, 4, 5).
- `/Users/aniketnamjoshi/knowledge-project/plan_schema.py` — new (Step 1).
- `/Users/aniketnamjoshi/knowledge-project/db.py` — single schema v3 migration (Step 2); migration framework at lines 8, 51, 282-311.
- `/Users/aniketnamjoshi/knowledge-project/memzero.py` — project scoping, superseding, ranking, prune/stats (Steps B2, B3, B5).
- `/Users/aniketnamjoshi/knowledge-project/mcp_server.py` — tool registration (Steps 4, 5, B2); version bump (Step 6); register tools in BOTH `tools/list` (mcp_server.py:356-640) and `tools/call` (mcp_server.py:641-804).
- `/Users/aniketnamjoshi/knowledge-project/cli.py` — subcommand dispatch (Steps 5, B1, B3); add to usage strings at lines 32 and 88.
- `/Users/aniketnamjoshi/knowledge-project/connect.py` — new (Step B1).
- `/Users/aniketnamjoshi/knowledge-project/mem_cli.py` — new (Step B3).
- `/Users/aniketnamjoshi/knowledge-project/hooks/{_hook_common.py,psyche_session_start.py,psyche_prompt_submit.py,psyche_extract.py}` — cwd/project + ledger (Steps B2, B4).
- `/Users/aniketnamjoshi/knowledge-project/tests/` — `test_plan_schema.py` (new), `test_connect.py` (new), and additions to `test_guidance.py`, `test_memory.py`, `test_migrations.py`.

---

The plan above is complete and self-contained. Save it verbatim to `/Users/aniketnamjoshi/knowledge-project/docs/implementation-plan-v0.6.md`.

Riskiest step and how to verify: **Step 3** (guidance rewrite) — it deletes the regex fallback and changes the public brief shape consumed by the CLI renderer and the MCP tool. Verify by (a) the three new `test_guidance` tests, (b) the no-chat smoke command which must not raise, and (c) grepping `guidance.py`/`mcp_server.py` for any lingering references to removed keys (`next_action`, `parse_error`, `raw_response`) before committing. The second-riskiest is **Step 2/B2/B5 contention on `db.py` + `memzero.py`**: enforce the ordering — Step 2 must land before B2/B5 begin, since those steps read columns Step 2 creates.