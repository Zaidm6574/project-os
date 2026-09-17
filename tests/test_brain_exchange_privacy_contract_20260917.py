"""Composed exchange privacy and failure-atomic private output regressions.

All content is synthetic. Each test uses an isolated installation and store;
no developer brain, external service, or credentials are accessed.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
BRAIN = ROOT / "addons/full-engine/brain/brain.py"
CENTRAL = ROOT / "addons/full-engine/brain/central_brain.py"
APPROVAL_FIELDS = ("approved", "summary_only", "raw_chat")


class ExchangeFixture(unittest.TestCase):
    def setUp(self):
        original_path = sys.path[:]
        self.addCleanup(setattr, sys, "path", original_path)
        self.tmp = tempfile.TemporaryDirectory(prefix="exchange-contract-")
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name).resolve() / "project"
        self.project.mkdir()
        self.brain_dir = self.project / "brain"
        self.brain_dir.mkdir()
        self.scripts = self.project / "scripts"
        self.scripts.mkdir()
        for name in ("brain_paths.py", "secret_patterns.py", "bb_lock.py"):
            shutil.copy2(ROOT / "scripts" / name, self.scripts / name)
        self.entry = self.brain_dir / "brain.py"
        shutil.copy2(BRAIN, self.entry)
        shutil.copy2(CENTRAL, self.brain_dir / "central_brain.py")
        self.store = self.brain_dir / "shared-brain.jsonl"
        self.env = dict(os.environ, PROJECT_OS_SHARED_BRAIN=str(self.store),
                        BB_LOCK_DIR=str(self.project / "locks"))
        with mock.patch.dict(os.environ, self.env):
            self.mod = self.load(self.entry)
        self.start_patch = mock.patch.object(self.mod.bb_lock, "LOCK_DIR", str(self.project / "locks"))
        self.start_patch.start()
        self.addCleanup(self.start_patch.stop)
        (self.project / "locks").mkdir()

    def load(self, path):
        spec = importlib.util.spec_from_file_location("exchange_test_brain", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def row(self, **extra):
        return dict({"id": "synthetic", "type": "lesson", "text": "Save clear lessons."}, **extra)

    def write_store(self, rows):
        self.store.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    def cli(self, *args, central=False):
        entry = self.brain_dir / "central_brain.py" if central else self.entry
        return subprocess.run([sys.executable, str(entry), *map(str, args)],
                              cwd=self.project, env=self.env, capture_output=True,
                              text=True, timeout=30)

    def import_into(self, destination):
        return self.mod.cmd_import(argparse.Namespace(into=str(destination)))


class ApprovalSurvivesNormalization(ExchangeFixture):
    def test_file_export_then_central_push_respects_explicit_metadata(self):
        cases = [
            ({}, True),
            ({"approved": True, "summary_only": True, "raw_chat": False}, True),
            ({"approved": False, "summary_only": False, "raw_chat": True}, False),
            ({"approved": False}, False),
            ({"summary_only": False}, False),
            ({"raw_chat": True}, False),
            ({"approved": "true", "summary_only": True, "raw_chat": False}, False),
        ]
        for extension in ("jsonl", "json"):
            for index, (fields, syncable) in enumerate(cases):
                with self.subTest(extension=extension, fields=fields):
                    row = self.row(**fields)
                    source = self.project / ("input." + extension)
                    source.write_text(json.dumps(row) + "\n" if extension == "jsonl"
                                      else json.dumps({"records": {"one": row}}), encoding="utf-8")
                    self.write_store([])
                    result = self.cli("export", "--from", source)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    actual = json.loads(self.store.read_text())
                    central = self.project / ("central-%s-%s" % (extension, index))
                    result = self.cli("push", "--path", central, "--project", self.project,
                                      "--project-id", "synthetic", central=True)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    rows = (central / "shared-brain.jsonl").read_text().splitlines()
                    self.assertEqual(len(rows), int(syncable))
                    self.assertEqual({k: actual[k] for k in APPROVAL_FIELDS if k in actual}, fields)

    def test_adapter_export_keeps_explicit_fields_without_inventing_approval(self):
        rows = [self.row(), self.row(approved=False, summary_only=False, raw_chat=True),
                self.row(approved=True, summary_only=True, raw_chat=False),
                self.row(approved=None)]
        sidecar = self.project / "sidecar.json"
        manifest = self.project / "manifest.json"
        manifest.write_text("{}", encoding="utf-8")
        records = {}
        for index, row in enumerate(rows):
            records[str(index)] = dict(row, memory_id="item-%s" % index, memory_type="lesson")
        sidecar.write_text(json.dumps({"schema": "synthetic-v1", "records": records}), encoding="utf-8")
        adapter = types.SimpleNamespace(SIDECAR_PATH=str(sidecar), MANIFEST_PATH=str(manifest),
                                        SIDECAR_SCHEMA="synthetic-v1")
        with mock.patch.dict(sys.modules, {"osvec_adapter": adapter}):
            normalized = self.mod._lessons_from_adapter()
        for original, actual in zip(rows, normalized):
            self.assertEqual({k: actual[k] for k in APPROVAL_FIELDS if k in actual},
                             {k: original[k] for k in APPROVAL_FIELDS if k in original})


class StructuredCredentials(ExchangeFixture):
    def test_multiword_sensitive_fields_refused_in_full_and_portable_layouts(self):
        # A separate temporary root keeps the portable copy away from scripts/.
        with tempfile.TemporaryDirectory(prefix="portable-exchange-") as temp:
            entry = Path(temp).resolve() / "brain.py"
            shutil.copy2(BRAIN, entry)
            portable_mod = self.load(entry)
        for module in (self.mod, portable_mod):
            for key in ("password", "db-password", "passwd", "client_secret", "authorization", "auth_token"):
                for value in ("violet meadow lantern", "a b c", b"violet meadow lantern"):
                    with self.subTest(portable=module is portable_mod, key=key, value=value):
                        row = self.row(metadata={key: value})
                        self.assertEqual(module.record_secret_path(row), "metadata." + key)
                        with self.assertRaises(SystemExit):
                            module.gate_record(row, where="regression")

    def test_sensitive_field_container_and_scalar_values_fail_closed(self):
        for value in (True, 12345, ["violet", "meadow"], {"one": "violet meadow"}):
            with self.subTest(value=value):
                self.assertIsNotNone(self.mod.record_secret_hit(self.row(password=value)))

    def test_placeholders_empty_values_and_non_sensitive_notes_remain_valid(self):
        for value in (None, "", False, "REDACTED", "***", "n/a", "TODO", "<redacted>"):
            with self.subTest(value=value):
                self.assertIsNone(self.mod.record_secret_hit(self.row(password=value)))
        for key in ("note", "password_note", "api_key_docs", "token_budget"):
            self.assertIsNone(self.mod.record_secret_hit(self.row(**{key: "rotate quarterly per runbook"})))
        self.assertIsNotNone(self.mod.record_secret_hit(self.row(password="REDACTED violet meadow")))

    def test_import_refuses_passphrase_before_destination_write_or_disclosure(self):
        phrase = "violet meadow lantern"
        self.write_store([self.row(password=phrase)])
        destination = self.project / "exchange.jsonl"
        destination.write_bytes(b"old exchange bytes\n")
        result = self.cli("import", "--into", destination)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(phrase, result.stdout + result.stderr)
        self.assertEqual(destination.read_bytes(), b"old exchange bytes\n")
        absent = self.project / "absent.jsonl"
        result = self.cli("import", "--into", absent)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(absent.exists())


class PrivateAtomicExchange(ExchangeFixture):
    def setUp(self):
        super().setUp()
        self.row_value = self.row(note="Write clear documentation.")
        self.write_store([self.row_value])
        self.destination = self.project / "exchange.jsonl"

    def test_new_output_is_private_even_with_permissive_umask(self):
        previous = os.umask(0)
        try:
            self.assertEqual(self.import_into(self.destination), 0)
        finally:
            os.umask(previous)
        self.assertEqual(stat.S_IMODE(self.destination.stat().st_mode), 0o600)
        self.assertEqual(json.loads(self.destination.read_text()), self.row_value)

    def test_replacement_tightens_permissions_and_retains_tighter_owner_mode(self):
        for mode, expected in ((0o644, 0o600), (0o640, 0o600), (0o400, 0o400)):
            with self.subTest(mode=oct(mode)):
                self.destination.write_bytes(b"old\n")
                self.destination.chmod(mode)
                self.import_into(self.destination)
                self.assertEqual(stat.S_IMODE(self.destination.stat().st_mode), expected)
                self.assertEqual(json.loads(self.destination.read_text()), self.row_value)
                self.destination.chmod(0o600)

    def test_serialization_failure_preserves_old_bytes_and_directory(self):
        self.destination.write_bytes(b"old\n")
        before = set(self.project.iterdir())
        with mock.patch.object(self.mod.json, "dumps", side_effect=ValueError("synthetic serialization failure")):
            with self.assertRaises(ValueError):
                self.import_into(self.destination)
        self.assertEqual(self.destination.read_bytes(), b"old\n")
        self.assertEqual(set(self.project.iterdir()), before)

    def test_staging_fsync_failure_preserves_old_bytes_and_cleans_stage(self):
        self.destination.write_bytes(b"old\n")
        before = set(self.project.iterdir())
        with mock.patch.object(self.mod.os, "fsync", side_effect=OSError("synthetic disk failure")):
            with self.assertRaises(OSError):
                self.import_into(self.destination)
        self.assertEqual(self.destination.read_bytes(), b"old\n")
        self.assertEqual(set(self.project.iterdir()), before)

    def test_replace_failure_preserves_old_bytes_and_cleans_stage(self):
        self.destination.write_bytes(b"old\n")
        before = set(self.project.iterdir())
        with mock.patch.object(self.mod.os, "replace", side_effect=OSError("synthetic publication failure")):
            with self.assertRaises(OSError):
                self.import_into(self.destination)
        self.assertEqual(self.destination.read_bytes(), b"old\n")
        self.assertEqual(set(self.project.iterdir()), before)

    def test_new_output_does_not_clobber_racing_creation(self):
        real_link = os.link

        def race(*args, **kwargs):
            self.destination.write_bytes(b"concurrent output\n")
            return real_link(*args, **kwargs)

        before = set(self.project.iterdir())
        with mock.patch.object(self.mod.os, "link", side_effect=race):
            with self.assertRaises(OSError):
                self.import_into(self.destination)
        self.assertEqual(self.destination.read_bytes(), b"concurrent output\n")
        self.assertEqual(set(self.project.iterdir()), before | {self.destination})

    def test_existing_output_changed_during_staging_is_not_replaced(self):
        self.destination.write_bytes(b"old\n")
        real_sync = os.fsync

        def race(fd):
            self.destination.write_bytes(b"concurrent edit\n")
            return real_sync(fd)

        with mock.patch.object(self.mod.os, "fsync", side_effect=race):
            with self.assertRaises(OSError):
                self.import_into(self.destination)
        self.assertEqual(self.destination.read_bytes(), b"concurrent edit\n")

    def test_symlink_hardlink_and_special_destinations_refuse_without_changes(self):
        victim = self.project / "victim.jsonl"
        victim.write_bytes(b"unrelated bytes\n")
        for kind in ("symlink", "dangling", "hardlink", "directory", "fifo"):
            with self.subTest(kind=kind):
                victim.write_bytes(b"unrelated bytes\n")
                destination = self.project / (kind + ".jsonl")
                fifo_fd = None
                if kind == "symlink":
                    destination.symlink_to(victim)
                elif kind == "dangling":
                    destination.symlink_to(self.project / "absent-target")
                elif kind == "hardlink":
                    os.link(victim, destination)
                elif kind == "directory":
                    destination.mkdir()
                else:
                    os.mkfifo(destination)
                    # Keep both ends open so the old truncating writer fails
                    # this assertion promptly instead of hanging on a FIFO.
                    fifo_fd = os.open(destination, os.O_RDWR | os.O_NONBLOCK)
                try:
                    result = self.cli("import", "--into", destination)
                finally:
                    if fifo_fd is not None:
                        os.close(fifo_fd)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(victim.read_bytes(), b"unrelated bytes\n")
                self.assertFalse((self.project / "absent-target").exists())
                if kind != "directory":
                    destination.unlink()

    def test_parent_swap_during_staging_cannot_redirect_publication(self):
        parent = self.project / "output"
        parent.mkdir()
        moved = self.project / "moved"
        victim = self.project / "victim"
        victim.mkdir()
        destination = parent / "exchange.jsonl"
        destination.write_bytes(b"old\n")
        (victim / destination.name).write_bytes(b"unrelated\n")
        real_sync = os.fsync

        def race(fd):
            parent.rename(moved)
            parent.symlink_to(victim, target_is_directory=True)
            return real_sync(fd)

        with mock.patch.object(self.mod.os, "fsync", side_effect=race):
            with self.assertRaises(OSError):
                self.import_into(destination)
        self.assertTrue(parent.is_symlink())
        self.assertEqual((victim / destination.name).read_bytes(), b"unrelated\n")
        self.assertEqual((moved / destination.name).read_bytes(), b"old\n")

    def test_brain_destination_lock_precedes_read_and_keeps_intervening_append(self):
        late = self.row(id="late", text="A committed intervening lesson.")
        real_acquire = self.mod.bb_lock.acquire

        def append_then_acquire(*args, **kwargs):
            self.write_store([self.row_value, late])
            return real_acquire(*args, **kwargs)

        with mock.patch.object(self.mod.bb_lock, "acquire", side_effect=append_then_acquire) as acquire:
            self.import_into(self.store)
        acquire.assert_called_once()
        actual = [json.loads(line) for line in self.store.read_text().splitlines()]
        self.assertEqual(actual, [self.row_value, late])

    def test_symlinked_output_parent_is_refused(self):
        actual = self.project / "actual"
        actual.mkdir()
        alias = self.project / "alias"
        alias.symlink_to(actual, target_is_directory=True)
        result = self.cli("import", "--into", alias / "out.jsonl")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(actual.iterdir()), [])

    def test_stdout_read_and_outside_path_controls(self):
        before = self.store.read_bytes()
        result = self.cli("import")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), self.row_value)
        outside = self.project.parent / "outside.jsonl"
        result = self.cli("import", "--into", outside)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(outside.exists())
        self.assertEqual(self.store.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
