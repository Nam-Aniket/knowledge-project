#!/usr/bin/env python3
import os
import sys
import argparse
import numpy as np
from dotenv import load_dotenv

# Ensure current directory is in path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from db import get_connection, get_all_embeddings_with_chunks
from llm_client import LLMClient

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

def format_context(similar_chunks: list[tuple[dict, float]], top_n: int = 5) -> str:
    """Formats retrieved chunks into a standard RAG context block."""
    context_blocks = []
    for idx, (record, score) in enumerate(similar_chunks[:top_n], 1):
        block = (
            f"Source [{idx}]: '{record['source_title']}' by {record['source_author']}\n"
            f"Similarity Score: {score:.4f}\n"
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
        print(f"Error: Database file '{db_path}' not found. Please ingest some files first.")
        sys.exit(1)
        
    # Initialize LLM client
    try:
        llm = LLMClient()
    except Exception as e:
        print(f"Error initializing LLM client: {e}")
        sys.exit(1)
        
    # Fetch all records
    conn = get_connection(db_path)
    try:
        records = get_all_embeddings_with_chunks(conn)
    finally:
        conn.close()
        
    if not records:
        print("Database is empty. Please run ingest.py to add documents first.")
        sys.exit(0)
        
    if not args.query and not args.chat:
        # Show database status
        titles = set(r["source_title"] for r in records)
        print("=== Database Status ===")
        print(f"Database Path  : {db_path}")
        print(f"Total Sources  : {len(titles)}")
        for t in titles:
            print(f" - {t}")
        print(f"Total Chunks   : {len(records)}")
        print("=======================")
        sys.exit(0)
        
    system_instruction = (
        "You are a helpful knowledge assistant. Synthesize a detailed, clear answer based on "
        "the retrieved context chunks below. You must ground your answers strictly in the "
        "provided context. If the answer cannot be found in the context, be honest and state "
        "that you do not have enough information in the ingested documents to answer."
    )
    
    if args.chat:
        print("\n=== Chat Mode Activated ===")
        print("Ask any questions about your ingested books. Type 'exit' or 'quit' to end.\n")
        chat_history = []
        
        while True:
            try:
                user_input = input("You > ")
            except (KeyboardInterrupt, EOFError):
                print("\nGoodbye!")
                break
                
            if user_input.strip().lower() in ["exit", "quit"]:
                print("Goodbye!")
                break
                
            if not user_input.strip():
                continue
                
            # Perform RAG
            q_vector = llm.get_embedding(user_input)
            similarities = calculate_similarities(q_vector, records)
            
            context_str = format_context(similarities, top_n=args.top)
            
            # Prepare conversation prompt
            history_str = ""
            for role, text in chat_history[-6:]:  # last 3 rounds
                history_str += f"{role}: {text}\n"
                
            prompt = (
                f"### RETRIEVED CONTEXT FROM BOOKS:\n{context_str}\n\n"
                f"### CONVERSATION HISTORY:\n{history_str}"
                f"User: {user_input}\n"
                f"Assistant:"
            )
            
            print("\nThinking...")
            try:
                response = llm.generate_completion(system_instruction, prompt)
                print(f"\nAssistant > {response}\n")
                
                # Show sources used
                print("📚 Sources:")
                for r, score in similarities[:args.top]:
                    if score > 0.3:
                        print(f" - '{r['source_title']}' (Score: {score:.2f})")
                print("="*40 + "\n")
                
                chat_history.append(("User", user_input))
                chat_history.append(("Assistant", response))
            except Exception as e:
                print(f"Error generating answer: {e}\n")
                
    else:
        # Single query mode
        query_text = args.query
        print(f"Query: '{query_text}'")
        print("Searching database and embedding query...")
        
        q_vector = llm.get_embedding(query_text)
        similarities = calculate_similarities(q_vector, records)
        
        context_str = format_context(similarities, top_n=args.top)
        
        prompt = (
            f"### RETRIEVED CONTEXT FROM BOOKS:\n{context_str}\n\n"
            f"User Query: {query_text}"
        )
        
        print("Generating synthesized response...")
        try:
            response = llm.generate_completion(system_instruction, prompt)
            print("\n" + "="*40)
            print("ANSWER:")
            print("="*40)
            print(response)
            print("="*40)
            
            print("\n📚 Context Sources Used:")
            for r, score in similarities[:args.top]:
                print(f" - '{r['source_title']}' by {r['source_author']} (Similarity: {score:.4f})")
            print("="*40)
        except Exception as e:
            print(f"Error generating answer: {e}")

if __name__ == "__main__":
    main()
