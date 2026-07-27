#!/usr/bin/env python3
"""adopt_project's goal-stub writer must never truncate 00-project-goal.md.

The defect (2026-07-27): ``_write_goal_stub()`` did

    with open(goal_path, "w", encoding="utf-8") as fh:
        fh.write(text)

which TRUNCATES the destination the moment it is opened, and only afterwards
re-read the file from disk to verify it with ``goal_guard.canonical_goal``.
The 2026-07-25 read-back verification is a detector, not a gate: it can only
report the loss, and only if the process is still alive to report it.

00-project-goal.md is hand-authored durable run data -- the canonical goal that
gets hashed into 21-agent-roster.md, plus the run's success criteria -- and
there is no template to regenerate an already-edited one from. So:

  1. A write that dies mid-flight (killed process, ENOSPC, EIO) must leave the
     PREVIOUS goal file byte-for-byte intact, not a 0-byte stub.
  2. A write whose content fails the goal_guard read-back must also leave the
     previous goal file intact, instead of leaving a verified-BAD file on disk
     and merely shouting about it.

The fix is the pattern already used by scripts/brain_archive.py and
addons/full-engine/memory/cost_actuals.py: write a sibling ``mkstemp`` temp
file, run the verification against THAT file, and only ``os.replace()`` it onto
the goal path once the parse succeeds.

Injection fairness (this repo has been burned by probes that pass either way):

  * The crash test fails ONLY operations that touch the victim path itself --
    a write-mode ``open()`` of 00-project-goal.md, and an ``os.replace()``
    whose destination is 00-project-goal.md. A temp file elsewhere is never
    sabotaged, but it can never reach the destination either, so the atomic
    writer is judged on its atomicity rather than on being handed an easier
    injection: it still takes a hard failure, it just does not take the user's
    goal down with it.
  * The verification test is path-BLIND on purpose -- it corrupts every byte
    the writer emits, wherever it stages them -- because its claim is "text
    goal_guard cannot vouch for never lands on the goal file". There
    ``os.replace`` is left working; refusing to rename is the whole fix.

Both tests are mutation-verified: restore the truncating writer and they FAIL.

Stdlib only; runs on the /usr/bin/python3 3.9.6 CI floor.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE_MEMORY = ROOT / "addons" / "full-engine" / "memory"
ADOPT_PATH = ENGINE_MEMORY / "adopt_project.py"
GOAL_GUARD_PATH = ENGINE_MEMORY / "goal_guard.py"

GOAL = "Track widgets locally"
PLACEHOLDER = "Replace this line with the one-sentence canonical goal."
# Deliberately NOT the pristine template: a real adoption target may already be
# hand-edited, which is exactly the data that cannot be regenerated.
EXISTING_GOAL_MD = (
    "# Project Goal\n\n## Canonical Goal\n\n"
    "<!-- ONE sentence. This exact line is hashed by goal_guard.py. -->\n\n"
    + PLACEHOLDER + "\n\n"
    "## Success Criteria\n\n"
    "- [ ] Hand-written criterion the operator typed by hand\n"
)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class _MangledWriter:
    """A write handle whose bytes land on disk corrupted."""

    def __init__(self, fh, mangle):
        self._fh = fh
        self._mangle = mangle

    def write(self, data):
        return self._fh.write(self._mangle(data))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self._fh.close()
        return False


class _DyingWriter:
    """A write handle that dies mid-write, like ENOSPC or a killed process."""

    def __init__(self, fh):
        self._fh = fh

    def write(self, data):
        self._fh.flush()
        raise OSError(28, "No space left on device")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self._fh.close()
        return False


class AdoptGoalStubIsNotWrittenInPlace(unittest.TestCase):
    """00-project-goal.md must be replaced atomically, never truncated."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="adopt-goal-atomic-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.run_dir = os.path.join(self.tmp, "run")
        os.makedirs(self.run_dir)
        self.goal_path = os.path.join(self.run_dir, "00-project-goal.md")
        with open(self.goal_path, "w", encoding="utf-8") as fh:
            fh.write(EXISTING_GOAL_MD)
        os.chmod(self.goal_path, 0o644)
        self.adopt = load("adopt_atomic_probe", ADOPT_PATH)

    def _patch_adopt_open(self, wrapper, victim_only):
        """Wrap adopt's write-mode open()s; optionally only for the goal file."""
        victim = os.path.realpath(self.goal_path)
        real_open = open

        def guarded_open(path, mode="r", *args, **kwargs):
            fh = real_open(path, mode, *args, **kwargs)
            if not any(c in mode for c in "wxa+"):
                return fh
            if victim_only and os.path.realpath(path) != victim:
                return fh
            return wrapper(fh)

        self.adopt.open = guarded_open

        def restore():
            if "open" in vars(self.adopt):
                del self.adopt.open

        self.addCleanup(restore)

    def _install_dying_write(self):
        """Kill ONLY the write that lands on 00-project-goal.md.

        The goal file's own write dies (as ENOSPC or a SIGKILL would) and any
        rename ONTO it fails too, so an atomic writer is judged on its
        atomicity rather than on being handed an easier injection: a temp file
        at some other path is never sabotaged, but it can never reach the
        destination either.
        """
        self._patch_adopt_open(_DyingWriter, victim_only=True)
        victim = os.path.realpath(self.goal_path)
        real_replace = os.replace

        def guarded_replace(src, dst, *args, **kwargs):
            if os.path.realpath(dst) == victim:
                raise OSError(28, "No space left on device")
            return real_replace(src, dst, *args, **kwargs)

        os.replace = guarded_replace
        self.addCleanup(setattr, os, "replace", real_replace)

    def _install_corrupting_write(self, mangle):
        """Corrupt every byte adopt writes, wherever it stages them.

        Path-blind on purpose: the claim under test is "text goal_guard cannot
        vouch for never lands on the goal file", so the injection has to reach
        the staged copy no matter where the writer chooses to put it. os.replace
        is deliberately left working -- refusing to rename is the fix.
        """
        self._patch_adopt_open(
            lambda fh: _MangledWriter(fh, mangle), victim_only=False)

    def _leftovers(self):
        return sorted(
            n for n in os.listdir(self.run_dir) if n != "00-project-goal.md"
        )

    def test_interrupted_write_preserves_the_previous_goal_file(self):
        """A write that dies mid-flight must not destroy the existing goal."""
        self._install_dying_write()

        with self.assertRaises(OSError):
            self.adopt._write_goal_stub(
                self.run_dir, GOAL, "README.md", self.tmp)

        with open(self.goal_path, encoding="utf-8") as fh:
            on_disk = fh.read()
        self.assertEqual(
            on_disk, EXISTING_GOAL_MD,
            "an interrupted goal-stub write destroyed 00-project-goal.md "
            "(%d bytes survived of %d). The writer truncates the real file in "
            "place; it must write a sibling temp file and os.replace() it."
            % (len(on_disk), len(EXISTING_GOAL_MD)),
        )
        self.assertEqual(
            self._leftovers(), [],
            "interrupted goal-stub write left a temp file behind: %r"
            % (self._leftovers(),),
        )

    def test_failed_verification_leaves_the_previous_goal_file(self):
        """A write goal_guard cannot vouch for must not land on the goal file.

        This is the half the 2026-07-25 read-back verification cannot do: it
        detects the bad write only after that write has already replaced the
        user's goal, so its SystemExit reports unrecoverable loss instead of
        preventing it.
        """
        # A short write: goal_guard finds no canonical goal at all.
        self._install_corrupting_write(lambda data: data[:40])

        with self.assertRaises(SystemExit) as ctx:
            self.adopt._write_goal_stub(
                self.run_dir, GOAL, "README.md", self.tmp)
        self.assertIn("adopt:", str(ctx.exception))

        with open(self.goal_path, encoding="utf-8") as fh:
            on_disk = fh.read()
        self.assertEqual(
            on_disk, EXISTING_GOAL_MD,
            "verification failed and adopt still left the unverifiable text on "
            "00-project-goal.md (%d bytes). Verify the TEMP file, then replace."
            % len(on_disk),
        )
        self.assertEqual(
            self._leftovers(), [],
            "rejected goal-stub write left a temp file behind: %r"
            % (self._leftovers(),),
        )

    def test_successful_write_still_lands_and_keeps_the_files_mode(self):
        """The atomic path must not regress the happy case, nor narrow perms.

        tempfile.mkstemp() always creates 0600 and os.replace() carries that
        mode across the rename, so an atomic writer that forgets to copy the
        destination's mode silently turns a world-readable goal file
        owner-only -- the exact silent narrowing already fixed in
        addons/full-engine/memory/cost_actuals.py.
        """
        goal_guard = load("gg_atomic_probe", GOAL_GUARD_PATH)
        before = os.stat(self.goal_path).st_mode & 0o777

        self.adopt._write_goal_stub(self.run_dir, GOAL, "README.md", self.tmp)

        with open(self.goal_path, encoding="utf-8") as fh:
            on_disk = fh.read()
        self.assertEqual(goal_guard.canonical_goal(on_disk), GOAL)
        self.assertEqual(
            os.stat(self.goal_path).st_mode & 0o777, before,
            "the goal-stub write changed 00-project-goal.md's permissions "
            "(%s -> %s)" % (oct(before),
                            oct(os.stat(self.goal_path).st_mode & 0o777)),
        )
        self.assertEqual(
            self._leftovers(), [],
            "successful goal-stub write left a temp file behind: %r"
            % (self._leftovers(),),
        )


if __name__ == "__main__":
    unittest.main()
