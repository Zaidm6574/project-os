#!/usr/bin/env python3
"""Regression guards for two b4cc70f fixes in score_rubric.py.

1. Per-criterion range check (b4cc70f): individual score/weight values were
   never range-checked, so e.g. weight=-0.5 paired with weight=1.5 elsewhere
   still summed to ~1.0 while pushing the weighted total (and the verdict)
   outside [0, 1].  An out-of-range criterion must raise ValueError instead of
   producing a confident Pass/Reject.

2. assert -> ValueError conversion (b4cc70f): the weights-sum guard used to be
   a bare ``assert``, which is stripped under ``python -O`` /
   ``PYTHONOPTIMIZE=1``, silently letting a bogus weight sum through to a
   confident verdict.  Because assert statements are compiled away under -O,
   the ONLY faithful regression test runs the scorer in a subprocess with
   PYTHONOPTIMIZE=1 and asserts the ValueError still fires there.  An
   in-process test without -O cannot distinguish the two implementations.

Both tests exercise the shipped module file (addons/full-engine/memory/
score_rubric.py) directly -- no fixture copy of the logic.
"""

import importlib.util
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCORE_RUBRIC = ROOT / "addons" / "full-engine" / "memory" / "score_rubric.py"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class PerCriterionRangeCheck(unittest.TestCase):
    """Fix 1: any single out-of-range score or weight must be rejected."""

    @classmethod
    def setUpClass(cls):
        cls.sr = load("score_rubric_guards_20260726", SCORE_RUBRIC)

    def test_score_above_one_raises_value_error(self):
        payload = {
            "criteria": [
                {"name": "a", "score": 1.5, "weight": 0.5},
                {"name": "b", "score": 0.9, "weight": 0.5},
            ],
            "artifact_type": "doc",
        }
        with self.assertRaises(ValueError) as ctx:
            self.sr.score(payload)
        self.assertIn("out of range", str(ctx.exception))

    def test_negative_score_raises_value_error(self):
        payload = {
            "criteria": [
                {"name": "a", "score": -0.1, "weight": 0.5},
                {"name": "b", "score": 0.9, "weight": 0.5},
            ],
            "artifact_type": "doc",
        }
        with self.assertRaises(ValueError):
            self.sr.score(payload)

    def test_out_of_range_weights_that_sum_to_one_raise(self):
        # The exact bypass the fix names: -0.5 and 1.5 sum to ~1.0, so the
        # weights-sum guard alone lets them through, and the weighted total
        # (and verdict) leave [0, 1].
        payload = {
            "criteria": [
                {"name": "a", "score": 0.9, "weight": -0.5},
                {"name": "b", "score": 0.9, "weight": 1.5},
            ],
            "artifact_type": "doc",
        }
        with self.assertRaises(ValueError) as ctx:
            self.sr.score(payload)
        self.assertIn("out of range", str(ctx.exception))

    def test_in_range_payload_still_scores(self):
        # Control: the range check must not reject a legitimate payload.
        verdict, total, _row, code = self.sr.score({
            "criteria": [
                {"name": "a", "score": 0.9, "weight": 0.5},
                {"name": "b", "score": 0.9, "weight": 0.5},
            ],
            "artifact_type": "doc",
        })
        self.assertEqual(verdict, "Pass")
        self.assertEqual(code, 0)
        self.assertAlmostEqual(total, 0.9)


class WeightsSumGuardSurvivesPythonOptimize(unittest.TestCase):
    """Fix 2: the weights-sum guard must fire even under PYTHONOPTIMIZE=1.

    A bare ``assert`` is compiled away under -O; the fix raises ValueError
    explicitly.  Run the shipped script in a subprocess with PYTHONOPTIMIZE=1:
    the reverted (assert) implementation lets the bad sum sail straight
    through to a clean exit-0 rubric row, the fixed one dies with ValueError.
    """

    # In-range criteria whose weights sum to 0.5, not ~1.0.  redteam and
    # strategy_change are supplied so that, if the guard is stripped, the
    # payload sails all the way through to a printed row and exit code 0 --
    # the "confident wrong answer" b4cc70f describes.
    BAD_SUM_PAYLOAD = {
        "criteria": [
            {"name": "a", "score": 0.9, "weight": 0.25},
            {"name": "b", "score": 0.9, "weight": 0.25},
        ],
        "artifact_type": "doc",
        "redteam": "Claim X fails if Y. Test: Z. Kill: <0.5.",
        "strategy_change": "Switch approach to W.",
    }

    def _run_optimized(self, payload):
        env = dict(os.environ)
        env["PYTHONOPTIMIZE"] = "1"
        return subprocess.run(
            [sys.executable, str(SCORE_RUBRIC)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=env,
            cwd=str(ROOT),
        )

    def test_bad_weight_sum_raises_value_error_under_optimize(self):
        proc = self._run_optimized(self.BAD_SUM_PAYLOAD)
        self.assertNotEqual(
            proc.returncode, 0,
            "bad weight sum sailed through under PYTHONOPTIMIZE=1: "
            "stdout=%r stderr=%r" % (proc.stdout, proc.stderr),
        )
        self.assertIn("ValueError", proc.stderr, proc.stderr)
        self.assertIn("weights must sum to ~1.0", proc.stderr, proc.stderr)
        # It must refuse, not emit a rubric verdict for the bogus input.
        self.assertNotIn("Log row:", proc.stdout, proc.stdout)

    def test_good_payload_still_scores_under_optimize(self):
        # Control: proves the subprocess harness itself works under -O, so
        # the failure above is the guard firing, not a broken interpreter.
        good = {
            "criteria": [
                {"name": "a", "score": 0.9, "weight": 0.5},
                {"name": "b", "score": 0.9, "weight": 0.5},
            ],
            "artifact_type": "doc",
        }
        proc = self._run_optimized(good)
        self.assertEqual(proc.returncode, 0, (proc.stdout, proc.stderr))
        self.assertIn("Verdict: Pass", proc.stdout, proc.stdout)


if __name__ == "__main__":
    unittest.main()
