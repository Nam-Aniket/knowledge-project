# Changelog

All notable changes to the **Psyche** project will be documented in this file.

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
