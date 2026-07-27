"""Regression tests for four unguarded crash-hardening one-liners from b4cc70f.

The 2026-07-26 handoff notes b4cc70f's "four crash-hardening one-liners" are
fix-present / test-owed. Each test here feeds the exact ordinary-bad-input the
hunk defends against through the real callable (module function or CLI) and
asserts the hardened behavior:

  1. scripts/os_nightly.py  stuck_plans(): a plan file holding valid JSON of
     the wrong shape (e.g. ``[]``) used to raise AttributeError and kill the
     whole nightly heartbeat; it must be reported "(unreadable)" while the
     other plans are still swept.
  2. scripts/brain_append.py  flag(): a bare trailing flag (``--line`` with no
     value) used to IndexError with a traceback; it must be a clean exit-2
     usage error, and a value merely starting with ``--`` must still be
     accepted.
  3. addons/full-engine/memory/cost_rollup.py  _parse_actuals(): a blank Model
     cell used to be mistaken for a table separator (``set("") <= set("-: ")``
     is True) and its measured $ silently dropped from the rollup.
  4. scripts/import_chat_history.py  write_summary(): re-running the importer
     used to truncate a pre-existing report with no backup; it must copy the
     prior bytes to ``<name>.bak`` before rewriting.

Every test was mutation-verified: hand-reverting the specific b4cc70f hunk it
guards (and only that hunk) in a scratch checkout makes exactly that test fail.
All fixtures live in tempfile sandboxes; the live blackboard/, memory/, and
runs/ trees are never written. Stdlib only — passes on the 3.9.6 CI floor.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
COST_ROLLUP = ROOT / "addons" / "full-engine" / "memory" / "cost_rollup.py"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestOsNightlyStuckPlansSurvivesNonDictPlan(unittest.TestCase):
    """b4cc70f: except (OSError, ValueError) -> + AttributeError in stuck_plans."""

    def test_non_dict_plan_is_reported_unreadable_and_sweep_continues(self):
        nightly = _load("os_nightly_under_test", SCRIPTS / "os_nightly.py")
        with tempfile.TemporaryDirectory() as tmp:
            plans = Path(tmp) / "plans"
            plans.mkdir()
            # A real plan/v2-shaped stuck plan (plan_artifact.py schema).
            good = plans / "p-good.json"
            good.write_text(json.dumps({
                "schema": "plan/v2",
                "id": "p-good",
                "goal": "sweep me",
                "status": "running",
                "steps": [
                    {"id": "s1", "role": "builder", "task": "t1", "done": True},
                    {"id": "s2", "role": "builder", "task": "t2", "done": False},
                ],
            }), encoding="utf-8")
            # Valid JSON, wrong shape: json.load succeeds, .get() does not exist.
            bad = plans / "p-bad.json"
            bad.write_text("[]", encoding="utf-8")
            stale = time.time() - 30 * 86400  # well past STALE_DAYS
            for fp in (good, bad):
                os.utime(fp, (stale, stale))

            original = nightly.PLANS
            nightly.PLANS = str(plans)
            try:
                out = nightly.stuck_plans()  # must not raise AttributeError
            finally:
                nightly.PLANS = original

        self.assertIn("p-bad.json (unreadable)", out,
                      "a non-dict plan file must be flagged, not crash the heartbeat")
        self.assertIn("p-good (1/2 steps)", out,
                      "the corrupt file must not stop the sweep of healthy plans")


class TestBrainAppendBareTrailingFlag(unittest.TestCase):
    """b4cc70f: brain_append.flag() missing-value guard (bare trailing flag
    used to IndexError; same fix already tested for bb_lock/plan_artifact but
    never for brain_append itself)."""

    def _env(self, tmp):
        env = dict(os.environ)
        env.update({
            "HOME": tmp,  # no stray ~/.project-os writes even on regression
            "PROJECT_OS_SHARED_BRAIN": str(Path(tmp) / "brain" / "shared-brain.jsonl"),
            "MNEME_INDEX": str(Path(tmp) / "mneme_index.json"),
            "MNEME_EMBEDDER": "lexical",
            "BB_LOCK_DIR": str(Path(tmp) / "locks"),
        })
        return env

    def _run(self, tmp, *argv):
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "brain_append.py"), *argv],
            capture_output=True, text=True, env=self._env(tmp),
            stdin=subprocess.DEVNULL, timeout=120)

    def test_bare_trailing_flag_is_a_clean_usage_error_not_a_traceback(self):
        cases = (
            ("--line",),
            ("--line", '{"kind":"lesson","text":"x"}', "--agent"),
        )
        for argv in cases:
            with self.subTest(argv=argv):
                with tempfile.TemporaryDirectory() as tmp:
                    r = self._run(tmp, *argv)
                    self.assertEqual(
                        r.returncode, 2,
                        "expected exit 2 usage error, got rc=%s\nstdout=%s\nstderr=%s"
                        % (r.returncode, r.stdout, r.stderr))
                    self.assertIn("missing value", r.stderr)
                    self.assertNotIn("Traceback", r.stderr)
                    self.assertFalse(
                        (Path(tmp) / "brain" / "shared-brain.jsonl").exists(),
                        "a usage error must not create or append the brain file")

    def test_value_starting_with_dashes_is_still_accepted(self):
        # Mirror direction: only the known flag tokens may be rejected as a
        # missing value — an --agent value that merely starts with "--" is
        # legitimate and the append must go through end-to-end.
        with tempfile.TemporaryDirectory() as tmp:
            r = self._run(tmp,
                          "--line", '{"kind":"lesson","text":"dashy agent ok"}',
                          "--agent", "--dashy-agent-name", "--no-reindex")
            self.assertEqual(r.returncode, 0,
                             "stdout=%s\nstderr=%s" % (r.stdout, r.stderr))
            brain = Path(tmp) / "brain" / "shared-brain.jsonl"
            self.assertTrue(brain.exists())
            rows = brain.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(rows), 1)
            self.assertEqual(json.loads(rows[0])["text"], "dashy agent ok")


class TestCostRollupBlankModelCell(unittest.TestCase):
    """b4cc70f: blank-label guard in _parse_actuals — set("") <= set("-: ") is
    True, so a blank Model cell was skipped as a separator and its measured $
    silently vanished from the rollup."""

    def test_blank_model_cell_dollars_are_counted_not_dropped(self):
        rollup_mod = _load("cost_rollup_under_test", COST_ROLLUP)
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run-blank-label"
            run_dir.mkdir()
            # Same table shape cost_actuals.py --write emits into the block.
            (run_dir / "09-cost-estimate.md").write_text(
                "# 09 — Cost estimate\n\n"
                "## Actuals\n"
                "<!-- ACTUALS:START -->\n"
                "| Model | Est $ | Measured $ | Variance |\n"
                "|---|---|---|---|\n"
                "| Main loop / orchestrator (Opus) | — | $2.0000 | — |\n"
                "|  | — | $5.0000 | — |\n"
                "| **Total** | — | **$7.0000** | — |\n"
                "<!-- ACTUALS:END -->\n",
                encoding="utf-8")
            result = rollup_mod.rollup(str(tmp))

        per = result.get("run-blank-label", {})
        self.assertAlmostEqual(per.get("opus", 0.0), 2.0)
        self.assertAlmostEqual(
            per.get("other", 0.0), 5.0,
            msg="a blank Model cell is a data row, not a separator — its "
                "measured $ must be counted, not silently dropped")
        # Mirror direction: header/separator/Total rows must all still be
        # skipped, so the run sums to exactly the two data rows.
        self.assertAlmostEqual(sum(per.values()), 7.0)
        self.assertEqual(set(per), {"opus", "other"})


class TestImportChatHistoryBacksUpPriorReport(unittest.TestCase):
    """b4cc70f: write_summary backs up any pre-existing report to <name>.bak
    before the unconditional open("w") truncates it."""

    def test_existing_report_is_backed_up_before_truncation(self):
        with tempfile.TemporaryDirectory() as tmp:
            export = Path(tmp) / "export.txt"
            export.write_text(
                "I want a small focused tracker\n"
                "build a website for the shop\n"
                "stuck on the login bug\n",
                encoding="utf-8")
            output = Path(tmp) / "private-memory" / "chat-memory.md"
            output.parent.mkdir(parents=True)
            prior = "# Hand-curated notes\n\nkeep these safe\n"
            output.write_text(prior, encoding="utf-8")

            r = subprocess.run(
                [sys.executable, str(SCRIPTS / "import_chat_history.py"),
                 "--input", str(export), "--output", str(output)],
                capture_output=True, text=True,
                stdin=subprocess.DEVNULL, timeout=120)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

            backup = output.with_name(output.name + ".bak")
            self.assertTrue(
                backup.exists(),
                "re-running the importer must back up the prior report "
                "before truncating it")
            self.assertEqual(backup.read_text(encoding="utf-8"), prior,
                             "the backup must hold the prior report verbatim")
            # And the report itself really was regenerated, not left stale.
            self.assertTrue(
                output.read_text(encoding="utf-8").startswith(
                    "# Private Chat Memory Summary"))

    def test_first_run_creates_no_spurious_backup(self):
        # Mirror direction: with nothing to protect there must be no .bak.
        with tempfile.TemporaryDirectory() as tmp:
            export = Path(tmp) / "export.txt"
            export.write_text("I want one clean report\n", encoding="utf-8")
            output = Path(tmp) / "private-memory" / "chat-memory.md"
            r = subprocess.run(
                [sys.executable, str(SCRIPTS / "import_chat_history.py"),
                 "--input", str(export), "--output", str(output)],
                capture_output=True, text=True,
                stdin=subprocess.DEVNULL, timeout=120)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertTrue(output.exists())
            self.assertFalse(output.with_name(output.name + ".bak").exists())


if __name__ == "__main__":
    unittest.main()
