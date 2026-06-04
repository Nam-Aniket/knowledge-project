#!/usr/bin/env python3
import os
import sys
import argparse
import json
import numpy as np
from rich.console import Console
from rich.progress import Progress
from rich.table import Table

# Ensure current directory is in path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from db import get_connection, get_all_embeddings_with_chunks, add_concept, add_concept_link
from llm_client import LLMClient

console = Console()

def kmeans(embeddings: np.ndarray, num_clusters: int, max_iter: int = 20):
    """Performs K-Means clustering on normalized embeddings using cosine similarity."""
    num_samples, dim = embeddings.shape
    if num_samples <= num_clusters:
        return np.arange(num_samples), embeddings
        
    # Standardize/normalize vectors to unit length
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    norm_embeddings = embeddings / norms
    
    # Initialize centroids randomly
    np.random.seed(42)  # For deterministic runs
    indices = np.random.choice(num_samples, num_clusters, replace=False)
    centroids = norm_embeddings[indices].copy()
    
    labels = np.zeros(num_samples, dtype=int)
    for iteration in range(max_iter):
        # Cosine similarity matrix (num_samples, num_clusters)
        similarities = np.dot(norm_embeddings, centroids.T)
        new_labels = np.argmax(similarities, axis=1)
        
        # Check convergence
        if np.array_equal(labels, new_labels):
            break
        labels = new_labels
        
        # Update centroids
        new_centroids = np.zeros_like(centroids)
        for c in range(num_clusters):
            cluster_points = norm_embeddings[labels == c]
            if len(cluster_points) > 0:
                mean_vec = cluster_points.mean(axis=0)
                norm = np.linalg.norm(mean_vec)
                if norm > 0:
                    new_centroids[c] = mean_vec / norm
                else:
                    new_centroids[c] = mean_vec
            else:
                new_centroids[c] = norm_embeddings[np.random.choice(num_samples)]
        centroids = new_centroids
        
    return labels, centroids

def clean_json_text(text: str) -> str:
    """Strips Markdown JSON wrappers from LLM output."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return cleaned

def build_concept_graph(db_path: str, num_clusters: int):
    # 1. Fetch chunks and embeddings
    conn = get_connection(db_path)
    try:
        records = get_all_embeddings_with_chunks(conn)
    finally:
        conn.close()
        
    if not records:
        console.print("[bold red]Error:[/bold red] Database is empty. Ingest some documents first.")
        sys.exit(1)
        
    console.print(f"\n[bold green]Creating GraphRAG Concept Graph from {len(records)} chunks...[/bold green]")
    
    # 2. Extract embeddings matrix
    embeddings_list = [r["embedding"] for r in records]
    embeddings = np.array(embeddings_list, dtype=np.float32)
    
    # Run K-Means clustering
    with console.status("[bold cyan]Semantically clustering text chunks...") as status:
        labels, centroids = kmeans(embeddings, num_clusters)
        
    # Group records by cluster label
    clusters = {c: [] for c in range(num_clusters)}
    for idx, label in enumerate(labels):
        clusters[label].append(records[idx])
        
    # 3. LLM Setup
    try:
        llm = LLMClient()
    except Exception as e:
        console.print(f"[bold red]Error initializing LLM client:[/bold red] {e}")
        sys.exit(1)
        
    # 4. Cluster-by-cluster extraction with sampling constraints
    conn = get_connection(db_path)
    total_concepts = 0
    total_links = 0
    
    try:
        for c in range(num_clusters):
            cluster_records = clusters[c]
            if not cluster_records:
                continue
                
            # Sample max 3 chunks per source title
            source_groups = {}
            for r in cluster_records:
                title = r["source_title"]
                if title not in source_groups:
                    source_groups[title] = []
                if len(source_groups[title]) < 3:
                    source_groups[title].append(r)
                    
            sampled_records = []
            for group in source_groups.values():
                sampled_records.extend(group)
                
            console.print(f"\n[bold cyan]Processing Theme {c+1}/{num_clusters} ({len(sampled_records)} sampled chunks from {len(source_groups)} sources)...[/bold cyan]")
            
            # Prepare summarized context for the LLM
            context_text = ""
            for idx, r in enumerate(sampled_records, 1):
                context_text += f"Document: {r['source_title']} ({r['location'] or 'Unknown'})\nContent:\n{r['text']}\n---\n"
                
            # LLM Prompt to extract concepts and connection links
            system_instruction = (
                "You are an expert knowledge graph extractor. Analyze the text representing a semantic theme. "
                "Identify the key high-level concepts, define them, categorize them, and document the direct relationships between them."
            )
            prompt = (
                f"Identify 2-4 key concepts and relationship links from the text below.\n\n"
                f"TEXT CONTENT:\n{context_text}\n\n"
                "You MUST output your response in raw JSON format matching this schema exactly:\n"
                "{\n"
                "  \"concepts\": [\n"
                "    {\"name\": \"Concept Name\", \"definition\": \"A clear, 1-2 sentence definition\", \"category\": \"e.g., Philosophy, Science, Habit, Finance\"}\n"
                "  ],\n"
                "  \"links\": [\n"
                "    {\"source\": \"Concept Name\", \"target\": \"Concept Name\", \"relationship\": \"e.g., implements / connects to / opposes\", \"description\": \"1 sentence description of connection\"}\n"
                "  ]\n"
                "}"
            )
            
            try:
                with console.status(f"[yellow]Extracting graph connections for Theme {c+1}...") as status:
                    raw_response = llm.generate_completion(system_instruction, prompt)
                    
                cleaned_response = clean_json_text(raw_response)
                graph_data = json.loads(cleaned_response)
                
                # Insert extracted concepts
                concepts_added = []
                for concept in graph_data.get("concepts", []):
                    name = concept.get("name")
                    definition = concept.get("definition")
                    category = concept.get("category")
                    if name:
                        add_concept(conn, name, definition, category)
                        concepts_added.append(name)
                        total_concepts += 1
                        
                # Insert links
                links_added = []
                for link in graph_data.get("links", []):
                    src = link.get("source")
                    tgt = link.get("target")
                    rel = link.get("relationship")
                    desc = link.get("description")
                    if src and tgt and rel:
                        add_concept_link(conn, src, tgt, rel, desc)
                        links_added.append(f"{src} --({rel})--> {tgt}")
                        total_links += 1
                        
                console.print(f" ✅ Extracted [bold green]{len(concepts_added)}[/bold green] Concepts: {', '.join(concepts_added[:4])}")
                if links_added:
                    console.print(f" ✅ Extracted [bold blue]{len(links_added)}[/bold blue] Links: {', '.join(links_added[:2])}")
                    
            except Exception as extract_err:
                console.print(f" ❌ Failed to extract Theme {c+1}: {extract_err}")
                
        console.print(f"\n✨ [bold green]Concept Graph Construction Completed![/bold green] Total added: {total_concepts} concepts, {total_links} links.\n")
        
    finally:
        conn.close()

def main():
    parser = argparse.ArgumentParser(description="Build a semantic concept graph from ingested chunks.")
    parser.add_argument("--clusters", type=int, default=6, help="Number of semantic cluster themes to identify.")
    parser.add_argument("--db-path", help="Database file path override. Default is read from .env (DATABASE_PATH).")
    
    args = parser.parse_args()
    db_path = args.db_path or os.getenv("DATABASE_PATH", "data/knowledge.db")
    
    if not os.path.exists(db_path):
        console.print(f"[bold red]Error:[/bold red] Database file '{db_path}' not found. Please ingest some files first.", file=sys.stderr)
        sys.exit(1)
        
    build_concept_graph(db_path, args.clusters)

if __name__ == "__main__":
    main()
