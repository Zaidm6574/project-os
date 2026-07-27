"""Installer owed-guards tests (2026-07-26).

Two behaviors, each proven in both directions against the real shipped
scripts (no invented fixtures):

1. setup_project_os.py remediation notice: merging the curated .gitignore only
   PREVENTS committing memory/mneme_index.json. When the target's git already
   TRACKS that file (an earlier install committed the cross-project semantic
   memory index), the installer must say so and name the remediation commands.
   No git / not a repo / untracked file must stay silent (fail open).

2. install_full_engine.py --force backup (b4cc70f): overwriting an existing,
   user-edited file must first save a one-deep .pre-force backup holding the
   prior content; the target must end up with the template content.

All work happens in tempfile sandboxes; nothing touches the live repo trees.
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "scripts" / "setup_project_os.py"
FULL_ENGINE = ROOT / "scripts" / "install_full_engine.py"
ADDON_MEMORY = ROOT / "addons" / "full-engine" / "memory"

REMEDIATION_CMD = "git rm --cached memory/mneme_index.json"
WARNING_MARKER = "already tracks memory/mneme_index.json"


def run_script(script, args, env=None, cwd=None):
    return subprocess.run(
        [sys.executable, str(script)] + list(args),
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        cwd=cwd or str(ROOT),
    )


def git(*args):
    return subprocess.run(
        ["git"] + list(args), capture_output=True, text=True, timeout=60
    )


def make_repo_with_committed_index(base):
    """Real git repo whose history already tracks memory/mneme_index.json."""
    target = Path(base) / "proj"
    (target / "memory").mkdir(parents=True)
    (target / "memory" / "mneme_index.json").write_text(
        '{"records": [{"text": "lesson excerpt from another project"}]}\n',
        encoding="utf-8",
    )
    assert git("init", "-q", str(target)).returncode == 0
    assert git("-C", str(target), "add", "memory/mneme_index.json").returncode == 0
    commit = git(
        "-C", str(target),
        "-c", "user.name=t",
        "-c", "user.email=t@example.invalid",
        "-c", "commit.gpgsign=false",
        "commit", "-q", "-m", "earlier install committed the index",
    )
    assert commit.returncode == 0, commit.stderr
    return target


@unittest.skipUnless(shutil.which("git"), "git required to build the tracked-index fixture repo")
class CommittedMnemeIndexNoticeTests(unittest.TestCase):
    def test_warning_printed_when_index_already_committed(self):
        with tempfile.TemporaryDirectory() as base:
            target = make_repo_with_committed_index(base)
            proc = run_script(SETUP, ["--target", str(target), "--dry-run"])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn(WARNING_MARKER, proc.stdout)
            self.assertIn(REMEDIATION_CMD, proc.stdout)
            # The warning must explain the leak and that history still holds it.
            self.assertIn("cross-project", proc.stdout)
            self.assertIn("earlier commits still contain the file", proc.stdout)

    def test_no_warning_for_non_git_target_with_index_on_disk(self):
        with tempfile.TemporaryDirectory() as base:
            target = Path(base) / "proj"
            (target / "memory").mkdir(parents=True)
            (target / "memory" / "mneme_index.json").write_text("{}\n", encoding="utf-8")
            proc = run_script(SETUP, ["--target", str(target), "--dry-run"])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn(WARNING_MARKER, proc.stdout)
            self.assertNotIn(REMEDIATION_CMD, proc.stdout)

    def test_no_warning_when_repo_exists_but_index_untracked(self):
        with tempfile.TemporaryDirectory() as base:
            target = Path(base) / "proj"
            (target / "memory").mkdir(parents=True)
            (target / "memory" / "mneme_index.json").write_text("{}\n", encoding="utf-8")
            self.assertEqual(git("init", "-q", str(target)).returncode, 0)
            proc = run_script(SETUP, ["--target", str(target), "--dry-run"])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn(WARNING_MARKER, proc.stdout)

    def test_fail_open_when_git_binary_absent(self):
        with tempfile.TemporaryDirectory() as base:
            target = make_repo_with_committed_index(base)
            empty_bin = Path(base) / "empty-bin"
            empty_bin.mkdir()
            env = {"PATH": str(empty_bin), "HOME": base}
            proc = run_script(SETUP, ["--target", str(target), "--dry-run"], env=env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn(WARNING_MARKER, proc.stdout)


class ForceBackupTests(unittest.TestCase):
    """install_full_engine --force must back up edited files before overwriting."""

    def _pick_template_file(self):
        candidates = sorted(ADDON_MEMORY.glob("*.py"))
        self.assertTrue(candidates, "no full-engine memory templates found")
        return candidates[0]

    def test_force_backs_up_prior_content_before_overwrite(self):
        template_src = self._pick_template_file()
        with tempfile.TemporaryDirectory() as base:
            target = Path(base) / "proj"
            # Real starter markers the script itself requires.
            (target / "blackboard").mkdir(parents=True)
            (target / "AGENTS.md").write_text("starter\n", encoding="utf-8")
            (target / "blackboard" / "00-project-goal.md").write_text("goal\n", encoding="utf-8")

            first = run_script(FULL_ENGINE, ["--target", str(target)])
            self.assertEqual(first.returncode, 0, first.stderr)

            installed = target / "memory" / template_src.name
            self.assertTrue(installed.is_file(), f"{template_src.name} was not installed")

            sentinel = "# user edit that must survive --force\n"
            installed.write_text(sentinel, encoding="utf-8")

            second = run_script(FULL_ENGINE, ["--target", str(target), "--force"])
            self.assertEqual(second.returncode, 0, second.stderr)

            # Overwrite happened: target now matches the template again.
            self.assertEqual(installed.read_bytes(), template_src.read_bytes())
            # ...and the user's prior content was backed up first.
            backup = installed.with_name(installed.name + ".pre-force")
            self.assertTrue(
                backup.is_file(),
                "--force overwrote a user-edited file without a .pre-force backup",
            )
            self.assertEqual(backup.read_text(encoding="utf-8"), sentinel)

    def test_force_on_unmodified_file_leaves_no_backup(self):
        template_src = self._pick_template_file()
        with tempfile.TemporaryDirectory() as base:
            target = Path(base) / "proj"
            (target / "blackboard").mkdir(parents=True)
            (target / "AGENTS.md").write_text("starter\n", encoding="utf-8")
            (target / "blackboard" / "00-project-goal.md").write_text("goal\n", encoding="utf-8")

            first = run_script(FULL_ENGINE, ["--target", str(target)])
            self.assertEqual(first.returncode, 0, first.stderr)
            second = run_script(FULL_ENGINE, ["--target", str(target), "--force"])
            self.assertEqual(second.returncode, 0, second.stderr)

            installed = target / "memory" / template_src.name
            backup = installed.with_name(installed.name + ".pre-force")
            self.assertFalse(
                backup.exists(),
                "--force created a .pre-force backup for an unmodified file",
            )


if __name__ == "__main__":
    unittest.main()
