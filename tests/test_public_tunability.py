"""Focused portability/configuration checks for the public memory tools."""

from __future__ import annotations

import importlib.util
import importlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class PublicTunabilityTests(unittest.TestCase):
    def test_osvec_integrity_suite_skips_when_no_numpy_runtime_exists(self):
        integrity = importlib.import_module("tests.test_osvec_integrity")

        @integrity.numpy_runtime_test
        def optional_check(case):
            case.fail("optional check should not run without NumPy")

        case = unittest.TestCase()
        with mock.patch.object(integrity, "NUMPY_AVAILABLE", False), \
                mock.patch.object(integrity, "_numpy_python", return_value=None):
            with self.assertRaises(unittest.SkipTest) as skipped:
                optional_check(case)
        self.assertIn("requires a Python interpreter that can import numpy",
                      str(skipped.exception))

    def test_osvec_help_works_without_numpy_and_data_command_is_actionable(self):
        script = ROOT / "addons/full-engine/memory/osvec_adapter.py"
        help_result = subprocess.run(
            [sys.executable, "-S", str(script), "--help"],
            capture_output=True, text=True, cwd=ROOT,
        )
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("selftest", help_result.stdout)
        stats_result = subprocess.run(
            [sys.executable, "-S", str(script), "stats"],
            capture_output=True, text=True, cwd=ROOT,
        )
        self.assertNotEqual(stats_result.returncode, 0)
        self.assertIn("optional dependency numpy", stats_result.stderr)
        self.assertNotIn("Traceback", stats_result.stderr)

    def test_mneme_rejects_unknown_embedder_without_touching_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            index = tmp_path / "mneme-index.json"
            brain = tmp_path / "shared-brain.jsonl"
            env = dict(os.environ)
            env.update({
                "MNEME_INDEX": str(index),
                "PROJECT_OS_SHARED_BRAIN": str(brain),
                "MNEME_EMBEDDER": "not-a-mode",
            })
            result = subprocess.run(
                [sys.executable, str(ROOT / "memory/mneme_adapter.py"), "build"],
                capture_output=True, text=True, cwd=ROOT, env=env,
            )
            self.assertFalse(index.exists(), "invalid embedder wrote an index")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must be one of auto, neural, lexical", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_ollama_batch_and_timeout_use_bounded_env_aliases(self):
        mneme = load_module("public_mneme_tunability", ROOT / "memory/mneme_adapter.py")

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def __init__(self, size):
                self.size = size

            def read(self):
                return json.dumps({"embeddings": [[1.0, 0.0]] * self.size}).encode()

        calls = []

        def fake_urlopen(request, timeout):
            calls.append((request, timeout))
            payload = json.loads(request.data.decode())
            return Response(len(payload["input"]))

        with mock.patch.dict(os.environ, {
                "MNEME_OLLAMA_TIMEOUT": "3.5",
                "MNEME_OLLAMA_BATCH_SIZE": "2",
                "OSVEC_OLLAMA_TIMEOUT": "9",
                "OSVEC_OLLAMA_BATCH_SIZE": "9",
            }, clear=False), mock.patch.object(
                mneme.urllib.request, "urlopen", side_effect=fake_urlopen):
            vectors = mneme.embed_neural_batch(["a", "b", "c", "d", "e"])

        self.assertEqual(len(vectors), 5)
        self.assertEqual([timeout for _, timeout in calls], [3.5, 3.5, 3.5])
        self.assertEqual([len(json.loads(req.data.decode())["input"])
                          for req, _ in calls], [2, 2, 1])

    def test_brain_scale_docs_match_project_local_resolver(self):
        text = (ROOT / "scripts/brain_scale.py").read_text(encoding="utf-8")
        self.assertIn("project-local brain/shared-brain.jsonl", text)
        self.assertNotIn("~/.project-os/central-brain/shared-brain.jsonl lines", text)

    def test_nightly_does_not_name_a_machine_global_error_log(self):
        text = (ROOT / "scripts/os_nightly.py").read_text(encoding="utf-8")
        self.assertNotIn("~/.project-os/logs/nightly.err", text)


if __name__ == "__main__":
    unittest.main()
