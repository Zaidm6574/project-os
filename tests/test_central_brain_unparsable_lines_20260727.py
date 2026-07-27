#!/usr/bin/env python3
"""central_brain must never drop an unparsable JSONL line in silence.

Repro (audit 2026-07-27): a 5-record project brain whose line 3 was
crash-truncated mid-write pushed as::

    central brain push: 4 lesson(s) added
    exit=0

and ``status`` then reported ``4 lesson(s), 1 project(s)``. Lesson L3 never
reached the central brain and NOTHING said so -- ``read_jsonl`` skipped the
damaged line and the "(N record(s) skipped by privacy/type gate)" note is only
printed when the privacy gate itself skipped something, so a parse-dropped
record was invisible on every surface (push, pull, sync, status).

That is this repo's data-loss-looks-like-success signature. The sibling reader
of the SAME file format, memory/brain_fts_mirror.py::read_records, already
names the offending line number ("malformed JSONL at line {n}"), so lenience
here was a gap and not a house convention.

Skipping the line is still right -- brain.py::_heal_truncated_tail exists
precisely because a crash-truncated tail is expected, and one damaged record
must not take a whole sync down -- so the fix is a REPORT, not a refusal to
read: an always-printed warning naming the file and the line numbers, plus a
non-zero exit for the commands that were supposed to move that data.

Both directions are asserted: a clean brain must stay quiet and exit 0, or the
warning would be noise nobody reads.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CENTRAL_BRAIN = REPO / "addons" / "full-engine" / "brain" / "central_brain.py"

# One good lesson, a line cut mid-write (no closing brace), then more good ones.
TRUNCATED_LINE_NO = 3


def _lesson(rid: str) -> str:
    return json.dumps(
        {
            "id": rid,
            "type": "lesson",
            "source": "project-os",
            "text": "lesson %s that should have synced" % rid,
            "tags": [],
        }
    )


def _brain_with_truncated_line() -> str:
    lines = [_lesson("L%d" % i) for i in range(1, 6)]
    lines[TRUNCATED_LINE_NO - 1] = lines[TRUNCATED_LINE_NO - 1][:30]
    return "\n".join(lines) + "\n"


def _clean_brain() -> str:
    return "".join(_lesson("L%d" % i) + "\n" for i in range(1, 6))


def _load_central_brain(name: str):
    spec = importlib.util.spec_from_file_location(name, str(CENTRAL_BRAIN))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class CentralBrainCliCase(unittest.TestCase):
    """Drives the SHIPPED central_brain.py as a subprocess, HOME isolated."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="central-brain-unparsable-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.central = self.tmp / "central"
        self.project = self.tmp / "proj"
        (self.project / "brain").mkdir(parents=True)
        self.project_brain = self.project / "brain" / "shared-brain.jsonl"
        self.central_brain_file = self.central / "shared-brain.jsonl"
        self.env = {
            "HOME": str(self.home),
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LC_ALL": "C",
        }

    def run_cli(self, *argv):
        done = subprocess.run(
            [sys.executable, str(CENTRAL_BRAIN)] + list(argv),
            capture_output=True,
            text=True,
            env=self.env,
            cwd=str(self.tmp),
            timeout=120,
        )
        return done, (done.stdout or "") + (done.stderr or "")


class PushSurfacesUnparsableLines(CentralBrainCliCase):
    def test_push_names_the_dropped_line_and_exits_non_zero(self):
        self.project_brain.write_text(_brain_with_truncated_line(), encoding="utf-8")

        done, out = self.run_cli(
            "push", "--path", str(self.central),
            "--project", str(self.project), "--project-id", "demo",
        )

        # The lesson really is gone from the central brain ...
        synced = [
            json.loads(line)
            for line in self.central_brain_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(len(synced), 4, out)
        # ... so the command must say so, name the file and the line, and fail.
        self.assertIn(str(self.project_brain), out)
        self.assertRegex(out, r"line[^\n]*\b3\b")
        self.assertRegex(out.lower(), r"unparsable|unparseable|malformed")
        self.assertNotEqual(
            done.returncode, 0,
            "push dropped a lesson and still exited 0:\n" + out,
        )
        # The existing success line must not regress.
        self.assertIn("4 lesson(s) added", out)

    def test_push_on_a_clean_brain_stays_quiet_and_exits_zero(self):
        self.project_brain.write_text(_clean_brain(), encoding="utf-8")

        done, out = self.run_cli(
            "push", "--path", str(self.central),
            "--project", str(self.project), "--project-id", "demo",
        )

        self.assertEqual(done.returncode, 0, out)
        self.assertIn("5 lesson(s) added", out)
        self.assertNotRegex(out.lower(), r"unparsable|unparseable|malformed")


class PullSyncAndStatusSurfaceUnparsableLines(CentralBrainCliCase):
    def _seed_central_with_a_truncated_line(self):
        self.central.mkdir(parents=True, exist_ok=True)
        records = [
            {
                "id": "demo/c%d" % i,
                "project_id": "other",
                "origin_id": "c%d" % i,
                "type": "lesson",
                "source": "project-os",
                "text": "central lesson %d" % i,
                "tags": [],
            }
            for i in range(1, 6)
        ]
        lines = [json.dumps(r) for r in records]
        lines[TRUNCATED_LINE_NO - 1] = lines[TRUNCATED_LINE_NO - 1][:30]
        self.central_brain_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_status_reports_the_unparsable_line_it_did_not_count(self):
        self._seed_central_with_a_truncated_line()

        done, out = self.run_cli("status", "--path", str(self.central))

        self.assertIn("4 lesson(s)", out)
        self.assertIn(str(self.central_brain_file), out)
        self.assertRegex(out, r"line[^\n]*\b3\b")
        self.assertNotEqual(
            done.returncode, 0,
            "status reported a knowingly short count and exited 0:\n" + out,
        )

    def test_status_on_a_clean_central_brain_stays_quiet_and_exits_zero(self):
        self.project_brain.write_text(_clean_brain(), encoding="utf-8")
        self.run_cli(
            "push", "--path", str(self.central),
            "--project", str(self.project), "--project-id", "demo",
        )

        done, out = self.run_cli("status", "--path", str(self.central))

        self.assertEqual(done.returncode, 0, out)
        self.assertNotRegex(out.lower(), r"unparsable|unparseable|malformed")

    def test_pull_reports_the_unparsable_central_line(self):
        self._seed_central_with_a_truncated_line()

        done, out = self.run_cli(
            "pull", "--path", str(self.central),
            "--project", str(self.project), "--project-id", "demo",
        )

        self.assertIn(str(self.central_brain_file), out)
        self.assertRegex(out, r"line[^\n]*\b3\b")
        self.assertNotEqual(
            done.returncode, 0,
            "pull silently skipped a central lesson and exited 0:\n" + out,
        )

    def test_sync_reports_damage_on_both_sides(self):
        self.project_brain.write_text(_brain_with_truncated_line(), encoding="utf-8")
        self._seed_central_with_a_truncated_line()

        done, out = self.run_cli(
            "sync", "--path", str(self.central),
            "--project", str(self.project), "--project-id", "demo",
        )

        self.assertIn(str(self.project_brain), out)
        self.assertIn(str(self.central_brain_file), out)
        self.assertNotEqual(done.returncode, 0, out)


class ReadJsonlReportsWhatItDropped(unittest.TestCase):
    """The reader itself must be able to hand back what it skipped."""

    def setUp(self):
        self.cb = _load_central_brain("central_brain_unparsable_probe")
        self.tmp = Path(tempfile.mkdtemp(prefix="central-brain-readjsonl-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)

    def test_line_numbers_of_dropped_lines_are_collected(self):
        path = self.tmp / "shared-brain.jsonl"
        path.write_text(
            _lesson("L1") + "\n"
            + "not json at all\n"
            + "\n"
            + '["a list is not a record"]\n'
            + _lesson("L5") + "\n",
            encoding="utf-8",
        )

        dropped: list = []
        records = self.cb.read_jsonl(path, dropped)

        self.assertEqual([r["id"] for r in records], ["L1", "L5"])
        self.assertEqual(dropped, [2, 4])

    def test_default_call_still_returns_a_plain_list_of_records(self):
        path = self.tmp / "shared-brain.jsonl"
        path.write_text(_lesson("L1") + "\n", encoding="utf-8")

        records = self.cb.read_jsonl(path)

        self.assertIsInstance(records, list)
        self.assertEqual([r["id"] for r in records], ["L1"])


if __name__ == "__main__":
    unittest.main()
