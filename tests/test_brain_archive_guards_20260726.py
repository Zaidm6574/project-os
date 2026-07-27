#!/usr/bin/env python3
"""Regression guards for two b4cc70f fixes in scripts/brain_archive.py.

1. Re-read-under-lock (cmd_apply): rows/move used to be computed from a
   pre-lock read; an append landing between that read and lock acquisition
   was silently destroyed by the stale `keep` snapshot while the tool still
   printed "archived 1". The fix re-reads BRAIN and re-derives move/keep
   after the lock is held. These tests drive the real cmd_apply and inject
   the concurrent append deterministically at the lock seam (the fake
   bb_lock.acquire appends a row before returning True) — exactly the window
   the fix closes.

2. ARCHIVE != BRAIN derivation (module import): the old
   BRAIN.replace(".jsonl", "-archive.jsonl") was a no-op for any configured
   brain path without a .jsonl suffix, collapsing ARCHIVE == BRAIN so
   archiving appended to the live file and then overwrote it — net deletion.
   The fix appends the suffix explicitly and asserts the paths differ.

Each test loads a fresh module instance with PROJECT_OS_SHARED_BRAIN pointing
into a tempfile sandbox, and stubs the loaded instance's bb_lock and
subprocess attributes so no live lock files are taken and the real
mneme_adapter rebuild never runs. Stdlib only; passes on python 3.9.6.
"""

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRAIN_ARCHIVE_PY = os.path.join(ROOT, "scripts", "brain_archive.py")
ENV = "PROJECT_OS_SHARED_BRAIN"

_SEQ = [0]


def _load_brain_archive(brain_path):
    """Import a fresh scripts/brain_archive.py instance with BRAIN=brain_path.

    The module resolves BRAIN/ARCHIVE at import time from the environment, so
    every test needs its own instance pointed at its own sandbox.
    """
    _SEQ[0] += 1
    name = "brain_archive_under_test_%d" % _SEQ[0]
    old = os.environ.get(ENV)
    os.environ[ENV] = brain_path
    try:
        spec = importlib.util.spec_from_file_location(name, BRAIN_ARCHIVE_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        if old is None:
            os.environ.pop(ENV, None)
        else:
            os.environ[ENV] = old
    return mod


def _write_rows(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for o in rows:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")


def _read_rows(path):
    if not os.path.isfile(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _ids(rows):
    return [o.get("id") for o in rows]


# Rows shaped like the shipped shared-brain schema (id/type/ts/text). The
# "interest" row is old enough to be an auto-candidate at the default 60d.
OLD_INTEREST = {"id": "interest-old-topic", "type": "interest",
                "ts": "2020-01-02T00:00:00Z", "text": "stale interest row"}
KEEP_LESSON = {"id": "lesson-keep", "type": "lesson",
               "ts": "2026-07-01T00:00:00Z", "text": "durable lesson row"}
CONCURRENT = {"id": "lesson-concurrent", "type": "lesson",
              "ts": "2026-07-26T00:00:00Z",
              "text": "appended between the pre-lock read and the locked write"}


def _stub_side_effects(mod, on_acquire=None):
    """Replace the loaded instance's bb_lock and subprocess with test stubs.

    on_acquire, if given, runs inside bb_lock.acquire() — i.e. after
    cmd_apply's initial read of BRAIN and before it holds the lock. That is
    the exact race window the re-read-under-lock fix exists for.
    """
    def acquire(target, agent="unknown", wait=10.0):
        if on_acquire is not None:
            on_acquire()
        return True

    def release(target, agent="unknown", force=False, token=None):
        return True

    mod.bb_lock = types.SimpleNamespace(acquire=acquire, release=release)
    mod.subprocess = types.SimpleNamespace(
        run=lambda *a, **k: types.SimpleNamespace(stdout="", stderr=""))


class ConcurrentAppendSurvivesApply(unittest.TestCase):
    """Fix 1: an append landing before lock acquisition must survive apply."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.brain = os.path.join(self.tmp.name, "shared-brain.jsonl")
        _write_rows(self.brain, [OLD_INTEREST, KEEP_LESSON])
        self.mod = _load_brain_archive(self.brain)

        def append_concurrent():
            with open(self.brain, "a", encoding="utf-8") as f:
                f.write(json.dumps(CONCURRENT, ensure_ascii=False) + "\n")

        _stub_side_effects(self.mod, on_acquire=append_concurrent)

    def _apply(self, ns):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = self.mod.cmd_apply(ns)
        return rc, out.getvalue()

    def _assert_concurrent_survived(self, rc, printed):
        self.assertEqual(rc, 0)
        self.assertIn("archived 1", printed)
        brain_ids = _ids(_read_rows(self.brain))
        self.assertIn(
            "lesson-concurrent", brain_ids,
            "concurrent append was destroyed by a stale pre-lock snapshot "
            "while the tool reported success (re-read-under-lock regression)")
        self.assertIn("lesson-keep", brain_ids)
        self.assertNotIn("interest-old-topic", brain_ids)
        archive_ids = _ids(_read_rows(self.mod.ARCHIVE))
        self.assertEqual(archive_ids, ["interest-old-topic"])

    def test_concurrent_append_survives_apply_by_ids(self):
        ns = types.SimpleNamespace(ids=["interest-old-topic"], interest_days=60)
        rc, printed = self._apply(ns)
        self._assert_concurrent_survived(rc, printed)

    def test_concurrent_append_survives_apply_by_interest_days(self):
        ns = types.SimpleNamespace(ids=None, interest_days=60)
        rc, printed = self._apply(ns)
        self._assert_concurrent_survived(rc, printed)


class ArchivePathNeverResolvesToBrain(unittest.TestCase):
    """Fix 2: a brain path without .jsonl must not collapse ARCHIVE == BRAIN."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # The defect: .replace(".jsonl", ...) was a no-op on suffixless paths.
        self.brain = os.path.join(self.tmp.name, "shared-brain")
        _write_rows(self.brain, [OLD_INTEREST, KEEP_LESSON])
        self.mod = _load_brain_archive(self.brain)

    def test_archive_path_distinct_for_suffixless_brain(self):
        self.assertNotEqual(
            self.mod.ARCHIVE, self.mod.BRAIN,
            "ARCHIVE resolved to the live brain file; archiving would "
            "silently delete entries")
        self.assertEqual(self.mod.ARCHIVE, self.brain + "-archive.jsonl")

    def test_apply_with_suffixless_brain_loses_no_rows(self):
        _stub_side_effects(self.mod)
        ns = types.SimpleNamespace(ids=["interest-old-topic"], interest_days=60)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = self.mod.cmd_apply(ns)
        self.assertEqual(rc, 0)
        archive_rows = _read_rows(self.mod.ARCHIVE)
        self.assertIn(
            "interest-old-topic", _ids(archive_rows),
            "archived entry vanished from disk entirely: ARCHIVE collided "
            "with BRAIN, so the append was overwritten by the rewrite")
        brain_ids = _ids(_read_rows(self.brain))
        self.assertNotIn("interest-old-topic", brain_ids)
        self.assertIn("lesson-keep", brain_ids)
        # Nothing is ever deleted: every original id survives somewhere.
        surviving = set(brain_ids) | set(_ids(archive_rows))
        self.assertLessEqual({"interest-old-topic", "lesson-keep"}, surviving)


if __name__ == "__main__":
    unittest.main()
