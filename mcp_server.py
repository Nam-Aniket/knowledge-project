#!/usr/bin/env python3
import sys
import os

# Signal that the process is running in a non-interactive MCP context
os.environ["PSYCHE_NONINTERACTIVE"] = "1"

import json
import traceback

# Save real stdout and redirect standard output to stderr
real_stdout = sys.stdout
sys.stdout = sys.stderr

# Ensure current directory is in path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from db import get_connection, get_all_embeddings_with_chunks, resolve_db_path, check_and_migrate_embeddings
from query import perform_hybrid_search, format_context, retrieve_concept_context
from llm_client import LLMClient

def log(msg):
    sys.stderr.write(f"[Psyche MCP] {msg}\n")
    sys.stderr.flush()

def search_knowledge_tool(query_text, topic=None, top=5):
    # Resolve db path
    db_path = resolve_db_path(os.getenv("DATABASE_PATH", "knowledge.db"))
    if topic:
        db_path = resolve_db_path(f"topic_{topic}.db")
        
    if not os.path.exists(db_path):
        return f"Error: Database for topic '{topic or 'default'}' not found at '{db_path}'."
        
    try:
        # Initialize LLM Client
        llm = LLMClient()
        check_and_migrate_embeddings(db_path, llm)
    except Exception as e:
        log(f"Failed to initialize LLM or migrate database: {e}. Falling back to offline mode.")
        class FakeLLM:
            provider = "none"
            embed_model = "none"
        llm = FakeLLM()
        
    conn = get_connection(db_path)
    try:
        records = get_all_embeddings_with_chunks(conn)
    finally:
        conn.close()
        
    if not records:
        return "Database is empty. Please ingest some documents first."
        
    similarities = perform_hybrid_search(db_path, query_text, records, llm)
    context = format_context(similarities, top_n=top)
    
    # Check if there is graph context
    conn = get_connection(db_path)
    try:
        graph_ctx = retrieve_concept_context(conn, query_text)
    finally:
        conn.close()
        
    result = ""
    if graph_ctx:
        result += f"### RELATED CONCEPTS:\n{graph_ctx}\n\n---\n\n"
    result += f"### RELEVANT TEXT PASSAGES:\n{context}"
    return result

def retrieve_graph_tool(topic=None):
    db_path = resolve_db_path(os.getenv("DATABASE_PATH", "knowledge.db"))
    if topic:
        db_path = resolve_db_path(f"topic_{topic}.db")
        
    if not os.path.exists(db_path):
        return f"Error: Database for topic '{topic or 'default'}' not found."
        
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        
        # Fetch concepts
        cursor.execute("SELECT name, definition, category FROM concepts")
        concepts = cursor.fetchall()
        
        # Fetch links
        cursor.execute("""
            SELECT c1.name, c2.name, l.relationship, l.description 
            FROM concept_links l
            JOIN concepts c1 ON l.source_concept_id = c1.id
            JOIN concepts c2 ON l.target_concept_id = c2.id
        """)
        links = cursor.fetchall()
    except Exception as e:
        return f"Error reading concept graph: {e}"
    finally:
        conn.close()
        
    if not concepts:
        return "No concepts found in the graph. Run 'psyche build-graph' to extract them."
        
    output = "### CONCEPTS\n"
    for name, definition, category in concepts:
        cat_str = f" [{category}]" if category else ""
        def_str = f": {definition}" if definition else ""
        output += f"- {name}{cat_str}{def_str}\n"
        
    if links:
        output += "\n### RELATIONSHIPS\n"
        for src, tgt, rel, desc in links:
            desc_str = f" ({desc})" if desc else ""
            output += f"- {src} --({rel})--> {tgt}{desc_str}\n"
            
    return output

def main():
    log("Server starting on stdio transport...")
    
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
                
            req = json.loads(line)
            method = req.get("method")
            req_id = req.get("id")
            
            # Default response format
            resp = {
                "jsonrpc": "2.0",
                "id": req_id
            }
            
            if method == "initialize":
                resp["result"] = {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {},
                        "prompts": {}
                    },
                    "serverInfo": {
                        "name": "psyche-mcp",
                        "version": "0.2.0"
                    }
                }
            elif method == "notifications/initialized":
                # Notifications don't get a response
                continue
            elif method == "prompts/list":
                resp["result"] = {
                    "prompts": [
                        {
                            "name": "psyche",
                            "description": "Ask a question or search for concepts across your Obsidian notes and books database.",
                            "arguments": [
                                {
                                    "name": "query",
                                    "description": "The search query or question to ask the database (e.g. 'writing tips')",
                                    "required": True
                                },
                                {
                                    "name": "topic",
                                    "description": "Optional topic database name (e.g. topic_<topic>.db)",
                                    "required": False
                                },
                                {
                                    "name": "top",
                                    "description": "Optional number of results to retrieve (default is 5)",
                                    "required": False
                                }
                            ]
                        }
                    ]
                }
            elif method == "prompts/get":
                params = req.get("params", {})
                name = params.get("name")
                arguments = params.get("arguments", {})
                
                if name == "psyche":
                    query = arguments.get("query")
                    topic = arguments.get("topic")
                    top_val = arguments.get("top", 5)
                    try:
                        top = int(top_val)
                    except Exception:
                        top = 5
                        
                    if not query:
                        raise ValueError("The 'query' argument is required.")
                        
                    text_result = search_knowledge_tool(query, topic, top)
                    
                    resp["result"] = {
                        "description": f"Retrieved knowledge from database for: '{query}'",
                        "messages": [
                            {
                                "role": "user",
                                "content": {
                                    "type": "text",
                                    "text": f"Use the following retrieved notes and passages to address the query: '{query}'\n\n{text_result}"
                                }
                            }
                        ]
                    }
                else:
                    resp["error"] = {
                        "code": -32601,
                        "message": f"Prompt '{name}' not found."
                    }
            elif method == "tools/list":
                resp["result"] = {
                    "tools": [
                        {
                            "name": "search_knowledge",
                            "description": "Perform hybrid semantic and keyword search across your notes and books database. Returns relevant text passages and matching concept graph elements.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "query": {
                                        "type": "string",
                                        "description": "The search query/question (e.g. 'Stoic discipline')"
                                    },
                                    "topic": {
                                        "type": "string",
                                        "description": "Optional topic/profile database name (maps to topic_<topic>.db). If omitted, uses the default database."
                                    },
                                    "top": {
                                        "type": "integer",
                                        "description": "Optional number of results to retrieve (default is 5)",
                                        "default": 5
                                    }
                                },
                                "required": ["query"]
                            }
                        },
                        {
                            "name": "retrieve_graph",
                            "description": "Retrieve concepts and concept connection links from the GraphRAG concept graph.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "topic": {
                                        "type": "string",
                                        "description": "Optional topic/profile database name. If omitted, uses the default database."
                                    }
                                }
                            }
                        }
                    ]
                }
            elif method == "tools/call":
                params = req.get("params", {})
                tool_name = params.get("name")
                arguments = params.get("arguments", {})
                
                if tool_name == "search_knowledge":
                    q = arguments.get("query")
                    topic = arguments.get("topic")
                    top = arguments.get("top", 5)
                    
                    text_result = search_knowledge_tool(q, topic, top)
                    resp["result"] = {
                        "content": [
                            {
                                "type": "text",
                                "text": text_result
                            }
                        ]
                    }
                elif tool_name == "retrieve_graph":
                    topic = arguments.get("topic")
                    text_result = retrieve_graph_tool(topic)
                    resp["result"] = {
                        "content": [
                            {
                                "type": "text",
                                "text": text_result
                            }
                        ]
                    }
                else:
                    resp["error"] = {
                        "code": -32601,
                        "message": f"Tool '{tool_name}' not found."
                    }
            else:
                if req_id is not None:
                    resp["error"] = {
                        "code": -32601,
                        "message": f"Method '{method}' not found."
                    }
                else:
                    continue
                    
            real_stdout.write(json.dumps(resp) + "\n")
            real_stdout.flush()
            
        except Exception as e:
            log(f"Error processing line: {traceback.format_exc()}")
            try:
                if 'req_id' in locals() and req_id is not None:
                    err_resp = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {
                            "code": -32603,
                            "message": str(e)
                        }
                    }
                    real_stdout.write(json.dumps(err_resp) + "\n")
                    real_stdout.flush()
            except Exception:
                pass

if __name__ == "__main__":
    main()
