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
            FOREIGN KEY (source_id) REFERENCES sources (id) ON DELETE CASCADE
        )
    """)
    
    # Create embeddings table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS embeddings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chunk_id INTEGER UNIQUE NOT NULL,
            embedding_blob BLOB NOT NULL,
            FOREIGN KEY (chunk_id) REFERENCES chunks (id) ON DELETE CASCADE
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

def add_chunk(conn: sqlite3.Connection, source_id: int, chunk_index: int, text: str) -> int:
    """Adds a chunk of text to the database. Returns the newly created chunk ID."""
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO chunks (source_id, chunk_index, text) VALUES (?, ?, ?)",
        (source_id, chunk_index, text)
    )
    conn.commit()
    return cursor.lastrowid

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
    """Retrieves all chunk texts and deserializes their corresponding embeddings."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 
            c.id, 
            c.text, 
            s.title, 
            s.author, 
            e.embedding_blob 
        FROM chunks c
        JOIN sources s ON c.source_id = s.id
        JOIN embeddings e ON e.chunk_id = c.id
    """)
    rows = cursor.fetchall()
    
    results = []
    for chunk_id, text, source_title, source_author, blob in rows:
        embedding = np.frombuffer(blob, dtype=np.float32)
        results.append({
            "chunk_id": chunk_id,
            "text": text,
            "source_title": source_title,
            "source_author": source_author,
            "embedding": embedding
        })
    return results
