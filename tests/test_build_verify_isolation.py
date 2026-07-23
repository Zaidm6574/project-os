"""Isolated-copy verification guard for build_verify.

Private hardening pass, 2026-07-22, RED-first. The attack: a verifier (or the
project's own test command) writes into the source tree — polluting or
destroying the thing under verification. --isolated must run the command
against a disposable copy and leave the source byte-identical.
"""
import hashlib
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD_VERIFY = ROOT / "addons" / "full-engine" / "memory" / "build_verify.py"
sys.path.insert(0, str(BUILD_VERIFY.parent))

import build_verify  # noqa: E402


WRITING_MAKEFILE = (
    ".PHONY: test\n"
    "test:\n"
    "\t@echo polluted > written-by-verifier.txt\n"
    "\t@echo damaged >> Makefile.log\n"
    "\t@echo ok\n"
)

FAILING_MAKEFILE = ".PHONY: test\ntest:\n\t@echo boom > blast.txt\n\t@exit 3\n"


def tree_fingerprint(root: Path) -> dict:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


class TestIsolatedCopyVerify(unittest.TestCase):
    def _project(self, tmp, makefile):
        proj = Path(tmp) / "proj"
        proj.mkdir()
        (proj / "Makefile").write_text(makefile, encoding="utf-8")
        (proj / "src.txt").write_text("precious source\n", encoding="utf-8")
        return proj

    def test_isolated_pass_leaves_source_tree_unchanged(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            proj = self._project(tmp, WRITING_MAKEFILE)
            before = tree_fingerprint(proj)
            rc = build_verify.verify(str(proj), timeout=30, isolated=True)
            self.assertEqual(rc, 0)
            self.assertEqual(before, tree_fingerprint(proj),
                             "isolated verify must not mutate the source tree")
            self.assertFalse((proj / "written-by-verifier.txt").exists())

    def test_isolated_failure_propagates_and_still_protects_source(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            proj = self._project(tmp, FAILING_MAKEFILE)
            before = tree_fingerprint(proj)
            rc = build_verify.verify(str(proj), timeout=30, isolated=True)
            self.assertEqual(rc, 1)
            self.assertEqual(before, tree_fingerprint(proj))
            self.assertFalse((proj / "blast.txt").exists())

    def test_unisolated_run_demonstrates_the_damage_the_guard_prevents(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            proj = self._project(tmp, WRITING_MAKEFILE)
            rc = build_verify.verify(str(proj), timeout=30)
            self.assertEqual(rc, 0)
            self.assertTrue((proj / "written-by-verifier.txt").exists(),
                            "control: legacy mode really does let verifiers write")

    def test_cli_exposes_isolated_flag_and_packet_contract_lines(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            proj = self._project(tmp, WRITING_MAKEFILE)
            r = subprocess.run(
                [sys.executable, str(BUILD_VERIFY), str(proj), "--isolated",
                 "--timeout", "30"],
                capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("BUILD-VERIFY-MODE: isolated-copy", r.stdout)
            self.assertIn("SOURCE-TREE: unchanged", r.stdout)
            self.assertIn("BUILD-VERIFY: PASS", r.stdout)
            self.assertFalse((proj / "written-by-verifier.txt").exists())

    def test_selftest_still_green(self):
        self.assertEqual(build_verify.selftest(), 0)


if __name__ == "__main__":
    unittest.main()
