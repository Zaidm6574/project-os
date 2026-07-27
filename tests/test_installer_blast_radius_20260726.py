"""Installer blast radius (audit 2026-07-26).

The product pitch is "install this into YOUR project", but the installer
followed symlinks in the target and wrote through them:

* a `scripts -> /elsewhere` DIRECTORY link carried the whole scripts/ tree out
  of the target, with no --force needed;
* a DANGLING file link was invisible to the `dst.exists()` "kept existing"
  branch, so the installer created the file wherever the link pointed;
* `--force` rewrote the file a link pointed at -- including through the
  `AGENTS.md -> CLAUDE.md` convention this repo itself endorses, whose only
  backup then held the wrong content;
* `$HOME` was accepted as a target, spraying ~119 files across the user's home
  and appending Project OS rules to ~/.gitignore;
* a mid-install write failure aborted with a raw traceback, leaving a
  half-installed target with no idea which half.

Every assertion here is on FILESYSTEM STATE (byte-for-byte snapshots of the
directory the links pointed at), not on log text. Near-miss cases -- an empty
dir, an ordinary repo, a target that is itself a symlink, a subdirectory of
$HOME, --dry-run, and the documented opt-in flag -- must keep working
unchanged: a guard that blocks real installs is its own defect.

Everything runs in tempfile sandboxes with HOME redirected; nothing touches
the real home directory or the live repo trees.
"""

import functools
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "scripts" / "setup_project_os.py"
INSTALL = ROOT / "install.sh"

OUTSIDE_SENTINEL = b"USER FILE OUTSIDE THE TARGET - MUST NOT BE TOUCHED\n"


def load_setup(name):
    spec = importlib.util.spec_from_file_location(name, SETUP)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def snapshot(root):
    """path -> (kind, bytes|link target) for everything under root."""
    root = Path(root)
    state = {}
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root))
        if path.is_symlink():
            state[rel] = ("symlink", os.readlink(str(path)))
        elif path.is_dir():
            state[rel] = ("dir", None)
        else:
            state[rel] = ("file", path.read_bytes())
    return state


def sandbox_env(home):
    """Env with HOME redirected, and no bytecode written into it.

    macOS's system python caches .pyc under ~/Library/Caches when it cannot
    write next to the source, which would show up as "files appeared in HOME"
    and has nothing to do with the installer.
    """
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def run_setup(args, home):
    """Run the real installer with HOME redirected into the sandbox."""
    return subprocess.run(
        [sys.executable, str(SETUP)] + [str(a) for a in args],
        capture_output=True, text=True, timeout=300, env=sandbox_env(home),
    )


_PYTHON_CANDIDATES = ("python3", "python", "python3.13", "python3.12",
                      "python3.11", "python3.10", "python3.14")


@functools.lru_cache(maxsize=None)
def _path_has_python310():
    """install.sh needs a >=3.10 interpreter on PATH; probe it the same way."""
    for candidate in _PYTHON_CANDIDATES:
        path = shutil.which(candidate)
        if not path:
            continue
        try:
            probe = subprocess.run(
                [path, "-c",
                 "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"],
                capture_output=True, timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if probe.returncode == 0:
            return True
    return False


class HostileTargetSandbox(unittest.TestCase):
    """Builds `outside/` (must never change) next to `target/` (installed into)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.home = self.base / "home"
        self.home.mkdir()
        self.outside = self.base / "outside"
        self.outside.mkdir()
        self.target = self.base / "target"
        self.target.mkdir()

    def assert_outside_unchanged(self, before, proc):
        self.assertEqual(
            snapshot(self.outside), before,
            "the installer wrote outside the target it was pointed at\n"
            "stdout:\n%s\nstderr:\n%s" % (proc.stdout, proc.stderr),
        )

    def test_symlinked_directory_is_not_written_through(self):
        (self.outside / "keep.txt").write_bytes(OUTSIDE_SENTINEL)
        os.symlink(str(self.outside), str(self.target / "scripts"))
        before = snapshot(self.outside)

        proc = run_setup(["--target", self.target], self.home)

        self.assert_outside_unchanged(before, proc)
        self.assertFalse((self.outside / "setup_project_os.py").exists())
        # the link itself is left alone, not replaced by a real directory
        self.assertTrue((self.target / "scripts").is_symlink())
        self.assertNotEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("REFUSED", proc.stdout + proc.stderr)

    def test_symlinked_file_is_not_overwritten_even_with_force(self):
        claude = self.outside / "CLAUDE.md"
        claude.write_bytes(OUTSIDE_SENTINEL)
        os.symlink(str(claude), str(self.target / "CLAUDE.md"))
        before = snapshot(self.outside)

        proc = run_setup(["--target", self.target, "--force"], self.home)

        self.assert_outside_unchanged(before, proc)
        self.assertEqual(claude.read_bytes(), OUTSIDE_SENTINEL)
        self.assertNotEqual(proc.returncode, 0, proc.stdout)

    def test_dangling_symlink_does_not_create_the_outside_file(self):
        """`dst.exists()` is False for a broken link, so nothing stopped this."""
        missing = self.outside / "AGENTS.md"
        os.symlink(str(missing), str(self.target / "AGENTS.md"))
        before = snapshot(self.outside)

        proc = run_setup(["--target", self.target], self.home)

        self.assert_outside_unchanged(before, proc)
        self.assertFalse(missing.exists(),
                         "installer created a file outside the target through a broken link")

    def test_symlinked_gitignore_is_not_merged_through(self):
        gitignore = self.outside / ".gitignore"
        gitignore.write_bytes(b"node_modules/\n")
        os.symlink(str(gitignore), str(self.target / ".gitignore"))
        before = snapshot(self.outside)

        proc = run_setup(["--target", self.target], self.home)

        self.assert_outside_unchanged(before, proc)
        self.assertEqual(gitignore.read_bytes(), b"node_modules/\n")
        self.assertNotIn(b"private-memory/", gitignore.read_bytes())

    def test_dangling_private_dir_symlink_does_not_create_outside_dir(self):
        """mkdir(exist_ok=True) follows links too: through a broken one it creates."""
        missing = self.outside / "somebody-elses-notes"
        os.symlink(str(missing), str(self.target / "private-memory"))
        before = snapshot(self.outside)

        proc = run_setup(["--target", self.target], self.home)

        self.assert_outside_unchanged(before, proc)
        self.assertFalse(missing.exists(),
                         "installer created a directory outside the target")
        # the old code did not create it either -- it died on the mkdir with a
        # raw traceback, which is its own defect
        self.assertNotIn("Traceback", proc.stderr)
        self.assertNotEqual(proc.returncode, 0, proc.stdout)

    def test_symlinked_pre_force_backup_path_does_not_leak_the_users_file(self):
        """The --force backup is a write too, and it copies the USER's content."""
        stolen = self.outside / "steal.txt"
        stolen.write_bytes(OUTSIDE_SENTINEL)
        user_content = b"# my own CLAUDE.md\n"
        (self.target / "CLAUDE.md").write_bytes(user_content)
        os.symlink(str(stolen), str(self.target / "CLAUDE.md.pre-force"))
        before = snapshot(self.outside)

        proc = run_setup(["--target", self.target, "--force"], self.home)

        self.assert_outside_unchanged(before, proc)
        # no safe backup means no overwrite: the user's edit survives
        self.assertEqual((self.target / "CLAUDE.md").read_bytes(), user_content)

    def test_nested_symlink_below_the_target_is_not_written_through(self):
        stolen = self.outside / "mneme_adapter.py"
        stolen.write_bytes(OUTSIDE_SENTINEL)
        (self.target / "memory").mkdir()
        os.symlink(str(stolen), str(self.target / "memory" / "mneme_adapter.py"))
        before = snapshot(self.outside)

        proc = run_setup(["--target", self.target, "--force"], self.home)

        self.assert_outside_unchanged(before, proc)
        self.assertEqual(stolen.read_bytes(), OUTSIDE_SENTINEL)


class ContainedSymlinkConventionTests(unittest.TestCase):
    """`AGENTS.md -> CLAUDE.md` is this repo's own convention (CONFIRMED HIGH).

    Following it under --force wrote the AGENTS.md template into the user's
    CLAUDE.md, so the only copy of their file ended up in a backup named
    AGENTS.md.pre-force and CLAUDE.md.pre-force held template text. The link
    must be skipped instead. It stays inside the target, so the rest of the
    install is still valid and the exit status stays 0.
    """

    def test_force_does_not_rewrite_claude_md_through_the_agents_link(self):
        user_content = b"# my own CLAUDE.md\n"
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            home = base / "home"
            home.mkdir()
            target = base / "project"
            target.mkdir()
            (target / "CLAUDE.md").write_bytes(user_content)
            os.symlink("CLAUDE.md", str(target / "AGENTS.md"))

            proc = run_setup(["--target", target, "--force"], home)

            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue((target / "AGENTS.md").is_symlink(),
                            "the user's link was replaced by a regular file")
            backup = target / "CLAUDE.md.pre-force"
            self.assertTrue(backup.is_file(),
                            "--force overwrote CLAUDE.md without a backup")
            self.assertEqual(
                backup.read_bytes(), user_content,
                "the .pre-force backup does not hold the user's file: the "
                "AGENTS.md template was written through the link first",
            )
            self.assertFalse(
                (target / "AGENTS.md.pre-force").exists(),
                "installer backed the user's file up under the wrong name",
            )
            self.assertEqual((target / "CLAUDE.md").read_bytes(),
                             (ROOT / "CLAUDE.md").read_bytes())
            self.assertIn("REFUSED", proc.stdout + proc.stderr)


class UnsafeTargetRootTests(unittest.TestCase):
    def test_home_directory_target_is_refused_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            (home / ".gitignore").write_bytes(b"personal\n")
            before = snapshot(home)

            proc = run_setup(["--target", home], home)

            after = snapshot(home)
            self.assertEqual(
                sorted(after), sorted(before),
                "installer wrote %d new path(s) into $HOME"
                % (len(after) - len(before)))
            self.assertEqual(after, before, "installer wrote into $HOME")
            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("REFUSED", proc.stdout + proc.stderr)
            self.assertIn("$HOME", proc.stdout + proc.stderr)
            self.assertIn("--allow-unsafe-target", proc.stdout + proc.stderr)

    def test_home_target_by_relative_path_is_refused(self):
        """`./install.sh .` from a home-directory shell is the likely accident."""
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            before = snapshot(home)
            proc = subprocess.run(
                [sys.executable, str(SETUP), "--target", "."],
                capture_output=True, text=True, timeout=300,
                env=sandbox_env(home), cwd=str(home),
            )
            self.assertEqual(snapshot(home), before, "installer wrote into $HOME")
            self.assertNotEqual(proc.returncode, 0, proc.stdout)

    def test_filesystem_root_and_system_dirs_are_named_unsafe(self):
        """Checked in-process: never point the installer itself at / or /usr."""
        setup = load_setup("setup_project_os_unsafe_roots")
        self.assertIn("filesystem root", setup.unsafe_target_root(Path("/")))
        self.assertTrue(setup.unsafe_target_root(Path("/usr")))
        self.assertTrue(setup.unsafe_target_root(Path("/tmp")))
        with tempfile.TemporaryDirectory() as tmp:
            # a real project folder is not unsafe, and neither is a folder
            # whose name merely starts like a system path
            self.assertEqual(setup.unsafe_target_root(Path(tmp) / "proj"), "")
            self.assertEqual(setup.unsafe_target_root(Path(tmp)), "")

    def test_opt_in_flag_allows_the_home_target(self):
        """Near-miss: the documented escape hatch must actually work."""
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            proc = run_setup(["--target", home, "--allow-unsafe-target", "--dry-run"], home)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("would write", proc.stdout)
            self.assertEqual(snapshot(home), {},
                             "--dry-run wrote into the target")

    def test_help_documents_the_opt_in_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = run_setup(["--help"], Path(tmp))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("--allow-unsafe-target", proc.stdout)

    def test_home_subdirectory_still_installs(self):
        """Near-miss: ~/code/my-project is a perfectly ordinary target."""
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            (home / "code").mkdir(parents=True)
            target = home / "code" / "my-project"

            proc = run_setup(["--target", target], home)

            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue((target / "AGENTS.md").is_file())
            self.assertTrue((target / ".gitignore").is_file())
            self.assertNotIn("REFUSED", proc.stdout + proc.stderr)


class PartialInstallFailureTests(unittest.TestCase):
    def test_mid_install_failure_reports_cleanly_and_says_how_to_reverse(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            home = base / "home"
            home.mkdir()
            target = base / "project"
            target.mkdir()
            # a plain FILE where the installer needs a directory: the first
            # copy into prompts/ raises FileExistsError part-way through
            (target / "prompts").write_bytes(b"not a directory\n")

            proc = run_setup(["--target", target], home)

            self.assertEqual(proc.returncode, 1, proc.stdout)
            self.assertNotIn("Traceback", proc.stderr)
            self.assertNotIn("Traceback", proc.stdout)
            output = proc.stdout + proc.stderr
            self.assertIn("REFUSED", output)
            # names what was already written...
            self.assertIn(str(target / "AGENTS.md"), output)
            # ...and how to undo it
            self.assertIn("reverse", output.lower())
            self.assertIn("git status --short --ignored", output)
            # the failure is still real: the target is half installed
            self.assertTrue((target / "AGENTS.md").is_file())


class OrdinaryInstallsAreUnchanged(unittest.TestCase):
    """Near-miss lane: the guards must not disturb a normal install."""

    def test_empty_directory_installs_the_full_starter(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            home = base / "home"
            home.mkdir()
            target = base / "project"
            target.mkdir()

            proc = run_setup(["--target", target], home)

            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn("REFUSED", proc.stdout + proc.stderr)
            self.assertEqual((target / "AGENTS.md").read_bytes(),
                             (ROOT / "AGENTS.md").read_bytes())
            self.assertEqual((target / "CLAUDE.md").read_bytes(),
                             (ROOT / "CLAUDE.md").read_bytes())
            for rel in ("prompts", "scripts", "memory", "blackboard", "runs",
                        "outputs", "addons/full-engine"):
                with self.subTest(rel=rel):
                    self.assertTrue(any((target / rel).rglob("*")), rel)
            self.assertTrue((target / "private-memory").is_dir())
            self.assertTrue((target / "private-imports").is_dir())
            self.assertIn("private-memory/",
                          (target / ".gitignore").read_text(encoding="utf-8"))
            self.assertGreater(len([p for p in target.rglob("*") if p.is_file()]), 100)

    def test_existing_repo_keeps_its_files_and_merges_its_gitignore(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            home = base / "home"
            home.mkdir()
            target = base / "repo"
            (target / "src").mkdir(parents=True)
            (target / "src" / "app.py").write_bytes(b"print('mine')\n")
            (target / ".gitignore").write_bytes(b"node_modules/\n")

            proc = run_setup(["--target", target], home)

            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn("REFUSED", proc.stdout + proc.stderr)
            self.assertEqual((target / "src" / "app.py").read_bytes(), b"print('mine')\n")
            gitignore = (target / ".gitignore").read_text(encoding="utf-8")
            self.assertTrue(gitignore.startswith("node_modules/\n"), gitignore[:80])
            self.assertIn("private-memory/", gitignore)
            self.assertTrue((target / "AGENTS.md").is_file())

    def test_target_that_is_itself_a_symlink_installs_into_the_real_directory(self):
        """The user pointing at their own link is a choice; writes land in the
        resolved directory and containment is measured from there."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            home = base / "home"
            home.mkdir()
            real = base / "real-project"
            real.mkdir()
            link = base / "link-to-project"
            os.symlink(str(real), str(link))

            proc = run_setup(["--target", link], home)

            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn("REFUSED", proc.stdout + proc.stderr)
            self.assertTrue((real / "AGENTS.md").is_file())
            self.assertTrue((real / "scripts" / "setup_project_os.py").is_file())

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            home = base / "home"
            home.mkdir()
            target = base / "project"
            target.mkdir()

            proc = run_setup(["--target", target, "--dry-run"], home)

            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("would write", proc.stdout)
            self.assertEqual(snapshot(target), {}, "--dry-run wrote files")


@unittest.skipUnless(_path_has_python310(), "install.sh requires Python >=3.10 on PATH")
class InstallWrapperTests(unittest.TestCase):
    def _run(self, args, home):
        return subprocess.run(["sh", str(INSTALL)] + [str(a) for a in args],
                              capture_output=True, text=True, timeout=300,
                              env=sandbox_env(home))

    def test_wrapper_refuses_a_home_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            before = snapshot(home)
            proc = self._run([home], home)
            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertEqual(snapshot(home), before)

    def test_wrapper_forwards_the_opt_in_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            proc = self._run([home, "--allow-unsafe-target", "--dry-run"], home)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("would write", proc.stdout)
            self.assertEqual(snapshot(home), {})

    def test_usage_documents_the_opt_in_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = self._run(["--help"], Path(tmp))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("--allow-unsafe-target", proc.stdout)


if __name__ == "__main__":
    unittest.main()
