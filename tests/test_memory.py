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

if __name__ == "__main__":
    unittest.main()
