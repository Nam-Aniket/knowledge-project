#!/usr/bin/env python3
import os
import sys
import argparse
import numpy as np
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.markdown import Markdown
from rich.status import Status
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.completion import WordCompleter

# Ensure current directory is in path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from db import get_connection, get_all_embeddings_with_chunks, search_fts
from llm_client import LLMClient

# Initialize rich console
console = Console()

# Load environment variables
load_dotenv()

def calculate_similarities(query_vector: list[float] | np.ndarray, chunk_records: list[dict]) -> list[tuple[dict, float]]:
    """Calculates cosine similarity between query embedding and all chunk embeddings."""
    q_vec = np.array(query_vector, dtype=np.float32)
    q_norm = np.linalg.norm(q_vec)
    if q_norm == 0:
        return []
        
    results = []
    for record in chunk_records:
        c_vec = record["embedding"]
        c_norm = np.linalg.norm(c_vec)
        if c_norm == 0:
            continue
            
        similarity = np.dot(q_vec, c_vec) / (q_norm * c_norm)
        results.append((record, float(similarity)))
        
    # Sort by similarity descending
    results.sort(key=lambda x: x[1], reverse=True)
    return results

def reciprocal_rank_fusion(semantic_results: list[tuple[dict, float]], keyword_results: list[dict], k: int = 60) -> list[tuple[dict, float]]:
    """Combines semantic search results and keyword search results using Reciprocal Rank Fusion.
    
    Returns a list of (record, rrf_score) sorted by score descending.
    """
    rrf_scores = {}
    
    # 1. Rank based on semantic scores
    for rank, (record, _) in enumerate(semantic_results, 1):
        chunk_id = record["chunk_id"]
        if chunk_id not in rrf_scores:
            rrf_scores[chunk_id] = {"record": record, "score": 0.0}
        rrf_scores[chunk_id]["score"] += 1.0 / (k + rank)
        
    # 2. Rank based on keyword scores
    for rank, record in enumerate(keyword_results, 1):
        chunk_id = record["chunk_id"]
        if chunk_id not in rrf_scores:
            rrf_scores[chunk_id] = {"record": record, "score": 0.0}
        rrf_scores[chunk_id]["score"] += 1.0 / (k + rank)
        
    combined = [(item["record"], item["score"]) for item in rrf_scores.values()]
    combined.sort(key=lambda x: x[1], reverse=True)
    return combined

def perform_hybrid_search(db_path: str, query_text: str, semantic_records: list[dict], llm: LLMClient) -> list[tuple[dict, float]]:
    """Runs semantic and keyword search, combining them via Reciprocal Rank Fusion (RRF)."""
    # 1. Semantic search
    q_vector = llm.get_embedding(query_text)
    semantic_results = calculate_similarities(q_vector, semantic_records)
    
    # 2. Keyword search
    conn = get_connection(db_path)
    try:
        keyword_results = search_fts(conn, query_text, limit=30)
    finally:
        conn.close()
        
    # 3. Combine via RRF
    return reciprocal_rank_fusion(semantic_results, keyword_results)

def format_context(similar_chunks: list[tuple[dict, float]], top_n: int = 5) -> str:
    """Formats retrieved chunks into a standard RAG context block."""
    context_blocks = []
    for idx, (record, score) in enumerate(similar_chunks[:top_n], 1):
        loc_str = f" [{record['location']}]" if record.get('location') else ""
        block = (
            f"Source [{idx}]: '{record['source_title']}' by {record['source_author']}{loc_str}\n"
            f"RRF Rank Score: {score:.4f}\n"
            f"Content:\n{record['text']}\n"
        )
        context_blocks.append(block)
    return "\n---\n".join(context_blocks)

def main():
    parser = argparse.ArgumentParser(description="Query the local knowledge base or start a chat session.")
    parser.add_argument("query", nargs="?", help="The search query to answer. If omitted, and --chat is not set, prints database status.")
    parser.add_argument("--chat", action="store_true", help="Start an interactive chat session.")
    parser.add_argument("--top", type=int, default=5, help="Number of context chunks to retrieve.")
    parser.add_argument("--db-path", help="Database file path override. Default is read from .env (DATABASE_PATH).")
    
    args = parser.parse_args()
    
    db_path = args.db_path or os.getenv("DATABASE_PATH", "data/knowledge.db")
    if not os.path.exists(db_path):
        console.print(f"[bold red]Error:[/bold red] Database file '{db_path}' not found. Please ingest some files first.", file=sys.stderr)
        sys.exit(1)
        
    # Initialize LLM client
    try:
        llm = LLMClient()
    except Exception as e:
        console.print(f"[bold red]Error initializing LLM client:[/bold red] {e}", file=sys.stderr)
        sys.exit(1)
        
    # Fetch all records
    conn = get_connection(db_path)
    try:
        records = get_all_embeddings_with_chunks(conn)
    finally:
        conn.close()
        
    if not records:
        console.print("[bold yellow]Warning:[/bold yellow] Database is empty. Please run ingest.py to add documents first.")
        sys.exit(0)
        
    if not args.query and not args.chat:
        # Show database status
        console.print("\n[bold green]📊 Database Status[/bold green]")
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Property", style="dim", width=25)
        table.add_column("Value")
        
        table.add_row("Database Path", db_path)
        table.add_row("Total Chunks", str(len(records)))
        
        # Unique sources details
        sources_info = {}
        for r in records:
            title = r["source_title"]
            author = r["source_author"]
            sources_info[title] = author
            
        table.add_row("Total Ingested Books", str(len(sources_info)))
        console.print(table)
        
        console.print("\n[bold cyan]📚 Ingested Books Catalog:[/bold cyan]")
        for title, author in sources_info.items():
            book_chunks = sum(1 for r in records if r["source_title"] == title)
            console.print(f" - [bold]{title}[/bold] by {author} [dim]({book_chunks} chunks)[/dim]")
        console.print("")
        sys.exit(0)
        
    system_instruction = (
        "You are a helpful knowledge assistant. Synthesize a detailed, clear answer based on "
        "the retrieved context chunks below. You must ground your answers strictly in the "
        "provided context. In your answer, you MUST explicitly cite your sources and page numbers/chapters "
        "whenever stating facts from them (e.g. [Title, Page X] or [Title, Chapter Y]). "
        "The Source identifier and location information is provided at the start of each context block. "
        "If the answer cannot be found in the context, be honest and state "
        "that you do not have enough information in the ingested documents to answer."
    )
    
    if args.chat:
        console.print("\n[bold green]=== Chat Mode Activated ===[/bold green]")
        console.print("Ask any questions about your ingested books. Type [bold red]/help[/bold red] for options, or [bold red]/exit[/bold red] to end.\n")
        
        # Set up prompt session with command history file and completion hints
        history_dir = os.path.dirname(os.path.abspath(db_path))
        os.makedirs(history_dir, exist_ok=True)
        history_file = os.path.join(history_dir, ".chat_history")
        
        commands_completer = WordCompleter([
            '/help', '/exit', '/quit', '/sources', '/status'
        ], ignore_case=True)
        
        session = PromptSession(
            history=FileHistory(history_file),
            completer=commands_completer,
            complete_while_typing=True
        )
        
        show_detailed_sources = False
        chat_history = []
        
        while True:
            try:
                # Use prompt_toolkit instead of simple input
                user_input = session.prompt("You > ")
            except (KeyboardInterrupt, EOFError):
                console.print("\n[bold yellow]Goodbye![/bold yellow]")
                break
                
            clean_input = user_input.strip()
            if not clean_input:
                continue
                
            # Process slash commands
            if clean_input.startswith('/'):
                cmd = clean_input.lower()
                if cmd in ['/exit', '/quit']:
                    console.print("[bold yellow]Goodbye![/bold yellow]")
                    break
                elif cmd == '/help':
                    console.print("\n[bold green]💡 Available Chat Commands:[/bold green]")
                    console.print("  [bold]/help[/bold]    - Show this help menu")
                    console.print("  [bold]/exit[/bold]    - Close the chat session")
                    console.print("  [bold]/quit[/bold]    - Close the chat session")
                    console.print("  [bold]/sources[/bold] - Toggle showing full matching book sections in the output")
                    console.print("  [bold]/status[/bold]  - Show active model configurations and database details\n")
                    continue
                elif cmd == '/sources':
                    show_detailed_sources = not show_detailed_sources
                    state_str = "ENABLED" if show_detailed_sources else "DISABLED"
                    console.print(f"[bold cyan]Detailed context sources display is now {state_str}.[/bold cyan]\n")
                    continue
                elif cmd == '/status':
                    console.print("\n[bold green]🛠️ System Status[/bold green]")
                    console.print(f"  [bold]Database:[/bold] {db_path}")
                    console.print(f"  [bold]Provider:[/bold] {llm.provider.upper()}")
                    console.print(f"  [bold]Embedding Model:[/bold] {llm.embed_model}")
                    console.print(f"  [bold]Chat Model:[/bold] {llm.chat_model}")
                    console.print(f"  [bold]Chunks Loaded:[/bold] {len(records)}\n")
                    continue
                else:
                    console.print(f"[bold red]Unknown command:[/bold red] {clean_input}. Type [bold]/help[/bold] for instructions.\n")
                    continue
                
            # Perform Hybrid RAG Search
            try:
                with console.status("[bold cyan]Retrieving context (Hybrid Search) and thinking...") as status:
                    similarities = perform_hybrid_search(db_path, clean_input, records, llm)
                    context_str = format_context(similarities, top_n=args.top)
                    
                    # Prepare conversation prompt
                    history_str = ""
                    for role, text in chat_history[-6:]:
                        history_str += f"{role}: {text}\n"
                        
                    prompt = (
                        f"### RETRIEVED CONTEXT FROM BOOKS:\n{context_str}\n\n"
                        f"### CONVERSATION HISTORY:\n{history_str}"
                        f"User: {clean_input}\n"
                        f"Assistant:"
                    )
                    response = llm.generate_completion(system_instruction, prompt)
                
                # Render LLM output
                console.print("\n[bold purple]Assistant[/bold purple] >")
                console.print(Markdown(response))
                console.print("")
                
                # Show sources used (top unique items from the retrieved list)
                sources_list = []
                for r, score in similarities[:args.top]:
                    loc_suffix = f" [{r['location']}]" if r.get('location') else ""
                    sources_list.append(f"'{r['source_title']}'{loc_suffix} [dim](Score: {score:.3f})[/dim]")
                
                if sources_list:
                    console.print(f"[dim]📚 Sources cited (RRF): {', '.join(sources_list)}[/dim]")
                    
                # If show_detailed_sources toggle is enabled, print the full texts
                if show_detailed_sources:
                    console.print("\n[bold yellow]--- DETAILED CONTEXT USED ---[/bold yellow]")
                    for idx, (r, score) in enumerate(similarities[:args.top], 1):
                        loc_suffix = f" [{r['location']}]" if r.get('location') else ""
                        console.print(f"\n[bold magenta][{idx}] {r['source_title']} by {r['source_author']}{loc_suffix} (RRF: {score:.4f})[/bold magenta]")
                        console.print(f"{r['text'].strip()}")
                    console.print("[bold yellow]-----------------------------[/bold yellow]")
                    
                console.print("[dim]" + "-" * 50 + "[/dim]\n")
                
                chat_history.append(("User", clean_input))
                chat_history.append(("Assistant", response))
            except Exception as e:
                console.print(f"[bold red]Error generating answer:[/bold red] {e}\n")
                
    else:
        # Single query mode
        query_text = args.query
        console.print(f"\n[bold]Query:[/bold] [cyan]'{query_text}'[/cyan]")
        
        try:
            with console.status("[bold cyan]Searching database (Hybrid Search) and synthesizing response...") as status:
                similarities = perform_hybrid_search(db_path, query_text, records, llm)
                context_str = format_context(similarities, top_n=args.top)
                
                prompt = (
                    f"### RETRIEVED CONTEXT FROM BOOKS:\n{context_str}\n\n"
                    f"User Query: {query_text}"
                )
                response = llm.generate_completion(system_instruction, prompt)
                
            console.print("\n" + "=" * 50)
            console.print("[bold green]ANSWER:[/bold green]")
            console.print("=" * 50)
            console.print(Markdown(response))
            console.print("=" * 50)
            
            console.print("\n[bold]📚 Context Sources Cited (RRF):[/bold]")
            for r, score in similarities[:args.top]:
                loc_suffix = f" [{r['location']}]" if r.get('location') else ""
                console.print(f" - [bold]{r['source_title']}[/bold] by {r['source_author']}{loc_suffix} [dim](RRF: {score:.4f})[/dim]")
            console.print("=" * 50 + "\n")
        except Exception as e:
            console.print(f"[bold red]Error generating answer:[/bold red] {e}")

if __name__ == "__main__":
    main()
