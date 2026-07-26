"""Regression guards for b4cc70f fixes that shipped with NO test.

b4cc70f landed 47 fixes and zero tests. An Opus adversary round on 2026-07-26
mutation-tested the files an earlier lens never reached and found that reverting
these guards leaves the full suite green — so a future edit could silently undo
any of them. The fixes are correct at HEAD; these tests make a revert FAIL.

Each test reproduces the guard's job through the real callable and asserts the
guarded behaviour, so deleting the guard breaks the test (mutation-verified when
written). Covered here: the critical cost-measurement exclusion and the two
path-traversal guards. The remaining b4cc70f guards from the same round (the
brain_archive re-read-under-lock, the unreadable-transcript refusal, and the
medium/low crash-hardening hunks) are harder to drive deterministically and are
logged as test-owed in the project-os handoff.
"""

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE_MEMORY = os.path.join(ROOT, "addons", "full-engine", "memory")
SCRIPTS = os.path.join(ROOT, "scripts")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CostActualsExcludesSubagentTranscripts(unittest.TestCase):
    """The 5000x-understatement guard: a subagent .jsonl is newer than the
    orchestrator's main file, so an unfiltered mtime-max picks it and measures
    only that one subagent -- a silent lie behind a 'Measured $' table."""

    def _find_latest_under(self, home):
        cost = _load("cost_actuals_sub", os.path.join(ENGINE_MEMORY, "cost_actuals.py"))
        # _find_latest_jsonl reads pathlib.Path.home(); point HOME at the sandbox.
        old = os.environ.get("HOME")
        os.environ["HOME"] = str(home)
        try:
            return cost._find_latest_jsonl()
        finally:
            if old is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old

    def test_a_newer_subagent_file_is_not_chosen_as_the_main_transcript(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            proj = home / ".claude" / "projects" / "p1"
            (proj / "subagents").mkdir(parents=True)
            main = proj / "main.jsonl"
            sub = proj / "subagents" / "sub.jsonl"
            main.write_text("{}\n")
            sub.write_text("{}\n")
            # Make the subagent file the newest by mtime -- the exact trap.
            os.utime(main, (1_000, 1_000))
            os.utime(sub, (2_000, 2_000))

            chosen = self._find_latest_under(home)
            self.assertEqual(
                chosen, main,
                "auto-detect chose the subagent transcript -- the orchestrator "
                "loop would go unmeasured and the total silently understated",
            )

    def test_a_subagent_only_tree_reports_no_main_file_rather_than_lying(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            proj = home / ".claude" / "projects" / "p1"
            (proj / "subagents").mkdir(parents=True)
            (proj / "subagents" / "sub.jsonl").write_text("{}\n")
            with self.assertRaises(FileNotFoundError):
                self._find_latest_under(home)


class EvolutionRunIsASinglePathSegment(unittest.TestCase):
    """`record --run ../../tmp/x` scaffolded evolution files outside runs/."""

    def setUp(self):
        self.evo = _load("evolution_guard", os.path.join(SCRIPTS, "evolution.py"))

    def test_traversal_and_absolute_run_names_are_refused(self):
        for bad in ("../../../../tmp/evil-evo", "/tmp/fgabs", "a/b", "..",
                    "sub\\dir"):
            with self.subTest(run=bad):
                with self.assertRaises(SystemExit) as ctx:
                    self.evo.paths(bad)
                self.assertEqual(ctx.exception.code, 2)

    def test_a_plain_run_name_resolves_inside_runs(self):
        d, jf, mf = self.evo.paths("20260726-real-run")
        runs_root = os.path.join(self.evo.ROOT, "runs")
        self.assertEqual(os.path.dirname(d), runs_root)
        self.assertTrue(jf.endswith(os.path.join("20260726-real-run", "evolution.json")))


class WorktreeNameCannotEscapeItsRoot(unittest.TestCase):
    """`wt create ../../../EVIL/sub` os.makedirs'd its way out before git ran."""

    def setUp(self):
        self.wt = _load("wt_guard", os.path.join(SCRIPTS, "wt.py"))

    def test_escaping_names_are_refused(self):
        for bad in ("../../../../../EVIL-WT/sub", "../sibling", "a/../../b"):
            with self.subTest(name=bad):
                with self.assertRaises(SystemExit):
                    self.wt.wt_dir("/some/repo", bad)

    def test_a_contained_name_is_allowed(self):
        got = self.wt.wt_dir("/some/repo", "feature-x")
        base = os.path.join(self.wt.HOME, ".project-os", "worktrees", "repo")
        self.assertEqual(os.path.commonpath([base, got]), os.path.normpath(base))


if __name__ == "__main__":
    unittest.main()
