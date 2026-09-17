"""Standalone runtime parity must reject retired entrypoints without deleting them."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SYNC = ROOT / "scripts/sync_runtime_assets.py"


class RuntimeRetirementContractTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.canon = self.root / "prompts/workflows"
        self.canon.mkdir(parents=True)
        self.staged = self.root / "addons/full-engine/staged"
        self.workflow("current")
        result = self.run_sync()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.run_sync("--check").returncode, 0)

    def workflow(self, name):
        path = self.canon / (name + ".md")
        path.write_text("---\nname: " + name + "\ndescription: fixture\n---\n"
                        "Synthetic action.\n", encoding="utf-8")
        return path

    def run_sync(self, *args):
        return subprocess.run([sys.executable, str(SYNC), "--root", str(self.root), *args],
                              capture_output=True, text=True, timeout=20)

    def snapshot(self):
        return {str(path.relative_to(self.root)): path.read_bytes()
                for path in self.root.rglob("*") if path.is_file()}

    def assert_retired(self, result, name):
        self.assertNotEqual(result.returncode, 0, result.stdout)
        diagnostic = result.stdout + result.stderr
        self.assertIn("commands/" + name + ".md", diagnostic)
        self.assertIn("codex-skills/" + name + "/SKILL.md", diagnostic)
        self.assertNotIn("Traceback", diagnostic)

    def test_deleted_workflow_is_drift_for_both_adapters(self):
        (self.canon / "current.md").unlink()
        before = self.snapshot()
        self.assert_retired(self.run_sync("--check"), "current")
        self.assertEqual(self.snapshot(), before)

    def test_deleted_workflow_refuses_sync_before_any_writes(self):
        (self.canon / "current.md").unlink()
        self.workflow("added")
        before = self.snapshot()
        self.assert_retired(self.run_sync(), "current")
        self.assertEqual(self.snapshot(), before)

    def test_retired_adapters_are_rejected_even_with_current_index(self):
        command = self.staged / "commands/current.md"
        skill = self.staged / "codex-skills/current/SKILL.md"
        old = {path: path.read_bytes() for path in (command, skill)}
        (self.canon / "current.md").unlink()
        for path in old:
            path.unlink()
        self.assertEqual(self.run_sync().returncode, 0)
        self.assertEqual(self.run_sync("--check").returncode, 0)
        for path, content in old.items():
            path.write_bytes(content)
        before = self.snapshot()
        self.assert_retired(self.run_sync("--check"), "current")
        self.assertEqual(self.snapshot(), before)

    def test_renamed_workflow_is_drift_and_sync_preserves_old_assets(self):
        (self.canon / "current.md").unlink()
        self.workflow("renamed")
        before = self.snapshot()
        self.assert_retired(self.run_sync("--check"), "current")
        self.assert_retired(self.run_sync(), "current")
        self.assertEqual(self.snapshot(), before)

    def test_unknown_user_adapters_are_reported_and_preserved(self):
        command = self.staged / "commands/custom.md"
        skill = self.staged / "codex-skills/custom/SKILL.md"
        skill.parent.mkdir()
        command.write_bytes(b"User-authored command, no generated marker.\n")
        skill.write_bytes(b"User-authored skill, no generated marker.\n")
        before = self.snapshot()
        self.assert_retired(self.run_sync("--check"), "custom")
        self.assert_retired(self.run_sync(), "custom")
        self.assertEqual(self.snapshot(), before)

    def test_auxiliary_files_are_ignored_and_preserved(self):
        for relative in ("commands/notes.txt", "codex-skills/current/reference.md",
                         "codex-skills/unrelated/readme.md"):
            path = self.staged / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"User reference.\n")
        before = self.snapshot()
        self.assertEqual(self.run_sync("--check").returncode, 0)
        self.assertEqual(self.run_sync().returncode, 0)
        self.assertEqual(self.snapshot(), before)

    def test_hand_edit_and_missing_adapter_are_still_repaired(self):
        command = self.staged / "commands/current.md"
        skill = self.staged / "codex-skills/current/SKILL.md"
        expected = self.snapshot()
        command.write_text("hand edit\n", encoding="utf-8")
        skill.unlink()
        drift = self.run_sync("--check")
        self.assertNotEqual(drift.returncode, 0)
        self.assertIn("hand-edited or stale", drift.stdout)
        self.assertIn("missing generated asset", drift.stdout)
        self.assertEqual(self.run_sync().returncode, 0)
        self.assertEqual(self.run_sync("--check").returncode, 0)
        self.assertEqual(self.snapshot(), expected)

    def test_new_workflow_is_generated_and_then_clean(self):
        self.workflow("added")
        self.assertNotEqual(self.run_sync("--check").returncode, 0)
        result = self.run_sync()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.run_sync("--check").returncode, 0)
        self.assertTrue((self.staged / "commands/added.md").is_file())
        self.assertTrue((self.staged / "codex-skills/added/SKILL.md").is_file())


if __name__ == "__main__":
    unittest.main()
