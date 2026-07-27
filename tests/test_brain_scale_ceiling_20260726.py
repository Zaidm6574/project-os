#!/usr/bin/env python3
"""Regression: brain_scale must calibrate flat-mode entries against SOURCES_CEIL.

Before b4cc70f, flat (non-neural) mode computed the shared-brain entries
ceiling from PAGES_CEIL (200 md pages) instead of SOURCES_CEIL (~100
sources). The two constants count different things, and reusing PAGES_CEIL
silently HALVED the reported retrieval-degradation risk: a brain at 150
entries -- unambiguously past the ~100-source flat-index ceiling -- reported
WATCH (exit 1) instead of CUTOVER (exit 2), and a brain at 70 entries (70% of
the real ceiling) reported OK (exit 0) instead of WATCH.

This test runs the SHIPPED scripts/brain_scale.py byte-for-byte, copied into
a tempfile sandbox laid out like the repo (<root>/scripts/brain_scale.py with
empty runs/ and blackboard/ and no memory/mneme_index.json, so flat mode is
in force and page counts are 0). The shared brain is pointed at a sandbox
JSONL via the script's own PROJECT_OS_SHARED_BRAIN override; HOME is
sandboxed so no personal store or taste inventory leaks in.

Reverting the hunk (entries_ceil back to PAGES_CEIL in flat mode) flips the
ceiling to 200, the statuses to WATCH/OK, and the exit codes to 1/0 -- every
flat-mode assertion below fails.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHIPPED_SCALE_PY = os.path.join(REPO, "scripts", "brain_scale.py")

ENTRIES_DIM = "shared-brain entries (active)"


class TestBrainScaleFlatCeiling(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="brain-scale-ceil-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        proj = os.path.join(self.tmp, "proj")
        os.makedirs(os.path.join(proj, "scripts"))
        os.makedirs(os.path.join(proj, "runs"))
        os.makedirs(os.path.join(proj, "blackboard"))
        self.proj = proj
        self.scale_py = os.path.join(proj, "scripts", "brain_scale.py")
        shutil.copy2(SHIPPED_SCALE_PY, self.scale_py)
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self.store = os.path.join(self.tmp, "store", "shared-brain.jsonl")
        os.makedirs(os.path.dirname(self.store))

    def _write_store(self, n_entries):
        with open(self.store, "w", encoding="utf-8") as f:
            for i in range(n_entries):
                f.write(json.dumps({"id": "e%d" % i, "type": "lesson", "text": "x"}) + "\n")

    def _write_mneme(self, embedder):
        memdir = os.path.join(self.proj, "memory")
        os.makedirs(memdir, exist_ok=True)
        with open(os.path.join(memdir, "mneme_index.json"), "w", encoding="utf-8") as f:
            json.dump({"embedder": embedder, "entries": []}, f)

    def _run(self):
        env = {
            "HOME": self.home,
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LC_ALL": "C",
            "PROJECT_OS_SHARED_BRAIN": self.store,
        }
        proc = subprocess.run(
            [sys.executable, self.scale_py, "--json"],
            cwd=self.home,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=120,
        )
        try:
            payload = json.loads(proc.stdout)
        except ValueError:
            self.fail(
                "brain_scale --json emitted unparseable output (exit %d)\n"
                "stdout: %s\nstderr: %s" % (proc.returncode, proc.stdout, proc.stderr)
            )
        return proc, payload

    def _entries_dim(self, payload):
        for dim in payload["dimensions"]:
            if dim["name"] == ENTRIES_DIM:
                return dim
        self.fail("dimension %r missing from output: %s" % (ENTRIES_DIM, payload))

    def test_flat_mode_past_sources_ceiling_is_cutover(self):
        # 150 entries is past the ~100-source flat ceiling. It must be
        # reported as CUTOVER against ceiling 100 (exit 2) -- not diluted to
        # 75% of a 200-page ceiling and waved through as WATCH.
        self._write_store(150)
        proc, payload = self._run()
        dim = self._entries_dim(payload)
        self.assertEqual(dim["count"], 150, "sandbox store was not the one counted")
        self.assertEqual(
            dim["ceiling"],
            100,
            "flat mode calibrated the entries ceiling against the wrong "
            "constant (expected SOURCES_CEIL=100, got %r)" % dim["ceiling"],
        )
        self.assertEqual(dim["status"], "CUTOVER")
        self.assertEqual(payload["overall"], "CUTOVER")
        self.assertEqual(
            proc.returncode,
            2,
            "a brain past the flat-index ceiling must exit 2 (CUTOVER), "
            "got %d\nstdout: %s" % (proc.returncode, proc.stdout),
        )

    def test_flat_mode_70_entries_is_watch_not_ok(self):
        # 70 entries = 70% of the real flat ceiling -> WATCH (exit 1). Under
        # the pre-fix PAGES_CEIL calibration this was 35% and read OK (exit 0),
        # i.e. the risk was silently halved.
        self._write_store(70)
        proc, payload = self._run()
        dim = self._entries_dim(payload)
        self.assertEqual(dim["count"], 70)
        self.assertEqual(dim["ceiling"], 100)
        self.assertEqual(dim["status"], "WATCH")
        self.assertEqual(payload["overall"], "WATCH")
        self.assertEqual(proc.returncode, 1)

    def test_neural_mode_keeps_relaxed_ceiling(self):
        # The fix must change ONLY flat mode. With a neural embedder live the
        # active-entries ceiling stays the soft 400, so 150 entries is OK.
        # Guards the mirror mutation (making SOURCES_CEIL unconditional).
        self._write_store(150)
        self._write_mneme("neural-nomic-embed-text")
        proc, payload = self._run()
        dim = self._entries_dim(payload)
        self.assertEqual(dim["count"], 150)
        self.assertEqual(dim["ceiling"], 400)
        self.assertEqual(dim["status"], "OK")
        self.assertEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
