import importlib.util
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "install.sh"
SETUP = ROOT / "scripts" / "setup_project_os.py"
TOOL_CHECK = ROOT / "scripts" / "check_optional_tools.py"
IMPORTER = ROOT / "scripts" / "import_chat_history.py"
FULL_ENGINE = ROOT / "scripts" / "install_full_engine.py"
CENTRAL_BRAIN = ROOT / "addons" / "full-engine" / "brain" / "central_brain.py"


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
            self.assertTrue((target / "addons" / "full-engine" / "README.md").exists())
            self.assertTrue((target / "addons" / "full-engine" / "staged" / "commands" / "ui-review.md").exists())
            self.assertTrue((target / "addons" / "full-engine" / "staged" / "agents" / "ui-ux-designer.md").exists())
            self.assertTrue((target / "addons" / "full-engine" / "staged" / "agents" / "frontend-builder.md").exists())

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
            self.assertIn("GraphOS", text)
            self.assertIn("OSVec", text)
            self.assertIn("Model routing", text)
            self.assertIn("not through `PROJECT_OS_GRAPHOS_CMD`", text)
            self.assertIn("PROJECT_OS_GRAPHOS_CMD", text)
            self.assertIn("PROJECT_OS_OSVEC_CMD", text)
            self.assertIn("Legacy `PROJECT_OS_GRAPH_CMD`", text)
            self.assertIn("scripts/install_full_engine.py", text)

    def test_install_script_full_engine_activates_local_tools_and_claude_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "project"
            result = subprocess.run(
                ["sh", str(INSTALL), str(target), "--full-engine", "--claude-engine", "--check-tools"],
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((target / "memory" / "new_run.py").exists())
            self.assertTrue((target / "memory" / "osvec_adapter.py").exists())
            self.assertTrue((target / "memory" / "build_graph.py").exists())
            self.assertTrue((target / "brain" / "brain.py").exists())
            self.assertTrue((target / "brain" / "central_brain.py").exists())
            self.assertTrue((target / "brain" / "shared-brain.jsonl").exists())
            self.assertTrue((target / "blackboard" / "21-agent-roster.md").exists())
            self.assertTrue((target / ".claude" / "commands" / "new-run.md").exists())
            self.assertTrue((target / ".claude" / "commands" / "save-chat.md").exists())
            self.assertTrue((target / ".claude" / "commands" / "ui-review.md").exists())
            self.assertTrue((target / ".claude" / "agents" / "project-os-ceo.md").exists())
            self.assertTrue((target / ".claude" / "agents" / "ui-ux-designer.md").exists())
            self.assertTrue((target / ".claude" / "agents" / "frontend-builder.md").exists())

            preflight = target / "blackboard" / "17-capability-preflight.md"
            text = preflight.read_text(encoding="utf-8")
            self.assertIn("Full engine GraphOS builder found: memory/build_graph.py", text)
            self.assertIn("graph artifact not built yet", text)
            self.assertIn("Full engine OSVec adapter found: memory/osvec_adapter.py", text)
            self.assertIn("vector store not populated yet", text)

    def test_install_script_full_engine_can_initialize_central_brain(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "project"
            central = Path(tmp) / "central"
            result = subprocess.run(
                [
                    "sh",
                    str(INSTALL),
                    str(target),
                    "--full-engine",
                    "--central-brain",
                    str(central),
                    "--project-id",
                    "demo",
                    "--check-tools",
                ],
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((central / "shared-brain.jsonl").exists())
            self.assertTrue((central / "README.md").exists())
            self.assertTrue((target / "brain" / "central_brain.py").exists())
            marker = target / "brain" / "CENTRAL_BRAIN.md"
            self.assertTrue(marker.exists())
            marker_text = marker.read_text(encoding="utf-8")
            self.assertIn("Project ID: `demo`", marker_text)
            self.assertIn(str(central), marker_text)

    def test_ui_workflow_guidance_is_available_for_web_app_runs(self):
        files = [
            ROOT / "AGENTS.md",
            ROOT / "CLAUDE.md",
            ROOT / "README.md",
            ROOT / "prompts" / "project-os-kickoff.md",
            ROOT / "addons" / "full-engine" / "README.md",
        ]
        combined = "\n".join(path.read_text(encoding="utf-8") for path in files)

        self.assertIn("ui-ux-designer", combined)
        self.assertIn("frontend-builder", combined)
        self.assertIn("/ui-review", combined)
        self.assertIn("responsive layout", combined)
        self.assertIn("browser QA", combined)

    def test_installed_full_engine_can_save_chat_summary_to_brain(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "project"
            install = subprocess.run(
                ["sh", str(INSTALL), str(target), "--full-engine"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(install.returncode, 0, install.stderr)

            result = subprocess.run(
                [
                    sys.executable,
                    str(target / "brain" / "brain.py"),
                    "save-chat",
                    "--summary",
                    "When users ask to remember a chat, save a compact approved summary instead of raw logs.",
                    "--id",
                    "chat-save-001",
                    "--kind",
                    "lesson",
                    "--tag",
                    "chat",
                    "--tag",
                    "privacy",
                ],
                cwd=target,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("save-chat: appended chat-save-001", result.stdout)
            records = [
                __import__("json").loads(line)
                for line in (target / "brain" / "shared-brain.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(records), 1)
            record = records[0]
            self.assertEqual(record["id"], "chat-save-001")
            self.assertEqual(record["type"], "lesson")
            self.assertEqual(record["source"], "chat-summary")
            self.assertTrue(record["summary_only"])
            self.assertFalse(record["raw_chat"])
            self.assertIn("chat", record["tags"])
            self.assertIn("privacy", record["tags"])

    def test_installed_full_engine_refuses_secret_like_chat_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "project"
            install = subprocess.run(
                ["sh", str(INSTALL), str(target), "--full-engine"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(install.returncode, 0, install.stderr)

            result = subprocess.run(
                [
                    sys.executable,
                    str(target / "brain" / "brain.py"),
                    "save-chat",
                    "--summary",
                    "The user pasted token sk-FAKEFAKEFAKEFAKEFAKEFAKE and asked us to keep it.",
                    "--id",
                    "bad-secret",
                ],
                cwd=target,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("refuse", result.stderr + result.stdout)
            self.assertEqual((target / "brain" / "shared-brain.jsonl").read_text(encoding="utf-8"), "")

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
            self.assertIn("memory/store/", text)
            self.assertIn("brain/shared-brain.jsonl", text)
            self.assertIn("central-brain/", text)
            self.assertIn(".project-os-central-brain/", text)
            self.assertIn("*.tvim", text)
            self.assertIn("secrets/", text)
            self.assertTrue((target / "scripts" / "import_chat_history.py").exists())
            self.assertTrue((target / "scripts" / "setup_project_os.py").exists())
            self.assertTrue((target / "scripts" / "check_optional_tools.py").exists())
            self.assertTrue((target / "scripts" / "install_full_engine.py").exists())
            self.assertTrue((target / "addons" / "full-engine" / "memory" / "new_run.py").exists())
            self.assertFalse((target / "scripts" / "__pycache__").exists())
            self.assertTrue((target / "memory" / "self-improvement-loop.md").exists())
            self.assertIn("Self-Improvement Loop", (target / "memory" / "self-improvement-loop.md").read_text(encoding="utf-8"))
            self.assertTrue((target / "blackboard" / "10-osvec-index.md").exists())
            self.assertFalse((target / "blackboard" / "10-vector-memory.md").exists())

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
            self.assertIn("Project OS core works without GraphOS or OSVec tools", text)
            self.assertIn("Model routing is configured in the AI tool", text)
            self.assertIn("| Model routing | Not auto-detected |", text)
            self.assertIn("blackboard/11-model-routing.md", text)
            self.assertIn("does not install anything", text)
            self.assertIn("scripts/install_full_engine.py --target .", text)

    def test_tool_check_detects_installed_full_engine_files(self):
        tool_check = load_module(TOOL_CHECK, "check_optional_tools")
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            (target / "memory").mkdir()
            (target / "memory" / "build_graph.py").write_text("# graphos\n", encoding="utf-8")
            (target / "memory" / "osvec_adapter.py").write_text("# osvec\n", encoding="utf-8")

            report = tool_check.build_report(target)

        self.assertIn("Full engine GraphOS builder found: memory/build_graph.py", report)
        self.assertIn("graph artifact not built yet", report)
        self.assertIn("Full engine OSVec adapter found: memory/osvec_adapter.py", report)
        self.assertIn("vector store not populated yet", report)
        self.assertIn("Do not tell the user GraphOS/OSVec are unavailable when these local scripts exist", report)

    def test_tool_check_detects_legacy_turbovec_adapter_as_osvec(self):
        tool_check = load_module(TOOL_CHECK, "check_optional_tools")
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            (target / "memory").mkdir()
            (target / "memory" / "turbovec_adapter.py").write_text("# legacy osvec\n", encoding="utf-8")

            report = tool_check.build_report(target)

        self.assertIn("Legacy OSVec/TurboVec adapter found: memory/turbovec_adapter.py", report)
        self.assertIn("prefer memory/osvec_adapter.py for new projects", report)
        self.assertNotIn("| OSVec | Not configured |", report)

    def test_full_engine_installer_preserves_existing_files_without_force(self):
        full_engine = load_module(FULL_ENGINE, "install_full_engine")
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            existing = target / "memory" / "new_run.py"
            existing.parent.mkdir(parents=True)
            existing.write_text("# keep me\n", encoding="utf-8")

            results = full_engine.install_full_engine(target, force=False, claude=False)

            self.assertEqual(existing.read_text(encoding="utf-8"), "# keep me\n")
            self.assertTrue((target / "memory" / "osvec_adapter.py").exists())
            self.assertTrue((target / "blackboard" / "21-agent-roster.md").exists())
            self.assertTrue((target / "brain" / "shared-brain.jsonl").exists())
            self.assertIn("skipped .claude agents/commands; pass --claude to install them", results)

    def test_central_brain_selftest_passes(self):
        result = subprocess.run(
            [sys.executable, str(CENTRAL_BRAIN), "--selftest"],
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("central_brain selftest: OK", result.stdout)

    def test_central_brain_pull_marks_imports_and_push_skips_imported_lessons(self):
        central_brain = load_module(CENTRAL_BRAIN, "central_brain")
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            central = base / "central"
            source_project = base / "source"
            receiving_project = base / "receiving"
            (source_project / "brain").mkdir(parents=True)
            (receiving_project / "brain").mkdir(parents=True)
            (source_project / "brain" / "shared-brain.jsonl").write_text(
                '{"id":"lesson-001","type":"lesson","text":"Use explicit opt-in before syncing central brain.","tags":["memory"]}\n',
                encoding="utf-8",
            )

            self.assertEqual(central_brain.push(central, source_project, "alpha"), 1)
            self.assertEqual(central_brain.pull(central, receiving_project, "beta"), 1)
            pulled = central_brain.read_jsonl(receiving_project / "brain" / "shared-brain.jsonl")
            self.assertEqual(len(pulled), 1)
            self.assertTrue(pulled[0]["central_import"])
            self.assertEqual(pulled[0]["source"], "central-brain")
            self.assertEqual(pulled[0]["origin_project_id"], "alpha")
            self.assertNotIn("project_path", pulled[0])

            self.assertEqual(central_brain.push(central, receiving_project, "beta"), 0)
            count, projects = central_brain.status(central)
            self.assertEqual(count, 1)
            self.assertEqual(projects, ["alpha"])

    def test_tool_check_prefers_graphos_and_osvec_env_vars(self):
        tool_check = load_module(TOOL_CHECK, "check_optional_tools")
        with mock.patch.dict(
            "os.environ",
            {
                "PROJECT_OS_GRAPHOS_CMD": "graphos build",
                "PROJECT_OS_GRAPH_CMD": "legacy-graph build",
                "PROJECT_OS_OSVEC_CMD": "osvec index",
                "PROJECT_OS_VECTOR_CMD": "legacy-vector index",
            },
            clear=False,
        ), mock.patch.object(tool_check, "has_command", return_value=True):
            report = tool_check.build_report()

        self.assertIn("PROJECT_OS_GRAPHOS_CMD is set", report)
        self.assertIn("PROJECT_OS_OSVEC_CMD is set", report)
        self.assertNotIn("legacy-graph", report)
        self.assertNotIn("legacy-vector", report)

    def test_tool_check_supports_legacy_graph_and_vector_env_vars(self):
        tool_check = load_module(TOOL_CHECK, "check_optional_tools")
        with mock.patch.dict(
            "os.environ",
            {
                "PROJECT_OS_GRAPH_CMD": "legacy-graph build",
                "PROJECT_OS_VECTOR_CMD": "legacy-vector index",
            },
            clear=False,
        ), mock.patch.object(tool_check, "has_command", return_value=True):
            report = tool_check.build_report()

        self.assertIn("PROJECT_OS_GRAPH_CMD is set", report)
        self.assertIn("prefer PROJECT_OS_GRAPHOS_CMD", report)
        self.assertIn("PROJECT_OS_VECTOR_CMD is set", report)
        self.assertIn("prefer PROJECT_OS_OSVEC_CMD", report)

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
