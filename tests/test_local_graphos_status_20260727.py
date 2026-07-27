"""Regression test for scripts/check_optional_tools.py::local_graphos_status.

Audit finding (2026-07-26 sweep, closed for OSVec, missed for GraphOS): a bare
file-existence check must never earn the "Verified" status. The script's own
contract (see the PRESENT_UNVERIFIED comment near the top of the module) says
"Verified" is earned only by an executable on PATH or an importable package.
local_graphos_status was still reporting "Verified" for a memory/build_graph.py
that merely exists — even when that file is empty, invalid, or otherwise not a
usable helper. It must instead mirror its hardened sibling local_osvec_status
and report PRESENT_UNVERIFIED, naming the command that would actually verify it.
"""

import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOL_CHECK = ROOT / "scripts" / "check_optional_tools.py"


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class LocalGraphosStatusTests(unittest.TestCase):
    def setUp(self):
        self.tool_check = load_module(TOOL_CHECK, "graphos_status_check_optional_tools")

    def _make_builder(self, target, contents=""):
        builder = target / "memory" / "build_graph.py"
        builder.parent.mkdir(parents=True, exist_ok=True)
        builder.write_text(contents, encoding="utf-8")
        return builder

    def test_empty_builder_file_is_not_verified(self):
        """A build_graph.py that merely exists (and is empty/invalid) must not
        be reported as "Verified"."""
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            self._make_builder(target, contents="")  # exists but empty/invalid

            status, evidence = self.tool_check.local_graphos_status(target)

        self.assertNotEqual(
            status,
            "Verified",
            "an unexecuted, empty build_graph.py must not earn the 'Verified' label",
        )
        self.assertEqual(status, self.tool_check.PRESENT_UNVERIFIED)
        # It should still name the command that would actually verify the helper.
        self.assertIn("memory/build_graph.py", evidence)

    def test_builder_with_graph_artifact_is_not_verified(self):
        """Even when graph.json is also present, nothing was executed, so the
        status must stay PRESENT_UNVERIFIED (files-only), never "Verified"."""
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            self._make_builder(target, contents="")
            graph = target / "graphify-out" / "graph.json"
            graph.parent.mkdir(parents=True, exist_ok=True)
            graph.write_text("{}", encoding="utf-8")

            status, evidence = self.tool_check.local_graphos_status(target)

        self.assertNotEqual(status, "Verified")
        self.assertEqual(status, self.tool_check.PRESENT_UNVERIFIED)
        self.assertIn("graphify-out/graph.json", evidence)

    def test_missing_builder_returns_none(self):
        """No local helper on disk -> defer to the next status source."""
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            self.assertIsNone(self.tool_check.local_graphos_status(target))

    def test_none_target_returns_none(self):
        self.assertIsNone(self.tool_check.local_graphos_status(None))


if __name__ == "__main__":
    unittest.main()
