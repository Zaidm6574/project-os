"""Integrity and concurrency coverage for the full-engine OSVec adapter."""

import contextlib
import functools
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
OSVEC = ROOT / "addons" / "full-engine" / "memory" / "osvec_adapter.py"
BRAIN = ROOT / "addons" / "full-engine" / "brain" / "brain.py"
BB_LOCK = ROOT / "scripts" / "bb_lock.py"
SECRET_PATTERNS = ROOT / "scripts" / "secret_patterns.py"
# A symlink-free base for temp fixtures (the adapter rejects symlinked store
# paths). realpath(gettempdir()) keeps that property while honoring TMPDIR,
# so sandboxed runners that deny raw /private/tmp writes still work.
REAL_TMP = os.path.realpath(tempfile.gettempdir())


def install_osvec(script):
    """Copy the adapter into a synthetic install, with the sibling
    scripts/secret_patterns.py its privacy gate requires (setup_project_os.py
    copies all of scripts/ in every install)."""
    script.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(OSVEC, script)
    scripts = script.parent.parent / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SECRET_PATTERNS, scripts / "secret_patterns.py")
    return script

try:
    import numpy  # noqa: F401
except Exception:
    NUMPY_AVAILABLE = False
else:
    NUMPY_AVAILABLE = True


DELEGATED_ENV = "PROJECT_OS_OSVEC_TEST_DELEGATED"


def _python_candidates():
    candidates = [sys.executable]
    base_executable = getattr(sys, "_base_executable", None)
    if base_executable:
        candidates.append(base_executable)
    for name in (
        "python3", "python3.14", "python3.13", "python3.12", "python3.11",
        "python3.10",
    ):
        candidate = shutil.which(name)
        if candidate:
            candidates.append(candidate)
    candidates.extend((
        "/opt/homebrew/bin/python3",
        "/opt/homebrew/bin/python3.14",
        "/opt/homebrew/bin/python3.13",
        "/opt/homebrew/bin/python3.12",
        "/usr/local/bin/python3",
        "/usr/local/bin/python3.14",
        "/usr/local/bin/python3.13",
        "/usr/local/bin/python3.12",
        "/Library/Frameworks/Python.framework/Versions/Current/bin/python3",
        "/Library/Frameworks/Python.framework/Versions/3.14/bin/python3.14",
        "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13",
        "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12",
        "/Library/Frameworks/Python.framework/Versions/3.11/bin/python3.11",
        "/Library/Frameworks/Python.framework/Versions/3.10/bin/python3.10",
    ))
    seen = set()
    for candidate in candidates:
        try:
            resolved = str(Path(candidate).resolve(strict=True))
        except (OSError, RuntimeError):
            continue
        if resolved in seen or not os.access(resolved, os.X_OK):
            continue
        seen.add(resolved)
        yield resolved


@functools.lru_cache(maxsize=1)
def _numpy_python():
    probe_env = dict(os.environ)
    probe_env["PYTHONDONTWRITEBYTECODE"] = "1"
    for candidate in _python_candidates():
        try:
            result = subprocess.run(
                [candidate, "-c", "import numpy"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=probe_env,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            return candidate
    return None


def numpy_runtime_test(test_method):
    """Run with NumPy, delegate when available, or skip with a clear reason.

    The public suite is intentionally zero-dependency. A machine with no
    NumPy-capable interpreter must still pass while naming the optional
    coverage it could not execute; a machine that *does* advertise NumPy must
    run the test and fail normally if that delegated execution is broken.
    """
    @functools.wraps(test_method)
    def run(self, *args, **kwargs):
        if NUMPY_AVAILABLE:
            return test_method(self, *args, **kwargs)
        exact_test = "%s.%s" % (type(self).__name__, test_method.__name__)
        if os.environ.get(DELEGATED_ENV):
            self.fail(
                "NumPy import failed inside delegated OSVec test %s; "
                "refusing recursive delegation" % exact_test
            )
        interpreter = _numpy_python()
        if interpreter is None:
            self.skipTest(
                "OSVec integrity test %s requires a Python interpreter that can "
                "import numpy; probed sys.executable, PATH, Homebrew, /usr/local, "
                "and Python Framework locations" % exact_test
            )

        delegated_root = self.base / "delegated-environment"
        env = dict(os.environ)
        env.update({
            DELEGATED_ENV: "1",
            "HOME": str(delegated_root / "home"),
            "BB_LOCK_DIR": str(delegated_root / "locks"),
            "PYTHONPYCACHEPREFIX": str(delegated_root / "pycache"),
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        try:
            result = subprocess.run(
                [interpreter, str(Path(__file__).resolve()), exact_test, "-v"],
                capture_output=True,
                text=True,
                cwd=str(ROOT),
                env=env,
                timeout=120,
            )
        except subprocess.TimeoutExpired as exc:
            self.fail(
                "delegated OSVec integrity test %s timed out under %s: %s"
                % (exact_test, interpreter, exc)
            )
        if result.returncode != 0:
            self.fail(
                "delegated OSVec integrity test %s failed under %s\nstdout:\n%s"
                "\nstderr:\n%s"
                % (exact_test, interpreter, result.stdout, result.stderr)
            )

    return run


def load_osvec():
    name = "osvec_integrity_%s" % uuid.uuid4().hex
    spec = importlib.util.spec_from_file_location(name, OSVEC)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load %s" % OSVEC)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@contextlib.contextmanager
def configured_store(module, store):
    names = ("STORE_DIR", "INDEX_PATH", "SIDECAR_PATH", "MANIFEST_PATH")
    old = {name: getattr(module, name) for name in names if hasattr(module, name)}
    module.STORE_DIR = str(store)
    module.INDEX_PATH = str(store / "project.tvim")
    module.SIDECAR_PATH = str(store / "project.sidecar.json")
    if hasattr(module, "MANIFEST_PATH"):
        module.MANIFEST_PATH = str(store / "project.manifest.json")
    try:
        yield
    finally:
        for name, value in old.items():
            setattr(module, name, value)


def directory_bytes(path):
    return {
        str(item.relative_to(path)): item.read_bytes()
        for item in sorted(path.rglob("*"))
        if item.is_file()
    }


class OSVecIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=REAL_TMP)
        self.base = Path(self.tmp.name)
        self.module = load_osvec() if NUMPY_AVAILABLE else None

    def tearDown(self):
        self.tmp.cleanup()

    def _fresh_fallback_memory(self, store):
        mem = self.module.ProjectMemory()
        mem.index = self.module._BruteForceIndex(mem.dim)
        mem.backend = self.module._BruteForceIndex.backend
        return mem

    @numpy_runtime_test
    def test_selftest_never_changes_the_configured_real_store(self):
        store = self.base / "store"
        store.mkdir()
        (store / "project.tvim").write_bytes(b"real-turbovec-index")
        (store / "project.tvim.npz").write_bytes(b"real-fallback-index")
        (store / "project.sidecar.json").write_bytes(b"real-sidecar-bytes")
        (store / "project.manifest.json").write_bytes(b"real-manifest-bytes")
        (store / "user-owned.bin").write_bytes(b"keep-me")
        before = directory_bytes(store)

        with configured_store(self.module, store), \
                contextlib.redirect_stdout(io.StringIO()):
            result = self.module._selftest()

        self.assertEqual(result, 0)
        self.assertEqual(directory_bytes(store), before)

    @numpy_runtime_test
    def test_sixteen_concurrent_cli_adds_preserve_all_sixteen(self):
        memory_dir = self.base / "installed" / "memory"
        memory_dir.mkdir(parents=True)
        script = memory_dir / "osvec_adapter.py"
        install_osvec(script)
        env = dict(os.environ)
        env["HOME"] = str(self.base / "home")
        env["PYTHONDONTWRITEBYTECODE"] = "1"

        processes = []
        for index in range(16):
            processes.append(subprocess.Popen(
                [
                    sys.executable,
                    str(script),
                    "add",
                    "--text",
                    "concurrent durable lesson %s" % index,
                    "--type",
                    "lesson",
                    "--source",
                    "tests/concurrency-%s.md" % index,
                    "--id",
                    "concurrent-%02d" % index,
                    "--run-slug",
                    "integrity-run",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            ))

        results = [process.communicate(timeout=30) + (process.returncode,)
                   for process in processes]
        failures = [result for result in results if result[2] != 0]
        self.assertEqual(failures, [], failures)

        sidecar = json.loads(
            (memory_dir / "store" / "project.sidecar.json").read_text(encoding="utf-8")
        )
        ids = {record["memory_id"] for record in sidecar["records"].values()}
        self.assertEqual(
            ids,
            {"integrity-run/concurrent-%02d" % index for index in range(16)},
        )

    @numpy_runtime_test
    def test_independent_library_instances_do_not_lose_a_stale_writer(self):
        store = self.base / "library-concurrency-store"
        with configured_store(self.module, store):
            first = self._fresh_fallback_memory(store)
            second = self._fresh_fallback_memory(store)

            first.add("first library writer", "lesson", memory_id="writer-one")
            first.save()
            second.add("second library writer", "lesson", memory_id="writer-two")
            second.save()

            loaded = self.module.ProjectMemory().load()

        self.assertEqual(
            {record["memory_id"] for record in loaded.sidecar.values()},
            {"writer-one", "writer-two"},
        )

    @numpy_runtime_test
    def test_stale_library_instance_refuses_a_same_id_write_conflict(self):
        store = self.base / "library-conflict-store"
        with configured_store(self.module, store):
            seed = self._fresh_fallback_memory(store)
            seed.add("original value", "lesson", memory_id="shared-id")
            seed.save()

            first = self.module.ProjectMemory().load()
            second = self.module.ProjectMemory().load()
            first.add("first writer update", "lesson", memory_id="shared-id")
            first.save()
            second.add("second writer update", "lesson", memory_id="shared-id")

            with self.assertRaisesRegex(self.module.OSVecError, "conflict"):
                second.save()

            loaded = self.module.ProjectMemory().load()

        self.assertEqual(
            next(iter(loaded.sidecar.values()))["text"], "first writer update"
        )

    @numpy_runtime_test
    def test_generated_ids_remain_unique_when_clock_does_not_advance(self):
        store = self.base / "generated-id-store"
        with configured_store(self.module, store):
            mem = self._fresh_fallback_memory(store)
            with mock.patch.object(self.module.time, "time", return_value=1234.5):
                first = mem.add("first generated lesson", "lesson")
                second = mem.add("second generated lesson", "lesson")

        self.assertNotEqual(first.memory_id, second.memory_id)
        self.assertEqual(len(mem.sidecar), 2)
        self.assertEqual(
            {record["text"] for record in mem.sidecar.values()},
            {"first generated lesson", "second generated lesson"},
        )

    @numpy_runtime_test
    def test_project_memory_rejects_a_symlinked_store_directory(self):
        outside = self.base / "outside-store"
        outside.mkdir()
        linked_store = self.base / "linked-store"
        linked_store.symlink_to(outside, target_is_directory=True)

        with self.assertRaisesRegex(self.module.OSVecError, "store.*symlink"):
            self.module.ProjectMemory(store_dir=str(linked_store))

        self.assertEqual(list(outside.iterdir()), [])

    @numpy_runtime_test
    def test_cli_rejects_a_symlinked_default_store_without_writing_outside(self):
        memory_dir = self.base / "symlinked-install" / "memory"
        memory_dir.mkdir(parents=True)
        script = memory_dir / "osvec_adapter.py"
        install_osvec(script)
        outside = self.base / "outside-cli-store"
        outside.mkdir()
        (memory_dir / "store").symlink_to(outside, target_is_directory=True)

        result = subprocess.run(
            [sys.executable, str(script), "stats"],
            capture_output=True,
            text=True,
            env={**os.environ, "HOME": str(self.base / "home")},
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(len(result.stderr.splitlines()), 1, result.stderr)
        self.assertIn("store", result.stderr.lower())
        self.assertIn("symlink", result.stderr.lower())
        self.assertNotIn("traceback", result.stderr.lower())
        self.assertEqual(list(outside.iterdir()), [])

    @numpy_runtime_test
    def test_search_rejects_negative_k_before_the_empty_store_shortcut(self):
        store = self.base / "negative-k-store"
        with configured_store(self.module, store):
            mem = self._fresh_fallback_memory(store)
            with self.assertRaisesRegex(ValueError, "nonnegative"):
                mem.search("anything", k=-1)

    @numpy_runtime_test
    def test_search_allows_zero_k_as_an_empty_result(self):
        store = self.base / "zero-k-store"
        with configured_store(self.module, store):
            mem = self._fresh_fallback_memory(store)
            mem.add("zero result lesson", "lesson", memory_id="zero-k")

            self.assertEqual(mem.search("lesson", k=0), [])

    @numpy_runtime_test
    def test_cli_negative_k_is_a_clean_one_line_error(self):
        memory_dir = self.base / "negative-k-install" / "memory"
        memory_dir.mkdir(parents=True)
        script = memory_dir / "osvec_adapter.py"
        install_osvec(script)

        result = subprocess.run(
            [sys.executable, str(script), "search", "--query", "anything", "-k", "-1"],
            capture_output=True,
            text=True,
            env={**os.environ, "HOME": str(self.base / "home")},
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(len(result.stderr.splitlines()), 1, result.stderr)
        self.assertIn("nonnegative", result.stderr.lower())
        self.assertNotIn("traceback", result.stderr.lower())

    @numpy_runtime_test
    def test_save_fsyncs_and_atomically_replaces_index_and_sidecar(self):
        store = self.base / "atomic-store"
        with configured_store(self.module, store):
            mem = self._fresh_fallback_memory(store)
            mem.add("atomic persistence lesson", "lesson", memory_id="atomic-1")
            with mock.patch.object(
                    self.module.os, "fsync", wraps=self.module.os.fsync) as fsync, \
                    mock.patch.object(
                        self.module.os, "replace", wraps=self.module.os.replace) as replace:
                mem.save()

        self.assertGreaterEqual(fsync.call_count, 2)
        destinations = {Path(call.args[1]).name for call in replace.call_args_list}
        self.assertIn("project.sidecar.json", destinations)
        self.assertTrue(
            {"project.tvim", "project.tvim.npz"} & destinations,
            destinations,
        )

    @numpy_runtime_test
    def test_invalid_staged_index_never_replaces_the_last_good_store(self):
        store = self.base / "validated-store"
        with configured_store(self.module, store):
            mem = self._fresh_fallback_memory(store)
            mem.add("last known good lesson", "lesson", memory_id="good-1")
            mem.save()
            before = directory_bytes(store)

            class CorruptIndex:
                def __len__(self):
                    return 1

                def contains(self, _uid):
                    return True

                def write(self, path):
                    Path(path + ".npz").write_bytes(b"not-a-valid-numpy-index")

            mem.index = CorruptIndex()
            with self.assertRaisesRegex(Exception, "index|persist|corrupt|valid"):
                mem.save()

        self.assertEqual(directory_bytes(store), before)

    @numpy_runtime_test
    def test_replace_failure_after_index_commit_restores_the_last_good_store(self):
        destinations = (
            "project.tvim.npz",
            "project.sidecar.json",
            "project.manifest.json",
        )
        for destination_name in destinations:
            with self.subTest(destination=destination_name):
                store = self.base / ("commit-rollback-" + destination_name)
                with configured_store(self.module, store):
                    mem = self._fresh_fallback_memory(store)
                    mem.add("last known good lesson", "lesson", memory_id="last-good")
                    mem.save()
                    before = directory_bytes(store)
                    mem.add("uncommitted second lesson", "lesson", memory_id="new-write")

                    real_replace = self.module.os.replace
                    injected = {"failed": False}

                    def fail_once(source, destination):
                        if (
                            not injected["failed"]
                            and Path(destination).name == destination_name
                        ):
                            injected["failed"] = True
                            raise OSError("injected commit failure")
                        return real_replace(source, destination)

                    with mock.patch.object(
                        self.module.os, "replace", side_effect=fail_once
                    ):
                        with self.assertRaisesRegex(
                            self.module.OSVecError, "commit|rollback"
                        ):
                            mem.save()

                    self.assertEqual(directory_bytes(store), before)
                    loaded = self.module.ProjectMemory().load()
                    self.assertEqual(
                        {record["memory_id"] for record in loaded.sidecar.values()},
                        {"last-good"},
                    )

    @numpy_runtime_test
    def test_first_save_replace_failures_leave_no_partial_store(self):
        destinations = (
            "project.tvim.npz",
            "project.sidecar.json",
            "project.manifest.json",
        )
        for destination_name in destinations:
            with self.subTest(destination=destination_name):
                store = self.base / ("first-save-rollback-" + destination_name)
                with configured_store(self.module, store):
                    mem = self._fresh_fallback_memory(store)
                    mem.add("first attempted lesson", "lesson", memory_id="first-write")
                    real_replace = self.module.os.replace
                    injected = {"failed": False}

                    def fail_once(source, destination):
                        if (
                            not injected["failed"]
                            and Path(destination).name == destination_name
                        ):
                            injected["failed"] = True
                            raise OSError("injected first-save failure")
                        return real_replace(source, destination)

                    with mock.patch.object(
                        self.module.os, "replace", side_effect=fail_once
                    ):
                        with self.assertRaisesRegex(
                            self.module.OSVecError, "commit|rollback"
                        ):
                            mem.save()

                remaining = {path.name for path in store.iterdir()}
                self.assertEqual(remaining, {".osvec.lock"})

    @numpy_runtime_test
    def test_corrupt_index_is_refused_instead_of_silently_rebuilt(self):
        store = self.base / "corrupt-index-store"
        with configured_store(self.module, store):
            mem = self._fresh_fallback_memory(store)
            mem.add("durable lesson", "lesson", memory_id="durable-1")
            mem.save()
            (store / "project.tvim.npz").write_bytes(b"corrupt-index")

            with self.assertRaisesRegex(Exception, "index|corrupt|integrity"):
                self.module.ProjectMemory().load()

    @numpy_runtime_test
    def test_same_count_sidecar_index_mismatch_is_refused(self):
        store = self.base / "mismatch-store"
        with configured_store(self.module, store):
            mem = self._fresh_fallback_memory(store)
            mem.add("first lesson", "lesson", memory_id="first-id")
            mem.save()

            sidecar_path = store / "project.sidecar.json"
            blob = json.loads(sidecar_path.read_text(encoding="utf-8"))
            old_record = next(iter(blob["records"].values()))
            new_uid = self.module.stable_u64("different-id")
            old_record["memory_id"] = "different-id"
            old_record["u64_id"] = new_uid
            blob["records"] = {str(new_uid): old_record}
            sidecar_path.write_text(json.dumps(blob), encoding="utf-8")

            with self.assertRaisesRegex(Exception, "mismatch|integrity|index"):
                self.module.ProjectMemory().load()

    @numpy_runtime_test
    def test_load_rejects_symlinked_and_hardlinked_persistence_leaves(self):
        leaf_names = (
            "project.tvim.npz",
            "project.sidecar.json",
            "project.manifest.json",
        )
        for link_kind in ("symlink", "hardlink"):
            for leaf_name in leaf_names:
                with self.subTest(link_kind=link_kind, leaf=leaf_name):
                    store = self.base / ("leaf-" + link_kind + "-" + leaf_name)
                    with configured_store(self.module, store):
                        mem = self._fresh_fallback_memory(store)
                        mem.add("durable leaf lesson", "lesson", memory_id="leaf-id")
                        mem.save()
                        leaf = store / leaf_name
                        outside = self.base / (
                            "outside-" + link_kind + "-" + leaf_name
                        )
                        if link_kind == "symlink":
                            leaf.replace(outside)
                            leaf.symlink_to(outside)
                        else:
                            os.link(leaf, outside)

                        with self.assertRaisesRegex(
                            self.module.OSVecError,
                            "regular|symlink|hardlink|link count|persistence leaf",
                        ):
                            self.module.ProjectMemory().load()

    @numpy_runtime_test
    def test_wrong_shape_sidecar_is_a_clean_one_line_cli_error(self):
        memory_dir = self.base / "bad-install" / "memory"
        store = memory_dir / "store"
        store.mkdir(parents=True)
        script = memory_dir / "osvec_adapter.py"
        install_osvec(script)
        (store / "project.sidecar.json").write_text("[1]\n", encoding="utf-8")

        result = subprocess.run(
            [sys.executable, str(script), "stats"],
            capture_output=True,
            text=True,
            env={**os.environ, "HOME": str(self.base / "home")},
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(len(result.stderr.splitlines()), 1, result.stderr)
        self.assertIn("sidecar", result.stderr.lower())
        self.assertNotIn("traceback", result.stderr.lower())

    @numpy_runtime_test
    def test_malformed_record_is_a_clean_one_line_cli_error(self):
        memory_dir = self.base / "bad-record-install" / "memory"
        store = memory_dir / "store"
        store.mkdir(parents=True)
        script = memory_dir / "osvec_adapter.py"
        install_osvec(script)
        uid = self.module.stable_u64("bad-record")
        sidecar = {
            "backend": "bruteforce-fallback",
            "dim": self.module.DIM,
            "embedder": "hashing-v1",
            "records": {
                str(uid): {
                    "memory_id": "bad-record",
                    "u64_id": uid,
                    "text": "malformed record probe",
                    "memory_type": [],
                    "source_file": "tests/probe.md",
                    "tags": [],
                    "created_at": "2026-07-26T00:00:00",
                    "run_slug": "",
                }
            },
        }
        (store / "project.sidecar.json").write_text(
            json.dumps(sidecar), encoding="utf-8"
        )

        result = subprocess.run(
            [sys.executable, str(script), "stats"],
            capture_output=True,
            text=True,
            env={**os.environ, "HOME": str(self.base / "home")},
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(len(result.stderr.splitlines()), 1, result.stderr)
        self.assertIn("memory_type", result.stderr.lower())
        self.assertNotIn("traceback", result.stderr.lower())

    @numpy_runtime_test
    def test_all_persisted_metadata_fields_are_secret_scanned(self):
        secret = "ghp_" + "ABCDEFGHIJKLMNOPQRSTUVWX"
        cases = {
            "text": {"text": secret},
            "id": {"memory_id": secret},
            "source": {"source_file": "notes/%s.md" % secret},
            "tags": {"tags": ["safe", secret]},
            "run slug": {"run_slug": "run-%s" % secret},
        }
        for field, override in cases.items():
            with self.subTest(field=field):
                store = self.base / ("secret-" + field.replace(" ", "-"))
                with configured_store(self.module, store):
                    mem = self._fresh_fallback_memory(store)
                    values = {
                        "text": "safe durable lesson",
                        "memory_type": "lesson",
                        "source_file": "blackboard/19-memory-harvest.md",
                        "memory_id": "safe-id",
                        "tags": ["safe"],
                        "run_slug": "safe-run",
                    }
                    values.update(override)
                    with self.assertRaisesRegex(ValueError, "secret"):
                        mem.add(**values)

    @numpy_runtime_test
    def test_save_secret_scans_created_at_and_unknown_nested_metadata(self):
        secret = "github_pat_" + "A" * 32
        mutations = {
            "created_at": lambda record: record.__setitem__("created_at", secret),
            "nested extra": lambda record: record.__setitem__(
                "audit", {"metadata": {"tokens": ["safe", secret]}}
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(field=label):
                store = self.base / ("persisted-secret-" + label.replace(" ", "-"))
                with configured_store(self.module, store):
                    mem = self._fresh_fallback_memory(store)
                    rec = mem.add(
                        "safe durable lesson", "lesson", memory_id="safe-%s" % label
                    )
                    mutate(mem.sidecar[str(rec.u64_id)])
                    with mock.patch.object(
                        self.module,
                        "_write_json_temp",
                        side_effect=AssertionError("secret reached JSON persistence"),
                    ):
                        with self.assertRaisesRegex(self.module.OSVecError, "secret"):
                            mem.save()

    @numpy_runtime_test
    def test_sidecar_secret_scans_embedder_and_backend_names(self):
        secret = "github_pat_" + "B" * 32
        memory_id = "safe-sidecar-record"
        uid = self.module.stable_u64(memory_id)
        base = {
            "schema": self.module.SIDECAR_SCHEMA,
            "backend": self.module._BruteForceIndex.backend,
            "dim": self.module.DIM,
            "embedder": "hashing-v1",
            "count": 1,
            "records": {
                str(uid): {
                    "memory_id": memory_id,
                    "u64_id": uid,
                    "text": "safe durable lesson",
                    "memory_type": "lesson",
                    "source_file": "blackboard/19-memory-harvest.md",
                    "tags": [],
                    "created_at": "2026-07-26T00:00:00",
                    "run_slug": "",
                }
            },
        }
        for field in ("embedder", "backend"):
            with self.subTest(field=field):
                blob = dict(base)
                blob[field] = secret
                with self.assertRaisesRegex(self.module.OSVecError, "secret"):
                    self.module._validate_sidecar_blob(
                        blob,
                        self.module.DIM,
                        blob["embedder"],
                        self.module.ProjectMemory.VALID_TYPES,
                    )

    @numpy_runtime_test
    def test_github_fine_grained_pat_pattern_is_rejected(self):
        secret = "github_pat_" + "C" * 32

        self.assertIsNotNone(self.module.looks_like_secret(secret))

    @numpy_runtime_test
    def test_brain_export_refuses_a_current_sidecar_without_its_manifest(self):
        project = self.base / "brain-consumer-project"
        brain_script = project / "brain" / "brain.py"
        adapter_script = project / "memory" / "osvec_adapter.py"
        lock_script = project / "scripts" / "bb_lock.py"
        for destination, source in (
            (brain_script, BRAIN),
            (adapter_script, OSVEC),
            (lock_script, BB_LOCK),
            (project / "scripts" / "secret_patterns.py", SECRET_PATTERNS),
            (project / "scripts" / "brain_paths.py",
             ROOT / "scripts" / "brain_paths.py"),
        ):
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

        memory_id = "corrupt-export-lesson"
        uid = self.module.stable_u64(memory_id)
        store = project / "memory" / "store"
        store.mkdir()
        (store / "project.sidecar.json").write_text(
            json.dumps({
                "schema": self.module.SIDECAR_SCHEMA,
                "backend": self.module._BruteForceIndex.backend,
                "dim": self.module.DIM,
                "embedder": "hashing-v1",
                "count": 1,
                "records": {
                    str(uid): {
                        "memory_id": memory_id,
                        "u64_id": uid,
                        "text": "must not export from a partial store",
                        "memory_type": "lesson",
                        "source_file": "blackboard/19-memory-harvest.md",
                        "tags": [],
                        "created_at": "2026-07-26T00:00:00",
                        "run_slug": "",
                    }
                },
            }),
            encoding="utf-8",
        )

        result = subprocess.run(
            [sys.executable, str(brain_script), "export"],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "HOME": str(self.base / "home"),
                "BB_LOCK_DIR": str(self.base / "brain-locks"),
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((project / "brain" / "shared-brain.jsonl").exists())
        self.assertEqual(result.stdout, "")
        self.assertEqual(len(result.stderr.splitlines()), 1, result.stderr)
        self.assertIn("osvec", result.stderr.lower())
        self.assertNotIn("traceback", result.stderr.lower())


if __name__ == "__main__":
    unittest.main()
