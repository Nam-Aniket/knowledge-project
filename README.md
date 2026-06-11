# Psyche 🧠

<div align="center">
  <p><strong>Give any AI assistant searchable, cited access to your private notes and documents.</strong></p>

  [![Version](https://img.shields.io/badge/version-0.5.0-blueviolet.svg?style=for-the-badge)](https://github.com/Nam-Aniket/psyche)
  [![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey.svg?style=for-the-badge)](https://github.com/Nam-Aniket/psyche)
  [![License](https://img.shields.io/badge/license-MIT-green.svg?style=for-the-badge)](https://github.com/Nam-Aniket/psyche)
  [![Model Context Protocol](https://img.shields.io/badge/MCP-Enabled-orange.svg?style=for-the-badge)](https://modelcontextprotocol.io)
  [![GitHub Stars](https://img.shields.io/github/stars/Nam-Aniket/psyche?style=for-the-badge&color=yellow)](https://github.com/Nam-Aniket/psyche/stargazers)
</div>

---

## 🆕 What's New in 0.6

- **Actionable Guidance Plans**: Turn goals into concrete tasks using `psyche guide "<goal>" --apply`.
- **Check-in Loop**: Follow through on your plans and log reviews using `psyche checkin <goal_id> -u "<update text>"`.
- **One-Command Onboarding**: Wire Psyche into your coding agents instantly with `psyche connect <client>`.
- **Memory CLI**: Manage your atomic facts from the terminal with the `psyche mem` subcommands (`list`, `search`, `add`, `prune`, `stats`).
- **Project-Scoped Memory**: Facts are now scoped to your project directories for higher relevance.
- **Token Ledger**: Track tokens saved from automatic memory injection in `psyche mem stats`.

---

## 🎯 Why This Matters

> **Turn your Obsidian vaults, books, and documents into a private, local-first knowledge-guided decision and experiment tracking system for AI assistants.**

Standard LLM assistants operate within a temporary, sliding window of context—every time you start a new chat, your guidelines, goals, and learnings are completely forgotten. Psyche bridges this gap by giving your AI tools a stateful, local-first guidance engine that runs 100% offline. It allows you to build a persistent, private second brain that tracks your goals, generates knowledge-grounded experiments, and helps you formulate personal rules.

---

## 🔒 Built on Trust (Local-First & Private)

Hosted RAG and document-search tools suffer from a critical privacy problem: they require uploading your private thoughts, diaries, and books to third-party servers.

Psyche is built from the ground up for absolute data safety:
*   🛡️ **100% Local Indexing**: All text parsing, chunking, and vector embedding calculations occur entirely on your local machine using fast ONNX models or Ollama.
*   🚫 **No Silent Uploads**: Your documents never leave your disk.
*   🔍 **Strict Citations**: Every single search result includes direct file paths, chapters, or page numbers so you can immediately verify where the assistant sourced its knowledge.

---

## ⚡ Absurdly Fast Installation (The 2-Step Golden Path)

Ready to connect your documents to your assistant? It takes under 60 seconds.

### 1. Ingest your notes and books
Point Psyche at folders containing markdown, PDFs, EPUBs, Org files, or DOCX documents:
```bash
npx psyche ingest ~/Documents ~/Obsidian
```

### 2. Expose your knowledge as an MCP Tool
Start the Model Context Protocol (MCP) server so Cursor, Claude Desktop, or Antigravity can query it:
```bash
npx psyche start-mcp
```

---

## 🧠 Stateful Agent Memory (Letta/MemGPT Hierarchy)

Rather than treating RAG as a static, read-only search engine, Psyche implements a dynamic, hierarchical memory system for your AI agents:

1.  **Document Knowledge (Archival RAG)**: Hybrid FTS5 (BM25) lexical search and HNSW vector search over your files (`search_knowledge`).
2.  **Core Memory (RAM)**: Key-value facts and project guidelines (e.g. coding preferences, styling choices, naming rules) that the agent writes and reads dynamically (`write_memory_core`).
3.  **Archival Memory (Disk)**: Vector-embedded logs, learnings, and debugging context that the agent archives for long-term reference (`append_memory_archival`).
4.  **Interaction History (Recall)**: Stateful logging of conversation turns to ensure context persistence across assistant sessions (`record_interaction`).
5.  **Atomic Memory (Cross-Agent Facts)**: Deduplicated, one-sentence facts with agent/run scope and entity links, hybrid-retrieved and injected into your coding agents automatically (`add_memory`, `search_memories`, `update_memory`, `delete_memory`, `list_entities`). See the next section.

---

## 🔁 Atomic Memory: One Brain Across Claude Code, Codex & Antigravity

> **Your AI assistant has amnesia. You've been paying for it — in tokens — every single session.**

Every new session, your coding agent re-reads the same files to rediscover your conventions, re-asks about preferences you stated last week, and repeats mistakes it already made. You pay for that re-derivation in tokens, latency, and patience — typically **10,000–40,000 redundant tokens per session** on a recurring project.

Psyche's atomic memory layer ends that. It is a mem0-class memory engine — extraction, deduplication, hybrid retrieval, entity links — that runs entirely on your machine, costs $0, and is shared by **every agent you use**. A preference you state in Codex is known to Claude Code. A lesson learned in Antigravity follows you everywhere.

| | The old way | With Psyche atomic memory |
|---|---|---|
| Session start | Agent rediscovers context from scratch | ~1.5 KB of standing facts injected automatically |
| Each prompt | You re-explain, agent re-reads | Up to ~800 tokens of *relevant* facts — only when a strong match exists, never noise |
| Session end | Everything is forgotten | Durable facts extracted and stored, zero tokens billed to your agent |
| Your data | Re-uploaded to a hosted memory SaaS | Never leaves your disk |
| Cross-agent | Each tool has its own silo | One shared local store for all of them |

The mechanics that make it free and fast:
*   ✂️ **ADD-only writes with a cosine duplicate guard** — no per-fact LLM judging loops burning API calls; conflicts resolve at read time by recency.
*   🎯 **Similarity-gated injection** — weak matches inject *nothing*. A session-level ledger guarantees a fact is never injected twice. Memory that wastes tokens isn't memory, it's noise.
*   🔍 **Three-signal retrieval** — HNSW vector search + FTS5 keywords + entity matching, fused with RRF. The same retrieval stack that powers `search_knowledge`, on a dedicated facts index that never pollutes your document search.

### Per-agent integration

*   🤖 **Claude Code — fully automatic (hooks).** Lifecycle hooks inject standing facts at session start, search memories on every prompt, and extract new facts at session end. The model spends **zero** tool schemas and zero turns on memory — the harness does it all.
*   ⌨️ **Codex — MCP + `AGENTS.md` protocol.** Codex calls `search_memories` at task start and `add_memory` when you state preferences or decisions, guided by a drop-in protocol block in `~/.codex/AGENTS.md`.
*   🛸 **Antigravity — MCP + global rules.** Same protocol via `~/.gemini/GEMINI.md`; the shared `~/.gemini/config/mcp_config.json` covers the IDE, CLI, and Antigravity 2.0 in one entry.
*   💬 **Claude Desktop / Cursor — MCP tools.** Six memory tools (`add_memory`, `search_memories`, `get_memory`, `update_memory`, `delete_memory`, `list_entities`) on the same Psyche server you already have configured.

> [!NOTE]
> Search and injection run on local ONNX embeddings out of the box. Automatic fact *extraction* from transcripts activates when a chat model is configured (`psyche setup` — a free Gemini key or local Ollama both work). Without one, facts accumulate through the explicit `add_memory` tool — and everything else works identically.

---

## 🧭 Personal Upgrade & Guidance Engine

Psyche goes beyond static memory by actively tracking your goals, experiments, and learnings across domains (Business, Health, Wealth, Career, Happiness, and Ideation). AI agents use this layer to act as your personal coach, grounded strictly in the knowledge you have ingested.

- **Goals & Metrics**: Track what you are trying to achieve and the objective metrics that define success.
- **Experiments**: Formulate actionable hypotheses grounded in your notes and books, complete with success/failure conditions.
- **Reviews**: Log what happened, what worked, and what didn't to extract actionable lessons.
- **Personal Rules**: Crystalize hard-learned lessons into enduring principles that the AI will remind you of in future sessions.
- **Idea Generation**: Expand upon raw ideas using your knowledge base to generate actionable prototypes and next steps.

When an AI assistant calls the `generate_guidance` tool, Psyche uses Reciprocal Rank Fusion and Cross-Encoder reranking to find the most relevant principles from your documents, compares them to your current goals and rules, and outputs a structured **Guidance Brief** detailing exactly what you should do next.

---

## 🍳 Recipes

Here is how you can put Psyche to work immediately:

### 📓 Chat with your Obsidian vault
Ingest your markdown notes recursively. Psyche automatically strips YAML frontmatter, cleans wikilinks (`[[Concept|Display]]` -> `Display`), and extracts tags:
```bash
npx psyche ingest ~/Obsidian/PersonalVault
```

### 📚 Query a folder of PDFs and Ebooks
Ingest a library of research papers, PDFs, or EPUB books. Psyche extracts text and tracks page/location details:
```bash
npx psyche ingest ~/Downloads/Books --ext pdf,epub
```

> [!TIP]
> **Robust PDF Extraction:** If your PDFs are malformed or show warnings (e.g., `Ignoring wrong pointing object`), install `pymupdf` in Psyche's environment for highly robust, C-accelerated parsing:
> ```bash
> pip install pymupdf
> ```
> **Re-ingestion / Overwriting:** If you want to force re-ingestion of already processed documents (e.g., to upgrade their extracted contents using PyMuPDF), use the `--force` (or `-f`) flag:
> ```bash
> npx psyche ingest ~/Downloads/Books --force
> ```


### 💾 Run fully offline with Ollama
Configure Ollama (`llama3` + `nomic-embed-text`) during the setup wizard to query your index completely offline with no network connection at all:
```bash
npx psyche setup
```

### 🧠 Give Claude Code automatic memory (hooks)
Wire the three bundled hook scripts into `~/.claude/settings.json` so facts flow in and out of sessions with zero model overhead:
```json
"hooks": {
  "SessionStart":     [{"hooks": [{"type": "command", "command": "<psyche>/.venv/bin/python <psyche>/hooks/psyche_session_start.py", "timeout": 15}]}],
  "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "<psyche>/.venv/bin/python <psyche>/hooks/psyche_prompt_submit.py", "timeout": 15}]}],
  "PreCompact":       [{"hooks": [{"type": "command", "command": "<psyche>/.venv/bin/python <psyche>/hooks/psyche_extract.py", "timeout": 60}]}],
  "SessionEnd":       [{"hooks": [{"type": "command", "command": "<psyche>/.venv/bin/python <psyche>/hooks/psyche_extract.py", "timeout": 60}]}]
}
```

### 🤝 Share one memory across Codex and Antigravity
With the Psyche MCP server registered in `~/.codex/config.toml` and `~/.gemini/config/mcp_config.json`, drop the memory protocol block ([docs/memory-protocol.md](docs/memory-protocol.md)) into `~/.codex/AGENTS.md` and `~/.gemini/GEMINI.md`. Both agents will then read and write the same fact store Claude Code uses.

---

## 🏗️ How it Works (System Architecture)

```mermaid
flowchart TD
    subgraph Local ["1. Private Documents"]
        obsidian["Obsidian Vaults (.md)"]
        books["Books (PDF, EPUB, DOCX)"]
        docs["Org-mode & Plain Text"]
    end

    subgraph Process ["2. Ingest & Index"]
        ingest["psyche ingest"]
        clean["Wikilink Cleaner & Frontmatter Stripper"]
        chunk["Location-Aware Chunking"]
        sqlite[("SQLite DB (Metadata)")]
        fts5["FTS5 (Keyword Index)"]
        sqlite_vec["sqlite-vec (Vector Index)"]
        usearch["usearch (HNSW Index)"]
    end

    subgraph Retrieve ["3. Hybrid Search & Rerank"]
        query["Hybrid Query"]
        rrf["Reciprocal Rank Fusion (RRF)"]
        flashrank["flashrank ONNX Reranker (Offline)"]
    end

    subgraph Serve ["4. AI Assistants (MCP)"]
        mcp["MCP JSON-RPC Server"]
        cli["Interactive REPL Chat"]
        editor["Cursor / Claude Desktop / Antigravity"]
    end

    Local --> ingest
    ingest --> clean --> chunk
    chunk --> sqlite & fts5 & sqlite_vec & usearch
    sqlite & fts5 & sqlite_vec & usearch --> query
    query --> rrf --> flashrank
    flashrank --> mcp & cli
    mcp --> editor
```

1.  **Ingest**: Scan folders.
2.  **Process**: Chunks texts, cleans markdown syntax, and prepares metadata.
3.  **Embed & Index**: Generates vector embeddings (locally via ONNX/fastembed) and indexes them in a C-level SQLite vector index (`sqlite-vec`) and an HNSW vector index (`usearch`).
4.  **Retrieve**: Merges lexical matches (FTS5 `bm25`) and semantic matches using **Reciprocal Rank Fusion (RRF)**.
5.  **Rerank**: Rescores matches locally on CPU using a lightweight ONNX Cross-Encoder model (`flashrank`).
6.  **Serve**: Exposes search/write tools to editors and LLMs via Model Context Protocol (MCP).

---

## 🔮 Theme Mapping (GraphRAG Concept Networks)

Identify connections across your entire notes collection. Run `psyche build-graph` to cluster vectors using K-Means and map co-occurrences of proper nouns. Ask your assistant conceptual questions like:
*   *"What themes connect my notes on career, discipline, and AI agents?"*
*   *"Summarize how my Stoicism files relate to my writing tips."*

---

## 🚀 Installation & Developer Setup

### 1. Install via NPM (Recommended)
Install the package globally:
```bash
npm install -g psyche
```

### 2. Install via Pipx (Python alternative)
```bash
pipx install git+https://github.com/Nam-Aniket/psyche.git
```

### 3. Clone & Develop Locally
```bash
git clone https://github.com/Nam-Aniket/psyche.git
cd psyche
./setup.sh
```

---

## 🔌 Integrating with Cursor / Claude Desktop / Antigravity

### 🛠️ Automatic Configuration (Smithery.ai)
```bash
npx -y @smithery/cli install psyche --client claude
```

### ⚙️ Manual Configuration (Cursor)
Open **Cursor Settings** -> **Features** -> **MCP**, click **+ Add New MCP Server**:
*   **Name:** `psyche`
*   **Type:** `command`
*   **Command:** `npx -y psyche start-mcp`

### ⚙️ Manual Configuration (Claude Desktop)
Add this to `~/Library/Application Support/Claude/claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "psyche": {
      "command": "npx",
      "args": [
        "-y",
        "psyche",
        "start-mcp"
      ]
    }
  }
}
```

---

## 🧪 Running Tests
Verify database migrations, FTS5 keywords, and vector searches:
```bash
.venv/bin/python -m unittest discover tests
```

---

## ⭐ Support the Project
If you find Psyche useful for giving your AI assistants a local brain, please consider starring the repository! It helps other developers discover the project and supports local-first, privacy-focused developer tooling.
