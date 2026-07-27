#!/usr/bin/env python3
"""Regression: ``brain.py --selftest`` must never touch the real brain store.

Before b4cc70f, ``_selftest()`` ran cmd_export/cmd_save_chat/cmd_import
against the module-level BRAIN_FILE with no override, so every ``--selftest``
invocation appended synthetic ``selftest-*`` records into the real, live
``shared-brain.jsonl`` sitting next to the script. (The untracked working-copy
store in this repo still carries that pollution from pre-fix runs.)

This test runs the SHIPPED brain.py byte-for-byte, copied into a tempfile
sandbox laid out exactly like the addon (a ``brain/`` dir inside a project
root, so ``_safe_path``'s ROOT containment behaves identically). The
sandbox's co-located ``shared-brain.jsonl`` plays the role of the real store,
and is seeded BY the shipped tool itself (``save-chat``), not by an invented
fixture. Then ``--selftest`` runs as a subprocess with HOME and cwd inside
the sandbox, and we assert zero writes to the store: identical bytes,
identical mtime, no ``selftest-*`` ids, and no leftover scratch files.

Reverting the isolation hunk (dropping the BRAIN_FILE swap) makes the
selftest append two records to the seeded store, which fails the byte and
id assertions here.
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
SHIPPED_BRAIN_PY = os.path.join(REPO, "addons", "full-engine", "brain", "brain.py")

SEED_ID = "seed-isolation-check"


class TestSelftestIsolation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="brain-selftest-iso-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        # Sandbox mirrors the shipped layout: <root>/brain/brain.py, so that
        # HERE/ROOT/_safe_path resolve the same way they do in the addon.
        self.proj = os.path.join(self.tmp, "proj")
        self.brain_dir = os.path.join(self.proj, "brain")
        os.makedirs(self.brain_dir)
        shutil.copy2(SHIPPED_BRAIN_PY, os.path.join(self.brain_dir, "brain.py"))
        self.brain_py = os.path.join(self.brain_dir, "brain.py")
        self.store = os.path.join(self.brain_dir, "shared-brain.jsonl")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self.env = {
            "HOME": self.home,
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LC_ALL": "C",
        }

    def _run(self, argv):
        return subprocess.run(
            [sys.executable, self.brain_py] + argv,
            cwd=self.home,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=120,
        )

    def _store_ids(self):
        ids = []
        with open(self.store, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    ids.append(json.loads(line).get("id"))
        return ids

    def test_selftest_leaves_real_store_untouched(self):
        # Seed the "real" store with the shipped tool itself, so its content
        # is exactly what the artifact writes -- not an invented fixture.
        seeded = self._run(
            [
                "save-chat",
                "--summary",
                "A real pre-existing lesson that must survive selftest.",
                "--id",
                SEED_ID,
                "--kind",
                "lesson",
            ]
        )
        self.assertEqual(
            seeded.returncode,
            0,
            "sandbox seeding via save-chat failed: %s%s" % (seeded.stdout, seeded.stderr),
        )
        self.assertTrue(os.path.exists(self.store), "seeding did not create the store")

        with open(self.store, "rb") as f:
            before_bytes = f.read()
        before_mtime = os.stat(self.store).st_mtime_ns
        before_listing = sorted(os.listdir(self.brain_dir))

        proc = self._run(["--selftest"])
        self.assertEqual(
            proc.returncode,
            0,
            "--selftest exited %d\nstdout: %s\nstderr: %s"
            % (proc.returncode, proc.stdout, proc.stderr),
        )
        self.assertIn("selftest: OK", proc.stdout)

        with open(self.store, "rb") as f:
            after_bytes = f.read()
        self.assertEqual(
            after_bytes,
            before_bytes,
            "--selftest WROTE to the real shared-brain.jsonl: the store's "
            "bytes changed during the selftest run (isolation regression)",
        )
        self.assertEqual(
            os.stat(self.store).st_mtime_ns,
            before_mtime,
            "--selftest touched the real shared-brain.jsonl (mtime changed)",
        )

        ids = self._store_ids()
        self.assertEqual(ids, [SEED_ID], "store records changed during selftest")
        for rid in ids:
            self.assertFalse(
                str(rid).startswith("selftest"),
                "synthetic selftest record %r leaked into the real store" % rid,
            )

        # No scratch droppings left next to the store either.
        self.assertEqual(
            sorted(os.listdir(self.brain_dir)),
            before_listing,
            "--selftest left new files beside the real store",
        )

    def test_selftest_ok_with_no_preexisting_store(self):
        # Fresh install shape: no shared-brain.jsonl at all. The selftest must
        # still pass AND must not create the real store as a side effect.
        self.assertFalse(os.path.exists(self.store))
        proc = self._run(["--selftest"])
        self.assertEqual(
            proc.returncode,
            0,
            "--selftest exited %d\nstdout: %s\nstderr: %s"
            % (proc.returncode, proc.stdout, proc.stderr),
        )
        self.assertIn("selftest: OK", proc.stdout)
        self.assertFalse(
            os.path.exists(self.store),
            "--selftest created the real shared-brain.jsonl on a fresh install",
        )


if __name__ == "__main__":
    unittest.main()
