#!/usr/bin/env python3
import os
import sys
import argparse
import hashlib
import logging
from dotenv import load_dotenv

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Ensure current directory is in path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from db import init_db, get_connection, check_checksum, add_source, add_chunk, add_embedding
from parsers import extract_text
from llm_client import LLMClient

# Load environment variables
load_dotenv()

def calculate_sha256(file_path: str) -> str:
    """Calculates the SHA-256 checksum of a file to prevent duplicate ingestion."""
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()

def clean_title_from_filename(filename: str) -> str:
    """Derives a clean title from a filename."""
    name, _ = os.path.splitext(os.path.basename(filename))
    cleaned = name.replace("_", " ").replace("-", " ")
    return cleaned.strip().title()

def chunk_text(text: str, chunk_size: int = 1500, overlap: int = 300) -> list[str]:
    """Splits text into chunks of character length chunk_size with overlap."""
    chunks = []
    if not text:
        return chunks
    
    start = 0
    while start < len(text):
        # Find a clean boundary like a space or newline near chunk_size
        end = start + chunk_size
        if end >= len(text):
            chunks.append(text[start:])
            break
            
        # Try to find a newline in the last 150 characters to split cleanly
        split_idx = text.rfind("\n", end - 150, end)
        if split_idx == -1 or split_idx <= start:
            # Try to find a space
            split_idx = text.rfind(" ", end - 100, end)
            
        if split_idx != -1 and split_idx > start:
            end = split_idx
            
        chunks.append(text[start:end].strip())
        start = end - overlap
        
    return [c for c in chunks if len(c.strip()) > 50] # filter out empty/tiny chunks

def main():
    parser = argparse.ArgumentParser(description="Ingest a book or document into the local RAG database.")
    parser.add_argument("--path", required=True, help="Absolute or relative path to the book file (PDF, EPUB, TXT, MD).")
    parser.add_argument("--title", help="Manual title override. Default is derived from filename.")
    parser.add_argument("--author", help="Author of the document.")
    parser.add_argument("--db-path", help="Database file path override. Default is read from .env (DATABASE_PATH).")
    parser.add_argument("--chunk-size", type=int, default=1500, help="Target chunk size in characters.")
    parser.add_argument("--overlap", type=int, default=300, help="Overlap between chunks in characters.")
    
    args = parser.parse_args()
    
    # 1. Resolve paths
    file_path = os.path.abspath(args.path)
    if not os.path.exists(file_path):
        logger.error(f"File not found: {args.path}")
        sys.exit(1)
        
    db_path = args.db_path or os.getenv("DATABASE_PATH", "data/knowledge.db")
    
    # 2. Checksum/Duplicate verification
    checksum = calculate_sha256(file_path)
    
    # Init DB if not exists
    init_db(db_path)
    
    conn = get_connection(db_path)
    try:
        existing_id = check_checksum(conn, checksum)
        if existing_id is not None:
            logger.warning(f"File matches an already ingested source ID: {existing_id}. Skipping ingestion.")
            conn.close()
            sys.exit(0)
            
        # 3. Clean up title
        title = args.title or clean_title_from_filename(file_path)
        author = args.author or "Unknown"
        logger.info(f"Ingesting: '{title}' by {author} (Checksum: {checksum[:8]}...)")
        
        # 4. Extract Text
        logger.info("Extracting text from file...")
        full_text = extract_text(file_path)
        if not full_text.strip():
            logger.error("No text could be extracted from the file.")
            sys.exit(1)
            
        # 5. Chunk Text
        logger.info("Splitting text into chunks...")
        chunks = chunk_text(full_text, chunk_size=args.chunk_size, overlap=args.overlap)
        logger.info(f"Created {len(chunks)} text chunks.")
        
        if not chunks:
            logger.error("No text chunks created. Is the file contents too short?")
            sys.exit(1)
            
        # 6. Initialize LLM Client and generate embeddings
        logger.info("Initializing LLM Client for embedding generation...")
        llm_client = LLMClient()
        
        logger.info("Generating embeddings in batch (this may take a moment)...")
        embeddings = llm_client.get_embeddings_batch(chunks)
        
        if len(embeddings) != len(chunks):
            logger.error(f"Mismatch in embeddings generated vs chunks. Chunks: {len(chunks)}, Embeddings: {len(embeddings)}")
            sys.exit(1)
            
        # 7. Write to database
        logger.info("Writing sources, chunks, and embeddings to database...")
        source_id = add_source(conn, title, author, file_path, checksum)
        
        for idx, (text_val, embedding) in enumerate(zip(chunks, embeddings)):
            chunk_id = add_chunk(conn, source_id, idx, text_val)
            add_embedding(conn, chunk_id, embedding)
            
        logger.info(f"Ingestion successful! Source ID: {source_id} (Added {len(chunks)} chunks and embeddings).")
        
    except Exception as e:
        logger.error(f"Ingestion failed: {e}")
        conn.rollback()
        sys.exit(1)
    finally:
        conn.close()

if __name__ == "__main__":
    main()
