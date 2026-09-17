"""Retrieval regressions with temporary records and mocked neural responses."""
import contextlib
import fcntl
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load(relative, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class RetrievalRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="retrieval-regression-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def test_shared_reader_exit_keeps_writer_excluded_on_same_inode(self):
        try:
            import numpy  # noqa: F401 -- required by the adapter at import time
        except ImportError:
            self.skipTest("NumPy is required to import the OSVec adapter")
        osvec = load("addons/full-engine/memory/osvec_adapter.py", "repair_osvec")
        store = self.base / "store"
        lock = store / "project.lock"

        def exclusive_available():
            fd = os.open(str(lock), os.O_RDWR | os.O_CREAT, 0o600)
            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return False
                fcntl.flock(fd, fcntl.LOCK_UN)
                return True
            finally:
                os.close(fd)

        with contextlib.ExitStack() as readers:
            first = osvec._store_lock(str(store), shared=True)
            first.__enter__()
            readers.callback(first.__exit__, None, None, None)
            second = osvec._store_lock(str(store), shared=True)
            second.__enter__()
            readers.callback(second.__exit__, None, None, None)
            inode = lock.stat().st_ino
            self.assertFalse(exclusive_available())  # control: both readers hold
            first.__exit__(None, None, None)
            self.assertTrue(lock.exists(), "the flock inode must retain its pathname")
            self.assertEqual(lock.stat().st_ino, inode)
            self.assertFalse(exclusive_available(), "second reader still excludes writer")
        self.assertTrue(exclusive_available())  # control: readers released
        with osvec._store_lock(str(store)):
            self.assertEqual(lock.stat().st_ino, inode)
            self.assertFalse(exclusive_available())
        self.assertTrue(exclusive_available())

    def _neural_index(self, module):
        index = self.base / "mneme.json"
        index.write_text(json.dumps({
            "dim": 2, "embedder": "neural-model-a", "count": 1,
            "entries": [{"id": "one", "source": "lesson", "text": "one", "vec": [1., 0.]}],
        }), encoding="utf-8")
        patch = mock.patch.object(module, "INDEX", str(index))
        patch.start()
        self.addCleanup(patch.stop)
        return index

    def test_neural_query_uses_index_model_even_when_configuration_changes(self):
        mneme = load("memory/mneme_adapter.py", "repair_mneme")
        self._neural_index(mneme)
        requests = []

        def respond(request, timeout):
            requests.append(json.loads(request.data))
            # Both models have the same dimension: dimension checks alone
            # cannot detect this incompatible embedding space.
            vector = [1., 0.] if requests[-1]["model"] == "model-a" else [0., 1.]
            return io.BytesIO(json.dumps({"embeddings": [vector]}).encode())

        with mock.patch.object(mneme.urllib.request, "urlopen", side_effect=respond):
            for configured in ("model-a", "model-b"):
                with self.subTest(configured=configured), mock.patch.object(
                        mneme, "NEURAL_MODEL", configured):
                    self.assertEqual(mneme.query("one")[0]["score"], 1.0)
                    self.assertEqual(requests[-1]["model"], "model-a")

    def test_neural_query_refuses_invalid_vectors_without_lexical_fallback(self):
        mneme = load("memory/mneme_adapter.py", "repair_mneme_invalid")
        index = self._neural_index(mneme)
        before = index.read_bytes()
        invalid = ([1., 0., 1.], [], [True, 0.], ["1", 0.],
                   [float("nan"), 0.], [float("inf"), 0.], [0., 0.], [10 ** 400, 0.])
        for vector in invalid:
            with self.subTest(vector=repr(vector)), mock.patch.object(
                    mneme.urllib.request, "urlopen", return_value=io.BytesIO(
                        json.dumps({"embeddings": [vector]}).encode())), mock.patch.object(
                    mneme, "embed", side_effect=AssertionError("mixed embedding spaces")):
                with self.assertRaisesRegex(SystemExit, "rebuild"):
                    mneme.query("one")
                self.assertEqual(index.read_bytes(), before)

    def test_neural_batch_rejects_inconsistent_dimensions_across_requests(self):
        mneme = load("memory/mneme_adapter.py", "repair_mneme_batch")
        responses = [io.BytesIO(json.dumps({"embeddings": [vector]}).encode())
                     for vector in ([1., 0.], [1., 0., 1.])]
        with mock.patch.object(mneme, "ollama_batch_size", return_value=1), mock.patch.object(
                mneme.urllib.request, "urlopen", side_effect=responses):
            with self.assertRaisesRegex(RuntimeError, "dimension"):
                mneme.embed_neural_batch(["first", "second"])

    def test_auto_falls_back_on_bad_model_output_but_not_bad_configuration(self):
        mneme = load("memory/mneme_adapter.py", "repair_mneme_auto")
        responses = (b'{"embeddings": [[true, 0]]}', b'{"embeddings": [[0, 0]]}',
                     b'{"embeddings": [[NaN, 0]]}', b'not JSON')
        for response in responses:
            with self.subTest(response=response), mock.patch.object(
                    mneme, "EMBEDDER_PREF", "auto"), mock.patch.object(
                    mneme.urllib.request, "urlopen", return_value=io.BytesIO(response)):
                self.assertEqual(mneme.pick_embedder(), "lexical-hash-v1")
        with mock.patch.object(mneme, "EMBEDDER_PREF", "auto"), mock.patch.dict(
                os.environ, {"MNEME_OLLAMA_TIMEOUT": "invalid"}), mock.patch.object(
                mneme.urllib.request, "urlopen") as request:
            with self.assertRaises(ValueError):
                mneme.pick_embedder()
            request.assert_not_called()

    def test_large_finite_neural_vector_normalizes_without_overflow(self):
        mneme = load("memory/mneme_adapter.py", "repair_mneme_large")
        vector = mneme._l2([1e300, 1e300])
        self.assertTrue(all(math.isfinite(value) for value in vector))
        self.assertAlmostEqual(sum(value * value for value in vector), 1.)

    def test_cosine_refuses_silent_dimension_truncation(self):
        mneme = load("memory/mneme_adapter.py", "repair_mneme_cosine")
        self.assertEqual(mneme.cosine([1., 0.], [1., 0.]), 1.)
        with self.assertRaisesRegex(ValueError, "dimension"):
            mneme.cosine([1., 0., 1.], [1., 0.])

    def test_lexical_index_never_contacts_neural_service(self):
        mneme = load("memory/mneme_adapter.py", "repair_mneme_lexical")
        vector = mneme.embed("signed receipt")
        index = self.base / "lexical.json"
        index.write_text(json.dumps({
            "dim": mneme.DIM, "embedder": "lexical-hash-v1", "count": 1,
            "entries": [{"id": "one", "source": "lesson", "text": "signed receipt", "vec": vector}],
        }), encoding="utf-8")
        with mock.patch.object(mneme, "INDEX", str(index)), mock.patch.object(
                mneme.urllib.request, "urlopen", side_effect=AssertionError("network")):
            self.assertEqual(mneme.query("signed receipt")[0]["score"], 1.)

    def test_fts_checks_actual_row_fields_with_unchanged_cached_hash(self):
        mirror = load("addons/full-engine/memory/brain_fts_mirror.py", "repair_fts")
        brain, db = self.base / "brain.jsonl", self.base / "mirror.db"
        brain.write_text(json.dumps({"id": "real", "type": "lesson", "ts": "2026-01-01",
                                     "text": "signed receipt before release"}) + "\n", encoding="utf-8")
        for field, value in (("record_id", "forged"), ("ts", "different"), ("type", "other"),
                             ("text", "signed receipt altered"), ("raw", "{}")):
            with self.subTest(field=field):
                mirror.rebuild(brain, db)
                self.assertEqual(mirror.verify(brain, db), [])
                self.assertEqual(mirror.query("signed receipt", brain_path=brain, db_path=db)[0]["id"], "real")
                with contextlib.closing(sqlite3.connect(db)) as con:
                    with con:
                        # Fields are a closed list above; values remain bound.
                        con.execute("UPDATE records SET " + field + " = ?", (value,))
                self.assertTrue(mirror.verify(brain, db))
                with self.assertRaises(mirror.MirrorError):
                    mirror.query("signed receipt", brain_path=brain, db_path=db)
        mirror.rebuild(brain, db)
        self.assertEqual(mirror.verify(brain, db), [])  # rebuild restores parity

    def test_indented_lessons_stage_and_remain_in_harvest_queue(self):
        harvest = load("scripts/harvest.py", "repair_harvest")
        runs, packets = self.base / "runs", self.base / "packets"
        brain = self.base / "empty-brain.jsonl"
        brain.write_text("", encoding="utf-8")
        with mock.patch.object(harvest, "RUNS", str(runs)), mock.patch.object(
                harvest, "PACKETS", str(packets)), mock.patch.object(
                harvest, "SHARED_BRAIN", str(brain)), mock.patch.object(
                harvest, "_secret_reason", return_value=None), contextlib.redirect_stdout(io.StringIO()):
            for number, prefix in enumerate(("- ", "  - ", "\t* ")):
                slug = "lesson-" + str(number)
                run = runs / slug
                run.mkdir(parents=True)
                (run / "13-delivery-report.md").write_text("# delivered\n", encoding="utf-8")
                (run / "19-memory-harvest.md").write_text(
                    "## Lessons\n" + prefix + "Back up the store before upgrade\n", encoding="utf-8")
                with self.subTest(prefix=prefix):
                    self.assertEqual(harvest.cmd_scan(slug), 0)
                    staged = list(packets.glob("harvest-" + slug + "-*.jsonl"))
                    self.assertEqual(len(staged), 1)
                    records = [json.loads(line) for line in staged[0].read_text().splitlines()]
                    self.assertEqual([r["text"] for r in records], ["Back up the store before upgrade"])
                    self.assertFalse((run / ".harvested").exists())
                    self.assertIn(slug, harvest.unharvested())

    def test_indented_rejected_lesson_still_uses_refusal_rules(self):
        harvest = load("scripts/harvest.py", "repair_harvest_reject")
        self.assertEqual(list(harvest.bullets_by_section(
            "## Lessons\n  - Private-only: keep the internal review note\n")), [])
        self.assertEqual(len(harvest.DROPPED), 1)

    def test_module_alias_shadowing_does_not_claim_an_ast_call(self):
        graph = load("memory/code_graph.py", "repair_graph")
        (self.base / "helper.py").write_text("def work():\n    return 1\n", encoding="utf-8")
        (self.base / "caller.py").write_text(
            "import helper\n"
            "def honest():\n    return helper.work()\n"
            "def parameter(helper):\n    return helper.work()\n"
            "def assignment(other):\n    helper = other\n    return helper.work()\n"
            "def local_import():\n    import other as helper\n    return helper.work()\n",
            encoding="utf-8")
        built = graph.build(str(self.base))
        calls = [e for e in built["edges"] if e["type"] == "calls"]
        self.assertEqual([(e["source"], e["target"], e["evidence"]["method"]) for e in calls],
                         [("caller:honest", "helper:work", "ast")])
        code, packet = graph.orient("caller:parameter", str(self.base), str(self.base / "code-graph.json"))
        self.assertEqual(code, 0)
        self.assertEqual(packet["callees"], [])

    def test_class_test_methods_are_visible_in_orientation(self):
        graph = load("memory/code_graph.py", "repair_graph_tests")
        (self.base / "worker.py").write_text("def work():\n    return 1\n", encoding="utf-8")
        (self.base / "test_worker.py").write_text(
            "import unittest\nfrom worker import work\n"
            "class WorkerTest(unittest.TestCase):\n"
            "    def test_work(self):\n        self.assertEqual(work(), 1)\n"
            "    async def test_async_work(self):\n        self.assertEqual(work(), 1)\n"
            "    def helper(self):\n        return work()\n", encoding="utf-8")
        built = graph.build(str(self.base))
        kinds = {n["id"]: n["type"] for n in built["nodes"]}
        self.assertEqual(kinds["test_worker:WorkerTest.helper"], "function")
        expected = {"test_worker:WorkerTest.test_work", "test_worker:WorkerTest.test_async_work"}
        for symbol in expected:
            self.assertEqual(kinds[symbol], "test")
        edges = {(e["source"], e["target"]) for e in built["edges"] if e["type"] == "tests"}
        self.assertEqual(edges, {(symbol, "worker:work") for symbol in expected})
        code, packet = graph.orient("worker:work", str(self.base), str(self.base / "code-graph.json"))
        self.assertEqual(code, 0)
        self.assertEqual({node["id"] for node in packet["tests"]}, expected)


if __name__ == "__main__":
    unittest.main()
