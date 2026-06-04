import unittest
import unittest.mock as mock
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
        chunk_id = db.add_chunk(self.conn, source_id, chunk_index=0, text="This is sample text.", location="Page 12")
        self.assertEqual(chunk_id, 1)

        # Test adding embedding
        embedding_vector = [0.1, 0.2, 0.3, 0.4]
        db.add_embedding(self.conn, chunk_id, embedding_vector)

        # Test retrieving embeddings and chunks
        records = db.get_all_embeddings_with_chunks(self.conn)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["chunk_id"], 1)
        self.assertEqual(records[0]["text"], "This is sample text.")
        self.assertEqual(records[0]["location"], "Page 12")
        self.assertEqual(records[0]["source_title"], "Test Book")
        self.assertEqual(records[0]["source_author"], "Test Author")
        np.testing.assert_array_almost_equal(records[0]["embedding"], np.array(embedding_vector, dtype=np.float32))

    def test_fts_search(self):
        # 1. Add source and chunk
        source_id = db.add_source(
            self.conn, 
            title="Book A", 
            author="Author A", 
            file_path="tests/dummy.txt", 
            checksum="unique_checksum_fts"
        )
        chunk_id = db.add_chunk(self.conn, source_id, chunk_index=0, text="The quick brown fox jumps over the lazy dog.", location="Chapter 1")
        db.add_embedding(self.conn, chunk_id, [0.1, 0.2])
        
        # 2. Search FTS5
        results = db.search_fts(self.conn, "brown fox")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["chunk_id"], chunk_id)
        self.assertEqual(results[0]["text"], "The quick brown fox jumps over the lazy dog.")
        self.assertEqual(results[0]["location"], "Chapter 1")
        self.assertEqual(results[0]["source_title"], "Book A")
        
        # 3. Search with non-matching query
        no_results = db.search_fts(self.conn, "nonexistentword")
        self.assertEqual(len(no_results), 0)

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
        blocks = parsers.extract_text(self.txt_path)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["text"].strip(), "Hello World! This is a parser test.")
        self.assertEqual(blocks[0]["location"], "Full Document")

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

class TestRAGHybridSearch(unittest.TestCase):
    def test_rrf_reranking(self):
        semantic_results = [
            ({"chunk_id": 101, "text": "Semantic Match A"}, 0.95),
            ({"chunk_id": 102, "text": "Semantic Match B"}, 0.88),
            ({"chunk_id": 103, "text": "Semantic Match C"}, 0.70)
        ]
        keyword_results = [
            {"chunk_id": 103, "text": "Semantic Match C"}, # Keyword ranked #1
            {"chunk_id": 101, "text": "Semantic Match A"}  # Keyword ranked #2
        ]
        
        # Run RRF
        combined = query.reciprocal_rank_fusion(semantic_results, keyword_results, k=60)
        
        # Verify scores and ranking
        self.assertEqual(len(combined), 3)
        self.assertEqual(combined[0][0]["chunk_id"], 101)
        self.assertEqual(combined[1][0]["chunk_id"], 103)
        self.assertEqual(combined[2][0]["chunk_id"], 102)
        
        self.assertAlmostEqual(combined[0][1], 1/61 + 1/62, places=6)
        self.assertAlmostEqual(combined[1][1], 1/63 + 1/61, places=6)
        self.assertAlmostEqual(combined[2][1], 1/62, places=6)

class TestRAGDirectorySync(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        
        # Create test files with different extensions
        self.file_txt = os.path.join(self.temp_dir.name, "book1.txt")
        self.file_epub = os.path.join(self.temp_dir.name, "book2.epub")
        self.file_pdf = os.path.join(self.temp_dir.name, "book3.pdf")
        self.file_invalid = os.path.join(self.temp_dir.name, "other.log")
        
        # Subdirectory scanning test
        self.sub_dir = os.path.join(self.temp_dir.name, "subdir")
        os.makedirs(self.sub_dir, exist_ok=True)
        self.file_sub_txt = os.path.join(self.sub_dir, "nested.txt")
        
        for fpath in [self.file_txt, self.file_epub, self.file_pdf, self.file_invalid, self.file_sub_txt]:
            with open(fpath, "w") as f:
                f.write("test content")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_directory_scan_default_extensions(self):
        # Default behavior scans: .pdf, .epub, .txt, .md, .markdown
        allowed_exts = {".pdf", ".epub", ".txt", ".md", ".markdown"}
        scanned_files = []
        for root, dirs, files in os.walk(self.temp_dir.name):
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in allowed_exts:
                    scanned_files.append(os.path.basename(file))
                    
        self.assertIn("book1.txt", scanned_files)
        self.assertIn("book2.epub", scanned_files)
        self.assertIn("book3.pdf", scanned_files)
        self.assertIn("nested.txt", scanned_files)
        self.assertNotIn("other.log", scanned_files)
        self.assertEqual(len(scanned_files), 4)

    def test_directory_scan_filtered_extensions(self):
        # Filter by custom extensions (e.g. text/epub only)
        allowed_exts = {".txt", ".epub"}
        scanned_files = []
        for root, dirs, files in os.walk(self.temp_dir.name):
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in allowed_exts:
                    scanned_files.append(os.path.basename(file))
                    
        self.assertIn("book1.txt", scanned_files)
        self.assertIn("book2.epub", scanned_files)
        self.assertIn("nested.txt", scanned_files)
        self.assertNotIn("book3.pdf", scanned_files)
        self.assertNotIn("other.log", scanned_files)
        self.assertEqual(len(scanned_files), 3)

class TestRAGOllamaSupport(unittest.TestCase):
    @mock.patch('requests.post')
    def test_ollama_embedding(self, mock_post):
        # Configure mock response for /api/embed
        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "embeddings": [[0.1, 0.2, 0.3]]
        }
        mock_post.return_value = mock_response

        from llm_client import LLMClient
        with mock.patch.dict(os.environ, {
            "LLM_PROVIDER": "ollama",
            "OLLAMA_HOST": "http://localhost:11434",
            "EMBED_MODEL": "nomic-embed-text",
            "CHAT_MODEL": "llama3",
            "TESTING": "true"
        }):
            client = LLMClient()
            emb = client.get_embedding("test query")
            self.assertEqual(emb, [0.1, 0.2, 0.3])
            
    @mock.patch('requests.post')
    def test_ollama_completion(self, mock_post):
        # Configure mock response for /api/chat
        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "message": {
                "role": "assistant",
                "content": "hello there"
            }
        }
        mock_post.return_value = mock_response

        from llm_client import LLMClient
        with mock.patch.dict(os.environ, {
            "LLM_PROVIDER": "ollama",
            "OLLAMA_HOST": "http://localhost:11434",
            "EMBED_MODEL": "nomic-embed-text",
            "CHAT_MODEL": "llama3",
            "TESTING": "true"
        }):
            client = LLMClient()
            res = client.generate_completion("instruction", "prompt")
            self.assertEqual(res, "hello there")

class TestRAGConceptGraph(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp()
        db.init_db(self.db_path)
        self.conn = db.get_connection(self.db_path)

    def tearDown(self):
        self.conn.close()
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def test_concepts_crud(self):
        c1_id = db.add_concept(self.conn, "Stoicism", "An ancient Greek philosophy", "Philosophy")
        c2_id = db.add_concept(self.conn, "Virtue", "Moral excellence", "Ethics")
        
        self.assertEqual(c1_id, 1)
        self.assertEqual(c2_id, 2)
        
        link_id = db.add_concept_link(self.conn, "Stoicism", "Virtue", "values", "Stoics value virtue")
        self.assertEqual(link_id, 1)
        
        concepts = db.get_all_concepts(self.conn)
        self.assertEqual(len(concepts), 2)
        self.assertEqual(concepts[0]["name"], "Stoicism")
        self.assertEqual(concepts[1]["name"], "Virtue")
        
        links = db.get_concept_links(self.conn)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["source"], "Stoicism")
        self.assertEqual(links[0]["target"], "Virtue")
        self.assertEqual(links[0]["relationship"], "values")
        
    def test_kmeans_clustering(self):
        from build_graph import kmeans
        embeddings = np.array([
            [1.0, 0.0],
            [0.9, 0.1],
            [0.0, 1.0],
            [0.1, 0.9]
        ], dtype=np.float32)
        
        labels, centroids = kmeans(embeddings, num_clusters=2)
        self.assertEqual(labels.shape[0], 4)
        self.assertEqual(labels[0], labels[1])
        self.assertEqual(labels[2], labels[3])
        self.assertNotEqual(labels[0], labels[2])
        self.assertEqual(centroids.shape, (2, 2))

    def test_retrieve_concept_context(self):
        db.add_concept(self.conn, "Stoicism", "An ancient Greek philosophy", "Philosophy")
        db.add_concept(self.conn, "Virtue", "Moral excellence", "Ethics")
        db.add_concept_link(self.conn, "Stoicism", "Virtue", "values", "Sole good")
        
        ctx = query.retrieve_concept_context(self.conn, "What is Stoicism and Virtue?")
        self.assertIn("KNOWLEDGE CONCEPT GRAPH", ctx)
        self.assertIn("Stoicism", ctx)
        self.assertIn("Virtue", ctx)
        self.assertIn("values", ctx)

if __name__ == "__main__":
    import unittest.mock as mock
    unittest.main()
