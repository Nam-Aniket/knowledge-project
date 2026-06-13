# Changelog

All notable changes to the **Psyche** project will be documented in this file.

## [0.8.0] - Unreleased

### Added
- **Outcome-capture ledger**: `record_outcome(memory_ids, rule_ids, outcome, confidence, source, session_id)` increments `wins`/`losses` on `atomic_memories` and `rules` rows for non-neutral outcomes with confidence >= 0.5; writes an audit row to the new `memory_outcomes` table in all cases. A per-`(memory_id, day)` cap prevents a single session from double-counting.
- **Automatic outcome capture at session end** (Claude Code hooks): the `SessionEnd`/`PreCompact` hook classifies the session transcript as `good`/`bad`/`neutral` using a cheap proxy-hint + LLM classifier and calls `record_outcome` against the injected IDs recovered from the durable session ledger.
- **Incremental mid-session extraction** (`Stop` hook, `hooks/psyche_stop.py`): durable facts are now captured *during* a session, not only at `SessionEnd`/`PreCompact` — so work is preserved even if the user never exits cleanly (abrupt close, `SIGKILL`, or walking away). The hook fires every assistant turn but is gated by a pure `should_extract` function: it extracts only when `PSYCHE_STOP_MIN_TURNS` (default 4) turns *or* `PSYCHE_STOP_MIN_MINUTES` (default 10) minutes have elapsed since the last extraction, and (on the turns path) the transcript grew by at least `PSYCHE_STOP_MIN_GROWTH` (default 800) chars. The timer path bypasses the growth check so a saturated transcript window can't suppress capture. When the gate passes, extraction runs in a **detached worker** so the hook never blocks the next prompt; outcome classification is intentionally excluded (final verdict stays at session end). Per-session watermark stored at `~/.psyche/sessions/<session_id>.extract.json`. Shared logic factored into `extract_facts()`/`count_turns()` in `psyche_extract.py`.
- **Durable injected-ID ledger**: `write_ledger` now mirrors the injected-ID set to `~/.psyche/sessions/<session_id>.json` in addition to `/tmp`; `read_injected_ids` prefers the durable copy for reliability across session-end timing.
- **Permissioned forget/retraction**: `forget_memory(query=...)` soft-retires matching memories by setting `retired_at`; `forget_memory(ids=[...], confirm=True, hard=True)` hard-deletes. Retired memories are excluded from `search_memories` and `standing_fact_rows`. `unforget(ids)` clears `retired_at`.
- **`psyche mem forget/review/unforget` CLI subcommands** for interactive memory management.
- **`forget_memory`, `record_outcome`, `unforget` MCP tools** registered on the MCP server for use from any host agent.
- **Check-in auto-scoring** (`score_experiment_completion`): when `checkin_plan` assesses an experiment, if `success_condition` contains a numeric comparator (`>=`, `<=`, `>`, `<`, `=`) and a matching metric log exists, the experiment is scored deterministically and `record_outcome` is called with `source="checkin"`.
- **`psyche mem outcomes` subcommand**: observability window for the experiential-learning loop. Shows total outcomes, breakdown by source (transcript/mcp/checkin) and by outcome (good/bad/neutral), sessions classified, top facts by observed win-rate, forget candidates, and retired count. Ranking is NOT yet affected by these counters — capture only.
- **Schema migration v4** (`SCHEMA_VERSION = 4`): adds `wins`, `losses`, `outcome_count`, `last_outcome_at`, `retired_at` to `atomic_memories`; `wins`, `losses`, `last_outcome_at` to `rules`; new `memory_outcomes` audit table.

### Notes
- Outcome counters (`wins`/`losses`) are captured only — they do not yet influence retrieval ranking. The ranking effect will be enabled in a future release once sufficient signal has been collected.

---

## [0.7.0] - 2026-06-12

### Added
- **Host-agent guidance (BYO-model)**: when no chat model is configured, `generate_guidance` now returns a structured `synthesis_pack` (retrieved context + plan schema + instruction) and a new `submit_guidance_plan` MCP tool that validates and materializes the host-agent-authored plan through the existing parser/materialization path — turning the old retrieval-only dead end into a tracked, agent-agnostic protocol. Plans carry a `synthesized_by` provenance field (`host-agent` vs `psyche-llm`) and dedup against recent identical goals.
- **`CHAT_PROVIDER`**: decouples the chat model from the embedding provider (defaults to `LLM_PROVIDER`, so existing configs are unchanged), letting local-embedding users pair an Ollama/Gemini/OpenAI chat model for terminal `psyche guide`.
- **Cache-stable injections**: the session-start memory block is now ordered by immutable `id` and rendered without per-fact dates, making it byte-stable across sessions so it no longer breaks the host model's prompt cache.
- **Per-provider cache-exposure metric**: the token ledger records the session-start block hash and `psyche mem stats` reports how often the cacheable prefix changed across sessions, plus a clearly-labeled modeled savings estimate using a per-provider discount table (Anthropic/OpenAI/Gemini).
- **Measured cache metrics in `psyche mem stats`**: real `cache_read`/`cache_creation` counts read from Claude Code transcripts; replaced the modeled savings figure with measured cache share + block-attributable cost-avoidance; per-project block-change metric.
- **Protocol guidance**: the `psyche connect` protocol block now documents the synthesis-pack flow and append-only placement of memory content.

### Notes
- Single-sourced the version via `mcp_server.__version__`; `pyproject.toml`, `package.json`, and the README badge are manual mirrors (resolves prior 0.4.0/0.5.0/0.6.0 drift).
- No schema migration — `SCHEMA_VERSION` remains 3.

---

## [0.6.0] - 2026-06-12

### Added
- **Guidance Redesign**: Actionable guidance plans via strict JSON parsing with retry, materialization to goals and experiments records, a check-in follow-through loop, graceful degradation for no-chat models, and atomic-memory context injection.
- **Memory Productization**: `psyche connect` for one-command onboarding (Claude Code, Codex, Gemini/Antigravity), project-scoped facts with cwd-derived keys and boosted retrieval, `psyche mem` CLI (list, search, add, delete, prune, stats), token-savings ledger, and contradiction superseding (similarity in [0.80,0.95)) with retrieval-count ranking tiebreak.

---

## [0.5.0] - 2026-06-08

### Added
- **Personal Upgrade & Guidance Layer**: Evolved Psyche beyond RAG into a knowledge-guided decision system. Added structured workflows for Goals, Experiments, Metric tracking, Reviews, and Personal Rules.
- **Guidance Engine**: New `psyche guide` subcommand that synthesizes retrieved knowledge into structured, actionable JSON-based guidance briefs.
- **MCP Guidance Tools**: Added `generate_guidance` and `list_goals_and_experiments` tools to expose the guidance layer to AI assistants.
- **Domain Packs**: Domain-specific heuristics and metrics for business, health, wealth, career, happiness, and ideation.
- **Idea Generation**: Expanded domain detection to include an `ideation` workflow for expanding ideas grounded in knowledge.

---

## [0.3.5] - 2026-06-05

### Added
- **New Document Parsers**: Added native support for parsing **Word DOCX**, **HTML/HTM**, and **Emacs Org-mode** files offline without external Python dependencies.
- **Directory Ingestion Expansion**: Updated directory scanning defaults in `ingest.py` to automatically discover and index `.docx`, `.html`, `.htm`, and `.org` files.

---

## [0.3.4] - 2026-06-05

### Added
- **Local Cross-Encoder Reranking (`flashrank`)**: Fully integrated an offline, CPU-bound ONNX reranker (`ms-marco-TinyBERT-L-2-v2`) to post-process RRF candidates and score relevance.
- **Native SQLite Vector Search (`sqlite-vec`)**: Ingested embeddings into a `vec0` virtual table for highly optimized, C-level semantic match calculations directly inside the SQLite engine.
- **Sub-millisecond ANN Indexing (`usearch`)**: Created a portable HNSW vector index (`knowledge.usearch`) alongside the SQLite database file for $O(\log N)$ semantic retrieval.
- **Dynamic Retrieval Tiering**: Fallback logic gracefully downgrades from `usearch` index searches to `sqlite-vec` MATCH queries, then to NumPy CPU matrices, and finally to pure FTS5 BM25.
- **First-Class Python Installation**: Added configuration and instructions for `pipx install git+https://github.com/Nam-Aniket/psyche.git`.
- **Discovery Keywords/Topics**: Added rich topic list to `package.json` for enhanced search discoverability (`mcp`, `second-brain`, `graphrag`, `local-first`, `pdf-rag`, `ollama`, etc.).

### Changed
- **Branding Renaming**: Unified naming mismatch, renaming all repo and package configurations to `psyche`.
- **Refactored Descriptions**: Replaced risky marketing terms like "premium" with concrete technical descriptors ("high-performance").

---

## [0.3.3] - 2026-06-04

### Changed
- **Vectorized Similarity**: NumPy-vectorized similarity calculations in `query.py` to replace sequential python loops, reducing CPU overhead during flat scans.

---

## [0.3.2] - 2026-06-04

### Added
- **BM25 FTS5 Keyword Scoring**: Switched SQLite keyword search to native FTS5 `bm25()` rank scoring.

---

## [0.3.1] - 2026-06-03

### Changed
- **Decoupled Text Retrieval**: Implemented separation of chunk texts from embedding vectors during retrieval, reducing memory load from 100MB+ to under 5MB.

---

## [0.3.0] - 2026-05-20

### Added
- **Multi-Path Ingestion**: Scan and sync multiple directories or files simultaneously (e.g. `psyche ingest ~/Vault1 ~/Books`).
- **Metadata Check Migration**: Detect embedding dimension changes and prompt/run automatic database migrations.

---

## [0.2.0] - 2026-04-10

### Added
- **AI-Free Fallbacks**: Statistically co-occurring proper-noun graph builder to construct concept links offline.
- **Interactive Chat REPL**: Command-line chat session with prompt history and command completion.

---

## [0.1.0-alpha] - 2026-03-01

### Added
- **Initial Core Implementation**: Parsers for Obsidian MD (wikilinks/frontmatter), EPUB, and PDF.
- **Hybrid RRF Search**: Merging keyword matching and semantic embeddings via Reciprocal Rank Fusion.
- **Model Context Protocol (MCP)**: Exposed tools `search_knowledge` and `retrieve_graph` to MCP-capable assistants.
