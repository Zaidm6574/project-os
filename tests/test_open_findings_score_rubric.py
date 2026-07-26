#!/usr/bin/env python3
"""Regression tests for score_rubric's artifact_type normalization.

Defect (2026-07-26): score() normalized artifact_type into artifact_type_norm
and the mandatory pass@k refusal gate used the normalized value, but the row
renderer compared the RAW value:

    passk_cell = passk if artifact_type == "executable" else "N/A"

So "Executable" or " executable " cleared the refusal gate carrying a real
k/pass-count and then emitted a rubric row whose pass@k cell said "N/A" — the
receipt understated what had actually been verified. The gate and the row must
agree on one normalized value.
"""

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCORE_RUBRIC = ROOT / "addons" / "full-engine" / "memory" / "score_rubric.py"

CRITERIA = [
    {"name": "a", "score": 0.9, "weight": 0.5},
    {"name": "b", "score": 0.9, "weight": 0.5},
]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class PassKCellUsesNormalizedArtifactType(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sr = load("score_rubric_open_findings", SCORE_RUBRIC)

    def _score(self, artifact_type, passk="3/3"):
        payload = {
            "criteria": [dict(c) for c in CRITERIA],
            "artifact_type": artifact_type,
        }
        if passk is not None:
            payload["passk"] = passk
        return self.sr.score(payload)

    def test_case_and_whitespace_variants_render_the_passk_cell(self):
        """The row must carry the real k/pass-count, not "N/A"."""
        for variant in ("executable", "Executable", "EXECUTABLE",
                        " executable ", "\texecutable\n"):
            verdict, _total, row, code = self._score(variant)
            self.assertEqual(code, 0, (variant, row))
            self.assertEqual(verdict, "Pass", (variant, row))
            self.assertIn(
                "3/3", row,
                "artifact_type %r cleared the pass@k gate but the row dropped "
                "the pass-count: %r" % (variant, row),
            )
            self.assertNotIn(
                "N/A", row,
                "artifact_type %r emitted an N/A pass@k cell: %r"
                % (variant, row),
            )

    def test_gate_and_row_agree_on_the_same_normalization(self):
        """Whatever clears the refusal gate must also render pass@k."""
        for variant in ("Executable", " executable "):
            # Same variant with no passk must still be refused (exit 3): if it
            # is gated as executable, it must also be rendered as executable.
            _v, _t, msg, code = self._score(variant, passk=None)
            self.assertEqual(code, 3, (variant, msg))

            _v, _t, row, code = self._score(variant, passk="5/5")
            self.assertEqual(code, 0, (variant, row))
            self.assertIn("5/5", row, (variant, row))

    def test_non_executable_still_renders_na(self):
        """The fix must not turn every artifact into an executable row."""
        for variant in ("doc", "Doc", " DOC ", "notebook"):
            _v, _t, row, code = self._score(variant, passk="3/3")
            self.assertEqual(code, 0, (variant, row))
            self.assertIn("N/A", row, (variant, row))
            self.assertNotIn("3/3", row, (variant, row))

    def test_missing_artifact_type_defaults_to_doc_row(self):
        _v, _t, row, code = self.sr.score({
            "criteria": [dict(c) for c in CRITERIA],
        })
        self.assertEqual(code, 0, row)
        self.assertIn("N/A", row)


class CliEmitsThePassKCell(unittest.TestCase):
    """End-to-end through the stdin CLI, not just the function."""

    def _run(self, payload):
        return subprocess.run(
            [sys.executable, str(SCORE_RUBRIC)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
        )

    def test_capitalized_executable_row_reports_passk(self):
        proc = self._run({
            "criteria": [dict(c) for c in CRITERIA],
            "artifact_type": "Executable",
            "passk": "3/3",
        })
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("3/3", proc.stdout, proc.stdout)
        self.assertNotIn("N/A", proc.stdout, proc.stdout)

    def test_selftest_entry_point_passes(self):
        proc = subprocess.run(
            [sys.executable, str(SCORE_RUBRIC), "--selftest"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("OK", proc.stdout)


if __name__ == "__main__":
    unittest.main()
