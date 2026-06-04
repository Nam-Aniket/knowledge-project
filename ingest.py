#!/usr/bin/env python3
import os
import sys
import argparse
import hashlib
from dotenv import load_dotenv
from rich.console import Console
from rich.progress import Progress
from rich.status import Status

# Ensure current directory is in path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from db import init_db, get_connection, check_checksum, add_source, add_chunk, add_embedding
from parsers import extract_text
from llm_client import LLMClient

# Initialize rich console
console = Console()

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
        end = start + chunk_size
        if end >= len(text):
            chunks.append(text[start:])
            break
            
        split_idx = text.rfind("\n", end - 150, end)
        if split_idx == -1 or split_idx <= start:
            split_idx = text.rfind(" ", end - 100, end)
            
        if split_idx != -1 and split_idx > start:
            end = split_idx
            
        chunks.append(text[start:end].strip())
        start = end - overlap
        
    return [c for c in chunks if len(c.strip()) > 50]

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
        console.print(f"[bold red]Error:[/bold red] File not found at '{args.path}'", file=sys.stderr)
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
            console.print(f"[bold yellow]Warning:[/bold yellow] File matches already ingested source ID: [cyan]{existing_id}[/cyan]. Skipping ingestion.")
            conn.close()
            sys.exit(0)
            
        # 3. Clean up title
        title = args.title or clean_title_from_filename(file_path)
        author = args.author or "Unknown"
        console.print(f"\n[bold green]Ingesting:[/bold green] [italic]'{title}'[/italic] by [bold]{author}[/bold]")
        console.print(f"[dim]SHA-256 Checksum: {checksum}[/dim]")
        
        # 4. Extract Text
        with console.status("[bold cyan]Extracting text and locations from document...") as status:
            extracted_blocks = extract_text(file_path)
            
        if not extracted_blocks:
            console.print("[bold red]Error:[/bold red] No text could be extracted from the file.", file=sys.stderr)
            sys.exit(1)
            
        # 5. Chunk Text
        chunks = [] # List of dict: {"text": str, "location": str | None}
        with console.status("[bold cyan]Splitting text into overlapping chunks by location...") as status:
            for block in extracted_blocks:
                block_text = block["text"]
                block_loc = block["location"]
                
                block_chunks = chunk_text(block_text, chunk_size=args.chunk_size, overlap=args.overlap)
                for c in block_chunks:
                    chunks.append({
                        "text": c,
                        "location": block_loc
                    })
            
        console.print(f"📄 Document split into [bold cyan]{len(chunks)}[/bold cyan] text chunks.")
        
        if not chunks:
            console.print("[bold red]Error:[/bold red] No text chunks created. Content is too short.", file=sys.stderr)
            sys.exit(1)
            
        # 6. Initialize LLM Client and generate embeddings
        console.print("[yellow]Initializing LLM Client...[/yellow]")
        try:
            llm_client = LLMClient()
        except Exception as e:
            console.print(f"[bold red]Error initializing LLM client:[/bold red] {e}", file=sys.stderr)
            sys.exit(1)
            
        embeddings = []
        batch_size = 50
        
        # Generate embeddings with a nice progress bar
        with Progress(console=console) as progress:
            task = progress.add_task("[cyan]Requesting API embeddings...", total=len(chunks))
            for i in range(0, len(chunks), batch_size):
                sub_batch = chunks[i:i+batch_size]
                sub_texts = [c["text"] for c in sub_batch]
                try:
                    sub_embeddings = llm_client.get_embeddings_batch(sub_texts)
                    embeddings.extend(sub_embeddings)
                    progress.update(task, advance=len(sub_batch))
                except Exception as api_err:
                    console.print(f"\n[bold red]API Error during batch starting at chunk {i}:[/bold red] {api_err}", file=sys.stderr)
                    raise api_err
        
        if len(embeddings) != len(chunks):
            console.print(f"[bold red]Error:[/bold red] Generated {len(embeddings)} embeddings for {len(chunks)} chunks.", file=sys.stderr)
            sys.exit(1)
            
        # 7. Write to database
        source_id = add_source(conn, title, author, file_path, checksum)
        
        with Progress(console=console) as progress:
            task = progress.add_task("[green]Storing chunks in SQLite...", total=len(chunks))
            for idx, (chunk_data, embedding) in enumerate(zip(chunks, embeddings)):
                chunk_id = add_chunk(conn, source_id, idx, chunk_data["text"], location=chunk_data["location"])
                add_embedding(conn, chunk_id, embedding)
                progress.update(task, advance=1)
                
        console.print(f"\n✨ [bold green]Ingestion successful![/bold green] Source ID: [bold cyan]{source_id}[/bold cyan] (Added {len(chunks)} chunks and vectors to database).\n")
        
    except Exception as e:
        console.print(f"\n[bold red]Ingestion failed:[/bold red] {e}", file=sys.stderr)
        conn.rollback()
        sys.exit(1)
    finally:
        conn.close()

if __name__ == "__main__":
    main()
