#!/usr/bin/env python3
import sys
import os

# Signal that the process is running in a non-interactive MCP context
os.environ["PSYCHE_NONINTERACTIVE"] = "1"

import json
import hashlib
import re
import traceback

# Save real stdout and redirect standard output to stderr
real_stdout = sys.stdout
sys.stdout = sys.stderr

# Ensure current directory is in path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from db import get_connection, get_all_embeddings_with_chunks, resolve_db_path, check_and_migrate_embeddings, index_path_for
from query import perform_hybrid_search, format_context, retrieve_concept_context
from llm_client import LLMClient

def log(msg):
    sys.stderr.write(f"[Psyche MCP] {msg}\n")
    sys.stderr.flush()

def resolve_topic_db_path(topic=None):
    """Resolves the database path for an optional topic, validating the topic name.

    A topic must contain only alphanumeric characters, underscores, or hyphens to
    prevent path traversal or injection via the topic_<topic>.db filename.
    """
    if topic and not re.fullmatch(r"[A-Za-z0-9_-]+", topic):
        raise ValueError(f"Invalid topic name: {topic!r}")
    if topic:
        return resolve_db_path(f"topic_{topic}.db")
    return resolve_db_path(os.getenv("DATABASE_PATH", "knowledge.db"))

def search_knowledge_tool(query_text, topic=None, top=5):
    import numpy as np
    from db import get_all_embeddings_only
    
    # Resolve top parameter to int
    try:
        top = int(top)
    except (ValueError, TypeError):
        top = 5
        
    # Resolve db path
    db_path = resolve_topic_db_path(topic)
        
    if not os.path.exists(db_path):
        raise RuntimeError(f"Error: Database for topic '{topic or 'default'}' not found at '{db_path}'.")
        
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
        
    # Try loading the usearch index FIRST; the full embeddings matrix is only a
    # numpy fallback used when the index is absent.
    records = []
    chunk_ids = np.array([], dtype=np.int32)
    embeddings_matrix = np.array([], dtype=np.float32)
    usearch_index = None
    if llm.provider != "none":
        index_path = index_path_for(db_path)
        try:
            from usearch.index import Index
            if os.path.exists(index_path):
                usearch_index = Index.restore(index_path)
        except Exception:
            usearch_index = None

        if usearch_index is None:
            # No index: fall back to loading all embeddings into a numpy matrix.
            conn = get_connection(db_path)
            try:
                records = get_all_embeddings_only(conn)
            finally:
                conn.close()
            chunk_ids = np.array([r["chunk_id"] for r in records if r["embedding"] is not None], dtype=np.int32)
            valid_embeddings = [r["embedding"] for r in records if r["embedding"] is not None]
            if valid_embeddings:
                embeddings_matrix = np.vstack(valid_embeddings)

    # If no index and no embeddings loaded, the database has nothing to search.
    if usearch_index is None and len(records) == 0 and llm.provider != "none":
        return "Database is empty. Please ingest some documents first."


    similarities = perform_hybrid_search(
        db_path=db_path,
        query_text=query_text,
        chunk_ids=chunk_ids,
        embeddings_matrix=embeddings_matrix,
        llm=llm,
        usearch_index=usearch_index,
        limit=top
    )
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
    db_path = resolve_topic_db_path(topic)
        
    if not os.path.exists(db_path):
        raise RuntimeError(f"Error: Database for topic '{topic or 'default'}' not found.")
        
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
        raise RuntimeError(f"Error reading concept graph: {e}")
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

def record_interaction_tool(session_id: str, role: str, content: str, tool_calls: str = None, topic: str = None):
    from datetime import datetime, timezone
    db_path = resolve_topic_db_path(topic)
        
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        created_at = datetime.now(timezone.utc).isoformat()
        cursor.execute("""
            INSERT INTO memory_recall (session_id, role, content, tool_calls, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (session_id, role, content, tool_calls, created_at))
        conn.commit()
        return f"Successfully recorded interaction for session '{session_id}'."
    except Exception as e:
        raise RuntimeError(f"Error recording interaction: {e}")
    finally:
        conn.close()

def write_memory_core_tool(key: str, value: str, category: str = "general", topic: str = None):
    from datetime import datetime, timezone
    db_path = resolve_topic_db_path(topic)
        
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO memory_core (key, value, category, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                category = COALESCE(excluded.category, category),
                updated_at = excluded.updated_at
        """, (key, value, category, datetime.now(timezone.utc).isoformat()))
        conn.commit()
        return f"Core memory updated successfully: {key} -> '{value}'"
    except Exception as e:
        raise RuntimeError(f"Error writing core memory: {e}")
    finally:
        conn.close()

def append_memory_archival_tool(text: str, topic: str = None, author: str = "Assistant"):
    from datetime import datetime, timezone
    from db import add_source, add_chunk, add_embedding, update_usearch_index_incrementally

    db_path = resolve_topic_db_path(topic)
        
    llm = LLMClient()
    if llm.provider == "none":
        raise RuntimeError("Error: Cannot write archival memory while running in AI-Free mode.")

    conn = get_connection(db_path)
    try:
        vector = llm.get_embedding(text)
        
        # Add source, chunk and embedding
        timestamp = datetime.now(timezone.utc).timestamp()
        checksum = "dynamic_" + hashlib.sha256(f"{text}{timestamp}".encode()).hexdigest()
        source_id = add_source(conn, f"Dynamic Agent Memory ({topic or 'default'})", author, "dynamic_memory", checksum)
        chunk_id = add_chunk(conn, source_id, 0, text, location="dynamic_memory_mcp")
        add_embedding(conn, chunk_id, vector)
        
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO memory_archival (chunk_id, created_at)
            VALUES (?, ?)
        """, (chunk_id, datetime.now(timezone.utc).isoformat()))
        conn.commit()
        
        # Incremental HNSW update
        update_usearch_index_incrementally(db_path, chunk_id, vector)
        
        return f"Successfully saved to archival memory (Chunk ID: {chunk_id})."
    except Exception as e:
        raise RuntimeError(f"Error writing archival memory: {e}")
    finally:
        conn.close()

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
                                    "required": False
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
                        resp["result"] = {
                            "description": "Ask a question or search for concepts across your Obsidian notes and books database.",
                            "messages": [
                                {
                                    "role": "user",
                                    "content": {
                                        "type": "text",
                                        "text": "Ask a question or search for concepts across your Obsidian notes and books database. (Try typing `/psyche query='your query'` or ask me directly!)"
                                    }
                                }
                            ]
                        }
                    else:
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
                        },
                        {
                            "name": "record_interaction",
                            "description": "Log a conversation message (user query, assistant response, or tool execution) into persistent session memory.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "session_id": {
                                        "type": "string",
                                        "description": "Unique session identifier for the conversation"
                                    },
                                    "role": {
                                        "type": "string",
                                        "description": "Role of the message author ('user', 'assistant', 'system', 'tool')"
                                    },
                                    "content": {
                                        "type": "string",
                                        "description": "Message content"
                                    },
                                    "tool_calls": {
                                        "type": "string",
                                        "description": "Optional JSON string representing tool calls executed"
                                    },
                                    "topic": {
                                        "type": "string",
                                        "description": "Optional topic/profile database name. If omitted, uses the default database."
                                    }
                                },
                                "required": ["session_id", "role", "content"]
                            }
                        },
                        {
                            "name": "write_memory_core",
                            "description": "Save or update a key-value fact or rule in the agent's core working memory (e.g. user preferences or project guidelines).",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "key": {
                                        "type": "string",
                                        "description": "The unique key identifying this memory/rule (e.g., 'naming_convention')"
                                    },
                                    "value": {
                                        "type": "string",
                                        "description": "The description/value of the fact or rule"
                                    },
                                    "category": {
                                        "type": "string",
                                        "description": "Optional category (e.g., 'user_preferences', 'project_guidelines', 'active_task')",
                                        "default": "general"
                                    },
                                    "topic": {
                                        "type": "string",
                                        "description": "Optional topic/profile database name. If omitted, uses the default database."
                                    }
                                },
                                "required": ["key", "value"]
                            }
                        },
                        {
                            "name": "append_memory_archival",
                            "description": "Vector-embed and write a new learning, fact, or debugging log to long-term RAG search memory.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "text": {
                                        "type": "string",
                                        "description": "The learning content or lesson to archive"
                                    },
                                    "topic": {
                                        "type": "string",
                                        "description": "Optional topic/profile database name. If omitted, uses the default database."
                                    },
                                    "author": {
                                        "type": "string",
                                        "description": "Optional author name",
                                        "default": "Assistant"
                                    }
                                },
                                "required": ["text"]
                            }
                        },
                        {
                            "name": "generate_guidance",
                            "description": "Generate a structured guidance brief from a goal or problem using knowledge retrieval and LLM context.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "goal_text": {
                                        "type": "string",
                                        "description": "The goal or problem to get guidance on (e.g. 'I want to save money')"
                                    },
                                    "domain": {
                                        "type": "string",
                                        "description": "Optional domain (e.g. 'wealth', 'health', 'business'). Auto-detected if omitted."
                                    },
                                    "topic": {
                                        "type": "string",
                                        "description": "Optional topic/profile database name."
                                    }
                                },
                                "required": ["goal_text"]
                            }
                        },
                        {
                            "name": "list_goals_and_experiments",
                            "description": "List active goals, experiments, and personal rules to understand current status.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "domain": {
                                        "type": "string",
                                        "description": "Optional domain to filter by (e.g. 'wealth', 'health')"
                                    },
                                    "topic": {
                                        "type": "string",
                                        "description": "Optional topic/profile database name."
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

                try:
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
                    elif tool_name == "record_interaction":
                        session_id = arguments.get("session_id")
                        role = arguments.get("role")
                        content = arguments.get("content")
                        tool_calls = arguments.get("tool_calls")
                        topic = arguments.get("topic")
                        text_result = record_interaction_tool(session_id, role, content, tool_calls, topic)
                        resp["result"] = {
                            "content": [
                                {
                                    "type": "text",
                                    "text": text_result
                                }
                            ]
                        }
                    elif tool_name == "write_memory_core":
                        key = arguments.get("key")
                        value = arguments.get("value")
                        category = arguments.get("category", "general")
                        topic = arguments.get("topic")
                        text_result = write_memory_core_tool(key, value, category, topic)
                        resp["result"] = {
                            "content": [
                                {
                                    "type": "text",
                                    "text": text_result
                                }
                            ]
                        }
                    elif tool_name == "append_memory_archival":
                        text = arguments.get("text")
                        topic = arguments.get("topic")
                        author = arguments.get("author", "Assistant")
                        text_result = append_memory_archival_tool(text, topic, author)
                        resp["result"] = {
                            "content": [
                                {
                                    "type": "text",
                                    "text": text_result
                                }
                            ]
                        }
                    elif tool_name == "generate_guidance":
                        goal_text = arguments.get("goal_text")
                        domain = arguments.get("domain")
                        topic = arguments.get("topic")
                        from guidance import generate_guidance_tool
                        text_result = generate_guidance_tool(goal_text, domain, topic)
                        resp["result"] = {
                            "content": [
                                {
                                    "type": "text",
                                    "text": text_result
                                }
                            ]
                        }
                    elif tool_name == "list_goals_and_experiments":
                        domain = arguments.get("domain")
                        topic = arguments.get("topic")
                        from guidance import list_goals_experiments_tool
                        text_result = list_goals_experiments_tool(domain, topic)
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
                except Exception as e:
                    log(f"Tool '{tool_name}' failed: {traceback.format_exc()}")
                    resp["result"] = {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True}
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
