import unittest
import os
import sqlite3
import tempfile
import numpy as np

# Ensure project root is in path
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import db
import parsers
import ingest
import query

class TestRAGDatabase(unittest.TestCase):
    def setUp(self):
        # Create a temp file for database
        self.db_fd, self.db_path = tempfile.mkstemp()
        db.init_db(self.db_path)
        self.conn = db.get_connection(self.db_path)

    def tearDown(self):
        self.conn.close()
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def test_database_schema_and_inserts(self):
        # Test adding a source
        source_id = db.add_source(
            self.conn, 
            title="Test Book", 
            author="Test Author", 
            file_path="tests/dummy.txt", 
            checksum="abc123checksum"
        )
        self.assertEqual(source_id, 1)

        # Test duplicate checksum check
        existing_id = db.check_checksum(self.conn, "abc123checksum")
        self.assertEqual(existing_id, 1)
        
        non_existing = db.check_checksum(self.conn, "wrongchecksum")
        self.assertIsNone(non_existing)

        # Test adding chunk
        chunk_id = db.add_chunk(self.conn, source_id, chunk_index=0, text="This is sample text.")
        self.assertEqual(chunk_id, 1)

        # Test adding embedding
        embedding_vector = [0.1, 0.2, 0.3, 0.4]
        db.add_embedding(self.conn, chunk_id, embedding_vector)

        # Test retrieving embeddings and chunks
        records = db.get_all_embeddings_with_chunks(self.conn)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["chunk_id"], 1)
        self.assertEqual(records[0]["text"], "This is sample text.")
        self.assertEqual(records[0]["source_title"], "Test Book")
        self.assertEqual(records[0]["source_author"], "Test Author")
        np.testing.assert_array_almost_equal(records[0]["embedding"], np.array(embedding_vector, dtype=np.float32))

class TestRAGParsers(unittest.TestCase):
    def setUp(self):
        # Create a temporary txt file
        self.txt_fd, self.txt_path = tempfile.mkstemp(suffix=".txt")
        with open(self.txt_path, "w", encoding="utf-8") as f:
            f.write("Hello World! This is a parser test.")

    def tearDown(self):
        os.close(self.txt_fd)
        os.unlink(self.txt_path)

    def test_txt_parser(self):
        text = parsers.extract_text(self.txt_path)
        self.assertEqual(text.strip(), "Hello World! This is a parser test.")

    def test_unsupported_parser(self):
        # Create an existing file with unsupported extension
        fd, path = tempfile.mkstemp(suffix=".invalidext")
        try:
            with self.assertRaises(ValueError):
                parsers.extract_text(path)
        finally:
            os.close(fd)
            os.unlink(path)

class TestRAGTextChunking(unittest.TestCase):
    def test_chunk_text(self):
        # Provide a longer text to make sure the chunks generated are longer than 50 characters
        text = (
            "This is a long sentence to make sure we hit the minimum chunk character requirement. "
            "It will be chunked into multiple pieces, each of which should be longer than 50 characters "
            "so it won't get filtered out. Here is another long sentence to keep the text length long enough "
            "for the test to pass successfully without filtering out any chunks."
        )
        chunks = ingest.chunk_text(text, chunk_size=80, overlap=20)
        
        # Verify that we created some chunks and they overlap/contain words
        self.assertTrue(len(chunks) > 0)
        for c in chunks:
            self.assertTrue(len(c) > 50)

class TestRAGCosineSimilarity(unittest.TestCase):
    def test_calculate_similarities(self):
        query_vector = [1.0, 0.0]
        records = [
            {"chunk_id": 1, "text": "Match 1", "embedding": np.array([1.0, 0.0], dtype=np.float32)},
            {"chunk_id": 2, "text": "Match 2", "embedding": np.array([0.0, 1.0], dtype=np.float32)},
            {"chunk_id": 3, "text": "Match 3", "embedding": np.array([0.707, 0.707], dtype=np.float32)}
        ]
        
        similarities = query.calculate_similarities(query_vector, records)
        
        # Match 1 should be highest (score = 1.0)
        self.assertEqual(similarities[0][0]["chunk_id"], 1)
        self.assertAlmostEqual(similarities[0][1], 1.0, places=4)
        
        # Match 3 should be middle (score = 0.707)
        self.assertEqual(similarities[1][0]["chunk_id"], 3)
        self.assertAlmostEqual(similarities[1][1], 0.707, places=3)
        
        # Match 2 should be lowest (score = 0.0)
        self.assertEqual(similarities[2][0]["chunk_id"], 2)
        self.assertAlmostEqual(similarities[2][1], 0.0, places=4)

if __name__ == "__main__":
    unittest.main()
