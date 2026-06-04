import sqlite3
import os
import numpy as np
from datetime import datetime, timezone

def init_db(db_path: str):
    """Initializes the database schema if it doesn't already exist."""
    # Ensure data directory exists
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
        
    conn = sqlite3.connect(db_path)
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
        JOIN embeddings e ON e.chunk_id = c.id
    """)
    rows = cursor.fetchall()
    
    results = []
    for chunk_id, text, location, source_title, source_author, blob in rows:
        embedding = np.frombuffer(blob, dtype=np.float32)
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
            JOIN embeddings e ON e.chunk_id = c.id
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
            JOIN embeddings e ON e.chunk_id = c.id
            WHERE c.text LIKE ?
            LIMIT ?
        """, (f"%{query_text}%", limit))
        rows = cursor.fetchall()
        
    results = []
    for chunk_id, text, location, source_title, source_author, blob in rows:
        embedding = np.frombuffer(blob, dtype=np.float32)
        results.append({
            "chunk_id": chunk_id,
            "text": text,
            "location": location,
            "source_title": source_title,
            "source_author": source_author,
            "embedding": embedding
        })
    return results
