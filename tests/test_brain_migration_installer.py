import hashlib
import importlib.util
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
FULL_ENGINE = ROOT / "scripts" / "install_full_engine.py"
INSTALL = ROOT / "install.sh"
COHORT_DOC = ROOT / "docs" / "legacy-resolver-cohort.md"

INSTALLED_COHORT_SOURCES = {
    "memory/mneme_adapter.py": "memory/mneme_adapter.py",
    "scripts/brain_append.py": "scripts/brain_append.py",
    "scripts/brain_archive.py": "scripts/brain_archive.py",
    "scripts/brain_scale.py": "scripts/brain_scale.py",
    "scripts/harvest.py": "scripts/harvest.py",
    "brain/brain.py": "addons/full-engine/brain/brain.py",
    "brain/central_brain.py": "addons/full-engine/brain/central_brain.py",
    "scripts/brain_paths.py": "scripts/brain_paths.py",
}


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def snapshot_tree(path: Path):
    if not os.path.lexists(path):
        return None
    rows = []
    for current, dirnames, filenames in os.walk(path, followlinks=False):
        current_path = Path(current)
        rows.append((str(current_path.relative_to(path)), "dir", None))
        for name in list(dirnames):
            child = current_path / name
            if child.is_symlink():
                rows.append((str(child.relative_to(path)), "symlink", os.readlink(child)))
                dirnames.remove(name)
        for name in filenames:
            child = current_path / name
            if child.is_symlink():
                rows.append((str(child.relative_to(path)), "symlink", os.readlink(child)))
            else:
                rows.append((str(child.relative_to(path)), "file", child.read_bytes()))
    return tuple(sorted(rows))


def starter_target(base: Path) -> Path:
    target = base / "project"
    (target / "blackboard").mkdir(parents=True)
    (target / "AGENTS.md").write_text("# Project OS\n", encoding="utf-8")
    (target / "blackboard" / "00-project-goal.md").write_text("# Goal\n", encoding="utf-8")
    return target


def fake_home(base: Path, *, active=b"", archive=b"") -> tuple[Path, Path, Path]:
    home = base / "home"
    legacy_dir = home / ".project-os" / "central-brain"
    legacy_dir.mkdir(parents=True)
    active_path = legacy_dir / "shared-brain.jsonl"
    archive_path = legacy_dir / "shared-brain-archive.jsonl"
    if active is not None:
        active_path.write_bytes(active)
    if archive is not None:
        archive_path.write_bytes(archive)
    return home, active_path, archive_path


def compact_json(payload) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


@contextmanager
def synthetic_legacy_cohort(installer, target: Path, name="synthetic-legacy"):
    signatures = {}
    for installed_rel in INSTALLED_COHORT_SOURCES:
        if installed_rel == "scripts/brain_paths.py":
            continue
        payload = f"legacy resolver fixture: {installed_rel}\n".encode("utf-8")
        destination = target / installed_rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
        signatures[installed_rel] = hashlib.sha256(payload).hexdigest()
    with mock.patch.object(installer, "LEGACY_RESOLVER_SIGNATURES", {name: signatures}):
        yield


class ResolverCohortRegistryTests(unittest.TestCase):
    def test_registry_has_strict_hashes_and_documents_public_and_candidate_cohorts(self):
        installer = load_module(FULL_ENGINE, "install_full_engine_registry")
        self.assertEqual(installer.RESOLVER_COHORT_SOURCES, INSTALLED_COHORT_SOURCES)
        required = {
            "published-v0.1.0-v0.1.1",
            "published-f034ea5",
            "published-07b5805",
            "published-df17b3f",
            "published-e4c6ca0",
            "published-ef0cb9",
            "verified-pre-canonical-2026-07-26",
        }
        self.assertTrue(required.issubset(installer.LEGACY_RESOLVER_SIGNATURES))
        for cohort, signatures in installer.LEGACY_RESOLVER_SIGNATURES.items():
            self.assertTrue(signatures, cohort)
            for relative, digest in signatures.items():
                self.assertRegex(digest, r"\A[0-9a-f]{64}\Z", (cohort, relative, digest))

        documented = COHORT_DOC.read_text(encoding="utf-8")
        self.assertIn("published-ef0cb9", documented)
        self.assertIn("verified-pre-canonical-2026-07-26", documented)
        registry_hashes = {
            digest
            for signatures in installer.LEGACY_RESOLVER_SIGNATURES.values()
            for digest in signatures.values()
        }
        self.assertTrue(
            registry_hashes.issubset(set(re.findall(r"\b[0-9a-f]{64}\b", documented)))
        )

    def test_public_and_candidate_cohorts_match_while_mixed_or_edited_refuse(self):
        installer = load_module(FULL_ENGINE, "install_full_engine_registry_matching")
        current = {relative: "f" * 64 for relative in INSTALLED_COHORT_SOURCES}
        for name in ("published-ef0cb9", "verified-pre-canonical-2026-07-26"):
            signatures = installer.LEGACY_RESOLVER_SIGNATURES[name]
            present = {
                relative: signatures[relative]
                for relative in INSTALLED_COHORT_SOURCES
                if relative in signatures
            }
            self.assertEqual(installer.match_resolver_cohort(present, current), name)
            interrupted = dict(present)
            interrupted["memory/mneme_adapter.py"] = current["memory/mneme_adapter.py"]
            self.assertEqual(installer.match_resolver_cohort(interrupted, current), name)

        mixed = {
            "memory/mneme_adapter.py": installer.LEGACY_RESOLVER_SIGNATURES[
                "published-ef0cb9"
            ]["memory/mneme_adapter.py"],
            "scripts/brain_append.py": installer.LEGACY_RESOLVER_SIGNATURES[
                "verified-pre-canonical-2026-07-26"
            ]["scripts/brain_append.py"],
        }
        with self.assertRaisesRegex(ValueError, "mixed|unknown|edited"):
            installer.match_resolver_cohort(mixed, current)
        edited = dict(mixed)
        edited["scripts/brain_append.py"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "mixed|unknown|edited"):
            installer.match_resolver_cohort(edited, current)


class BrainMigrationInstallerTests(unittest.TestCase):
    def setUp(self):
        self.installer = load_module(
            FULL_ENGINE, f"install_full_engine_migration_{self._testMethodName}"
        )

    def test_modified_cohort_refuses_even_with_force_before_target_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            target = starter_target(base)
            home, _, _ = fake_home(base, active=b'{"legacy":true}\n', archive=None)
            with synthetic_legacy_cohort(self.installer, target):
                edited = target / "scripts" / "brain_append.py"
                edited.write_bytes(edited.read_bytes() + b"# local edit\n")
                before = snapshot_tree(target)
                with mock.patch.dict(os.environ, {"HOME": str(home)}, clear=True):
                    with self.assertRaisesRegex(
                        ValueError, "edited|unknown|mixed"
                    ) as caught:
                        self.installer.install_full_engine(
                            target, force=True, brain_migration="migrate"
                        )
                self.assertIn("migrate manually", str(caught.exception))
                self.assertEqual(snapshot_tree(target), before)

    def test_nonempty_legacy_requires_mode_before_any_target_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            target = starter_target(base)
            home, _, _ = fake_home(base, active=b"  \n", archive=b'{"archived":true}\n')
            with synthetic_legacy_cohort(self.installer, target):
                before = snapshot_tree(target)
                with mock.patch.dict(os.environ, {"HOME": str(home)}, clear=True):
                    with self.assertRaises(ValueError) as caught:
                        self.installer.install_full_engine(target)
                required = "--brain-migration {migrate,bind,fresh-local}"
                self.assertIn(required, str(caught.exception))
                self.assertEqual(str(caught.exception).count(required), 1)
                self.assertEqual(snapshot_tree(target), before)

    def test_migrate_preserves_bytes_modes_receipt_and_upgrades_cohort(self):
        active_bytes = b'{"text":"caf\xc3\xa9"}\r\n\x00tail'
        archive_bytes = b'  {"archive":1}\n'
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            target = starter_target(base)
            home, legacy_active, legacy_archive = fake_home(
                base, active=active_bytes, archive=archive_bytes
            )
            legacy_active.chmod(0o644)
            legacy_archive.chmod(0o666)
            with synthetic_legacy_cohort(self.installer, target):
                with mock.patch.dict(os.environ, {"HOME": str(home)}, clear=True):
                    results = self.installer.install_full_engine(
                        target, brain_migration="migrate"
                    )

            local_active = target / "brain" / "shared-brain.jsonl"
            local_archive = target / "brain" / "shared-brain-archive.jsonl"
            receipt = target / "brain" / "shared-brain-migration-receipt.jsonl"
            self.assertEqual(local_active.read_bytes(), active_bytes)
            self.assertEqual(local_archive.read_bytes(), archive_bytes)
            self.assertEqual(legacy_active.read_bytes(), active_bytes)
            self.assertEqual(legacy_archive.read_bytes(), archive_bytes)
            self.assertEqual(stat.S_IMODE(local_active.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(local_archive.stat().st_mode), 0o600)
            self.assertEqual(
                receipt.read_bytes(),
                compact_json(
                    {
                        "schema": "project-os/shared-brain-migration-receipt/v1",
                        "mode": "migrate",
                        "legacy_active": str(legacy_active.resolve()),
                        "legacy_archive": str(legacy_archive.resolve()),
                    }
                ),
            )
            self.assertTrue(any("migrated legacy shared brain" in item for item in results))
            for installed_rel, source_rel in INSTALLED_COHORT_SOURCES.items():
                self.assertEqual(
                    (target / installed_rel).read_bytes(), (ROOT / source_rel).read_bytes()
                )

    def test_migrate_uses_explicit_legacy_central_brain_env_path(self):
        active_bytes = b'{"custom":"active"}\n'
        archive_bytes = b'{"custom":"archive"}\n'
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            target = starter_target(base)
            home, _, _ = fake_home(base, active=None, archive=None)
            custom_dir = base / "portable-state" / "legacy-brain"
            custom_dir.mkdir(parents=True)
            custom_active = custom_dir / "shared-brain.jsonl"
            custom_archive = custom_dir / "shared-brain-archive.jsonl"
            custom_active.write_bytes(active_bytes)
            custom_archive.write_bytes(archive_bytes)
            with synthetic_legacy_cohort(self.installer, target):
                env = {
                    "HOME": str(home),
                    "PROJECT_OS_LEGACY_CENTRAL_BRAIN": str(custom_dir),
                }
                with mock.patch.dict(os.environ, env, clear=True):
                    self.installer.install_full_engine(
                        target, brain_migration="migrate"
                    )

            self.assertEqual(
                (target / "brain" / "shared-brain.jsonl").read_bytes(), active_bytes
            )
            self.assertEqual(
                (target / "brain" / "shared-brain-archive.jsonl").read_bytes(),
                archive_bytes,
            )
            receipt = json.loads(
                (target / "brain" / "shared-brain-migration-receipt.jsonl")
                .read_text(encoding="utf-8")
            )
            self.assertEqual(receipt["legacy_active"], str(custom_active.resolve()))
            self.assertEqual(receipt["legacy_archive"], str(custom_archive.resolve()))

    def test_migrate_refuses_nonempty_local_active_or_archive(self):
        for local_relative in (
            "brain/shared-brain.jsonl",
            "brain/shared-brain-archive.jsonl",
        ):
            with self.subTest(local_relative=local_relative), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                target = starter_target(base)
                local = target / local_relative
                local.parent.mkdir(parents=True, exist_ok=True)
                local.write_bytes(b"local data\n")
                home, _, _ = fake_home(
                    base, active=b"legacy data\n", archive=b"legacy archive\n"
                )
                with synthetic_legacy_cohort(self.installer, target):
                    before = snapshot_tree(target)
                    with mock.patch.dict(os.environ, {"HOME": str(home)}, clear=True):
                        with self.assertRaisesRegex(ValueError, "nonempty local"):
                            self.installer.install_full_engine(
                                target, brain_migration="migrate"
                            )
                    self.assertEqual(snapshot_tree(target), before)

    def test_bind_writes_only_exact_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            target = starter_target(base)
            home, legacy_active, _ = fake_home(
                base, active=b"private legacy\n", archive=b"archive\n"
            )
            with synthetic_legacy_cohort(self.installer, target):
                with mock.patch.dict(os.environ, {"HOME": str(home)}, clear=True):
                    self.installer.install_full_engine(target, brain_migration="bind")

            binding = target / "brain" / "shared-brain-binding.jsonl"
            self.assertEqual(
                binding.read_bytes(),
                b'{"schema":"project-os/shared-brain-binding/v1","target":"'
                + str(legacy_active.resolve()).encode("utf-8")
                + b'"}\n',
            )
            self.assertFalse(
                (target / "brain" / "shared-brain-migration-receipt.jsonl").exists()
            )
            self.assertFalse((target / "brain" / "shared-brain.jsonl").exists())
            self.assertFalse((target / "brain" / "shared-brain-archive.jsonl").exists())

    def test_fresh_local_writes_receipt_without_copying_legacy(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            target = starter_target(base)
            home, legacy_active, legacy_archive = fake_home(
                base, active=b"do not copy active\n", archive=b"do not copy archive\n"
            )
            with synthetic_legacy_cohort(self.installer, target):
                with mock.patch.dict(os.environ, {"HOME": str(home)}, clear=True):
                    self.installer.install_full_engine(
                        target, brain_migration="fresh-local"
                    )

            self.assertEqual((target / "brain" / "shared-brain.jsonl").read_bytes(), b"")
            self.assertFalse((target / "brain" / "shared-brain-archive.jsonl").exists())
            self.assertEqual(
                (target / "brain" / "shared-brain-migration-receipt.jsonl").read_bytes(),
                compact_json(
                    {
                        "schema": "project-os/shared-brain-migration-receipt/v1",
                        "mode": "fresh-local",
                        "legacy_active": str(legacy_active.resolve()),
                        "legacy_archive": str(legacy_archive.resolve()),
                    }
                ),
            )

    def test_valid_explicit_env_bypasses_unsafe_legacy_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            target = starter_target(base)
            home, legacy_active, _ = fake_home(base, active=None, archive=None)
            outside = base / "outside.jsonl"
            outside.write_bytes(b"outside\n")
            legacy_active.symlink_to(outside)
            explicit = base / "explicit.jsonl"
            explicit.write_bytes(b"explicit\n")
            env = {
                "HOME": str(home),
                "PROJECT_OS_SHARED_BRAIN": str(explicit.resolve()),
            }
            with synthetic_legacy_cohort(self.installer, target):
                with mock.patch.dict(os.environ, env, clear=True):
                    self.installer.install_full_engine(target)

            self.assertFalse(
                (target / "brain" / "shared-brain-migration-receipt.jsonl").exists()
            )
            self.assertFalse((target / "brain" / "shared-brain-binding.jsonl").exists())
            self.assertEqual(outside.read_bytes(), b"outside\n")

    def test_dry_run_migrate_is_byte_for_byte_non_mutating(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            target = starter_target(base)
            home, legacy_active, legacy_archive = fake_home(
                base, active=b"legacy active\n", archive=b"legacy archive\n"
            )
            with synthetic_legacy_cohort(self.installer, target):
                before_target = snapshot_tree(target)
                before_active = legacy_active.read_bytes()
                before_archive = legacy_archive.read_bytes()
                with mock.patch.dict(os.environ, {"HOME": str(home)}, clear=True):
                    results = self.installer.install_full_engine(
                        target, brain_migration="migrate", dry_run=True
                    )
                self.assertEqual(snapshot_tree(target), before_target)
                self.assertEqual(legacy_active.read_bytes(), before_active)
                self.assertEqual(legacy_archive.read_bytes(), before_archive)
                self.assertTrue(
                    any("would migrate legacy shared brain" in item for item in results)
                )

    def test_legacy_symlink_or_hardlink_refuses_before_target_mutation(self):
        for kind in ("symlink", "hardlink"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                target = starter_target(base)
                home, legacy_active, _ = fake_home(base, active=None, archive=None)
                outside = base / "outside.jsonl"
                outside.write_bytes(b"legacy\n")
                if kind == "symlink":
                    legacy_active.symlink_to(outside)
                else:
                    os.link(outside, legacy_active)
                with synthetic_legacy_cohort(self.installer, target):
                    before = snapshot_tree(target)
                    with mock.patch.dict(os.environ, {"HOME": str(home)}, clear=True):
                        with self.assertRaisesRegex(ValueError, "symlink|hardlink"):
                            self.installer.install_full_engine(
                                target, brain_migration="migrate"
                            )
                    self.assertEqual(snapshot_tree(target), before)

    def test_migrate_preserves_already_restrictive_legacy_permissions(self):
        active_bytes = b'{"keep":"0600"}\n'
        archive_bytes = b'{"keep":"0400"}\n'
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            target = starter_target(base)
            home, legacy_active, legacy_archive = fake_home(
                base, active=active_bytes, archive=archive_bytes
            )
            legacy_active.chmod(0o600)
            legacy_archive.chmod(0o400)
            with synthetic_legacy_cohort(self.installer, target):
                with mock.patch.dict(os.environ, {"HOME": str(home)}, clear=True):
                    self.installer.install_full_engine(
                        target, brain_migration="migrate"
                    )

            local_active = target / "brain" / "shared-brain.jsonl"
            local_archive = target / "brain" / "shared-brain-archive.jsonl"
            self.assertEqual(local_active.read_bytes(), active_bytes)
            self.assertEqual(local_archive.read_bytes(), archive_bytes)
            # 0600 stays 0600 and an even tighter 0400 is never widened.
            self.assertEqual(stat.S_IMODE(local_active.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(local_archive.stat().st_mode), 0o400)
            self.assertEqual(stat.S_IMODE(legacy_active.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(legacy_archive.stat().st_mode), 0o400)

    def test_migrate_holds_bb_lock_and_aborts_when_fence_is_lost(self):
        active_bytes = b'{"fenced":"active"}\n'
        archive_bytes = b'{"fenced":"archive"}\n'
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            target = starter_target(base)
            home, legacy_active, legacy_archive = fake_home(
                base, active=active_bytes, archive=archive_bytes
            )
            original_load = self.installer._load_sibling_module
            lock_calls = {"acquire": [], "renew": 0, "release": []}

            def instrumented_load(name):
                module = original_load(name)
                if name != "bb_lock":
                    return module

                def acquire(lock_target, agent="unknown", wait=10.0):
                    lock_calls["acquire"].append(
                        (os.path.realpath(str(lock_target)), agent)
                    )
                    return module.acquire(lock_target, agent=agent, wait=wait)

                def renew(lock_target, token=None):
                    lock_calls["renew"] += 1
                    if lock_calls["renew"] >= 2:
                        # Simulate losing the lease at the publication fence.
                        return False
                    return module.renew(lock_target, token)

                def release(lock_target, agent=None, force=False, token=None):
                    outcome = module.release(
                        lock_target, agent=agent, force=force, token=token
                    )
                    lock_calls["release"].append(outcome)
                    return outcome

                return types.SimpleNamespace(
                    acquire=acquire, renew=renew, release=release,
                    fenced=module.fenced, LockLeaseLost=module.LockLeaseLost
                )

            with synthetic_legacy_cohort(self.installer, target):
                with mock.patch.dict(os.environ, {"HOME": str(home)}, clear=True):
                    with mock.patch.object(
                        self.installer, "_load_sibling_module", instrumented_load
                    ):
                        with self.assertRaisesRegex(
                            OSError, "lock lease was lost before publication"
                        ):
                            self.installer.install_full_engine(
                                target, brain_migration="migrate"
                            )

            expected_lock_target = os.path.realpath(str(legacy_active))
            self.assertEqual(
                lock_calls["acquire"],
                [(expected_lock_target, "brain-migration-installer")] * 2,
            )
            self.assertEqual(lock_calls["renew"], 2)
            self.assertEqual(lock_calls["release"], [True, True])
            self.assertEqual(legacy_active.read_bytes(), active_bytes)
            self.assertEqual(legacy_archive.read_bytes(), archive_bytes)
            brain_dir = target / "brain"
            self.assertFalse((brain_dir / "shared-brain.jsonl").exists())
            self.assertFalse((brain_dir / "shared-brain-archive.jsonl").exists())
            self.assertFalse(
                (brain_dir / "shared-brain-migration-receipt.jsonl").exists()
            )
            self.assertFalse((brain_dir / "shared-brain-binding.jsonl").exists())
            if brain_dir.exists():
                self.assertEqual(
                    [path.name for path in brain_dir.glob(".*.installer-*")], []
                )


class BrainMigrationCliTests(unittest.TestCase):
    def test_direct_help_advertises_brain_migration_choices(self):
        result = subprocess.run(
            [sys.executable, str(FULL_ENGINE), "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "--brain-migration {migrate,bind,fresh-local}", result.stdout
        )
        self.assertIn("--legacy-central-brain", result.stdout)
        self.assertIn("PROJECT_OS_LEGACY_CENTRAL_BRAIN", result.stdout)

    def test_wrapper_forwards_mode_to_preflight_and_final_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            distribution = base / "distribution"
            (distribution / "scripts").mkdir(parents=True)
            (distribution / "addons" / "full-engine" / "memory").mkdir(parents=True)
            (distribution / "addons" / "full-engine" / "brain").mkdir(parents=True)
            (distribution / "addons" / "full-engine" / "blackboard-addons").mkdir(
                parents=True
            )
            (distribution / "install.sh").write_bytes(INSTALL.read_bytes())
            (distribution / "scripts" / "setup_project_os.py").write_text(
                "raise SystemExit(0)\n", encoding="utf-8"
            )
            (distribution / "scripts" / "install_full_engine.py").write_text(
                "import json, os, sys\n"
                "with open(os.environ['ARGS_LOG'], 'a', encoding='utf-8') as stream:\n"
                "    stream.write(json.dumps(sys.argv[1:]) + '\\n')\n",
                encoding="utf-8",
            )
            for relative in (
                "memory/new_run.py",
                "memory/validate_run.py",
                "memory/score_rubric.py",
                "brain/brain.py",
                "brain/central_brain.py",
                "blackboard-addons/21-agent-roster.md",
            ):
                path = distribution / "addons" / "full-engine" / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fixture\n", encoding="utf-8")

            fake_bin = base / "bin"
            fake_bin.mkdir()
            python3 = fake_bin / "python3"
            python3.write_text(
                "#!/bin/sh\n"
                'if [ "$#" -eq 2 ] && [ "$1" = "-c" ]; then exit 0; fi\n'
                f'exec {shlex.quote(sys.executable)} "$@"\n',
                encoding="utf-8",
            )
            python3.chmod(python3.stat().st_mode | stat.S_IXUSR)
            args_log = base / "args.jsonl"
            home = base / "home"
            home.mkdir()
            env = os.environ.copy()
            env.update(
                {
                    "ARGS_LOG": str(args_log),
                    "HOME": str(home),
                    "PATH": str(fake_bin) + os.pathsep + env.get("PATH", ""),
                }
            )
            target = base / "target"
            result = subprocess.run(
                [
                    "sh",
                    str(distribution / "install.sh"),
                    str(target),
                    "--full-engine",
                    "--brain-migration",
                    "bind",
                    "--dry-run",
                ],
                env=env,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            calls = [
                json.loads(line)
                for line in args_log.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(calls), 2)
            for call in calls:
                index = call.index("--brain-migration")
                self.assertEqual(call[index + 1], "bind")
                self.assertIn("--starter-planned", call)
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
