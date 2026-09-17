"""A successful append retry must reconcile the derived index, without duplicates.

Exercise the real CLI and lexical index in a temporary copy. No user memory,
network embedder, or repository index is read or written.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class MemoryRetryContractTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for relative in (
            "scripts/brain_append.py", "scripts/bb_lock.py",
            "scripts/brain_paths.py", "scripts/secret_patterns.py",
            "memory/mneme_adapter.py",
        ):
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)
        self.brain = self.root / "brain.jsonl"
        self.index = self.root / "index.json"
        self.env = dict(os.environ, HOME=str(self.root / "home"),
                        BB_LOCK_DIR=str(self.root / "locks"),
                        PROJECT_OS_SHARED_BRAIN=str(self.brain),
                        MNEME_INDEX=str(self.index), MNEME_EMBEDDER="lexical",
                        OSVEC_EMBEDDER="lexical")
        self.record = {"id": "new", "type": "lesson", "text": "new synthetic lesson"}
        self.brain.write_text(json.dumps({
            "id": "old", "type": "lesson", "text": "old synthetic lesson",
        }) + "\n", encoding="utf-8")
        built = self.run_cli("memory/mneme_adapter.py", "build")
        self.assertEqual(built.returncode, 0, built.stderr)
        self.assert_index_ids({"old"})

    def run_cli(self, relative, *args, embedder="lexical"):
        return subprocess.run(
            [sys.executable, str(self.root / relative), *args],
            cwd=self.root, env=dict(self.env, MNEME_EMBEDDER=embedder),
            capture_output=True, text=True, timeout=20,
        )

    def append(self, *args, embedder="lexical", record=None):
        return self.run_cli("scripts/brain_append.py", "--line",
                            json.dumps(self.record if record is None else record),
                            *args, embedder=embedder)

    def assert_index_ids(self, expected):
        index = json.loads(self.index.read_text(encoding="utf-8"))
        self.assertEqual(index["count"], len(expected))
        self.assertEqual({row["id"] for row in index["entries"]}, expected)

    def failed_first_append(self):
        before = self.index.read_bytes()
        result = self.append(embedder="invalid-retry-fixture")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("appended, but reindex FAILED", result.stderr)
        self.assertIn("memory/mneme_adapter.py build manually", result.stderr)
        self.assertEqual(self.index.read_bytes(), before)
        rows = [json.loads(line) for line in self.brain.read_text().splitlines()]
        self.assertEqual([row["id"] for row in rows], ["old", "new"])
        return self.brain.read_bytes()

    def test_retry_repairs_failed_index_refresh_without_reappending(self):
        committed = self.failed_first_append()
        retry = self.append()
        self.assertEqual(retry.returncode, 0, retry.stderr)
        self.assertIn("kept existing id", retry.stdout)
        self.assertEqual(self.brain.read_bytes(), committed)
        self.assert_index_ids({"old", "new"})

    def test_duplicate_with_missing_index_builds_it(self):
        old = {"id": "old", "type": "lesson", "text": "old synthetic lesson"}
        committed = self.brain.read_bytes()
        self.index.unlink()
        retry = self.append(record=old)
        self.assertEqual(retry.returncode, 0, retry.stderr)
        self.assertTrue(self.index.exists(), "success must recreate the missing index")
        self.assert_index_ids({"old"})
        self.assertEqual(self.brain.read_bytes(), committed)

    def test_duplicate_rebuild_failure_remains_an_error_with_accurate_outcome(self):
        committed = self.failed_first_append()
        before = self.index.read_bytes()
        retry = self.append(embedder="invalid-retry-fixture")
        self.assertEqual(retry.returncode, 1, retry.stdout)
        self.assertIn("kept existing id", retry.stderr)
        self.assertIn("reindex FAILED", retry.stderr)
        self.assertIn("memory/mneme_adapter.py build manually", retry.stderr)
        self.assertNotIn("appended", retry.stderr)
        self.assertNotIn("Traceback", retry.stderr)
        self.assertEqual(self.brain.read_bytes(), committed)
        self.assertEqual(self.index.read_bytes(), before)

    def test_no_reindex_explicitly_skips_new_and_duplicate_rebuilds(self):
        before = self.index.read_bytes()
        first = self.append("--no-reindex", embedder="invalid-retry-fixture")
        self.assertEqual(first.returncode, 0, first.stderr)
        committed = self.brain.read_bytes()
        retry = self.append("--no-reindex", embedder="invalid-retry-fixture")
        self.assertEqual(retry.returncode, 0, retry.stderr)
        self.assertIn("kept existing id", retry.stdout)
        self.assertEqual(self.brain.read_bytes(), committed)
        self.assertEqual(self.index.read_bytes(), before)
        self.assert_index_ids({"old"})

    def test_new_append_rebuilds_normally(self):
        result = self.append()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("appended + reindexed", result.stdout)
        self.assert_index_ids({"old", "new"})

    def test_duplicate_preserves_original_text_when_request_differs(self):
        committed = self.failed_first_append()
        retry = self.append(record=dict(self.record, text="changed retry text"))
        self.assertEqual(retry.returncode, 0, retry.stderr)
        self.assertEqual(self.brain.read_bytes(), committed)
        self.assert_index_ids({"old", "new"})
        rows = json.loads(self.index.read_text(encoding="utf-8"))["entries"]
        new = next(row for row in rows if row["id"] == "new")
        self.assertEqual(new["text"], self.record["text"])


if __name__ == "__main__":
    unittest.main()
