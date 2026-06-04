# Knowledge Project RAG Engine

A clean, lightweight, open-source retrieval-augmented generation (RAG) engine. Anyone can download this repository, ingest their books (PDF/EPUB) or notes via a simple CLI, configure their preferred LLM API keys, and query or chat with their accumulated knowledge.

## Features

- **Lightweight & Clean**: Built with minimal external dependencies. Uses API-based embeddings (Gemini or OpenAI) and local SQLite storage. No heavy local model weights.
- **Dependency-Free EPUB Parsing**: Built-in spine-order EPUB text extractor.
- **Fast CLI Tooling**:
  - `ingest.py` for indexing books/notes.
  - `query.py` for single-shot search/retrieval and interactive chat.
- **Local-First & Git-Safe**: Local SQLite database and raw books are kept in `data/` and excluded from Git commits.

## Setup

1. **Clone the repository** (if downloaded from GitHub).
2. **Create a virtual environment & install dependencies**:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
3. **Configure environment variables**:
   Copy the template and fill in your API key:
   ```bash
   cp .env.example .env
   ```
   Open `.env` and set `GEMINI_API_KEY` or `OPENAI_API_KEY`.

4. **Initialize database structure**:
   (Database initialization is done automatically on first run of `ingest.py` or `query.py`).

## Usage

### Ingesting Documents
You can ingest PDF, EPUB, or TXT/MD files by running:
```bash
python ingest.py --path "/path/to/your/book.epub"
```
Or specify a custom title and author:
```bash
python ingest.py --path "/path/to/book.pdf" --title "The Lean Startup" --author "Eric Ries"
```

### Querying and Chatting
To run a single-shot query and retrieve answer synthesized from your books:
```bash
python query.py "What are the core principles of customer discovery?"
```

To start an interactive chat session with your knowledge base:
```bash
python query.py --chat
```

## Running Tests
Run the unit test suite to verify parsers, chunking, and database operations:
```bash
python -m unittest discover tests
```
