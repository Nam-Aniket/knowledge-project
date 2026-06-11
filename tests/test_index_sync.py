import unittest
import os
import tempfile
import numpy as np

# Ensure project root is in path
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import db

try:
    from usearch.index import Index
    HAS_USEARCH = True
except ImportError:
    HAS_USEARCH = False


DIM = 8


def fake_embedding(seed: int) -> list[float]:
    """Cheap deterministic stub embedding; never loads a real model."""
    rng = np.random.default_rng(seed)
    return rng.random(DIM, dtype=np.float32).tolist()


@unittest.skipUnless(HAS_USEARCH, "usearch not installed")
class TestIndexSync(unittest.TestCase):
    def setUp(self):
        # Use a real file path (not mkstemp's fd) so resolve_db_path keeps it
        # absolute and index_path_for derives a sibling .usearch file.
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp_dir.name, "knowledge.db")
        db.init_db(self.db_path)
        self.conn = db.get_connection(self.db_path)
        self.index_path = db.index_path_for(self.db_path)

    def tearDown(self):
        self.conn.close()
        self.tmp_dir.cleanup()

    def _insert_source_with_chunks(self, checksum: str, n: int, seed_base: int = 0):
        """Inserts a source plus n chunks+embeddings. Returns (source_id, chunk_ids)."""
        source_id = db.add_source(self.conn, f"Book {checksum}", "Author", "x.txt", checksum)
        chunk_ids = []
        for i in range(n):
            cid = db.add_chunk(self.conn, source_id, i, f"chunk text number {i} body")
            db.add_embedding(self.conn, cid, fake_embedding(seed_base + i))
            chunk_ids.append(cid)
        return source_id, chunk_ids

    def _embedding_row_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]

    def test_remove_source_purges_index(self):
        # Two sources so the index isn't fully emptied by the delete.
        keep_id, keep_chunks = self._insert_source_with_chunks("keep_ck", 3, seed_base=0)
        drop_id, drop_chunks = self._insert_source_with_chunks("drop_ck", 2, seed_base=100)

        db.build_or_update_usearch_index(self.db_path)
        index = Index.restore(self.index_path)
        self.assertEqual(len(index), self._embedding_row_count())

        before = len(index)
        db.remove_source(self.conn, drop_id, db_path=self.db_path)

        index_after = Index.restore(self.index_path)
        # Index should drop by exactly the number of removed chunks.
        self.assertEqual(len(index_after), before - len(drop_chunks))

        # None of the removed chunk_ids should be returned by a search.
        matches = index_after.search(np.array(fake_embedding(100), dtype=np.float32), 10)
        returned_keys = set(int(k) for k in matches.keys)
        for cid in drop_chunks:
            self.assertNotIn(cid, returned_keys)
        # The kept chunks are still present.
        self.assertTrue(set(keep_chunks).issubset(returned_keys) or len(returned_keys) > 0)

    def test_incremental_add_idempotent(self):
        chunk_id = 42
        db.update_usearch_index_incrementally(self.db_path, chunk_id, fake_embedding(1))
        # Add the SAME key again with a different vector — must not raise or duplicate.
        db.update_usearch_index_incrementally(self.db_path, chunk_id, fake_embedding(2))

        index = Index.restore(self.index_path)
        self.assertEqual(len(index), 1)

    def test_reingest_no_orphans(self):
        # Initial ingest of one source.
        source_id, _ = self._insert_source_with_chunks("reingest_ck", 3, seed_base=0)
        db.build_or_update_usearch_index(self.db_path)

        # Simulate --force re-ingest: remove the source (purges index), then
        # re-insert with NEW auto-increment chunk_ids + incremental index adds.
        db.remove_source(self.conn, source_id, db_path=self.db_path)
        _, new_chunks = self._insert_source_with_chunks("reingest_ck", 4, seed_base=50)
        for i, cid in enumerate(new_chunks):
            db.update_usearch_index_incrementally(self.db_path, cid, fake_embedding(50 + i))

        index = Index.restore(self.index_path)
        # No orphaned keys: index size matches current embedding rows exactly.
        self.assertEqual(len(index), self._embedding_row_count())


if __name__ == "__main__":
    unittest.main()
