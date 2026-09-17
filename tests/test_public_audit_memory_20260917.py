"""Memory storage regression controls using only disposable synthetic records."""
import argparse
import contextlib
from collections import Counter
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load(relative, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def lesson(rid="lesson-1"):
    return {"id": rid, "type": "lesson", "text": "Keep a recovery copy."}


def seed(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")


def rows(path):
    result = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            result.append(record)
    return result


def synthetic_secret():
    return "sk_" + "live_" + "F" * 32


class MemoryControls(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        env = mock.patch.dict(os.environ, {"BB_LOCK_DIR": str(self.base / "locks")})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("PROJECT_OS_SHARED_BRAIN", None)
        self.central = load("addons/full-engine/brain/central_brain.py", "memory_central_regression")

    def archive(self, folder, tail):
        active = folder / "shared-brain.jsonl"
        archived = folder / "shared-brain-archive.jsonl"
        seed(active, [lesson("move"), lesson("keep")])
        archived.write_bytes(tail)
        module = load("scripts/brain_archive.py", "memory_archive_regression")
        patches = contextlib.ExitStack()
        self.addCleanup(patches.close)
        patches.enter_context(mock.patch.object(module, "BRAIN", str(active)))
        patches.enter_context(mock.patch.object(module, "ARCHIVE", str(archived)))
        patches.enter_context(mock.patch.object(module.bb_lock, "LOCK_DIR", str(self.base / "locks")))
        patches.enter_context(mock.patch.object(module.subprocess, "run", return_value=argparse.Namespace(returncode=0, stdout="", stderr="")))
        return module, active, archived

    def apply(self, module, ids=None):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return module.cmd_apply(argparse.Namespace(ids=ids or ["move"], interest_days=60))

    def test_archive_preserves_partial_complete_and_terminated_tails(self):
        valid = json.dumps(lesson("existing")).encode("utf-8")
        for label, tail in (("partial", b'{"incomplete":'), ("complete", valid),
                            ("terminated", valid + b"\n"), ("empty", b"")):
            with self.subTest(case=label):
                module, active, archived = self.archive(self.base / label, tail)
                before = active.read_bytes()
                self.assertEqual(self.apply(module), 0)
                self.assertEqual([r["id"] for r in rows(active)], ["keep"])
                expected = (["existing"] if label in {"complete", "terminated"} else []) + ["move"]
                self.assertEqual([r["id"] for r in rows(archived)], expected)
                self.assertTrue(archived.read_bytes().startswith(tail))
                backups = list(active.parent.glob("*.pre-archive-*"))
                self.assertEqual(len(backups), 1)
                self.assertEqual(backups[0].read_bytes(), before)

    def test_archive_failed_active_replace_preserves_recovery_and_active(self):
        module, active, archived = self.archive(self.base / "replace-failure", b'{"incomplete":')
        before = active.read_bytes()
        with mock.patch.object(module.os, "replace", side_effect=OSError("synthetic disk failure")):
            with self.assertRaises(OSError):
                self.apply(module)
        self.assertEqual(active.read_bytes(), before)
        self.assertEqual([r["id"] for r in rows(archived)], ["move"])
        self.assertEqual(list(active.parent.glob("*.pre-archive-*"))[0].read_bytes(), before)
        self.assertEqual(list(active.parent.glob("*.tmp")), [])

    def test_archive_final_fence_rejects_expired_matching_token(self):
        module, active, archived = self.archive(self.base / "expired-archive", b'{"incomplete":')
        before = (active.read_bytes(), archived.read_bytes())
        replacement = active.parent / "replacement.tmp"
        replacement.write_text("", encoding="utf-8")
        token = module.bb_lock.acquire(str(active), agent="synthetic", wait=1)
        try:
            stale = time.time() - module.bb_lock.STALE_AFTER_SEC - 1
            os.utime(module.bb_lock.lock_path(str(active)), (stale, stale))
            self.assertFalse(module._commit_if_owned(token, str(replacement), [json.dumps(lesson()) + "\n"], 0o600))
            self.assertEqual((active.read_bytes(), archived.read_bytes()), before)
            self.assertTrue(replacement.exists())
        finally:
            module.bb_lock.release(str(active), agent="synthetic", token=token)

    def test_archive_unknown_id_leaves_both_files_unchanged(self):
        module, active, archived = self.archive(self.base / "unknown", b'{"incomplete":')
        before = (active.read_bytes(), archived.read_bytes())
        self.assertEqual(self.apply(module, ["missing"]), 2)
        self.assertEqual((active.read_bytes(), archived.read_bytes()), before)

    def test_central_lock_missing_timeout_and_exception_refuse_without_write(self):
        dest = self.base / "no-write.jsonl"
        for case in ("missing", "timeout", "exception"):
            with self.subTest(case=case):
                if case == "missing":
                    patcher = mock.patch.object(self.central, "_BB_LOCK", None)
                elif case == "timeout":
                    patcher = mock.patch.object(self.central._BB_LOCK, "acquire", return_value=None)
                else:
                    patcher = mock.patch.object(self.central._BB_LOCK, "acquire", side_effect=OSError("synthetic failure"))
                with patcher, self.assertRaises(RuntimeError):
                    self.central.append_new(dest, [lesson()])
                self.assertFalse(dest.exists())

    def test_central_valid_lock_deduplicates_and_flushes(self):
        dest = self.base / "owned.jsonl"
        real_fsync = self.central.os.fsync
        with mock.patch.object(self.central.os, "fsync", wraps=real_fsync) as flush:
            self.assertEqual(self.central.append_new(dest, [lesson(), lesson()]), 1)
            self.assertGreater(flush.call_count, 0)
        before = dest.read_bytes()
        self.assertEqual(self.central.append_new(dest, [lesson()]), 0)
        self.assertEqual(dest.read_bytes(), before)

    def test_central_expired_and_replaced_tokens_cannot_publish(self):
        lock = self.central._BB_LOCK
        for case in ("expired", "replaced"):
            with self.subTest(case=case):
                dest = self.base / (case + ".jsonl")
                original_acquire = lock.acquire
                def acquire(*args, **kwargs):
                    token = original_acquire(*args, **kwargs)
                    lock_file = lock.lock_path(str(dest))
                    if case == "expired":
                        stale = time.time() - lock.STALE_AFTER_SEC - 1
                        os.utime(lock_file, (stale, stale))
                    else:
                        data = json.loads(Path(lock_file).read_text())
                        data["token"] = "replacement-owner"
                        Path(lock_file).write_text(json.dumps(data))
                    return token
                with mock.patch.object(lock, "acquire", side_effect=acquire), self.assertRaises(RuntimeError):
                    self.central.append_new(dest, [lesson()])
                self.assertFalse(dest.exists())

    def test_central_holds_stable_guard_across_read_and_publication(self):
        dest = self.base / "guarded.jsonl"
        lock = self.central._BB_LOCK
        entered = threading.Event()
        started = threading.Event()
        workers = []
        original_read = self.central.read_jsonl
        def read(path, *args):
            def contender():
                started.set()
                with lock._guard(lock.lock_path(str(dest))):
                    entered.set()
            worker = threading.Thread(target=contender)
            workers.append(worker)
            worker.start()
            self.assertTrue(started.wait(1))
            self.assertFalse(entered.wait(.05), "publication guard released before read/append")
            return original_read(path, *args)
        try:
            with mock.patch.object(self.central, "read_jsonl", side_effect=read):
                self.assertEqual(self.central.append_new(dest, [lesson()]), 1)
        finally:
            for worker in workers:
                worker.join(2)
        self.assertTrue(entered.is_set())
        self.assertEqual(rows(dest), [lesson()])

    def test_central_disk_failure_is_not_reported_as_success(self):
        dest = self.base / "disk-failure.jsonl"
        with mock.patch.object(self.central.os, "fsync", side_effect=OSError("synthetic disk failure")):
            with self.assertRaises(OSError):
                self.central.append_new(dest, [lesson()])
        self.assertIsNone(self.central._BB_LOCK.read_lock(self.central._BB_LOCK.lock_path(str(dest))))

    def test_installed_brain_refuses_missing_lock_and_expired_lease(self):
        brain = load("addons/full-engine/brain/brain.py", "memory_installed_lock_regression")
        dest = self.base / "installed.jsonl"
        self.assertIsNotNone(brain.CORE_SCRIPTS)
        with mock.patch.object(brain, "bb_lock", None), self.assertRaises(RuntimeError):
            brain._append_new(str(dest), [lesson()], "synthetic")
        self.assertFalse(dest.exists())
        lock = brain.bb_lock
        original_acquire = lock.acquire
        def expired(*args, **kwargs):
            token = original_acquire(*args, **kwargs)
            stale = time.time() - lock.STALE_AFTER_SEC - 1
            os.utime(lock.lock_path(str(dest)), (stale, stale))
            return token
        with mock.patch.object(lock, "acquire", side_effect=expired), self.assertRaises(RuntimeError):
            brain._append_new(str(dest), [lesson()], "synthetic")
        self.assertFalse(dest.exists())

    def test_central_selftest_preserves_environment_bound_store(self):
        target = self.base / "operator-store.jsonl"
        seed(target, [lesson("sentinel")])
        before = target.read_bytes()
        with mock.patch.dict(os.environ, {"PROJECT_OS_SHARED_BRAIN": str(target)}):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(self.central.selftest(), 0)
            self.assertEqual(os.environ["PROJECT_OS_SHARED_BRAIN"], str(target))
        self.assertEqual(target.read_bytes(), before)

    def bind(self, project, external, ignored=True):
        folder = project / "brain"
        folder.mkdir(parents=True)
        if ignored:
            (project / ".gitignore").write_text("brain/shared-brain-binding.jsonl\n")
        (folder / "shared-brain-binding.jsonl").write_text(json.dumps({
            "schema": "project-os/shared-brain-binding/v1", "target": str(external)
        }) + "\n")

    def test_bound_project_push_and_pull_use_same_store(self):
        project = self.base / "bound-project"
        external = self.base / "external.jsonl"
        self.bind(project, external)
        seed(external, [lesson("bound")])
        central = self.base / "exchange"
        self.assertEqual(self.central.project_brain_path(project), external)
        self.assertEqual(self.central.push(central, project, "first"), (1, 0))
        self.assertEqual(rows(central / "shared-brain.jsonl")[0]["origin_id"], "bound")
        seed(central / "shared-brain.jsonl", [dict(lesson("incoming"), project_id="second")])
        self.assertEqual(self.central.pull(central, project, "first"), (1, 0))
        self.assertEqual([r["id"] for r in rows(external)], ["bound", "incoming"])
        self.assertFalse((project / "brain" / "shared-brain.jsonl").exists())

    def test_environment_override_precedes_binding_and_local_default_works(self):
        project = self.base / "override-project"
        external = self.base / "bound.jsonl"
        override = self.base / "override.jsonl"
        self.bind(project, external)
        seed(external, [lesson("bound")])
        seed(override, [lesson("override")])
        with mock.patch.dict(os.environ, {"PROJECT_OS_SHARED_BRAIN": str(override)}):
            self.assertEqual(self.central.project_brain_path(project), override)
            self.assertEqual(self.central.push(self.base / "override-exchange", project, "first"), (1, 0))
        self.assertEqual(rows(self.base / "override-exchange" / "shared-brain.jsonl")[0]["origin_id"], "override")
        local_project = self.base / "local-project"
        seed(local_project / "brain" / "shared-brain.jsonl", [lesson("local")])
        self.assertEqual(self.central.push(self.base / "local-exchange", local_project, "local"), (1, 0))

    def test_invalid_binding_refuses_instead_of_falling_back_to_local(self):
        project = self.base / "invalid-project"
        external = self.base / "invalid-external.jsonl"
        self.bind(project, external, ignored=False)
        seed(external, [lesson("bound")])
        seed(project / "brain" / "shared-brain.jsonl", [lesson("local")])
        before = external.read_bytes()
        with self.assertRaises(self.central._brain_paths.BrainPathError):
            self.central.push(self.base / "invalid-exchange", project, "first")
        self.assertEqual(external.read_bytes(), before)

    def test_secret_refusal_never_echoes_keys_ids_or_ancestor_keys(self):
        brain = load("addons/full-engine/brain/brain.py", "memory_privacy_regression")
        shared = load("scripts/secret_patterns.py", "memory_secret_regression")
        secret = synthetic_secret()
        for record in ({secret: "note"}, {"id": secret}, {secret: {"note": secret}},
                       {secret[:10]: secret[10:]}, {secret + "_token": "value"}):
            self.assertTrue(bool(shared.secret_reason(record)))
            self.assertFalse(secret in shared.secret_reason(record))
            for batch in (False, True):
                with self.assertRaises(SystemExit) as caught:
                    if batch:
                        brain.gate_records([record], where="synthetic")
                    else:
                        brain.gate_record(record, where="synthetic")
                self.assertFalse(secret in str(caught.exception))
        self.assertEqual(brain.gate_records([lesson()], where="synthetic"), [lesson()])

    def test_append_cli_refuses_sensitive_key_without_printing_it(self):
        secret = synthetic_secret()
        dest = self.base / "refused.jsonl"
        env = dict(os.environ, PROJECT_OS_SHARED_BRAIN=str(dest))
        command = [sys.executable, str(ROOT / "scripts" / "brain_append.py"), "--no-reindex"]
        for record in (dict(lesson(), **{secret: "note"}), dict(lesson(), kind=secret)):
            proc = subprocess.run(command, input=json.dumps(record), env=env,
                                  capture_output=True, text=True, timeout=10)
            self.assertNotEqual(proc.returncode, 0)
            self.assertFalse(secret in proc.stdout + proc.stderr)
            self.assertFalse(dest.exists())
        proc = subprocess.run(command, input=json.dumps(lesson()), env=env,
                              capture_output=True, text=True, timeout=10)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(rows(dest), [lesson()])

    def test_default_redaction_matches_cli_pem_order_and_counts(self):
        importer = load("scripts/import_chat_history.py", "memory_import_regression")
        explicit = importer.redaction_patterns(importer.load_credential_patterns())
        start = "-----BEGIN " + "RSA PRIVATE KEY-----"
        end = "-----END " + "RSA PRIVATE KEY-----"
        body = "SYNTHETICBODYFORTESTING"
        for text in (start + "\n" + body + "\n" + end,
                     start + "\n" + body,
                     start + "\n" + body + "\n\nKeep this ordinary note."):
            counts = Counter()
            output = importer.redact(text, counts=counts)
            self.assertFalse(body in output)
            self.assertEqual(output, importer.redact(text, explicit))
            self.assertEqual(counts["[REDACTED_PRIVATE_KEY]"], 1)
        ordinary = "Keep this ordinary note. The secret: happiness comes from within."
        self.assertEqual(importer.redact(ordinary), ordinary)
        self.assertEqual(importer.redact(start + "\n" + body + "\n\n" + ordinary).split("\n\n")[1], ordinary)


if __name__ == "__main__":
    unittest.main()
