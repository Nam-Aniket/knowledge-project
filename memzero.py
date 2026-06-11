"""Atomic memory layer (mem0-style) for Psyche.

Add-on module: stores small atomic facts with agent/run scope, indexes them in
a dedicated usearch index (<db>.mem.usearch) plus the atomic_memories_fts
table, and retrieves a few-KB slice via hybrid (vector + keyword + entity)
search. ADD-only writes with a cosine near-duplicate guard; conflicts resolve
at read time via recency and the superseded_by column. Base Psyche tables and
the chunks usearch index are untouched.
"""
import json
import os
import re
import sqlite3
from datetime import datetime, timezone

import numpy as np

from db import resolve_db_path, get_connection, init_db

# Skip storing a fact whose cosine similarity to an existing live fact exceeds this.
DUP_SIMILARITY = 0.95
# Semantic matches below this similarity are discarded — weak matches waste
# injected tokens. bge-small scores unrelated text ~0.4-0.5, related ~0.6+.
DEFAULT_MIN_SCORE = float(os.getenv("PSYCHE_MEM_MIN_SCORE", "0.55"))
VALID_CATEGORIES = {"preference", "decision", "fact", "lesson"}

EXTRACTION_SYSTEM = """You extract durable atomic memories from a conversation transcript.
Return a STRICT JSON array (no markdown fences) of objects:
  {"fact": "<one self-contained sentence>", "category": "preference|decision|fact|lesson", "entities": ["<entity>", ...]}
Rules:
- Only durable information useful in FUTURE sessions: user preferences, decisions made and why, lessons learned, stable project facts.
- Exclude anything derivable from the code or repository itself, one-off task details, transient state, secrets, API keys, and file contents.
- Each fact must stand alone without the conversation.
- At most 10 facts. Return [] if nothing durable was said."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def mem_index_path_for(db_path: str) -> str:
    """Derives the atomic-memory usearch index path for a database file."""
    return os.path.splitext(db_path)[0] + ".mem.usearch"


def _load_mem_index(db_path: str):
    try:
        from usearch.index import Index
    except ImportError:
        return None
    index_path = mem_index_path_for(db_path)
    if not os.path.exists(index_path):
        return None
    try:
        return Index.restore(index_path)
    except Exception:
        return None


def _add_to_mem_index(db_path: str, memory_id: int, vector: list[float]):
    try:
        from usearch.index import Index
    except ImportError:
        return
    index_path = mem_index_path_for(db_path)
    index = Index(ndim=len(vector), metric="cosine")
    if os.path.exists(index_path):
        try:
            index.load(index_path)
        except Exception:
            pass
    keys_arr = np.array([memory_id], dtype=np.int64)
    vectors_matrix = np.array([vector], dtype=np.float32).reshape(1, -1)
    try:
        if len(index) > 0 and memory_id in index:
            index.remove(keys_arr)
    except Exception:
        pass
    index.add(keys_arr, vectors_matrix)
    index.save(index_path)


def _remove_from_mem_index(db_path: str, memory_ids: list[int]):
    try:
        from usearch.index import Index
    except ImportError:
        return
    index_path = mem_index_path_for(db_path)
    if not os.path.exists(index_path):
        return
    try:
        index = Index.restore(index_path)
        if index is None or len(index) == 0:
            return
        present = [k for k in memory_ids if k in index]
        if not present:
            return
        for key in present:
            try:
                index.remove(np.int64(key))
            except Exception:
                pass
        # Mirror db.py: never persist an emptied index (usearch can segfault
        # reloading one); a fresh index is rebuilt lazily on the next add.
        if len(index) == 0:
            os.remove(index_path)
        else:
            index.save(index_path)
    except Exception:
        pass


def _get_llm(llm=None):
    if llm is not None:
        return llm
    from llm_client import LLMClient
    return LLMClient()


def _ensure_db(db_path: str = None) -> str:
    resolved = resolve_db_path(db_path)
    init_db(resolved)
    return resolved


def _embed(llm, text: str):
    if getattr(llm, "provider", "none") == "none":
        return None
    try:
        return llm.get_embedding(text)
    except Exception:
        return None


def _find_duplicate(db_path: str, vector) -> int | None:
    """Returns the id of an existing live fact nearly identical to vector, else None."""
    if vector is None:
        return None
    index = _load_mem_index(db_path)
    if index is None or len(index) == 0:
        return None
    try:
        matches = index.search(np.array(vector, dtype=np.float32), 1)
        if len(matches) == 0:
            return None
        key = int(matches[0].key)
        similarity = 1.0 - float(matches[0].distance)
        if similarity < DUP_SIMILARITY:
            return None
        conn = get_connection(db_path)
        try:
            row = conn.execute(
                "SELECT id FROM atomic_memories WHERE id = ? AND superseded_by IS NULL",
                (key,),
            ).fetchone()
            return row[0] if row else None
        finally:
            conn.close()
    except Exception:
        return None


def add_memory(fact: str, category: str = None, entities: list[str] = None,
               agent_id: str = None, run_id: str = None,
               db_path: str = None, llm=None) -> dict:
    """Stores a single atomic fact verbatim. Returns {id, fact, duplicate_of}."""
    fact = (fact or "").strip()
    if not fact:
        raise ValueError("fact must be a non-empty string")
    if category and category not in VALID_CATEGORIES:
        category = "fact"
    resolved = _ensure_db(db_path)
    llm = _get_llm(llm)
    vector = _embed(llm, fact)

    dup_id = _find_duplicate(resolved, vector)
    if dup_id is not None:
        return {"id": dup_id, "fact": fact, "duplicate_of": dup_id}

    now = _now()
    blob = np.array(vector, dtype=np.float32).tobytes() if vector is not None else None
    conn = get_connection(resolved)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO atomic_memories (fact, agent_id, run_id, category, embedding_blob, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (fact, agent_id, run_id, category, blob, now, now),
        )
        memory_id = cursor.lastrowid
        cursor.execute(
            "INSERT INTO atomic_memories_fts (memory_id, fact) VALUES (?, ?)",
            (memory_id, fact),
        )
        for entity in set(e.strip().lower() for e in (entities or []) if e and e.strip()):
            cursor.execute(
                "INSERT OR IGNORE INTO memory_entities (memory_id, entity) VALUES (?, ?)",
                (memory_id, entity),
            )
        conn.commit()
    finally:
        conn.close()
    if vector is not None:
        _add_to_mem_index(resolved, memory_id, vector)
    return {"id": memory_id, "fact": fact, "duplicate_of": None}


def extract_and_store(transcript_text: str, agent_id: str = None, run_id: str = None,
                      db_path: str = None, llm=None) -> list[dict]:
    """LLM-extracts atomic facts from a transcript and stores them.

    Returns the list of stored facts. Returns [] without storing when no chat
    model is configured (CHAT_MODEL=none) — verbatim transcript storage would
    only add noise.
    """
    llm = _get_llm(llm)
    if getattr(llm, "chat_model", "none") == "none":
        return []
    transcript_text = (transcript_text or "").strip()
    if len(transcript_text) < 200:
        return []
    try:
        raw = llm.generate_completion(EXTRACTION_SYSTEM, transcript_text[-12000:])
    except Exception:
        return []
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
    try:
        candidates = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    stored = []
    for item in candidates[:10] if isinstance(candidates, list) else []:
        if not isinstance(item, dict) or not item.get("fact"):
            continue
        result = add_memory(
            fact=str(item["fact"]),
            category=item.get("category"),
            entities=item.get("entities") if isinstance(item.get("entities"), list) else None,
            agent_id=agent_id,
            run_id=run_id,
            db_path=db_path,
            llm=llm,
        )
        if result["duplicate_of"] is None:
            stored.append(result)
    return stored


_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "you", "your", "please",
    "can", "could", "would", "should", "want", "need", "use", "using",
    "make", "help", "how", "what", "when", "where", "why", "are", "was",
    "have", "has", "not", "all", "any", "but", "get", "set", "out", "new",
}


def _fts_tokens(query: str) -> list[str]:
    return [t for t in re.findall(r"[A-Za-z0-9_]{3,}", query.lower())
            if t not in _STOPWORDS][:12]


def search_memories(query: str, agent_id: str = None, top: int = 8,
                    db_path: str = None, llm=None,
                    min_score: float = DEFAULT_MIN_SCORE) -> list[dict]:
    """Hybrid search over live atomic facts. Returns [] on weak matches so
    callers don't inject noise."""
    resolved = resolve_db_path(db_path)
    if not os.path.exists(resolved):
        return []
    query = (query or "").strip()
    if not query:
        return []

    # Semantic signal
    semantic = []  # list of (memory_id, similarity)
    llm = _get_llm(llm)
    vector = _embed(llm, query)
    if vector is not None:
        index = _load_mem_index(resolved)
        if index is not None and len(index) > 0:
            try:
                matches = index.search(np.array(vector, dtype=np.float32), min(top * 3, len(index)))
                semantic = [(int(m.key), 1.0 - float(m.distance)) for m in matches]
            except Exception:
                semantic = []

    conn = get_connection(resolved)
    try:
        try:
            conn.execute("SELECT 1 FROM atomic_memories LIMIT 1")
        except sqlite3.OperationalError:
            return []

        # Keyword signal
        keyword = []
        tokens = _fts_tokens(query)
        if tokens:
            match_expr = " OR ".join(f'"{t}"' for t in tokens)
            try:
                rows = conn.execute(
                    "SELECT memory_id FROM atomic_memories_fts WHERE atomic_memories_fts MATCH ? "
                    "ORDER BY rank LIMIT ?",
                    (match_expr, top * 3),
                ).fetchall()
                keyword = [int(r[0]) for r in rows]
            except sqlite3.OperationalError:
                keyword = []

        # Entity signal
        entity_hits = []
        if tokens:
            placeholders = ",".join("?" for _ in tokens)
            rows = conn.execute(
                f"SELECT DISTINCT memory_id FROM memory_entities WHERE entity IN ({placeholders})",
                tokens,
            ).fetchall()
            entity_hits = [int(r[0]) for r in rows]

        # Gate: drop weak semantic matches entirely; bail if no signal remains.
        semantic = [(mid, sim) for mid, sim in semantic if sim >= min_score]
        if not semantic and not keyword and not entity_hits:
            return []

        # Reciprocal rank fusion across the three signals.
        scores = {}
        for rank, (mid, _sim) in enumerate(semantic):
            scores[mid] = scores.get(mid, 0.0) + 1.0 / (60 + rank + 1)
        for rank, mid in enumerate(keyword):
            scores[mid] = scores.get(mid, 0.0) + 1.0 / (60 + rank + 1)
        for rank, mid in enumerate(entity_hits):
            scores[mid] = scores.get(mid, 0.0) + 0.5 / (60 + rank + 1)
        if not scores:
            return []
        ranked_ids = [mid for mid, _ in sorted(scores.items(), key=lambda kv: -kv[1])]

        placeholders = ",".join("?" for _ in ranked_ids)
        sql = (
            f"SELECT id, fact, category, agent_id, updated_at FROM atomic_memories "
            f"WHERE id IN ({placeholders}) AND superseded_by IS NULL"
        )
        params = list(ranked_ids)
        if agent_id:
            sql += " AND agent_id = ?"
            params.append(agent_id)
        rows = {r[0]: r for r in conn.execute(sql, params).fetchall()}
    finally:
        conn.close()

    sim_by_id = dict(semantic)
    results = []
    for mid in ranked_ids:
        if mid not in rows:
            continue
        _id, fact, category, agent, updated_at = rows[mid]
        results.append({
            "id": _id, "fact": fact, "category": category,
            "agent_id": agent, "updated_at": updated_at,
            "similarity": round(sim_by_id.get(mid, 0.0), 3),
        })
        if len(results) >= top:
            break
    return results


def format_facts(results: list[dict], max_chars: int = 3000) -> str:
    """Formats facts as compact bullets for context injection."""
    lines = []
    total = 0
    for r in results:
        date = (r.get("updated_at") or "")[:10]
        suffix = f" ({r['category']}, {date})" if r.get("category") else (f" ({date})" if date else "")
        line = f"- {r['fact']}{suffix}"
        if total + len(line) > max_chars:
            break
        lines.append(line)
        total += len(line) + 1
    return "\n".join(lines)


def get_memory(memory_id: int, db_path: str = None) -> dict | None:
    resolved = resolve_db_path(db_path)
    conn = get_connection(resolved)
    try:
        row = conn.execute(
            "SELECT id, fact, category, agent_id, run_id, created_at, updated_at, superseded_by "
            "FROM atomic_memories WHERE id = ?", (memory_id,)
        ).fetchone()
        if not row:
            return None
        entities = [r[0] for r in conn.execute(
            "SELECT entity FROM memory_entities WHERE memory_id = ?", (memory_id,)
        ).fetchall()]
    finally:
        conn.close()
    return {"id": row[0], "fact": row[1], "category": row[2], "agent_id": row[3],
            "run_id": row[4], "created_at": row[5], "updated_at": row[6],
            "superseded_by": row[7], "entities": entities}


def update_memory(memory_id: int, fact: str, db_path: str = None, llm=None) -> bool:
    fact = (fact or "").strip()
    if not fact:
        raise ValueError("fact must be a non-empty string")
    resolved = resolve_db_path(db_path)
    llm = _get_llm(llm)
    vector = _embed(llm, fact)
    blob = np.array(vector, dtype=np.float32).tobytes() if vector is not None else None
    conn = get_connection(resolved)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE atomic_memories SET fact = ?, embedding_blob = ?, updated_at = ? WHERE id = ?",
            (fact, blob, _now(), memory_id),
        )
        if cursor.rowcount == 0:
            return False
        cursor.execute("DELETE FROM atomic_memories_fts WHERE memory_id = ?", (memory_id,))
        cursor.execute("INSERT INTO atomic_memories_fts (memory_id, fact) VALUES (?, ?)", (memory_id, fact))
        conn.commit()
    finally:
        conn.close()
    if vector is not None:
        _add_to_mem_index(resolved, memory_id, vector)
    return True


def delete_memory(memory_id: int, db_path: str = None) -> bool:
    resolved = resolve_db_path(db_path)
    conn = get_connection(resolved)
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM atomic_memories WHERE id = ?", (memory_id,))
        deleted = cursor.rowcount > 0
        cursor.execute("DELETE FROM atomic_memories_fts WHERE memory_id = ?", (memory_id,))
        conn.commit()
    finally:
        conn.close()
    if deleted:
        _remove_from_mem_index(resolved, [memory_id])
    return deleted


def list_entities(db_path: str = None) -> list[dict]:
    resolved = resolve_db_path(db_path)
    if not os.path.exists(resolved):
        return []
    conn = get_connection(resolved)
    try:
        try:
            rows = conn.execute(
                "SELECT e.entity, COUNT(*) FROM memory_entities e "
                "JOIN atomic_memories m ON m.id = e.memory_id AND m.superseded_by IS NULL "
                "GROUP BY e.entity ORDER BY COUNT(*) DESC, e.entity"
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    finally:
        conn.close()
    return [{"entity": r[0], "count": r[1]} for r in rows]


def standing_fact_rows(top: int = 12, db_path: str = None) -> list[dict]:
    """Recent durable preference/decision/lesson facts for session-start injection."""
    resolved = resolve_db_path(db_path)
    if not os.path.exists(resolved):
        return []
    conn = get_connection(resolved)
    try:
        try:
            rows = conn.execute(
                "SELECT id, fact, category, agent_id, updated_at FROM atomic_memories "
                "WHERE superseded_by IS NULL AND category IN ('preference','decision','lesson') "
                "ORDER BY updated_at DESC LIMIT ?", (top,)
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    finally:
        conn.close()
    return [{"id": r[0], "fact": r[1], "category": r[2], "agent_id": r[3], "updated_at": r[4]}
            for r in rows]


def standing_facts(top: int = 12, db_path: str = None, max_chars: int = 1500) -> str:
    return format_facts(standing_fact_rows(top, db_path), max_chars=max_chars)
