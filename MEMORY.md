# Developer Memory & Learning Log

This file contains the persistent context, architecture guidelines, user preferences, and historical learnings for AI coding agents (such as Antigravity, Cursor, and Claude Desktop) working on the **Psyche** project.

---

## 🧭 Agent Rules of Engagement

### 1. Session Boot Protocol
At the beginning of any development session:
* Read this file ([MEMORY.md](file:///Users/aniketnamjoshi/knowledge-project/MEMORY.md)) to restore architectural context, coding rules, and the state of active objectives.
* If a task relates to past debugging, indexing, or external integrations, run `search_knowledge` or look in `/Users/aniketnamjoshi/Obsidian/AgentLogs/` for historical context.

### 2. Session Close Protocol
At the end of any session:
* Update the **Chronological Learning Log** at the bottom of this file with key decisions, technical learnings, and gotchas discovered during the session.
* If you built a reusable skill, resolved a complex system issue, or established a core coding template, write a standalone markdown file to `/Users/aniketnamjoshi/Obsidian/AgentLogs/` (e.g., `YYYY-MM-DD-topic.md`) so it can be indexed by Psyche for future reference.

---

## 🛠️ Technology Stack & Styling Rules

* **Framework & Base**: Python (3.10+) workspace with Node wrapper for packaging and global execution (`package.json`, `postinstall.js`).
* **Core Storage**: SQLite (`db.py`) storing document metadata, FTS5 keyword indices, and proper noun relations.
* **Vector Companion**: Native C-level HNSW vector search indices via `usearch`.
* **Frontend/Web Aesthetics**: When building interfaces or dashboards:
  * Use **Vanilla CSS** with tailored HSL colors, modern typography (e.g., Google Fonts Outfit/Inter), and smooth micro-animations.
  * Do NOT use Tailwind CSS unless explicitly requested.
  * Use dark modes, glassmorphism, and responsive layouts. No generic colors.

---

## 🏗️ System Architecture Core

* **`db.py`**: SQLite database gateway. Manages document schemas, FTS5 triggers, concept maps, proper noun extraction, and the **Guidance Layer tables/CRUD** (goals, experiments, metric_logs, reviews, rules).
* **`ingest.py`**: Chunks markdown/books/PDFs recursively, strips YAML/wikilinks, calculates lexical features, and queues texts for embeddings.
* **`query.py`**: Implements hybrid search combining SQLite FTS5 (BM25) and vector cosine similarity via Reciprocal Rank Fusion (RRF), followed by Lightweight CPU Cross-Encoder reranking (`flashrank`).
* **`guidance.py`**: Core guidance engine. Manages domain question packs (YAML), handles domain detection, generates structured briefs using hybrid search + concept graph + LLM, and formats briefs for terminal display.
* **`mcp_server.py`**: Implements Model Context Protocol endpoints exposing knowledge search (`search_knowledge`, `retrieve_graph`), session memory, and guidance tools (`generate_guidance`, `list_goals_and_experiments`) to editors and LLMs.

---

## 🎯 Active Objectives & State

1. **Guidance Layer & Personal Upgrade (Hermes Upgrade)**:
   * **Completed**: Added SQLite schemas, domain question packs (YAML) in `~/.psyche/domains/`, CLI subcommands, new MCP server tools (`generate_guidance`, `list_goals_and_experiments`), and a comprehensive unit test suite (`tests/test_guidance.py`).
   * **State**: Verified and passing all tests. Ready for production usage.
2. **GitHub Growth & Promoters**:
   * Focus on developer marketing, repository README clarity, and optimizing Smithery/NPM integrations to maximize stars and visibility.
3. **Local Memory Integration (Hermes Loop)**:
   * Keep this `MEMORY.md` file updated.
   * Monitor sync loop using the `/Users/aniketnamjoshi/Obsidian/AgentLogs` folder and `/Users/aniketnamjoshi/.psyche/watcher.log`.

---

## 📜 Chronological Learning Log

### 2026-06-08: Psyche Guidance Layer & Personal Upgrade
* **Feature**: Designed and implemented the Psyche Guidance Layer to evolve Psyche into a knowledge-guided decision/experiment tracking system.
* **Design Decisions**:
  * **Domain packs**: Seed domain question packs stored as YAML files in `~/.psyche/domains/` for easy sharing, versioning, and editing.
  * **Co-location**: Guidance tables (goals, experiments, metric logs, reviews, rules) are stored inside the same topic/default SQLite databases. This preserves the single-file profile/topic replication model and makes queries simple and fast.
  * **MCP Tools**: Added `generate_guidance` and `list_goals_and_experiments` to `mcp_server.py` to allow AI assistants to directly interact with the decision loop.
  * **Verification**: Added `tests/test_guidance.py` covering schema creation, CRUD helpers, domain pack loading, keyword domain detection, brief generation, and MCP wrapper endpoints. All 45 project tests are passing.

### 2026-06-05: Shared Memory Loop Verification
* **Discovery/Issue**: Investigated why the watched `AgentLogs` folder was empty despite the watcher system being configured.
* **Paths involved**:
  * Local DB: `/Users/aniketnamjoshi/.psyche/knowledge.db`
  * Watcher Log: `/Users/aniketnamjoshi/.psyche/watcher.log`
  * Obsidian Target: `/Users/aniketnamjoshi/Obsidian/AgentLogs`
  * LaunchAgent: `com.psyche.watcher`
* **Resolution**: Verified that the watcher and sync scripts are functioning perfectly. When a markdown file (e.g., `2026-06-05-shared-memory-loop.md`) is written to `AgentLogs`, it is immediately processed, embedded, and added to the SQLite database. Future sessions can retrieve it via `search_knowledge`.
* **Action Item**: Always write durable context into `AgentLogs` at the end of sessions.

### 2026-06-05: Establishing the Hermes memory loop
* **Action**: Created [MEMORY.md](file:///Users/aniketnamjoshi/knowledge-project/MEMORY.md) to serve as a working memory file for all current and future assistant sessions.

### 2026-06-05: release v0.4.0 & GraphRAG compilation
* **Action**: Bumped version to `0.4.0` in `package.json`, `pyproject.toml`, and `README.md`. Optimized package keyword metadata for NPM and PyPI to boost Google and GitHub search index visibility.
* **Feature**: Built the native write-back memory engine (WAL mode, database migrations, incremental HNSW updates, compaction script, and tools) and pushed the commit branch to origin.
* **GraphRAG**: Compiled the co-occurrence GraphRAG concept network from 22,383 database chunks, extracting 30 core concepts and 53 links.

