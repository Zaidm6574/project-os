import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "install.sh"
SETUP = ROOT / "scripts" / "setup_project_os.py"
TOOL_CHECK = ROOT / "scripts" / "check_optional_tools.py"
IMPORTER = ROOT / "scripts" / "import_chat_history.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class SetupProjectOSTests(unittest.TestCase):
    def test_install_script_bootstraps_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "project"
            result = subprocess.run(
                ["sh", str(INSTALL), str(target)],
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((target / "AGENTS.md").exists())
            self.assertTrue((target / "prompts" / "research-refresh.md").exists())
            self.assertTrue((target / "blackboard" / "20-research-refresh.md").exists())

    def test_install_script_check_tools_writes_capability_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "project"
            result = subprocess.run(
                ["sh", str(INSTALL), str(target), "--check-tools"],
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            preflight = target / "blackboard" / "17-capability-preflight.md"
            text = preflight.read_text(encoding="utf-8")
            self.assertIn("Automated Optional Tool Check", text)
            self.assertIn("Knowledge Graph", text)
            self.assertIn("Vector Memory", text)
            self.assertIn("Model routing", text)
            self.assertIn("not through `PROJECT_OS_GRAPH_CMD`", text)
            self.assertIn("PROJECT_OS_GRAPH_CMD", text)

    def test_setup_installs_private_ignore_rules_and_scripts(self):
        setup = load_module(SETUP, "setup_project_os")
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "project"
            setup.bootstrap(target, force=False)

            gitignore = target / ".gitignore"
            self.assertTrue(gitignore.exists())
            text = gitignore.read_text(encoding="utf-8")
            self.assertIn("private-memory/", text)
            self.assertIn("private-imports/", text)
            self.assertIn("graphify-out/", text)
            self.assertIn("*.tvim", text)
            self.assertIn("secrets/", text)
            self.assertTrue((target / "scripts" / "import_chat_history.py").exists())
            self.assertTrue((target / "scripts" / "setup_project_os.py").exists())
            self.assertTrue((target / "scripts" / "check_optional_tools.py").exists())
            self.assertFalse((target / "scripts" / "__pycache__").exists())
            self.assertTrue((target / "memory" / "self-improvement-loop.md").exists())
            self.assertIn("Self-Improvement Loop", (target / "memory" / "self-improvement-loop.md").read_text(encoding="utf-8"))

            (target / "private-memory" / "chat-memory.md").write_text("private\n", encoding="utf-8")
            (target / "graphify-out").mkdir()
            (target / "graphify-out" / "graph.json").write_text('{"private": true}\n', encoding="utf-8")
            subprocess.run(["git", "init", str(target)], check=True, capture_output=True, text=True)
            status = subprocess.run(
                ["git", "-C", str(target), "status", "--short", "--untracked-files=all"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            self.assertNotIn("private-memory/chat-memory.md", status)
            self.assertNotIn("graphify-out/graph.json", status)

    def test_setup_merges_gitignore_without_overwriting_existing_content(self):
        setup = load_module(SETUP, "setup_project_os")
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "project"
            target.mkdir()
            (target / ".gitignore").write_text("custom-rule\n", encoding="utf-8")
            setup.bootstrap(target, force=False)

            text = (target / ".gitignore").read_text(encoding="utf-8")
            self.assertIn("custom-rule", text)
            self.assertIn("private-memory/", text)

    def test_setup_completes_partial_existing_gitignore(self):
        setup = load_module(SETUP, "setup_project_os")
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "project"
            target.mkdir()
            (target / ".gitignore").write_text("private-memory/\n", encoding="utf-8")
            setup.bootstrap(target, force=False)

            text = (target / ".gitignore").read_text(encoding="utf-8")
            self.assertIn("private-memory/", text)
            self.assertIn("private-imports/", text)
            self.assertIn("graphify-out/", text)
            self.assertIn("secrets/", text)
            self.assertIn(".secrets", text)

    def test_force_does_not_replace_existing_gitignore_content(self):
        setup = load_module(SETUP, "setup_project_os")
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "project"
            target.mkdir()
            (target / ".gitignore").write_text("custom-rule\n", encoding="utf-8")
            setup.bootstrap(target, force=True)

            text = (target / ".gitignore").read_text(encoding="utf-8")
            self.assertIn("custom-rule", text)
            self.assertIn("private-memory/", text)
            self.assertIn("private-imports/", text)


class ImportChatHistoryTests(unittest.TestCase):
    def run_importer(self, input_path: Path, output_path: Path, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(IMPORTER), "--input", str(input_path), "--output", str(output_path), *extra],
            capture_output=True,
            text=True,
        )

    def test_importer_fails_for_missing_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "summary.md"
            result = self.run_importer(Path(tmp) / "missing", output)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(output.exists())

    def test_importer_default_output_does_not_copy_raw_sensitive_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "export.txt"
            output = Path(tmp) / "summary.md"
            source.write_text(
                "\n".join(
                    [
                        "I want to build a project app with sk-FAKEFAKEFAKEFAKEFAKEFAKE.",
                        "I prefer local tools, but my home address is 123 Main Street and my medical condition is private.",
                        "My GitHub token ghp_FAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKE and email friend@example.com should be hidden.",
                        "My Google key AIzaFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKE should be hidden too.",
                    ]
                ),
                encoding="utf-8",
            )

            result = self.run_importer(source, output)
            self.assertEqual(result.returncode, 0, result.stderr)
            text = output.read_text(encoding="utf-8")
            self.assertNotIn("123 Main Street", text)
            self.assertNotIn("medical condition", text)
            self.assertNotIn("ghp_", text)
            self.assertNotIn("sk-", text)
            self.assertNotIn("AIza", text)
            self.assertNotIn("friend@example.com", text)
            self.assertIn("Candidate preference lines", text)


class OptionalToolCheckTests(unittest.TestCase):
    def test_tool_check_can_write_report_without_project_bootstrap(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [sys.executable, str(TOOL_CHECK), "--target", tmp],
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            text = (Path(tmp) / "blackboard" / "17-capability-preflight.md").read_text(encoding="utf-8")
            self.assertIn("Automated Optional Tool Check", text)
            self.assertIn("Project OS core works without graph or vector tools", text)
            self.assertIn("Model routing is configured in the AI tool", text)
            self.assertIn("| Model routing | Not auto-detected |", text)
            self.assertIn("blackboard/11-model-routing.md", text)

    def test_tool_check_statuses_match_template_labels(self):
        tool_check = load_module(TOOL_CHECK, "check_optional_tools")
        template = (ROOT / "blackboard-template" / "17-capability-preflight.md").read_text(encoding="utf-8")
        label_line = next(line for line in template.splitlines() if line.startswith("Status labels:"))
        labels = {label.strip().rstrip(".") for label in label_line.removeprefix("Status labels:").split(",")}
        report = tool_check.build_report()
        statuses = set()
        for line in report.splitlines():
            if not line.startswith("| ") or line.startswith("| Capability") or line.startswith("|---"):
                continue
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            statuses.add(cells[1])

        self.assertTrue(statuses)
        self.assertLessEqual(statuses, labels)


if __name__ == "__main__":
    unittest.main()
