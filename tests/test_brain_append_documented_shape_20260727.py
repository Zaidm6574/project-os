#!/usr/bin/env python3
"""The example in brain_append's --help must write a record canon can READ.

Audit finding (2026-07-27). `scripts/brain_append.py` is the doctrine-mandated
shared-brain write path (AGENTS.md: "Append lessons via scripts/brain_append.py"),
and its module docstring is also its `--help` / usage text -- it is printed
verbatim whenever the command is run with no line. That text told operators to
append::

    {"kind":"lesson","text":"..."}

Nothing in canon reads `kind`. `memory/validate_run.py:_valid_jsonl_record`
requires nonempty id/type/text and fails the WHOLE brain file (and with it the
"Graph/memory artifact present" closure check) if any record misses them;
`brain/central_brain.py` gates on `record.get("type") != "lesson"`. So following
the shipped example produced a row that every reader drops and that silently
flipped an already-green run to FAIL. The one tolerant reader in the tree is
`addons/full-engine/memory/brain_fts_mirror.py:94`.

The confusion has a real root: brain.py's CLI FLAG is `--kind` (brain.py:655)
but it writes `"type": args.kind` (brain.py:573). The docstring copied the flag
name into the record shape.

Two forcing functions live here:

1. DOC CONTRACT -- every JSON object literal in brain_append's docstring is
   extracted, pushed through the REAL brain_append subprocess, and then handed
   to the REAL canon readers (validate_run._has_graph_or_memory and
   central_brain.lessons_for_central). Asserting on the docstring's text would
   be a substring test of the kind this repo has already been burned by; these
   run the writer and the readers, so any future example that canon cannot read
   turns them red no matter how it is spelled.

2. NO SILENT DROP -- brain_append must say so, on stderr, when the record it is
   about to append is missing the fields canon requires. The append still
   succeeds (this is a warning, not a new gate: harvest.py and every other
   caller keep working), but the operator no longer has to discover the loss
   through central_brain reporting it as a "privacy/type gate" skip, which
   reads like a privacy event rather than a schema typo. A canonical record
   must NOT warn -- that control is what stops "warn on everything" from
   passing.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRAIN_APPEND = ROOT / "scripts" / "brain_append.py"
VALIDATE_RUN = ROOT / "addons" / "full-engine" / "memory" / "validate_run.py"
CENTRAL_BRAIN = ROOT / "addons" / "full-engine" / "brain" / "central_brain.py"

# Read, never written. Its job is to prove the sandbox redirect held: an
# unredirected probe once appended to the developer's real brain, which
# central_brain.py pull then fans out to every other project.
HOME_BRAIN = Path(os.path.expanduser("~/.project-os/central-brain/shared-brain.jsonl"))


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, module)
    spec.loader.exec_module(module)
    return module


def _documented_records() -> list[dict]:
    """Every JSON object literal in brain_append's module docstring."""
    doc = ast.get_docstring(ast.parse(BRAIN_APPEND.read_text(encoding="utf-8")))
    assert doc, "scripts/brain_append.py lost its module docstring (== its --help)"
    found = []
    for blob in re.findall(r"\{[^{}]*\}", doc):
        try:
            record = json.loads(blob)
        except ValueError:
            continue
        if isinstance(record, dict):
            found.append(record)
    assert found, "no JSON example left in brain_append's --help text to check"
    return found


def _sandbox_env(tmp: Path, brain_path: Path) -> dict:
    """Redirect BOTH of brain_append's HOME-rooted writes into tmp.

    PROJECT_OS_SHARED_BRAIN defaults to ~/.project-os/central-brain/... and
    BB_LOCK_DIR to ~/.project-os/locks, where the lock leaves a PERSISTENT
    <hash>.lock.guard behind.
    """
    env = dict(os.environ)
    env["PROJECT_OS_SHARED_BRAIN"] = str(brain_path)
    env["BB_LOCK_DIR"] = str(tmp / "locks")
    return env


class DocumentedExampleIsReadableByCanon(unittest.TestCase):
    """Run the shipped example through the real writer, then the real readers."""

    def setUp(self) -> None:
        self.validator = _load(VALIDATE_RUN, "validate_run_doc_shape")
        self.central = _load(CENTRAL_BRAIN, "central_brain_doc_shape")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home_brain_before = (
            HOME_BRAIN.read_bytes() if HOME_BRAIN.exists() else None
        )
        self.addCleanup(self._assert_home_brain_untouched)

    def _assert_home_brain_untouched(self) -> None:
        after = HOME_BRAIN.read_bytes() if HOME_BRAIN.exists() else None
        self.assertEqual(
            self.home_brain_before, after,
            "this probe wrote to the REAL shared brain; the sandbox redirect broke",
        )

    def _append(self, record: dict, project: Path):
        brain_path = project / "brain" / "shared-brain.jsonl"
        brain_path.parent.mkdir(parents=True, exist_ok=True)
        return subprocess.run(
            [sys.executable, str(BRAIN_APPEND), "--line", json.dumps(record),
             "--no-reindex"],
            capture_output=True, text=True, cwd=str(ROOT), check=False,
            env=_sandbox_env(project, brain_path),
        )

    def test_documented_example_keeps_the_run_validator_green(self) -> None:
        for i, record in enumerate(_documented_records()):
            with self.subTest(example=record):
                project = Path(self.tmp.name) / ("validator-%d" % i)
                run_dir = project / "runs" / "demo"
                run_dir.mkdir(parents=True)
                # A pre-existing, canon-valid lesson: the closure check is
                # already green before the documented example is appended, so a
                # failure below is the NEW row, not an empty brain.
                brain_path = project / "brain" / "shared-brain.jsonl"
                brain_path.parent.mkdir(parents=True)
                brain_path.write_text(
                    '{"id":"seed-1","type":"lesson","text":"Seed lesson"}\n',
                    encoding="utf-8",
                )
                self.assertTrue(
                    self.validator._has_graph_or_memory(str(run_dir)),
                    "fixture is wrong: the closure check was already red",
                )

                proc = self._append(record, project)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

                self.assertTrue(
                    self.validator._has_graph_or_memory(str(run_dir)),
                    "the documented brain_append example %r made "
                    "memory/validate_run.py reject the whole brain file, so "
                    "'Graph/memory artifact present' flips to [ ] and the run "
                    "cannot close. Records need nonempty id/type/text."
                    % (record,),
                )

    def test_documented_example_reaches_the_central_brain(self) -> None:
        for i, record in enumerate(_documented_records()):
            with self.subTest(example=record):
                project = Path(self.tmp.name) / ("central-%d" % i)
                project.mkdir(parents=True)

                proc = self._append(record, project)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

                lessons, skipped = self.central.lessons_for_central(project, "demo")
                self.assertEqual(
                    (len(lessons), skipped), (1, 0),
                    "central_brain.py dropped the documented brain_append "
                    "example %r and counted it as 'skipped by privacy/type "
                    "gate' -- which reads as a privacy event, not a schema "
                    "typo. Records need type == 'lesson'." % (record,),
                )

    def test_control_a_record_canon_cannot_read_still_fails_these_probes(self) -> None:
        """Without this, both probes above would pass on ANY appended record."""
        project = Path(self.tmp.name) / "control"
        run_dir = project / "runs" / "demo"
        run_dir.mkdir(parents=True)
        brain_path = project / "brain" / "shared-brain.jsonl"
        brain_path.parent.mkdir(parents=True)
        brain_path.write_text(
            '{"id":"seed-1","type":"lesson","text":"Seed lesson"}\n', encoding="utf-8")

        proc = self._append({"kind": "lesson", "text": "the old doc shape"}, project)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

        self.assertFalse(
            self.validator._has_graph_or_memory(str(run_dir)),
            "validate_run started ACCEPTING a record with no id/type; the "
            "probes above no longer discriminate the doc shape",
        )
        lessons, skipped = self.central.lessons_for_central(project, "demo")
        self.assertEqual(
            (len(lessons), skipped), (1, 1),
            "central_brain stopped dropping a 'kind'-shaped record; the probes "
            "above no longer discriminate the doc shape",
        )


class AppendWarnsInsteadOfDroppingSilently(unittest.TestCase):
    """A row canon will drop must be reported by the writer, at write time."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.brain = Path(self.tmp.name) / "shared-brain.jsonl"
        self.env = _sandbox_env(Path(self.tmp.name), self.brain)
        self.home_brain_before = (
            HOME_BRAIN.read_bytes() if HOME_BRAIN.exists() else None
        )
        self.addCleanup(self._assert_home_brain_untouched)

    def _assert_home_brain_untouched(self) -> None:
        after = HOME_BRAIN.read_bytes() if HOME_BRAIN.exists() else None
        self.assertEqual(
            self.home_brain_before, after,
            "this probe wrote to the REAL shared brain; the sandbox redirect broke",
        )

    def _append(self, record: dict):
        return subprocess.run(
            [sys.executable, str(BRAIN_APPEND), "--line", json.dumps(record),
             "--no-reindex"],
            capture_output=True, text=True, cwd=str(ROOT), check=False,
            env=self.env,
        )

    def test_kind_shaped_record_is_appended_but_reported(self) -> None:
        proc = self._append({"kind": "lesson", "text": "doc example"})
        # Still exit 0: this is a warning, not a new refusal. harvest.py aborts
        # the whole batch on a nonzero brain_append.
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(
            len(self.brain.read_text(encoding="utf-8").strip().splitlines()), 1)

        err = proc.stderr
        self.assertIn("WARNING", err,
                      "brain_append appended a row every canon reader drops "
                      "and said nothing: " + repr(proc.stdout + err))
        for field in ("id", "type"):
            self.assertIn(field, err,
                          "the warning must name the missing field %r" % field)
        self.assertIn("kind", err,
                      "the record field is \"type\" while brain.py's FLAG is "
                      "--kind; the warning must say so when 'kind' is present")

    def test_missing_id_alone_is_reported(self) -> None:
        proc = self._append({"type": "lesson", "text": "no id"})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("WARNING", proc.stderr)
        self.assertIn("id", proc.stderr)

    def test_canonical_record_appends_with_no_warning(self) -> None:
        """Control: 'warn on everything' must not satisfy the tests above."""
        proc = self._append(
            {"id": "L9", "type": "lesson", "text": "canonical control record"})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertNotIn(
            "WARNING", proc.stderr,
            "brain_append warned about a canon-valid record: " + repr(proc.stderr))


if __name__ == "__main__":
    unittest.main()
