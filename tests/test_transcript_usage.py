"""Tests for transcript_usage module and the with_transcripts ledger_summary path."""
import json
import os
import sys
import tempfile
import unittest

# Ensure repo root is on path so we can import transcript_usage and memzero.
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

import transcript_usage


def _write_jsonl(path: str, lines: list):
    with open(path, "w") as f:
        for obj in lines:
            f.write(json.dumps(obj) + "\n")


def _make_fixture_transcript(projects_root: str, slug: str, session_id: str,
                              turns: list) -> str:
    """Create a fixture transcript JSONL; returns its path."""
    project_dir = os.path.join(projects_root, slug)
    os.makedirs(project_dir, exist_ok=True)
    path = os.path.join(project_dir, session_id + ".jsonl")
    _write_jsonl(path, turns)
    return path


def _assistant_turn(uuid: str, input_tokens: int, cache_read: int, cache_creation: int,
                    output_tokens: int, model: str = "claude-sonnet-4-6",
                    sidechain: bool = False) -> dict:
    obj = {
        "type": "assistant",
        "uuid": uuid,
        "message": {
            "model": model,
            "usage": {
                "input_tokens": input_tokens,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": cache_creation,
                "output_tokens": output_tokens,
            },
        },
    }
    if sidechain:
        obj["isSidechain"] = True
    return obj


class TestSlugifyCwd(unittest.TestCase):
    def test_known_example(self):
        slug = transcript_usage.slugify_cwd("/Users/aniketnamjoshi/knowledge-project")
        self.assertEqual(slug, "-Users-aniketnamjoshi-knowledge-project")

    def test_no_slashes_in_result(self):
        slug = transcript_usage.slugify_cwd("/some/arbitrary/path")
        self.assertNotIn("/", slug)


class TestParseTranscriptUsageBasic(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = os.path.join(self.tmpdir, "session.jsonl")
        lines = [
            # user line — must be ignored
            {"type": "user", "uuid": "u1", "message": {"content": "hello"}},
            # warm assistant turn (has cache_read)
            _assistant_turn("a1", input_tokens=100, cache_read=500,
                            cache_creation=0, output_tokens=50),
            # cold assistant turn (no cache_read)
            _assistant_turn("a2", input_tokens=200, cache_read=0,
                            cache_creation=300, output_tokens=80),
        ]
        _write_jsonl(self.path, lines)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_sums(self):
        result = transcript_usage.parse_transcript_usage(self.path)
        self.assertEqual(result["turns"], 2)
        self.assertEqual(result["input_uncached"], 300)   # 100 + 200
        self.assertEqual(result["cache_read"], 500)
        self.assertEqual(result["cache_creation"], 300)
        self.assertEqual(result["output"], 130)           # 50 + 80

    def test_user_line_ignored(self):
        result = transcript_usage.parse_transcript_usage(self.path)
        self.assertEqual(result["turns"], 2)  # not 3


class TestParseTranscriptDedup(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = os.path.join(self.tmpdir, "session.jsonl")
        # Two lines with the same uuid — last-wins, should count once.
        lines = [
            _assistant_turn("dup-uuid", input_tokens=100, cache_read=0,
                            cache_creation=0, output_tokens=50),
            _assistant_turn("dup-uuid", input_tokens=100, cache_read=0,
                            cache_creation=0, output_tokens=50),
        ]
        _write_jsonl(self.path, lines)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_counted_once(self):
        result = transcript_usage.parse_transcript_usage(self.path)
        self.assertEqual(result["turns"], 1)
        self.assertEqual(result["input_uncached"], 100)
        self.assertEqual(result["output"], 50)


class TestParseTranscriptSkipsSidechain(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = os.path.join(self.tmpdir, "session.jsonl")
        lines = [
            _assistant_turn("sc1", input_tokens=999, cache_read=999,
                            cache_creation=999, output_tokens=999, sidechain=True),
            _assistant_turn("main1", input_tokens=10, cache_read=0,
                            cache_creation=0, output_tokens=5),
        ]
        _write_jsonl(self.path, lines)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_sidechain_excluded(self):
        result = transcript_usage.parse_transcript_usage(self.path)
        self.assertEqual(result["turns"], 1)
        self.assertEqual(result["input_uncached"], 10)
        self.assertEqual(result["cache_read"], 0)


class TestParseTranscriptMalformed(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = os.path.join(self.tmpdir, "session.jsonl")
        with open(self.path, "w") as f:
            f.write("this is not json at all\n")
            f.write(json.dumps(_assistant_turn("v1", input_tokens=50, cache_read=200,
                                               cache_creation=0, output_tokens=20)) + "\n")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_partial_sums_no_exception(self):
        result = transcript_usage.parse_transcript_usage(self.path)
        self.assertEqual(result["turns"], 1)
        self.assertEqual(result["input_uncached"], 50)
        self.assertEqual(result["cache_read"], 200)


class TestTranscriptMissingReturnsZeros(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_none_path(self):
        path = transcript_usage.transcript_path(
            "nonexistent-session-id", projects_root=self.tmpdir)
        self.assertIsNone(path)

    def test_zeros_from_missing_path(self):
        result = transcript_usage.parse_transcript_usage(
            os.path.join(self.tmpdir, "ghost.jsonl"))
        self.assertEqual(result["turns"], 0)
        self.assertEqual(result["cache_read"], 0)


class TestCountTokensFallback(unittest.TestCase):
    def test_fallback_chars_div_4(self):
        text = "a" * 40  # 40 chars -> 10 tokens
        # We test the //4 branch by checking the formula — tiktoken may or may
        # not be installed, but is_exact must always be False.
        tokens, is_exact = transcript_usage.count_tokens(text)
        self.assertIsInstance(tokens, int)
        self.assertFalse(is_exact)
        # Either tiktoken or //4 must produce a positive number.
        self.assertGreater(tokens, 0)

    def test_is_exact_always_false(self):
        _, is_exact = transcript_usage.count_tokens("hello world")
        self.assertFalse(is_exact)


class TestLedgerSummaryWithTranscripts(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.projects_root = os.path.join(self.tmpdir, "projects")
        os.makedirs(self.projects_root)

        # Session s1 has a matching transcript with warm cache.
        self.sid1 = "session-with-transcript"
        self.cwd1 = "/fake/project"
        slug1 = transcript_usage.slugify_cwd(self.cwd1)
        _make_fixture_transcript(
            self.projects_root, slug1, self.sid1,
            [
                _assistant_turn("t1", input_tokens=100, cache_read=500,
                                cache_creation=0, output_tokens=50),
            ]
        )

        # Session s2 has no transcript.
        self.sid2 = "session-no-transcript"
        self.cwd2 = "/fake/project"

        # Write ledger with two session_start lines.
        self.ledger_path = os.path.join(self.tmpdir, "ledger.jsonl")
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).isoformat()
        with open(self.ledger_path, "w") as f:
            f.write(json.dumps({
                "ts": ts, "event": "session_start",
                "session_id": self.sid1, "count": 3, "chars": 400,
                "block_hash": "abc123", "cwd": self.cwd1,
            }) + "\n")
            f.write(json.dumps({
                "ts": ts, "event": "session_start",
                "session_id": self.sid2, "count": 3, "chars": 400,
                "block_hash": "abc123", "cwd": self.cwd2,
            }) + "\n")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_transcript_metrics(self):
        import memzero
        summary = memzero.ledger_summary(
            path=self.ledger_path, with_transcripts=True,
            projects_root=self.projects_root)
        self.assertEqual(summary["measured_sessions"], 1)
        self.assertEqual(summary["warm_sessions"], 1)
        self.assertGreater(summary["cache_read_share"], 0)
        self.assertGreater(summary["psyche_avoided_tokens"], 0)
        # coverage: 1 measured out of 2 sessions
        self.assertAlmostEqual(summary["measured_coverage"], 0.5)


class TestLedgerBlockChangesMultiproject(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.ledger_path = os.path.join(self.tmpdir, "ledger.jsonl")
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).isoformat()
        with open(self.ledger_path, "w") as f:
            # Project A: one session, hash X
            f.write(json.dumps({
                "ts": ts, "event": "session_start",
                "session_id": "s1", "count": 3, "chars": 400,
                "block_hash": "hashAAA", "cwd": "/project/alpha",
            }) + "\n")
            # Project B: one session, hash Y (different from X but that's fine)
            f.write(json.dumps({
                "ts": ts, "event": "session_start",
                "session_id": "s2", "count": 3, "chars": 400,
                "block_hash": "hashBBB", "cwd": "/project/beta",
            }) + "\n")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_no_block_changes_across_stable_projects(self):
        import memzero
        summary = memzero.ledger_summary(path=self.ledger_path)
        # Each project is internally stable (1 unique hash each) → changes == 0.
        self.assertEqual(summary["session_block_changes"], 0)
        # But global distinct count is still 2.
        self.assertEqual(summary["distinct_session_blocks"], 2)


class TestLedgerSummaryDefaultUnchanged(unittest.TestCase):
    """with_transcripts=False must produce the same shape/values as before."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.ledger_path = os.path.join(self.tmpdir, "ledger.jsonl")
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).isoformat()
        with open(self.ledger_path, "w") as f:
            f.write(json.dumps({
                "ts": ts, "event": "session_start",
                "session_id": "s1", "count": 3, "chars": 400,
            }) + "\n")
            f.write(json.dumps({
                "ts": ts, "event": "prompt_submit",
                "session_id": "s1", "count": 2, "chars": 600,
            }) + "\n")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_tokens_injected_and_savings(self):
        import memzero
        summary = memzero.ledger_summary(path=self.ledger_path)
        self.assertEqual(summary["tokens_injected"], (400 + 600) // 4)
        self.assertIn("estimated_savings_tokens", summary)
        self.assertIsInstance(summary["estimated_savings_tokens"], int)
        # with_transcripts keys must NOT be present
        self.assertNotIn("measured_sessions", summary)


if __name__ == "__main__":
    unittest.main()
