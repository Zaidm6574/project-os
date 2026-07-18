import argparse
import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
BRAIN = ROOT / "addons" / "full-engine" / "brain" / "brain.py"
CENTRAL_BRAIN = ROOT / "addons" / "full-engine" / "brain" / "central_brain.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class BrainPrivacyTests(unittest.TestCase):
    def test_raw_chat_save_is_not_automatically_approved(self):
        brain = load_module(BRAIN, "brain_raw_chat_approval")
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            brain_file = project / "brain" / "shared-brain.jsonl"
            brain_file.parent.mkdir(parents=True)
            args = argparse.Namespace(
                summary="Raw private conversation content.",
                summary_file=None,
                id="raw-chat-001",
                kind="lesson",
                tag=[],
                source=None,
                mode="raw",
            )

            with mock.patch.object(brain, "ROOT", str(project)), mock.patch.object(
                brain, "BRAIN_FILE", str(brain_file)
            ), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(brain.cmd_save_chat(args), 0)

            record = json.loads(brain_file.read_text(encoding="utf-8"))
            self.assertFalse(record["approved"])
            self.assertTrue(record["raw_chat"])
            self.assertFalse(record["summary_only"])

    def test_safe_path_rejects_symlink_escape_outside_project(self):
        brain = load_module(BRAIN, "brain_symlink_escape")
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            project = base / "project"
            outside = base / "outside"
            project.mkdir()
            outside.mkdir()
            (outside / "private.txt").write_text("private\n", encoding="utf-8")
            (project / "escape").symlink_to(outside, target_is_directory=True)

            with mock.patch.object(brain, "ROOT", str(project)):
                with self.assertRaises(SystemExit) as raised:
                    brain._safe_path(str(project / "escape" / "private.txt"))

            self.assertIn("outside the project", str(raised.exception))

    def test_import_refuses_symlinked_brain_file(self):
        # import must pass BRAIN_FILE through the same containment gate as
        # export/save-chat, or a symlinked brain file exfiltrates external
        # content (independent review finding, 2026-07-17)
        brain = load_module(BRAIN, "brain_import_symlink")
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            project = base / "project"
            outside = base / "outside"
            (project / "brain").mkdir(parents=True)
            outside.mkdir()
            external = outside / "external.jsonl"
            external.write_text(
                json.dumps({"id": "x", "text": "external secret"}) + "\n",
                encoding="utf-8",
            )
            link = project / "brain" / "shared-brain.jsonl"
            link.symlink_to(external)

            with mock.patch.object(brain, "ROOT", str(project)), mock.patch.object(
                brain, "BRAIN_FILE", str(link)
            ):
                with self.assertRaises(SystemExit) as raised:
                    brain.cmd_import(argparse.Namespace(into=None))

            self.assertIn("outside the project", str(raised.exception))

    def test_central_sync_rejects_raw_and_unapproved_records(self):
        central_brain = load_module(CENTRAL_BRAIN, "central_brain_privacy_boundary")
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            central = base / "central"
            project = base / "project"
            project_brain = project / "brain" / "shared-brain.jsonl"
            project_brain.parent.mkdir(parents=True)
            records = [
                {
                    "id": "approved-summary",
                    "source": "chat-summary",
                    "type": "lesson",
                    "text": "Approved compact summary.",
                    "tags": ["chat-summary"],
                    "approved": True,
                    "summary_only": True,
                    "raw_chat": False,
                },
                {
                    "id": "approved-raw-chat",
                    "source": "chat-raw",
                    "type": "lesson",
                    "text": "Raw private conversation content.",
                    "tags": ["raw-chat"],
                    "approved": True,
                    "summary_only": False,
                    "raw_chat": True,
                },
                {
                    "id": "unapproved-summary",
                    "source": "chat-summary",
                    "type": "lesson",
                    "text": "Unapproved compact summary.",
                    "tags": ["chat-summary"],
                    "approved": False,
                    "summary_only": True,
                    "raw_chat": False,
                },
            ]
            project_brain.write_text(
                "".join(json.dumps(record) + "\n" for record in records),
                encoding="utf-8",
            )

            self.assertEqual(central_brain.push(central, project, "privacy-test"), (1, 2))
            synced = central_brain.read_jsonl(central / "shared-brain.jsonl")
            self.assertEqual([record["origin_id"] for record in synced], ["approved-summary"])

    def test_plain_export_lessons_sync_while_raw_chat_stays_local(self):
        # F1 regression (hostile-judge finding, 2026-07-17): requiring chat
        # approval fields on every record made `brain.py export` followed by
        # `central_brain.py push` report "0 lesson(s) added". Plain lesson
        # records from the export pipeline carry no approval vocabulary and
        # must keep syncing; raw save-chat records must not.
        brain = load_module(BRAIN, "brain_export_sync_regression")
        central_brain = load_module(CENTRAL_BRAIN, "central_brain_export_sync")
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            central = base / "central"
            project = base / "project"
            receiving = base / "receiving"
            brain_file = project / "brain" / "shared-brain.jsonl"
            brain_file.parent.mkdir(parents=True)
            source_file = project / "from.jsonl"
            source_file.write_text(
                json.dumps(
                    {
                        "id": "exported-lesson-001",
                        "ts": "2026-07-17T00:00:00Z",
                        "source": "codex",
                        "type": "lesson",
                        "text": "Plain exported lesson with no approval fields.",
                        "tags": ["export"],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            with mock.patch.object(brain, "ROOT", str(project)), mock.patch.object(
                brain, "BRAIN_FILE", str(brain_file)
            ), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    brain.cmd_export(argparse.Namespace(from_file=str(source_file))), 0
                )
                self.assertEqual(
                    brain.cmd_save_chat(
                        argparse.Namespace(
                            summary="Raw private conversation content.",
                            summary_file=None,
                            id="raw-chat-002",
                            kind="lesson",
                            tag=[],
                            source=None,
                            mode="raw",
                        )
                    ),
                    0,
                )

            # exported lesson pushes; the raw chat record is the one skip
            self.assertEqual(central_brain.push(central, project, "alpha"), (1, 1))
            synced = central_brain.read_jsonl(central / "shared-brain.jsonl")
            self.assertEqual(
                [record["origin_id"] for record in synced], ["exported-lesson-001"]
            )

            # and it pulls into a second project untouched by the raw record
            self.assertEqual(central_brain.pull(central, receiving, "beta"), (1, 0))
            pulled = central_brain.read_jsonl(
                receiving / "brain" / "shared-brain.jsonl"
            )
            self.assertEqual(len(pulled), 1)
            self.assertEqual(pulled[0]["origin_id"], "exported-lesson-001")
            self.assertNotIn("Raw private conversation", pulled[0]["text"])


if __name__ == "__main__":
    unittest.main()
