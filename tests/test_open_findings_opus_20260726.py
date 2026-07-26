"""Three defects found by an independent adversary on 2026-07-26.

Each is guarded here by BEHAVIOUR — what git actually ignores, what lands on
disk, what survives in the brain file — never by grepping a module's source. All
three were verified to fail before their fix and pass after (see the commit).
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CURATED_IGNORE = os.path.join(ROOT, "scripts", "project-os.gitignore")
NEW_RUN = os.path.join(ROOT, "addons", "full-engine", "memory", "new_run.py")
BRAIN_APPEND = os.path.join(ROOT, "scripts", "brain_append.py")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class InstalledProjectsIgnoreTheRetrievalIndex(unittest.TestCase):
    """memory/mneme_index.json stores text[:240] of the CROSS-PROJECT brain.

    Unignored, a lesson harvested on one client's work gets committed into the
    next client's repository by an ordinary `git add -A`.
    """

    def _check_ignore(self, relpath):
        """True if git, using ONLY the curated list, ignores relpath."""
        with tempfile.TemporaryDirectory() as d:
            subprocess.run(["git", "init", "-q", "."], cwd=d, check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            with open(CURATED_IGNORE, encoding="utf-8") as src:
                with open(os.path.join(d, ".gitignore"), "w", encoding="utf-8") as dst:
                    dst.write(src.read())
            full = os.path.join(d, relpath)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as fh:
                fh.write("{}")
            done = subprocess.run(["git", "check-ignore", "-q", relpath], cwd=d)
            return done.returncode == 0

    def test_the_retrieval_index_is_ignored_at_any_depth(self):
        for path in ("memory/mneme_index.json",
                     "nested/deeper/memory/mneme_index.json"):
            with self.subTest(path=path):
                self.assertTrue(
                    self._check_ignore(path),
                    "%s would be committed into a user's project" % path)

    def test_a_users_own_json_under_memory_is_still_tracked(self):
        """The control. Restoring a blanket `memory/*.json` would untrack real work."""
        for path in ("memory/my_own_notes.json", "app/index.json"):
            with self.subTest(path=path):
                self.assertFalse(
                    self._check_ignore(path),
                    "%s was over-matched; the curated list must not untrack "
                    "a user's own files" % path)


class ARunSlugIsADirectoryNameNotAPath(unittest.TestCase):
    """os.path.join(RUNS, slug) accepts '../..' and discards RUNS for an
    absolute slug, so a run could be scaffolded anywhere on disk while the
    command printed success and --reindex never saw it."""

    ESCAPES = ["../../../tmp/escape", "/tmp/absolute", "a/b", "..", ".", "",
               "-startsWithDash", "has space"]

    def setUp(self):
        self.nr = _load("new_run_slug", NEW_RUN)

    def test_traversal_and_absolute_slugs_are_refused(self):
        for slug in self.ESCAPES:
            with self.subTest(slug=slug):
                self.assertEqual(
                    self.nr._reject_unsafe_slug(slug), 2,
                    "slug %r was accepted; it can escape runs/" % slug)

    def test_ordinary_slugs_are_still_accepted(self):
        """The control: an over-tightened guard that refuses everything is
        just as broken as no guard.

        `_selftest_tmp_run` and `_probe` are not hypothetical -- they are the
        slugs this module's OWN selftest scaffolds, and --reindex deliberately
        skips `slug.startswith("_")` so scratch runs stay out of INDEX.md. The
        first version of this guard excluded a leading underscore from the
        opening character class and broke the selftest, which this control did
        not catch because its inputs were invented rather than taken from the
        code being guarded.
        """
        for slug in ("my-run", "run_2", "2026-07-26-audit", "a", "A.b-c_1",
                     "_selftest_tmp_run", "_probe"):
            with self.subTest(slug=slug):
                self.assertEqual(
                    self.nr._reject_unsafe_slug(slug), 0,
                    "legitimate slug %r was refused" % slug)

    def test_the_modules_own_selftest_still_passes(self):
        """The end-to-end control: whatever the guard does, `new_run.py
        --selftest` must still scaffold, validate and clean up its probe runs."""
        proc = subprocess.run([sys.executable, NEW_RUN, "--selftest"],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0,
                         "new_run --selftest broke:\n%s" % (proc.stdout + proc.stderr))

    def test_scaffold_creates_nothing_when_the_slug_escapes(self):
        with tempfile.TemporaryDirectory() as d:
            runs = os.path.join(d, "runs")
            os.makedirs(runs)
            self.nr.RUNS = runs
            target = os.path.join(d, "escaped")
            rc = self.nr.scaffold(os.path.join("..", "escaped"), tier="solo")
            self.assertEqual(rc, 2)
            self.assertFalse(os.path.exists(target),
                             "scaffold wrote outside runs/")
            self.assertEqual(os.listdir(runs), [],
                             "scaffold left something behind in runs/")


class BrainAppendNeverWeldsOntoATruncatedTail(unittest.TestCase):
    """A crash mid-write leaves a partial line with no newline. Appending to it
    merges two records into one unparseable line, and every reader skips
    unparseable lines silently, so the loss is permanent and invisible."""

    NEW = {"id": "new-1", "text": "THE NEW LESSON I JUST SAVED"}

    def _append_into(self, existing):
        with tempfile.TemporaryDirectory() as d:
            brain = os.path.join(d, "b.jsonl")
            locks = os.path.join(d, "locks")
            os.makedirs(locks)
            with open(brain, "w", encoding="utf-8") as fh:
                fh.write(existing)
            env = dict(os.environ,
                       PROJECT_OS_SHARED_BRAIN=brain, BB_LOCK_DIR=locks)
            proc = subprocess.run(
                [sys.executable, BRAIN_APPEND, "--line", json.dumps(self.NEW),
                 "--no-reindex"],
                cwd=ROOT, env=env, capture_output=True, text=True)
            with open(brain, encoding="utf-8") as fh:
                lines = [ln for ln in fh.read().splitlines() if ln.strip()]
            ids = []
            for ln in lines:
                try:
                    ids.append(json.loads(ln).get("id"))
                except ValueError:
                    ids.append(None)          # unparseable
            return proc, ids

    def test_the_new_record_survives_a_crash_truncated_last_line(self):
        proc, ids = self._append_into(
            '{"id":"good-1","text":"first"}\n{"id":"partial","text":"tru')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("new-1", ids,
                      "the record being saved was welded onto the damaged tail "
                      "and lost; ids on disk: %r" % (ids,))
        self.assertIn("good-1", ids, "an intact earlier record was damaged")

    def test_a_normal_append_still_adds_exactly_one_line(self):
        """The control: don't fix the crash case by inserting stray blank lines."""
        proc, ids = self._append_into('{"id":"good-1","text":"first"}\n')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(ids, ["good-1", "new-1"])

    def test_appending_to_an_empty_brain_works(self):
        proc, ids = self._append_into("")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(ids, ["new-1"])


if __name__ == "__main__":
    unittest.main()
