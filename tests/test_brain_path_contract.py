"""Shared-brain resolver contract regressions."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BRAIN_PATHS = ROOT / "scripts" / "brain_paths.py"
BINDING_SCHEMA = "project-os/shared-brain-binding/v1"


def load_brain_paths():
    spec = importlib.util.spec_from_file_location("brain_paths_contract", BRAIN_PATHS)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {BRAIN_PATHS}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def absolute(path: Path) -> Path:
    return Path(os.path.abspath(str(path)))


class SharedBrainResolverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.project = self.base / "project"
        self.project.mkdir()
        self.addCleanup(self.tmp.cleanup)

    def _allow_binding(self):
        (self.project / ".gitignore").write_text(
            "brain/shared-brain-binding.jsonl\n"
            "brain/shared-brain-migration-receipt.jsonl\n",
            encoding="utf-8",
        )

    def _binding(self, target: Path, **extra):
        brain = self.project / "brain"
        brain.mkdir(exist_ok=True)
        path = brain / "shared-brain-binding.jsonl"
        path.write_text(
            json.dumps({"schema": BINDING_SCHEMA, "target": str(target), **extra}) + "\n",
            encoding="utf-8",
        )
        return path

    def test_missing_local_brain_never_falls_back_to_home(self):
        paths = load_brain_paths()
        self.assertEqual(
            paths.resolve_shared_brain(self.project, environ={}),
            absolute(self.project / "brain" / "shared-brain.jsonl"),
        )

    def test_absolute_environment_path_wins_and_relative_is_rejected(self):
        paths = load_brain_paths()
        target = absolute(self.base / "env" / "shared-brain.jsonl")
        target.parent.mkdir()
        self.assertEqual(
            paths.resolve_shared_brain(
                self.project, environ={"PROJECT_OS_SHARED_BRAIN": str(target)}
            ),
            target,
        )
        with self.assertRaisesRegex(paths.BrainPathError, "absolute"):
            paths.resolve_shared_brain(
                self.project,
                environ={"PROJECT_OS_SHARED_BRAIN": "relative/shared-brain.jsonl"},
            )

    def test_strict_ignored_binding_selects_external_target(self):
        paths = load_brain_paths()
        target = absolute(self.base / "external" / "shared-brain.jsonl")
        target.parent.mkdir()
        self._allow_binding()
        self._binding(target)
        self.assertEqual(paths.resolve_shared_brain(self.project, environ={}), target)

    def test_binding_requires_exact_ignore_rule_and_schema(self):
        paths = load_brain_paths()
        target = absolute(self.base / "external" / "shared-brain.jsonl")
        target.parent.mkdir()
        binding = self._binding(target)
        with self.assertRaisesRegex(paths.BrainPathError, "gitignore|ignored"):
            paths.resolve_shared_brain(self.project, environ={})

        self._allow_binding()
        binding.write_text(
            json.dumps(
                {"schema": BINDING_SCHEMA, "target": str(target), "unexpected": True}
            )
            + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(paths.BrainPathError, "keys|binding"):
            paths.resolve_shared_brain(self.project, environ={})

        binding.write_text(
            json.dumps({"schema": BINDING_SCHEMA, "target": str(target)})
            + "\n"
            + json.dumps({"schema": BINDING_SCHEMA, "target": str(target)})
            + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(paths.BrainPathError, "one nonblank"):
            paths.resolve_shared_brain(self.project, environ={})

    def test_symlinked_binding_target_and_local_escape_are_rejected(self):
        paths = load_brain_paths()
        self._allow_binding()
        external = self.base / "external"
        external.mkdir()
        target = external / "shared-brain.jsonl"
        target.write_text("", encoding="utf-8")
        binding = self.project / "brain" / "shared-brain-binding.jsonl"
        binding.parent.mkdir()
        binding.symlink_to(target)
        with self.assertRaisesRegex(paths.BrainPathError, "symlink|regular"):
            paths.resolve_shared_brain(self.project, environ={})

        binding.unlink()
        real_target = external / "real.jsonl"
        real_target.write_text("", encoding="utf-8")
        target.unlink()
        target.symlink_to(real_target)
        self._binding(absolute(target))
        with self.assertRaisesRegex(paths.BrainPathError, "symlink"):
            paths.resolve_shared_brain(self.project, environ={})

        binding.unlink()
        outside = self.base / "outside"
        outside.mkdir()
        (self.project / "brain").rmdir()
        (self.project / "brain").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(paths.BrainPathError, "symlink"):
            paths.resolve_shared_brain(self.project, environ={})


class ConsumerResolverIntegrationTests(unittest.TestCase):
    def test_all_root_consumers_default_local_before_brain_exists(self):
        probes = {
            "memory/mneme_adapter.py": "SHARED_BRAIN",
            "scripts/brain_append.py": "SHARED_BRAIN",
            "scripts/brain_archive.py": "BRAIN",
            "scripts/brain_scale.py": "SHARED_BRAIN",
            "scripts/harvest.py": "SHARED_BRAIN",
        }
        with tempfile.TemporaryDirectory() as home:
            env = dict(os.environ)
            env.pop("PROJECT_OS_SHARED_BRAIN", None)
            env["HOME"] = home
            env["PYTHONPYCACHEPREFIX"] = str(Path(home) / "pycache")
            # A developer may deliberately configure this checkout with the
            # ignored binding sidecar.  The consumers must follow the one
            # resolver in either configuration; the default-local behavior is
            # covered independently above without a binding.
            expected = load_brain_paths().resolve_shared_brain(ROOT, environ=env)
            for relative, constant in probes.items():
                with self.subTest(consumer=relative):
                    code = (
                        "import runpy; "
                        f"d=runpy.run_path({str(ROOT / relative)!r}, run_name='probe'); "
                        f"print(d[{constant!r}])"
                    )
                    result = subprocess.run(
                        [sys.executable, "-c", code],
                        cwd=ROOT,
                        env=env,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(Path(result.stdout.strip()), expected)

    def test_source_addons_resolve_repo_root_and_keep_engine_adapter_path(self):
        brain = ROOT / "addons/full-engine/brain/brain.py"
        code = (
            "import runpy; "
            f"d=runpy.run_path({str(brain)!r}, run_name='probe'); "
            "print(d['ROOT']); print(d['BRAIN_FILE']); "
            "print('\\n'.join(d['_adapter_search_paths']()))"
        )
        env = dict(os.environ)
        env.pop("PROJECT_OS_SHARED_BRAIN", None)
        expected_brain = load_brain_paths().resolve_shared_brain(ROOT, environ=env)
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(Path(lines[0]), absolute(ROOT))
        self.assertEqual(
            Path(lines[1]), expected_brain
        )
        self.assertIn(
            str(absolute(ROOT / "addons/full-engine/memory")),
            lines[2:],
        )

        central = ROOT / "addons/full-engine/brain/central_brain.py"
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                f"import runpy; d=runpy.run_path({str(central)!r}, run_name='probe'); "
                "print(d['DEFAULT_PROJECT'])",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(result.stdout.strip()), absolute(ROOT))



class RecordSchemaContractTests(unittest.TestCase):
    def test_aliases_canonicalize_to_type_with_stable_missing_id(self):
        paths = load_brain_paths()
        from_kind = paths.canonicalize_record(
            {"kind": "lesson", "text": "Keep one canonical schema."}
        )
        from_memory_type = paths.canonicalize_record(
            {"memory_type": "lesson", "text": "Keep one canonical schema."}
        )

        self.assertEqual(from_kind, from_memory_type)
        self.assertEqual(from_kind["type"], "lesson")
        self.assertNotIn("kind", from_kind)
        self.assertNotIn("memory_type", from_kind)
        self.assertRegex(from_kind["id"], r"^memory-[0-9a-f]{16}$")

    def test_matching_aliases_are_accepted_but_conflicts_are_rejected(self):
        paths = load_brain_paths()
        canonical = paths.canonicalize_record(
            {
                "id": "same",
                "type": "lesson",
                "kind": "lesson",
                "memory_type": "lesson",
                "text": "matching",
            }
        )
        self.assertEqual(canonical["type"], "lesson")
        self.assertNotIn("kind", canonical)
        self.assertNotIn("memory_type", canonical)

        for record in (
            {"type": "lesson", "kind": "interest", "text": "conflict"},
            {"kind": "lesson", "memory_type": "decision", "text": "conflict"},
        ):
            with self.subTest(record=record):
                with self.assertRaisesRegex(paths.BrainRecordError, "conflict"):
                    paths.canonicalize_record(record)

    def test_brain_append_writes_canonical_row_and_dedupes_generated_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            brain = absolute(base / "brain" / "shared-brain.jsonl")
            env = dict(os.environ)
            env.update(
                {
                    "PROJECT_OS_SHARED_BRAIN": str(brain),
                    "BB_LOCK_DIR": str(base / "locks"),
                    "PYTHONPYCACHEPREFIX": str(base / "pycache"),
                }
            )
            records = (
                {"kind": "lesson", "text": "Canonical append contract."},
                {"memory_type": "lesson", "text": "Canonical append contract."},
            )
            results = []
            for record in records:
                results.append(
                    subprocess.run(
                        [
                            sys.executable,
                            str(ROOT / "scripts/brain_append.py"),
                            "--line",
                            json.dumps(record),
                            "--no-reindex",
                        ],
                        cwd=ROOT,
                        env=env,
                        capture_output=True,
                        text=True,
                    )
                )
            self.assertTrue(all(result.returncode == 0 for result in results), results)
            rows = [
                json.loads(line)
                for line in brain.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["type"], "lesson")
            self.assertNotIn("kind", rows[0])
            self.assertNotIn("memory_type", rows[0])
            self.assertRegex(rows[0]["id"], r"^memory-[0-9a-f]{16}$")
            self.assertIn("kept existing id", results[1].stdout)

    def test_brain_append_rejects_alias_conflict_before_creating_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            brain = absolute(base / "brain" / "shared-brain.jsonl")
            env = dict(os.environ)
            env.update(
                {
                    "PROJECT_OS_SHARED_BRAIN": str(brain),
                    "BB_LOCK_DIR": str(base / "locks"),
                }
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/brain_append.py"),
                    "--line",
                    json.dumps(
                        {
                            "type": "lesson",
                            "kind": "interest",
                            "text": "must be refused",
                        }
                    ),
                    "--no-reindex",
                ],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("conflict", result.stderr.lower())
            self.assertNotIn("Traceback", result.stderr)
            self.assertFalse(brain.exists())

    def test_kind_and_memory_type_legacy_rows_reach_addon_and_central_readers(self):
        brain_spec = importlib.util.spec_from_file_location(
            "brain_reader_contract", ROOT / "addons/full-engine/brain/brain.py"
        )
        central_spec = importlib.util.spec_from_file_location(
            "central_reader_contract",
            ROOT / "addons/full-engine/brain/central_brain.py",
        )
        self.assertIsNotNone(brain_spec)
        self.assertIsNotNone(central_spec)
        brain_module = importlib.util.module_from_spec(brain_spec)
        central_module = importlib.util.module_from_spec(central_spec)
        brain_spec.loader.exec_module(brain_module)
        central_spec.loader.exec_module(central_module)

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            source = project / "source.jsonl"
            rows = [
                {"id": "kind-row", "kind": "lesson", "text": "legacy kind"},
                {
                    "id": "memory-type-row",
                    "memory_type": "lesson",
                    "text": "legacy memory type",
                },
            ]
            source.write_text(
                "".join(json.dumps(row) + "\n" for row in rows),
                encoding="utf-8",
            )
            brain_module.ROOT = str(project)
            addon_lessons = brain_module._lessons_from_file(str(source))
            self.assertEqual(
                {row["id"] for row in addon_lessons},
                {"kind-row", "memory-type-row"},
            )

            project_brain = project / "brain" / "shared-brain.jsonl"
            project_brain.parent.mkdir()
            project_brain.write_bytes(source.read_bytes())
            previous = os.environ.pop("PROJECT_OS_SHARED_BRAIN", None)
            try:
                central_lessons, skipped = central_module.lessons_for_central(
                    project, "legacy"
                )
            finally:
                if previous is not None:
                    os.environ["PROJECT_OS_SHARED_BRAIN"] = previous
            self.assertEqual(
                {row["origin_id"] for row in central_lessons},
                {"kind-row", "memory-type-row"},
            )
            self.assertEqual(skipped, 0)

if __name__ == "__main__":
    unittest.main()
