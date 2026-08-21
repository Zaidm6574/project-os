"""Final adversarial regressions for durable-memory privacy and integrity."""

import argparse
import ast
import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
BRAIN_APPEND = ROOT / "scripts" / "brain_append.py"
BRAIN = ROOT / "addons" / "full-engine" / "brain" / "brain.py"
CENTRAL_BRAIN = ROOT / "addons" / "full-engine" / "brain" / "central_brain.py"
OSVEC = ROOT / "addons" / "full-engine" / "memory" / "osvec_adapter.py"
MNEME = ROOT / "memory" / "mneme_adapter.py"
HARVEST = ROOT / "scripts" / "harvest.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def osvec_secret_patterns():
    """The compiled denylist the OSVec gate uses.

    osvec_adapter now loads scripts/secret_patterns.py at import instead of
    carrying its own copy (four copies had drifted apart), so this asserts the
    wiring and then returns the shared patterns. It cannot import the adapter
    directly: that module exits when numpy is missing.
    """
    source = OSVEC.read_text(encoding="utf-8")
    for expected in ("_secret_patterns = _load_secret_patterns()",
                     "_SECRET_RE = _secret_patterns.SECRET_PATTERNS",
                     "looks_like_secret = _secret_patterns.looks_like_secret"):
        if expected not in source:
            raise AssertionError(
                f"osvec_adapter no longer wires the shared denylist: {expected}")
    shared = load_module(ROOT / "scripts" / "secret_patterns.py",
                         "shared_secret_patterns_for_osvec")
    return list(shared.SECRET_PATTERNS)


class MemoryPrivacyEdgeTests(unittest.TestCase):
    def test_brain_append_rejects_sensitive_aliases_and_extended_tokens(self):
        samples = (
            {"id": "generic-token", "token": "ordinary-looking-sensitive-value"},
            {"id": "provider-key", "openai_api_key": "ordinary-looking-sensitive-value"},
            {"id": "authorization", "authorization": "Bearer ordinary-sensitive-value"},
            {"id": "github-oauth", "text": "gho_" + "A" * 36},
            {"id": "github-user", "text": "ghu_" + "B" * 36},
            {"id": "stripe-restricted", "text": "rk_live_" + "C" * 24},
            {"id": "bearer", "text": "Authorization: Bearer " + "D" * 32},
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            brain = root / "shared-brain.jsonl"
            env = dict(os.environ)
            env.update(
                {
                    "PROJECT_OS_SHARED_BRAIN": str(brain),
                    "BB_LOCK_DIR": str(root / "locks"),
                    "PYTHONPYCACHEPREFIX": str(root / "pycache"),
                }
            )
            for sample in samples:
                with self.subTest(sample=sample["id"]):
                    result = subprocess.run(
                        [
                            sys.executable,
                            str(BRAIN_APPEND),
                            "--line",
                            json.dumps(sample),
                            "--no-reindex",
                        ],
                        capture_output=True,
                        text=True,
                        env=env,
                    )
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertIn("secret", result.stderr.lower())
                    self.assertNotIn("Traceback", result.stderr)
                    self.assertFalse(brain.exists())

    def test_extended_token_families_are_blocked_by_every_text_gate(self):
        brain = load_module(BRAIN, "brain_final_secret_patterns")
        central = load_module(CENTRAL_BRAIN, "central_final_secret_patterns")
        osvec_patterns = osvec_secret_patterns()
        samples = (
            "gho_" + "A" * 36,
            "ghu_" + "B" * 36,
            "ghs_" + "C" * 36,
            "ghr_" + "D" * 36,
            "rk_live_" + "E" * 24,
            "Authorization: Bearer " + "F" * 32,
        )
        for sample in samples:
            with self.subTest(prefix=sample[:12]):
                self.assertTrue(brain._looks_like_secret(sample))
                self.assertTrue(central.looks_like_secret(sample))
                self.assertTrue(any(pattern.search(sample) for pattern in osvec_patterns))

    def test_save_chat_rejects_secret_looking_id_source_and_tags(self):
        brain = load_module(BRAIN, "brain_all_field_save_gate")
        token = "gho_" + "M" * 36
        for field in ("id", "source", "tag"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                project = root / "project"
                brain_file = project / "brain" / "shared-brain.jsonl"
                brain_file.parent.mkdir(parents=True)
                values = {
                    "id": "safe-id",
                    "source": "chat-summary",
                    "tag": ["chat-summary"],
                }
                values[field] = [token] if field == "tag" else token
                args = argparse.Namespace(
                    summary="Reviewed harmless summary.",
                    summary_file=None,
                    id=values["id"],
                    kind="lesson",
                    tag=values["tag"],
                    source=values["source"],
                    mode="summary",
                    approved=True,
                )
                with mock.patch.object(brain, "ROOT", str(project)), mock.patch.object(
                    brain, "BRAIN_FILE", str(brain_file)
                ), mock.patch.object(
                    brain.bb_lock, "LOCK_DIR", str(root / "locks")
                ), contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaises(SystemExit) as raised:
                        brain.cmd_save_chat(args)
                self.assertIn("secret", str(raised.exception).lower())
                self.assertFalse(brain_file.exists())

    def test_export_rejects_secret_looking_metadata_before_brain_write(self):
        brain = load_module(BRAIN, "brain_all_field_export_gate")
        token = "ghu_" + "N" * 36
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            brain_file = project / "brain" / "shared-brain.jsonl"
            brain_file.parent.mkdir(parents=True)
            source = project / "lessons.jsonl"
            source.write_text(
                json.dumps(
                    {
                        "id": "safe-id",
                        "source": "project-os",
                        "type": "lesson",
                        "text": "Harmless lesson text.",
                        "tags": [token],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with mock.patch.object(brain, "ROOT", str(project)), mock.patch.object(
                brain, "BRAIN_FILE", str(brain_file)
            ), mock.patch.object(
                brain.bb_lock, "LOCK_DIR", str(root / "locks")
            ), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    brain.cmd_export(argparse.Namespace(from_file=str(source)))
            self.assertIn("secret", str(raised.exception).lower())
            self.assertFalse(brain_file.exists())

    def test_central_push_skips_secrets_in_id_source_and_tags(self):
        central = load_module(CENTRAL_BRAIN, "central_all_field_push_gate")
        token = "ghs_" + "P" * 36
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            central_dir = root / "central"
            project_brain = project / "brain" / "shared-brain.jsonl"
            project_brain.parent.mkdir(parents=True)
            records = [
                {"id": token, "source": "project-os", "type": "lesson",
                 "text": "Harmless id probe.", "tags": []},
                {"id": "source-probe", "source": token, "type": "lesson",
                 "text": "Harmless source probe.", "tags": []},
                {"id": "tag-probe", "source": "project-os", "type": "lesson",
                 "text": "Harmless tag probe.", "tags": [token]},
            ]
            project_brain.write_text(
                "".join(json.dumps(record) + "\n" for record in records),
                encoding="utf-8",
            )
            with mock.patch.object(central.bb_lock, "LOCK_DIR", str(root / "locks")):
                result = central.push(central_dir, project, "alpha")
            self.assertEqual(result, (0, 3))
            self.assertEqual(central.read_jsonl(central_dir / "shared-brain.jsonl"), [])

    def test_central_pull_skips_secret_looking_metadata(self):
        central = load_module(CENTRAL_BRAIN, "central_all_field_pull_gate")
        token = "ghr_" + "Q" * 36
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            central_dir = root / "central"
            project = root / "project"
            central_dir.mkdir()
            (central_dir / "shared-brain.jsonl").write_text(
                json.dumps(
                    {
                        "id": "central-id",
                        "origin_id": "origin-id",
                        "project_id": "alpha",
                        "source": "project-os",
                        "type": "lesson",
                        "text": "Harmless central lesson.",
                        "tags": [token],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with mock.patch.object(central.bb_lock, "LOCK_DIR", str(root / "locks")):
                result = central.pull(central_dir, project, "beta")
            self.assertEqual(result, (0, 1))
            destination = project / "brain" / "shared-brain.jsonl"
            self.assertFalse(destination.exists())


class BrainJsonlFailClosedTests(unittest.TestCase):
    @staticmethod
    def _copied_brain_layout(root):
        brain_script = root / "brain" / "brain.py"
        brain_script.parent.mkdir(parents=True)
        shutil.copy2(BRAIN, brain_script)
        scripts = root / "scripts"
        scripts.mkdir()
        shutil.copy2(ROOT / "scripts" / "bb_lock.py", scripts / "bb_lock.py")
        shutil.copy2(ROOT / "scripts" / "secret_patterns.py",
                     scripts / "secret_patterns.py")
        shutil.copy2(ROOT / "scripts" / "brain_paths.py",
                     scripts / "brain_paths.py")
        return brain_script, brain_script.parent / "shared-brain.jsonl"

    def _run_save_chat_against_existing_bytes(self, existing):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            brain_script, brain_file = self._copied_brain_layout(root)
            brain_file.write_bytes(existing)
            env = dict(os.environ)
            env.update(
                {
                    "BB_LOCK_DIR": str(root / "locks"),
                    "PYTHONPYCACHEPREFIX": str(root / "pycache"),
                }
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(brain_script),
                    "save-chat",
                    "--summary",
                    "Harmless reviewed summary.",
                    "--id",
                    "new-id",
                    "--approved",
                ],
                capture_output=True,
                text=True,
                env=env,
            )
            return result, brain_file.read_bytes()

    def test_save_chat_rejects_malformed_existing_jsonl_without_mutation(self):
        existing = (
            b'{"id":"kept","type":"lesson","text":"keep"}\n'
            b'{oops\n'
        )
        result, after = self._run_save_chat_against_existing_bytes(existing)

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertRegex(result.stderr, r"shared-brain\.jsonl.*line 2")
        self.assertIn("invalid JSON", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(after, existing)

    def test_save_chat_rejects_non_object_existing_jsonl_without_mutation(self):
        existing = (
            b'{"id":"kept","type":"lesson","text":"keep"}\n'
            b'["not", "an", "object"]\n'
        )
        result, after = self._run_save_chat_against_existing_bytes(existing)

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertRegex(result.stderr, r"shared-brain\.jsonl.*line 2")
        self.assertIn("expected a JSON object", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(after, existing)


class BrainSelftestIsolationTests(unittest.TestCase):
    def test_selftest_does_not_touch_real_brain_or_fixed_source_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            brain_script, brain_file = BrainJsonlFailClosedTests._copied_brain_layout(root)
            brain_sentinel = (
                b'{"id":"real","type":"lesson","text":"must stay byte-identical"}\n'
            )
            source_sentinel = b"fixed-name sentinel must survive\n"
            brain_file.write_bytes(brain_sentinel)
            fixed_source = brain_script.parent / ".selftest-from.jsonl"
            fixed_source.write_bytes(source_sentinel)
            env = dict(os.environ)
            env.update(
                {
                    "BB_LOCK_DIR": str(root / "real-locks"),
                    "PYTHONPYCACHEPREFIX": str(root / "pycache"),
                }
            )

            result = subprocess.run(
                [sys.executable, str(brain_script), "--selftest"],
                capture_output=True,
                text=True,
                env=env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("selftest: OK", result.stdout)
            self.assertNotIn("Traceback", result.stderr)
            self.assertTrue(fixed_source.exists())
            self.assertEqual(fixed_source.read_bytes(), source_sentinel)
            self.assertEqual(brain_file.read_bytes(), brain_sentinel)

    def test_selftest_restores_globals_when_temporary_source_write_fails(self):
        brain = load_module(BRAIN, "brain_selftest_restore_on_setup_failure")
        original_root = brain.ROOT
        original_brain_file = brain.BRAIN_FILE
        original_lock_dir = brain.bb_lock.LOCK_DIR

        with mock.patch("builtins.open", side_effect=OSError("synthetic source failure")):
            with self.assertRaisesRegex(OSError, "synthetic source failure"):
                brain._selftest()

        self.assertEqual(brain.ROOT, original_root)
        self.assertEqual(brain.BRAIN_FILE, original_brain_file)
        self.assertEqual(brain.bb_lock.LOCK_DIR, original_lock_dir)


class CentralInitLeafSafetyTests(unittest.TestCase):
    @staticmethod
    def _copied_central_layout(root):
        central_script = root / "brain" / "central_brain.py"
        central_script.parent.mkdir(parents=True)
        shutil.copy2(CENTRAL_BRAIN, central_script)
        scripts = root / "scripts"
        scripts.mkdir()
        shutil.copy2(ROOT / "scripts" / "bb_lock.py", scripts / "bb_lock.py")
        shutil.copy2(ROOT / "scripts" / "secret_patterns.py",
                     scripts / "secret_patterns.py")
        shutil.copy2(ROOT / "scripts" / "brain_paths.py",
                     scripts / "brain_paths.py")
        return central_script

    @staticmethod
    def _make_unsafe_leaf(root, leaf, kind):
        fixed_ns = 946684800_000_000_000
        victim = root / f"outside-{leaf.name}"
        if kind == "dangling-symlink":
            os.symlink(str(victim), str(leaf))
            return victim, None
        if kind == "hardlink":
            victim.write_bytes(b"outside victim must not change\n")
            os.utime(victim, ns=(fixed_ns, fixed_ns))
            os.link(victim, leaf)
            return victim, (victim.read_bytes(), victim.stat().st_mtime_ns)
        if kind == "fifo":
            os.mkfifo(leaf)
            os.utime(leaf, ns=(fixed_ns, fixed_ns))
            return victim, (stat.S_IFMT(os.lstat(leaf).st_mode), os.lstat(leaf).st_mtime_ns)
        if kind == "directory":
            leaf.mkdir()
            os.utime(leaf, ns=(fixed_ns, fixed_ns))
            return victim, (stat.S_IFMT(os.lstat(leaf).st_mode), os.lstat(leaf).st_mtime_ns)
        raise AssertionError(f"unknown unsafe leaf kind: {kind}")

    @staticmethod
    def _assert_unsafe_leaf_unchanged(testcase, leaf, kind, victim, before):
        if kind == "dangling-symlink":
            testcase.assertTrue(leaf.is_symlink())
            testcase.assertFalse(victim.exists())
        elif kind == "hardlink":
            testcase.assertEqual(victim.read_bytes(), before[0])
            testcase.assertEqual(victim.stat().st_mtime_ns, before[1])
            testcase.assertEqual(os.lstat(leaf).st_ino, victim.stat().st_ino)
        else:
            current = os.lstat(leaf)
            testcase.assertEqual(stat.S_IFMT(current.st_mode), before[0])
            testcase.assertEqual(current.st_mtime_ns, before[1])

    def _assert_init_rejects_each_unsafe_kind(self, leaf_name):
        for kind in ("dangling-symlink", "hardlink", "fifo", "directory"):
            with self.subTest(leaf=leaf_name, kind=kind), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                central_script = self._copied_central_layout(root)
                central_dir = root / "central"
                central_dir.mkdir()
                leaf = central_dir / leaf_name
                victim, before = self._make_unsafe_leaf(root, leaf, kind)
                other_name = "README.md" if leaf_name == "shared-brain.jsonl" else "shared-brain.jsonl"
                other_leaf = central_dir / other_name
                env = dict(os.environ)
                env.update(
                    {
                        "BB_LOCK_DIR": str(root / "locks"),
                        "PYTHONPYCACHEPREFIX": str(root / "pycache"),
                    }
                )

                result = subprocess.run(
                    [
                        sys.executable,
                        str(central_script),
                        "init",
                        "--path",
                        str(central_dir),
                    ],
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=5,
                )

                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("refuse", result.stderr.lower())
                self.assertIn(leaf_name, result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self._assert_unsafe_leaf_unchanged(self, leaf, kind, victim, before)
                self.assertFalse(other_leaf.exists())

    def test_init_rejects_unsafe_shared_brain_leafs(self):
        self._assert_init_rejects_each_unsafe_kind("shared-brain.jsonl")

    def test_init_rejects_unsafe_readme_leafs(self):
        self._assert_init_rejects_each_unsafe_kind("README.md")


class BrainAppendConcurrencyTests(unittest.TestCase):
    @staticmethod
    def _two_thread_snapshot_barrier(real_reader):
        barrier = threading.Barrier(2)

        def synchronized_reader(path):
            result = real_reader(path)
            try:
                barrier.wait(timeout=0.4)
            except threading.BrokenBarrierError:
                # Once read+append is correctly serialized, the first caller
                # times out while holding the lock and the second reads later.
                pass
            return result

        return synchronized_reader

    def test_central_append_new_serializes_dedupe_with_append(self):
        central = load_module(CENTRAL_BRAIN, "central_atomic_append")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "central" / "shared-brain.jsonl"
            path.parent.mkdir(parents=True)
            path.touch()
            record = {"id": "same", "type": "lesson", "text": "one copy"}
            results = []
            errors = []

            def writer():
                try:
                    results.append(central.append_new(path, [record]))
                except BaseException as exc:  # make thread failures visible
                    errors.append(exc)

            reader = self._two_thread_snapshot_barrier(central.read_jsonl)
            with mock.patch.object(
                central.bb_lock, "LOCK_DIR", str(root / "locks")
            ), mock.patch.object(central, "read_jsonl", side_effect=reader):
                threads = [threading.Thread(target=writer) for _ in range(2)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(3)

            self.assertFalse(any(thread.is_alive() for thread in threads))
            self.assertEqual(errors, [])
            self.assertEqual(sum(results), 1)
            self.assertEqual([row["id"] for row in central.read_jsonl(path)], ["same"])

    def test_save_chat_serializes_dedupe_with_append(self):
        brain = load_module(BRAIN, "brain_atomic_save_chat")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            brain_file = project / "brain" / "shared-brain.jsonl"
            brain_file.parent.mkdir(parents=True)
            brain_file.touch()
            args = argparse.Namespace(
                summary="Compact reviewed summary.",
                summary_file=None,
                id="same",
                kind="lesson",
                tag=[],
                source=None,
                mode="summary",
                approved=True,
            )
            errors = []

            def writer():
                try:
                    brain.cmd_save_chat(args)
                except BaseException as exc:  # make thread failures visible
                    errors.append(exc)

            reader = self._two_thread_snapshot_barrier(brain._existing_ids)
            with mock.patch.object(
                brain.bb_lock, "LOCK_DIR", str(root / "locks")
            ), mock.patch.object(brain, "ROOT", str(project)), mock.patch.object(
                brain, "BRAIN_FILE", str(brain_file)
            ), mock.patch.object(brain, "_existing_ids", side_effect=reader):
                threads = [threading.Thread(target=writer) for _ in range(2)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(3)

            self.assertFalse(any(thread.is_alive() for thread in threads))
            self.assertEqual(errors, [])
            rows = [json.loads(line) for line in brain_file.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["id"] for row in rows], ["same"])

    def test_brain_append_concurrent_same_id_is_written_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            brain = root / "shared-brain.jsonl"
            env = dict(os.environ)
            env.update(
                {
                    "HOME": str(root / "home"),
                    "BB_LOCK_DIR": str(root / "locks"),
                    "PROJECT_OS_SHARED_BRAIN": str(brain),
                    "MNEME_INDEX": str(root / "mneme.json"),
                    "MNEME_EMBEDDER": "lexical",
                    "PYTHONPYCACHEPREFIX": str(root / "pycache"),
                }
            )
            payload = json.dumps(
                {"id": "one-logical-id", "type": "lesson", "text": "one durable row"}
            )
            processes = [
                subprocess.Popen(
                    [
                        sys.executable,
                        str(BRAIN_APPEND),
                        "--line",
                        payload,
                        "--no-reindex",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=env,
                )
                for _ in range(8)
            ]
            results = [process.communicate(timeout=15) for process in processes]

            self.assertEqual([process.returncode for process in processes], [0] * 8, results)
            rows = [json.loads(line) for line in brain.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["id"] for row in rows], ["one-logical-id"])
            combined = "\n".join(stdout for stdout, _ in results)
            self.assertEqual(combined.count("appended"), 1)
            self.assertEqual(combined.count("kept existing"), 7)


class HarvestConcurrentDedupeTests(unittest.TestCase):
    def test_stale_snapshot_is_deduped_inside_brain_append_and_counted_as_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for relative in (
                "scripts/harvest.py",
                "scripts/brain_append.py",
                "scripts/bb_lock.py",
                "scripts/secret_patterns.py",
                "scripts/brain_paths.py",
            ):
                source = ROOT / relative
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            mneme = root / "memory" / "mneme_adapter.py"
            mneme.parent.mkdir(parents=True)
            mneme.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
            harvest = load_module(root / "scripts" / "harvest.py", "harvest_stale_snapshot")
            run = root / "runs" / "same-run"
            run.mkdir(parents=True)
            (root / "blackboard" / "packets").mkdir(parents=True)
            brain = root / "shared-brain.jsonl"
            brain.write_text(
                json.dumps({"id": "same-id", "type": "lesson", "text": "existing"})
                + "\n",
                encoding="utf-8",
            )
            proposal = root / "proposal.jsonl"
            proposal.write_text(
                json.dumps(
                    {
                        "id": "same-id",
                        "project_id": "same-run",
                        "type": "lesson",
                        "text": "same logical proposal",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            env = {
                "HOME": str(root / "home"),
                "BB_LOCK_DIR": str(root / "locks"),
                "PROJECT_OS_SHARED_BRAIN": str(brain),
                "MNEME_INDEX": str(root / "mneme.json"),
                "MNEME_EMBEDDER": "lexical",
                "PYTHONPYCACHEPREFIX": str(root / "pycache"),
            }
            output = io.StringIO()
            with mock.patch.dict(os.environ, env, clear=False), mock.patch.object(
                harvest, "_existing_brain_ids", return_value=set()
            ), contextlib.redirect_stdout(output):
                result = harvest.cmd_apply(str(proposal))

            self.assertEqual(result, 0)
            rows = [json.loads(line) for line in brain.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["id"] for row in rows], ["same-id"])
            self.assertIn("appended 0 lessons (kept 1 existing)", output.getvalue())


class MnemePublishValidationTests(unittest.TestCase):
    def test_ragged_embedding_batch_cannot_replace_a_valid_index(self):
        mneme = load_module(MNEME, "mneme_ragged_publish")
        with tempfile.TemporaryDirectory() as tmp:
            index_path = Path(tmp) / "mneme_index.json"
            sentinel = {
                "dim": mneme.DIM,
                "embedder": "lexical-hash-v1",
                "count": 1,
                "entries": [
                    {
                        "id": "sentinel",
                        "source": "lesson",
                        "text": "keep",
                        "vec": [0.0] * mneme.DIM,
                    }
                ],
            }
            index_path.write_text(json.dumps(sentinel), encoding="utf-8")
            before = index_path.read_bytes()
            gathered = [("one", "lesson", "first"), ("two", "lesson", "second")]
            with mock.patch.object(mneme, "INDEX", str(index_path)), mock.patch.object(
                mneme, "pick_embedder", return_value="neural-test-model"
            ), mock.patch.object(mneme, "_gather", return_value=gathered), mock.patch.object(
                mneme, "embed_neural_batch", return_value=[[1.0, 0.0], [1.0]]
            ):
                with self.assertRaisesRegex(ValueError, "dimension"):
                    mneme._build_locked()
            self.assertEqual(index_path.read_bytes(), before)

    def test_truncated_embedding_batch_cannot_publish_partial_corpus(self):
        mneme = load_module(MNEME, "mneme_truncated_publish")
        with tempfile.TemporaryDirectory() as tmp:
            index_path = Path(tmp) / "mneme_index.json"
            gathered = [("one", "lesson", "first"), ("two", "lesson", "second")]
            with mock.patch.object(mneme, "INDEX", str(index_path)), mock.patch.object(
                mneme, "pick_embedder", return_value="neural-test-model"
            ), mock.patch.object(mneme, "_gather", return_value=gathered), mock.patch.object(
                mneme, "embed_neural_batch", return_value=[[1.0, 0.0]]
            ):
                with self.assertRaisesRegex(ValueError, "count"):
                    mneme._build_locked()
            self.assertFalse(index_path.exists())

    def test_lexical_index_dimension_must_match_the_hash_embedder(self):
        mneme = load_module(MNEME, "mneme_lexical_dimension")
        with tempfile.TemporaryDirectory() as tmp:
            index_path = Path(tmp) / "mneme_index.json"
            index_path.write_text(
                json.dumps(
                    {
                        "dim": 1,
                        "embedder": "lexical-hash-v1",
                        "count": 1,
                        "entries": [
                            {"id": "bad", "source": "lesson", "text": "bad", "vec": [1.0]}
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.object(mneme, "INDEX", str(index_path)):
                with self.assertRaisesRegex(ValueError, "lexical"):
                    mneme.load()


if __name__ == "__main__":
    unittest.main()
