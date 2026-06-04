# 🧠 Psyche

[![Version](https://img.shields.io/badge/version-0.2.0-blueviolet.svg?style=for-the-badge)](https://github.com/Nam-Aniket/knowledge-project)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey.svg?style=for-the-badge)](https://github.com/Nam-Aniket/knowledge-project)
[![License](https://img.shields.io/badge/license-MIT-green.svg?style=for-the-badge)](https://github.com/Nam-Aniket/knowledge-project)
[![Model Context Protocol](https://img.shields.io/badge/MCP-Enabled-orange.svg?style=for-the-badge)](https://modelcontextprotocol.io)

A premium, lightweight, completely offline-capable **GraphRAG & RAG Engine** for your Obsidian notes, books, and documents. Connect your second brain directly to coding assistants (like Antigravity or Claude) using the built-in **Model Context Protocol (MCP) Server**, or query and chat with it locally via an interactive terminal interface.

<p align="center">
<pre>
    ____  _____  __  __   ______ __  __ ______
   / __ \/ ___/  \ \/ /  / ____// / / // ____/
  / /_/ /\__ \    \  /  / /    / /_/ // __/   
 / ____/___/ /    / /  / /___ / __  // /___   
/_/    /____/    /_/   \____//_/ /_//_____/   
</pre>
</p>


---

## ⚡ Key Features

*   🌍 **Multi-Path Position Sync**: Ingest multiple books, documents, or entire directories dynamically (e.g. `psyche ingest ~/Obsidian ~/Downloads/Books`).
*   🕸️ **Hybrid FTS5 + Semantic Search**: Rerank keyword hits and semantic vector matches using **Reciprocal Rank Fusion (RRF)** for ultra-precise context.
*   🔮 **GraphRAG Concept Map**: Semantic K-Means clustering and LLM-guided schema builders mapping links and definitions across your corpus.
*   📓 **Obsidian Note Sync**: Automatically strips YAML frontmatter, extracts markdown tags as keywords, prunes system directories (`.obsidian`, `.trash`), and cleans wikilinks (`[[Concept|Display]]` -> `Display`).
*   🔌 **Model Context Protocol (MCP)**: Directly expose your books and notes to LLMs in the background. Assistants can query your brain database dynamically.
*   🛡️ **100% Local / AI-Free Fallbacks**:
    *   No API keys needed — run locally using Ollama (`llama3` + `nomic-embed-text`).
    *   **AI-Free Search**: Fall back to pure-retrieval Rich markdown views.
    *   **AI-Free Graphs**: Statistical proper-noun co-occurrence extraction builder.

---

## 🏗️ System Architecture

```mermaid
flowchart TD
    subgraph Ingestion Pipeline
        paths[Positional Paths] --> parse[parsers.py: PDF / EPUB / Obsidian]
        parse --> clean[Obsidian Stripper & Wikilink Cleaner]
        clean --> chunk[Location-Aware Chunking]
        chunk --> embed[llm_client.py: Embeddings]
        embed --> db[(SQLite Database)]
        chunk --> fts[FTS5 Search Table]
    end

    subgraph Interface & RAG
        cli[cli.py Router] --> query[query.py: Hybrid Search]
        db --> query
        fts --> query
        query --> chat[Interactive REPL Chat / Single Query]
        query --> assistant[Antigravity / Claude Code via MCP]
    end

    subgraph Concept Graph
        db --> clusters[K-Means Vector Clustering]
        clusters --> concept_graph["LLM / Statistical Co-occurrence Graph"]
        concept_graph --> db
    end
```

---

## 🚀 Setup & Installation

### 1. Run the Setup Command (Recommended)
Clone the repository and run the setup script:
```bash
git clone https://github.com/Nam-Aniket/knowledge-project.git
cd knowledge-project
./setup.sh
```

This script will automatically:
1. Initialize a Python virtual environment (`.venv`) if it doesn't exist.
2. Install all dependencies and the package in editable mode.
3. Link the global `psyche` command so you can run it from any directory.
4. **Launch the Interactive Setup Wizard** to configure your model provider (Gemini, OpenAI, Ollama, or AI-Free) and API keys.

---

### 2. Manual Installation
If you prefer not to use the shell script, you can run the setup command directly in Python:
```bash
python3 cli.py setup
```





---

## 📖 CLI Usage Reference

### 1. Ingesting Notes and Books
Pass files, folders, or directories positionally. The tool only reads notes without editing them:
```bash
# Ingest folders recursively
psyche ingest ~/Obsidian/PersonalVault ~/Downloads/Books

# Ingest with tag and directory filters
psyche ingest ~/Obsidian/PersonalVault --ext md,txt

# Keep folders separate under isolated databases
psyche ingest ~/Obsidian/WorkVault --topic career
```

### 2. Searching and Chatting
Ask one-off questions or activate the premium interactive REPL chat:
```bash
# Query the default database
psyche query "What did Seneca write about focus?"

# Query a specific topic database
psyche query "What is the sprint structure?" --topic career

# Launch interactive chat shell
psyche chat
```
*REPL commands inside Chat*:
*   `/status` — Inspect database sizes, model providers, and active topic.
*   `/sources` — Toggle displaying full matching excerpts in outputs.
*   `/exit` — Exit the chat.

### 3. Generating GraphRAG Concept Networks
Build semantic relationship diagrams dynamically:
```bash
# Build concept connections
psyche build-graph --clusters 8
```

### 4. Running the MCP Server
Connect coding assistants (such as Antigravity or Claude Desktop) directly:
```bash
psyche start-mcp
```

---

## 🔌 Integrating with Antigravity / Claude

To expose your books and notes database directly to coding assistants, add the following to your configuration file (e.g., `~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "psyche": {
      "command": "/Users/aniketnamjoshi/knowledge-project/.venv/bin/python",
      "args": [
        "/Users/aniketnamjoshi/knowledge-project/cli.py",
        "start-mcp"
      ],
      "env": {
        "DATABASE_PATH": "/Users/aniketnamjoshi/knowledge-project/data/knowledge.db"
      }
    }
  }
}
```

---

## 🧪 Running Tests
Verify database connections, FTS5 parsers, and similarity algorithms:
```bash
.venv/bin/python -m unittest discover tests
```
