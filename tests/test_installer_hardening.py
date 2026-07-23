import functools
import importlib.util
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "install.sh"
SETUP = ROOT / "scripts" / "setup_project_os.py"
FULL_ENGINE = ROOT / "scripts" / "install_full_engine.py"


# Mirrors the interpreter probe in install.sh — same candidates, same order,
# same >=3.10 version gate. Cached so the skip decorators probe PATH once.
_PYTHON_CANDIDATES = ("python3", "python", "python3.13", "python3.12",
                      "python3.11", "python3.10", "python3.14")


@functools.lru_cache(maxsize=None)
def _find_python310():
    """Return the path of the first PATH candidate that is Python >=3.10, else None."""
    for candidate in _PYTHON_CANDIDATES:
        path = shutil.which(candidate)
        if not path:
            continue
        try:
            probe = subprocess.run(
                [path, "-c",
                 "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"],
                capture_output=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if probe.returncode == 0:
            return path
    return None


def _path_has_python310():
    return _find_python310() is not None


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class InstallerHardeningTests(unittest.TestCase):
    def test_copy_helpers_exclude_shared_brain_files(self):
        setup = load_module(SETUP, "setup_project_os_shared_brain_filter")
        full_engine = load_module(FULL_ENGINE, "install_full_engine_shared_brain_filter")

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source"
            (source / "brain").mkdir(parents=True)
            (source / "brain" / "helper.py").write_text("# safe helper\n", encoding="utf-8")
            (source / "shared-brain.jsonl").write_text('{"private": true}\n', encoding="utf-8")
            (source / "brain" / "shared-brain.jsonl").write_text('{"private": true}\n', encoding="utf-8")

            starter_target = base / "starter"
            engine_target = base / "engine"
            setup.copy_tree_files(source, starter_target, force=False)
            full_engine.copy_tree(source, engine_target, force=False)

            self.assertTrue((starter_target / "brain" / "helper.py").is_file())
            self.assertTrue((engine_target / "brain" / "helper.py").is_file())
            self.assertFalse((starter_target / "shared-brain.jsonl").exists())
            self.assertFalse((starter_target / "brain" / "shared-brain.jsonl").exists())
            self.assertFalse((engine_target / "shared-brain.jsonl").exists())
            self.assertFalse((engine_target / "brain" / "shared-brain.jsonl").exists())

    def test_copy_helpers_exclude_generated_databases(self):
        # a stale brain-fts-mirror db (or its wal/shm/tmp siblings) can hold raw
        # brain text and an absolute source path — it must never ship on install
        setup = load_module(SETUP, "setup_project_os_db_filter")
        full_engine = load_module(FULL_ENGINE, "install_full_engine_db_filter")

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source"
            (source / "memory").mkdir(parents=True)
            (source / "memory" / "helper.py").write_text("# safe helper\n", encoding="utf-8")
            generated = (
                "brain-fts-mirror.db",
                "brain-fts-mirror.db-wal",
                "brain-fts-mirror.db-shm",
                "brain-fts-mirror.db-journal",
                "tmpabc123.db.tmp",
                "tmpabc123.db.tmp-journal",
            )
            for name in generated:
                (source / "memory" / name).write_text("derived\n", encoding="utf-8")

            starter_target = base / "starter"
            engine_target = base / "engine"
            setup.copy_tree_files(source, starter_target, force=False)
            full_engine.copy_tree(source, engine_target, force=False)

            for target in (starter_target, engine_target):
                self.assertTrue((target / "memory" / "helper.py").is_file())
                for name in generated:
                    self.assertFalse((target / "memory" / name).exists(), name)

    def test_install_script_falls_back_from_old_python3_to_valid_python(self):
        # Point the shim at a REAL >=3.10 interpreter discovered the same way
        # install.sh probes, instead of assuming sys.executable qualifies.
        valid_interpreter = _find_python310()
        if valid_interpreter is None and sys.version_info >= (3, 10):
            valid_interpreter = sys.executable
        if valid_interpreter is None:
            self.skipTest("no Python >=3.10 interpreter found anywhere to act as the fallback target")

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            fake_bin = base / "bin"
            fake_bin.mkdir()

            old_python3 = fake_bin / "python3"
            old_python3.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            old_python3.chmod(old_python3.stat().st_mode | stat.S_IXUSR)

            valid_python = fake_bin / "python"
            valid_python.write_text(f"#!/bin/sh\nexec {shlex.quote(valid_interpreter)} \"$@\"\n", encoding="utf-8")
            valid_python.chmod(valid_python.stat().st_mode | stat.S_IXUSR)

            target = base / "project"
            env = os.environ.copy()
            env["PATH"] = os.pathsep.join((str(fake_bin), "/usr/bin", "/bin"))
            result = subprocess.run(
                ["sh", str(INSTALL), str(target), "--dry-run"],
                capture_output=True,
                text=True,
                env=env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Project OS setup dry run complete.", result.stdout)
            self.assertFalse(target.exists())

    def test_probe_failure_names_found_interpreter(self):
        # env-floor pass (2026-07-17): the old failure said only "no compatible
        # Python" — the probe must name what it DID find (version + path)
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            fake_bin = base / "bin"
            fake_bin.mkdir()
            shim = ("#!/bin/sh\n"
                    'case "$*" in\n'
                    "  *version_info*) exit 1 ;;\n"
                    '  *python_version*) echo "3.9.99" ;;\n'
                    "  *) exit 1 ;;\n"
                    "esac\n")
            for name in _PYTHON_CANDIDATES:
                fake = fake_bin / name
                fake.write_text(shim, encoding="utf-8")
                fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
            target = base / "project"
            env = os.environ.copy()
            env["PATH"] = os.pathsep.join((str(fake_bin), "/usr/bin", "/bin"))
            result = subprocess.run(
                ["sh", str(INSTALL), str(target), "--dry-run"],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("found python3 = 3.9.99", result.stderr)
            self.assertIn(str(fake_bin / "python3"), result.stderr)
            self.assertIn("Python 3.10 or newer", result.stderr)
            self.assertFalse(target.exists())

    def test_direct_full_engine_dry_run_requires_existing_starter(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "blank-project"
            result = subprocess.run(
                [sys.executable, str(FULL_ENGINE), "--target", str(target), "--dry-run"],
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("starter Project OS workspace", result.stderr)
            # clean CLI error, not a raw traceback (judge finding F4)
            self.assertNotIn("Traceback", result.stderr)
            self.assertFalse(target.exists())

    @unittest.skipUnless(_path_has_python310(), "installer requires Python >=3.10 on PATH")
    def test_wrapper_full_engine_dry_run_can_plan_starter_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "planned-project"
            result = subprocess.run(
                ["sh", str(INSTALL), str(target), "--dry-run", "--full-engine"],
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Project OS setup dry run complete.", result.stdout)
            self.assertIn("Project OS full engine add-on dry run complete.", result.stdout)
            self.assertFalse(target.exists())

    def test_central_brain_marker_shell_commands_quote_path_and_project_id(self):
        full_engine = load_module(FULL_ENGINE, "install_full_engine_central_brain_marker")

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            target = base / "project"
            (target / "blackboard").mkdir(parents=True)
            (target / "AGENTS.md").write_text("# Project OS\n", encoding="utf-8")
            (target / "blackboard" / "00-project-goal.md").write_text("# Goal\n", encoding="utf-8")
            central_path = base / "central brain's archive"
            project_id = "client's project id"

            full_engine.install_full_engine(target, central_brain=central_path, project_id=project_id)

            marker = (target / "brain" / "CENTRAL_BRAIN.md").read_text(encoding="utf-8")
            commands = [line for line in marker.splitlines() if line.startswith("python3 ")]
            expected = [
                "python3",
                "brain/central_brain.py",
                "push",
                "--path",
                str(central_path.resolve()),
                "--project",
                ".",
                "--project-id",
                project_id,
            ]

            self.assertEqual([shlex.split(command) for command in commands], [expected, expected[:2] + ["pull"] + expected[3:]])


if __name__ == "__main__":
    unittest.main()
