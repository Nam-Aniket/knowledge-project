import unittest
import os
import sqlite3
import numpy as np
from datetime import datetime, timezone
import db
import mcp_server
import synthesis

class TestMemoryEngine(unittest.TestCase):
    def setUp(self):
        self.db_path = "test_memory.db"
        self.resolved_db_path = db.resolve_db_path(self.db_path)
        # Ensure clean slate
        if os.path.exists(self.resolved_db_path):
            os.remove(self.resolved_db_path)
        index_path = self.resolved_db_path.replace(".db", "") + ".usearch"
        if os.path.exists(index_path):
            os.remove(index_path)
            
        # Initialize DB
        db.init_db(self.db_path)

    def tearDown(self):
        if os.path.exists(self.resolved_db_path):
            os.remove(self.resolved_db_path)
        index_path = self.resolved_db_path.replace(".db", "") + ".usearch"
        if os.path.exists(index_path):
            os.remove(index_path)

    def test_db_initialization(self):
        """Verify that init_db creates all required memory tables."""
        conn = db.get_connection(self.resolved_db_path)
        try:
            cursor = conn.cursor()
            # Check memory_core table
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='memory_core'")
            self.assertIsNotNone(cursor.fetchone())
            
            # Check memory_recall table
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='memory_recall'")
            self.assertIsNotNone(cursor.fetchone())
            
            # Check memory_archival table
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='memory_archival'")
            self.assertIsNotNone(cursor.fetchone())
        finally:
            conn.close()

    def test_write_memory_core(self):
        """Test write_memory_core_tool upserts key-value facts."""
        os.environ["DATABASE_PATH"] = self.db_path
        
        # Insert new fact
        res1 = mcp_server.write_memory_core_tool(
            key="user_style",
            value="prefers clean, modular Python code",
            category="preferences"
        )
        self.assertIn("Core memory updated successfully", res1)
        
        # Verify value
        conn = db.get_connection(self.resolved_db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT value, category FROM memory_core WHERE key = 'user_style'")
            row = cursor.fetchone()
            self.assertEqual(row[0], "prefers clean, modular Python code")
            self.assertEqual(row[1], "preferences")
        finally:
            conn.close()
            
        # Update (Conflict check)
        res2 = mcp_server.write_memory_core_tool(
            key="user_style",
            value="prefers type-annotated, modern Python code",
            category="preferences"
        )
        self.assertIn("Core memory updated successfully", res2)
        
        # Verify updated value
        conn = db.get_connection(self.resolved_db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM memory_core WHERE key = 'user_style'")
            row = cursor.fetchone()
            self.assertEqual(row[0], "prefers type-annotated, modern Python code")
        finally:
            conn.close()

    def test_record_interaction(self):
        """Test record_interaction_tool appends logs to memory_recall."""
        os.environ["DATABASE_PATH"] = self.db_path
        
        res = mcp_server.record_interaction_tool(
            session_id="session_123",
            role="assistant",
            content="Added a new mcp server function.",
            tool_calls='[{"name": "write_memory_core"}]'
        )
        self.assertIn("Successfully recorded", res)
        
        conn = db.get_connection(self.resolved_db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT session_id, role, content, tool_calls FROM memory_recall")
            row = cursor.fetchone()
            self.assertEqual(row[0], "session_123")
            self.assertEqual(row[1], "assistant")
            self.assertEqual(row[2], "Added a new mcp server function.")
            self.assertEqual(row[3], '[{"name": "write_memory_core"}]')
        finally:
            conn.close()

    def test_incremental_usearch_update(self):
        """Test update_usearch_index_incrementally adds vectors dynamically."""
        vector = [0.1, 0.2, 0.3]
        chunk_id = 42
        
        db.update_usearch_index_incrementally(self.db_path, chunk_id, vector)
        
        index_path = self.resolved_db_path.replace(".db", "") + ".usearch"
        self.assertTrue(os.path.exists(index_path))
        
        # Test loading and searching
        from usearch.index import Index
        index = Index(ndim=3, metric="cosine")
        index.load(index_path)
        self.assertEqual(len(index), 1)
        
        # Test search match
        q = np.array([0.1, 0.2, 0.3], dtype=np.float32)
        matches = index.search(q, 1)
        self.assertEqual(matches.keys[0], chunk_id)

class FakeEmbedLLM:
    """Deterministic embedding stub: exact-text vector mapping, no chat."""
    provider = "fake"
    chat_model = "none"

    def __init__(self, mapping):
        self.mapping = mapping

    def get_embedding(self, text):
        return self.mapping[text]


class TestAtomicMemoryScoping(unittest.TestCase):
    GLOBAL_FACT = "Deploys always run from the main branch"
    PROJECT_FACT = "The alpha repo uses pnpm for package management"
    QUERY = "how should I deploy and which package manager"

    def setUp(self):
        self.db_path = "test_memory_scoping.db"
        self.resolved_db_path = db.resolve_db_path(self.db_path)
        self.mem_index_path = os.path.splitext(self.resolved_db_path)[0] + ".mem.usearch"
        for p in (self.resolved_db_path, self.mem_index_path):
            if os.path.exists(p):
                os.remove(p)
        db.init_db(self.db_path)
        # Orthogonal fact vectors (no dup/supersede interference); the query
        # vector sits at ~0.707 similarity to both — above the 0.55 floor.
        self.llm = FakeEmbedLLM({
            self.GLOBAL_FACT: [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            self.PROJECT_FACT: [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            self.QUERY: [0.707, 0.707, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        })

    def tearDown(self):
        for p in (self.resolved_db_path, self.mem_index_path):
            if os.path.exists(p):
                os.remove(p)

    def _seed(self):
        import memzero
        memzero.add_memory(self.GLOBAL_FACT, db_path=self.db_path, llm=self.llm)
        memzero.add_memory(self.PROJECT_FACT, project="alpha", db_path=self.db_path, llm=self.llm)

    def test_project_fact_scoping(self):
        import memzero
        self._seed()
        alpha = memzero.search_memories(self.QUERY, project="alpha", db_path=self.db_path, llm=self.llm)
        self.assertEqual({r["fact"] for r in alpha}, {self.GLOBAL_FACT, self.PROJECT_FACT})
        beta = memzero.search_memories(self.QUERY, project="beta", db_path=self.db_path, llm=self.llm)
        self.assertEqual({r["fact"] for r in beta}, {self.GLOBAL_FACT})

    def test_project_boost_orders_first(self):
        import memzero
        self._seed()
        alpha = memzero.search_memories(self.QUERY, project="alpha", db_path=self.db_path, llm=self.llm)
        self.assertEqual(alpha[0]["fact"], self.PROJECT_FACT)

    def test_retrieval_count_increments(self):
        import memzero
        self._seed()
        results = memzero.search_memories(self.QUERY, db_path=self.db_path, llm=self.llm)
        self.assertTrue(results)
        conn = db.get_connection(self.resolved_db_path)
        try:
            count = conn.execute(
                "SELECT retrieval_count FROM atomic_memories WHERE id = ?", (results[0]["id"],)
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertGreaterEqual(count, 1)

    def test_project_key_for_basename(self):
        import memzero
        self.assertEqual(memzero.project_key_for("/tmp/some/dir"), "dir")
        self.assertIsNone(memzero.project_key_for(None))


if __name__ == "__main__":
    unittest.main()
