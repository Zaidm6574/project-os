"""Regression guards: cost_rollup must not silently drop an unreadable run.

addons/full-engine/memory/cost_rollup.py builds the portfolio "All runs" grand
total by scanning runs/*/09-cost-estimate.md. Its ``except OSError: continue``
conflated two very different situations:

  * the run has no 09-cost-estimate.md at all — legitimately skipped, the
    docstring says "every run with parseable actuals"; and
  * the file EXISTS but could not be opened/read (mode 000, an I/O error, a
    directory in its place) — the run's measured spend is real, it just could
    not be read, so dropping it prints an understated grand total.

Repro (2026-07-27): three runs at $100/$250/$400 render
``| **All runs** | $750.0000 | **$750.0000** |``; ``chmod 000`` on one run's
cost file renders ``| **All runs** | $350.0000 | **$350.0000** |`` — a 53%
understatement, no footnote, nothing on stderr, exit 0.

Second, related silent success: ``--runs /does/not/exist`` printed
"No runs with recorded actuals found under ..." and exited 0, so a typo'd path
was indistinguishable from a genuinely empty portfolio.

Sibling addons/full-engine/memory/cost_actuals.py already tracks an
``unreadable`` list and refuses rather than reporting a confident number; these
tests pin the same doctrine for the rollup.

Every test drives the real callable (module function or CLI) and each was
mutation-verified: restoring ``except OSError: continue`` (and the exit-0
missing-dir path) makes exactly these tests FAIL. The mirror-direction tests
below exist so the fix cannot be "make everything an error": a run with no cost
file, and an existing-but-empty runs directory, must both stay silent/exit 0.
Stdlib only, passes on the 3.9.6 CI floor.
"""

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COST_ROLLUP = os.path.join(ROOT, "addons", "full-engine", "memory", "cost_rollup.py")

IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0

ACTUALS = (
    "# 09 - Cost estimate\n\n"
    "## Actuals\n"
    "<!-- ACTUALS:START -->\n"
    "| Model | Est $ | Measured $ | Variance |\n"
    "|---|---|---|---|\n"
    "| Main loop / orchestrator (Opus) | - | $%.4f | - |\n"
    "| **Total** | - | **$%.4f** | - |\n"
    "<!-- ACTUALS:END -->\n"
)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_run(base, slug, amount):
    """Write one run directory holding a well-formed ACTUALS block."""
    d = os.path.join(base, slug)
    os.makedirs(d)
    path = os.path.join(d, "09-cost-estimate.md")
    with open(path, "w") as fh:
        fh.write(ACTUALS % (amount, amount))
    return path


def _portfolio(base):
    """Three readable runs: $100 + $250 + $400 = $750."""
    return {
        "run-a": _make_run(base, "run-a", 100.0),
        "run-b": _make_run(base, "run-b", 250.0),
        "run-c": _make_run(base, "run-c", 400.0),
    }


def _cli(*args):
    return subprocess.run(
        [sys.executable, COST_ROLLUP] + list(args),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)


class TestRollupTracksUnreadableRun(unittest.TestCase):
    """rollup() must distinguish "no cost file" from "cost file unreadable"."""

    @unittest.skipIf(IS_ROOT, "root ignores mode 000, cannot make a file unreadable")
    def test_unreadable_cost_file_is_recorded_not_silently_dropped(self):
        mod = _load("cost_rollup_unreadable_under_test", COST_ROLLUP)
        with tempfile.TemporaryDirectory() as tmp:
            paths = _portfolio(tmp)
            os.chmod(paths["run-c"], 0o000)
            try:
                unreadable = []
                data = mod.rollup(tmp, unreadable)
            finally:
                os.chmod(paths["run-c"], 0o644)

        self.assertEqual(
            [os.path.basename(os.path.dirname(p)) for p in unreadable], ["run-c"],
            msg="a run whose 09-cost-estimate.md exists but cannot be read must "
                "be reported as unreadable, not dropped from the portfolio: "
                "unreadable=%r data=%r" % (unreadable, sorted(data)))
        # The other two runs are still rolled up - this is a report, not a halt.
        self.assertEqual(sorted(data), ["run-a", "run-b"])

    def test_directory_in_place_of_cost_file_is_recorded_unreadable(self):
        """Same branch as mode 000, but reproducible even when running as root."""
        mod = _load("cost_rollup_unreadable_dir_under_test", COST_ROLLUP)
        with tempfile.TemporaryDirectory() as tmp:
            _portfolio(tmp)
            os.remove(os.path.join(tmp, "run-c", "09-cost-estimate.md"))
            os.makedirs(os.path.join(tmp, "run-c", "09-cost-estimate.md"))
            unreadable = []
            data = mod.rollup(tmp, unreadable)

        self.assertEqual(
            [os.path.basename(os.path.dirname(p)) for p in unreadable], ["run-c"],
            msg="09-cost-estimate.md exists (as a directory) so it is not a "
                "missing-cost-file run; it must be reported unreadable: "
                "unreadable=%r data=%r" % (unreadable, sorted(data)))
        self.assertEqual(sorted(data), ["run-a", "run-b"])

    def test_run_without_a_cost_file_is_still_legitimately_skipped(self):
        """Mirror direction: the fix must not turn a normal skip into an error."""
        mod = _load("cost_rollup_missing_under_test", COST_ROLLUP)
        with tempfile.TemporaryDirectory() as tmp:
            _make_run(tmp, "run-a", 100.0)
            os.makedirs(os.path.join(tmp, "run-no-costs"))  # no 09-cost-estimate.md
            unreadable = []
            data = mod.rollup(tmp, unreadable)

        self.assertEqual(unreadable, [],
                         msg="a run that simply has no 09-cost-estimate.md is "
                             "not unreadable - it has nothing to read")
        self.assertEqual(sorted(data), ["run-a"])

    def test_rollup_still_callable_with_one_argument(self):
        """Existing callers (and tests) pass only runs_dir; keep that working."""
        mod = _load("cost_rollup_arity_under_test", COST_ROLLUP)
        with tempfile.TemporaryDirectory() as tmp:
            _portfolio(tmp)
            data = mod.rollup(tmp)
        self.assertEqual(sorted(data), ["run-a", "run-b", "run-c"])


class TestCostRollupCliUnreadableRun(unittest.TestCase):
    """The rendered report itself must carry the caveat, and the CLI must fail."""

    @unittest.skipIf(IS_ROOT, "root ignores mode 000, cannot make a file unreadable")
    def test_cli_footnotes_the_table_and_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _portfolio(tmp)
            os.chmod(paths["run-c"], 0o000)
            try:
                r = _cli("--runs", tmp)
            finally:
                os.chmod(paths["run-c"], 0o644)

        combined = r.stdout + r.stderr
        self.assertIn("$350.0000", r.stdout,
                      msg="readable runs should still roll up: %r" % (r.stdout,))
        self.assertIn(
            "run-c", combined,
            msg="the unreadable run must be named somewhere in the output; "
                "stdout=%r stderr=%r" % (r.stdout, r.stderr))
        self.assertIn(
            "EXCLUDES", r.stdout,
            msg="the grand total is understated by an unreadable run, so the "
                "RENDERED TABLE (which gets pasted into reports) must say so, "
                "not just stderr; stdout=%r" % (r.stdout,))
        self.assertNotEqual(
            0, r.returncode,
            msg="an understated portfolio total must not exit 0; "
                "stdout=%r stderr=%r" % (r.stdout, r.stderr))

    def test_cli_is_silent_and_exits_zero_when_every_run_is_readable(self):
        """Mirror direction: no false alarm on a healthy portfolio."""
        with tempfile.TemporaryDirectory() as tmp:
            _portfolio(tmp)
            r = _cli("--runs", tmp)

        self.assertEqual(0, r.returncode,
                         msg="stdout=%r stderr=%r" % (r.stdout, r.stderr))
        self.assertIn("| **All runs** | $750.0000 | **$750.0000** |", r.stdout)
        self.assertNotIn("EXCLUDES", r.stdout)
        self.assertNotIn("UNREADABLE", r.stderr)


class TestCostRollupCliRunsDirMustExist(unittest.TestCase):

    def test_nonexistent_runs_dir_is_an_error_not_an_empty_portfolio(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "typo-does-not-exist")
            r = _cli("--runs", missing)

        self.assertNotEqual(
            0, r.returncode,
            msg="a typo'd --runs path must not be indistinguishable from an "
                "empty portfolio; stdout=%r stderr=%r" % (r.stdout, r.stderr))
        self.assertIn(missing, r.stdout + r.stderr)

    def test_existing_runs_dir_with_no_actuals_is_still_exit_zero(self):
        """Mirror direction: a genuinely empty portfolio is not an error."""
        with tempfile.TemporaryDirectory() as tmp:
            r = _cli("--runs", tmp)

        self.assertEqual(0, r.returncode,
                         msg="stdout=%r stderr=%r" % (r.stdout, r.stderr))
        self.assertIn("No runs with recorded actuals found", r.stdout)

    def test_selftest_still_passes(self):
        r = _cli("--selftest")
        self.assertEqual(0, r.returncode,
                         msg="stdout=%r stderr=%r" % (r.stdout, r.stderr))
        self.assertIn("cost_rollup selftest: OK", r.stdout)


if __name__ == "__main__":
    unittest.main()
