import sqlite3
import os
import numpy as np
from datetime import datetime, timezone

def resolve_db_path(db_path: str = None) -> str:
    """Resolves relative database paths to ~/.psyche/ folder, while leaving absolute paths intact."""
    if not db_path:
        db_path = os.getenv("DATABASE_PATH", "knowledge.db")
    
    if os.path.isabs(db_path):
        return db_path
        
    basename = os.path.basename(db_path)
    home_dir = os.path.expanduser("~/.psyche")
    return os.path.join(home_dir, basename)

def init_db(db_path: str):
    """Initializes the database schema if it doesn't already exist."""
    resolved_path = resolve_db_path(db_path)
    # Ensure directory exists
    db_dir = os.path.dirname(resolved_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
        
    conn = sqlite3.connect(resolved_path)
    cursor = conn.cursor()
    
    # Create sources table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            author TEXT,
            file_path TEXT,
            checksum TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    
    # Create chunks table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL,
            chunk_index INTEGER NOT NULL,
            text TEXT NOT NULL,
            location TEXT,
            FOREIGN KEY (source_id) REFERENCES sources (id) ON DELETE CASCADE
        )
    """)
    
    # Migration helper: Add location column to chunks if database exists but lacks it
    try:
        cursor.execute("ALTER TABLE chunks ADD COLUMN location TEXT")
    except sqlite3.OperationalError:
        pass
    
    # Create embeddings table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS embeddings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chunk_id INTEGER UNIQUE NOT NULL,
            embedding_blob BLOB NOT NULL,
            FOREIGN KEY (chunk_id) REFERENCES chunks (id) ON DELETE CASCADE
        )
    """)
    
    # Create FTS5 virtual table for keyword search
    cursor.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            chunk_id UNINDEXED,
            text
        )
    """)
    
    # Create concepts table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS concepts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            definition TEXT,
            category TEXT
        )
    """)
    
    # Create concept_links table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS concept_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_concept_id INTEGER NOT NULL,
            target_concept_id INTEGER NOT NULL,
            relationship TEXT NOT NULL,
            description TEXT,
            FOREIGN KEY (source_concept_id) REFERENCES concepts (id) ON DELETE CASCADE,
            FOREIGN KEY (target_concept_id) REFERENCES concepts (id) ON DELETE CASCADE,
            UNIQUE(source_concept_id, target_concept_id, relationship)
        )
    """)
    
    conn.commit()
    conn.close()

def get_connection(db_path: str) -> sqlite3.Connection:
    """Returns a connection to the SQLite database."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def check_checksum(conn: sqlite3.Connection, checksum: str) -> int | None:
    """Checks if a file with the given checksum has already been ingested.
    Returns the source_id if it exists, otherwise None.
    """
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM sources WHERE checksum = ?", (checksum,))
    row = cursor.fetchone()
    return row[0] if row else None

def add_source(conn: sqlite3.Connection, title: str, author: str | None, file_path: str, checksum: str) -> int:
    """Adds a new source file to the database. Returns the newly created source ID."""
    cursor = conn.cursor()
    created_at = datetime.now(timezone.utc).isoformat()
    cursor.execute(
        "INSERT INTO sources (title, author, file_path, checksum, created_at) VALUES (?, ?, ?, ?, ?)",
        (title, author, file_path, checksum, created_at)
    )
    conn.commit()
    return cursor.lastrowid

def add_chunk(conn: sqlite3.Connection, source_id: int, chunk_index: int, text: str, location: str | None = None) -> int:
    """Adds a chunk of text to the database with location metadata and indexes it in FTS5. Returns the chunk ID."""
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO chunks (source_id, chunk_index, text, location) VALUES (?, ?, ?, ?)",
        (source_id, chunk_index, text, location)
    )
    chunk_id = cursor.lastrowid
    cursor.execute(
        "INSERT INTO chunks_fts (chunk_id, text) VALUES (?, ?)",
        (chunk_id, text)
    )
    conn.commit()
    return chunk_id

def add_embedding(conn: sqlite3.Connection, chunk_id: int, embedding: list[float] | np.ndarray):
    """Serializes and adds an embedding vector for a specific chunk."""
    cursor = conn.cursor()
    # Convert embedding list/array to float32 binary representation
    vector_arr = np.array(embedding, dtype=np.float32)
    blob = vector_arr.tobytes()
    cursor.execute(
        "INSERT INTO embeddings (chunk_id, embedding_blob) VALUES (?, ?)",
        (chunk_id, blob)
    )
    conn.commit()

def get_all_embeddings_with_chunks(conn: sqlite3.Connection) -> list[dict]:
    """Retrieves all chunk texts, locations, and deserializes their corresponding embeddings."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 
            c.id, 
            c.text, 
            c.location,
            s.title, 
            s.author, 
            e.embedding_blob 
        FROM chunks c
        JOIN sources s ON c.source_id = s.id
        LEFT JOIN embeddings e ON e.chunk_id = c.id
    """)
    rows = cursor.fetchall()
    
    results = []
    for chunk_id, text, location, source_title, source_author, blob in rows:
        embedding = np.frombuffer(blob, dtype=np.float32) if blob is not None else None
        results.append({
            "chunk_id": chunk_id,
            "text": text,
            "location": location,
            "source_title": source_title,
            "source_author": source_author,
            "embedding": embedding
        })
    return results

def search_fts(conn: sqlite3.Connection, query_text: str, limit: int = 20) -> list[dict]:
    """Searches the FTS5 virtual table for keyword matches and returns the original chunk records."""
    cursor = conn.cursor()
    try:
        # FTS5 Match query
        cursor.execute("""
            SELECT 
                c.id, 
                c.text, 
                c.location,
                s.title, 
                s.author,
                e.embedding_blob
            FROM chunks_fts fts
            JOIN chunks c ON fts.chunk_id = c.id
            JOIN sources s ON c.source_id = s.id
            LEFT JOIN embeddings e ON e.chunk_id = c.id
            WHERE chunks_fts MATCH ?
            LIMIT ?
        """, (query_text, limit))
        rows = cursor.fetchall()
    except sqlite3.OperationalError:
        # Fallback to simple LIKE search if query syntax is not supported by FTS MATCH
        cursor.execute("""
            SELECT 
                c.id, 
                c.text, 
                c.location,
                s.title, 
                s.author,
                e.embedding_blob
            FROM chunks c
            JOIN sources s ON c.source_id = s.id
            LEFT JOIN embeddings e ON e.chunk_id = c.id
            WHERE c.text LIKE ?
            LIMIT ?
        """, (f"%{query_text}%", limit))
        rows = cursor.fetchall()
        
    results = []
    for chunk_id, text, location, source_title, source_author, blob in rows:
        embedding = np.frombuffer(blob, dtype=np.float32) if blob is not None else None
        results.append({
            "chunk_id": chunk_id,
            "text": text,
            "location": location,
            "source_title": source_title,
            "source_author": source_author,
            "embedding": embedding
        })
    return results

def add_concept(conn: sqlite3.Connection, name: str, definition: str | None = None, category: str | None = None) -> int:
    """Inserts a concept if it doesn't exist, or updates its definition and category. Returns concept ID."""
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO concepts (name, definition, category)
        VALUES (?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            definition = COALESCE(excluded.definition, definition),
            category = COALESCE(excluded.category, category)
    """, (name.strip(), definition, category))
    conn.commit()
    
    cursor.execute("SELECT id FROM concepts WHERE name = ?", (name.strip(),))
    return cursor.fetchone()[0]

def add_concept_link(conn: sqlite3.Connection, source_name: str, target_name: str, relationship: str, description: str | None = None) -> int:
    """Creates a directed relationship between two concepts by name. Auto-creates concepts if missing."""
    source_id = add_concept(conn, source_name)
    target_id = add_concept(conn, target_name)
    
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO concept_links (source_concept_id, target_concept_id, relationship, description)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(source_concept_id, target_concept_id, relationship) DO UPDATE SET
            description = COALESCE(excluded.description, description)
    """, (source_id, target_id, relationship.strip(), description))
    conn.commit()
    return cursor.lastrowid

def get_all_concepts(conn: sqlite3.Connection) -> list[dict]:
    """Retrieves all concepts from the database."""
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, definition, category FROM concepts")
    rows = cursor.fetchall()
    return [{"id": r[0], "name": r[1], "definition": r[2], "category": r[3]} for r in rows]

def get_concept_links(conn: sqlite3.Connection) -> list[dict]:
    """Retrieves all concept relationship links with name values."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 
            cl.id, 
            c1.name AS source_name, 
            c2.name AS target_name, 
            cl.relationship, 
            cl.description 
        FROM concept_links cl
        JOIN concepts c1 ON cl.source_concept_id = c1.id
        JOIN concepts c2 ON cl.target_concept_id = c2.id
    """)
    rows = cursor.fetchall()
    return [{"id": r[0], "source": r[1], "target": r[2], "relationship": r[3], "description": r[4]} for r in rows]
