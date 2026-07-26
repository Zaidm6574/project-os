import functools
import importlib.util
import json
import os
import shlex
import shutil
import stat
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
COST_ACTUALS = ROOT / "addons" / "full-engine" / "memory" / "cost_actuals.py"
VALIDATE_RUN = ROOT / "addons" / "full-engine" / "memory" / "validate_run.py"


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


class SetupProjectOSTests(unittest.TestCase):
    def test_copy_helpers_exclude_generated_runtime_state(self):
        setup = load_module(SETUP, "setup_project_os_runtime_filter")
        full_engine = load_module(FULL_ENGINE, "install_full_engine_runtime_filter")
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source"
            (source / "memory" / "store").mkdir(parents=True)
            (source / "second-brain" / "out").mkdir(parents=True)
            (source / "memory" / "helper.py").write_text("# helper\n", encoding="utf-8")
            (source / "memory" / "store" / "project.sidecar.json").write_text("{}\n", encoding="utf-8")
            (source / "memory" / "store" / "project.tvim").write_bytes(b"runtime")
            (source / "second-brain" / "out" / "graph.json").write_text("{}\n", encoding="utf-8")

            starter_target = base / "starter"
            engine_target = base / "engine"
            setup.copy_tree_files(source, starter_target, force=False)
            full_engine.copy_tree(source, engine_target, force=False)

            self.assertTrue((starter_target / "memory" / "helper.py").exists())
            self.assertTrue((engine_target / "memory" / "helper.py").exists())
            self.assertFalse((starter_target / "memory" / "store").exists())
            self.assertFalse((engine_target / "memory" / "store").exists())
            self.assertFalse((starter_target / "second-brain" / "out").exists())
            self.assertFalse((engine_target / "second-brain" / "out").exists())

    @unittest.skipUnless(_path_has_python310(), "installer requires Python >=3.10 on PATH")
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
            self.assertTrue((target / "addons" / "full-engine" / "staged" / "agents" / "context-scout.md").exists())
            self.assertFalse((target / "addons" / "second-brain").exists())
            self.assertTrue((target / "memory" / "build_graph.py").exists())
            self.assertTrue((target / "memory" / "osvec_adapter.py").exists())

    @unittest.skipUnless(_path_has_python310(), "installer requires Python >=3.10 on PATH")
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

    @unittest.skipUnless(_path_has_python310(), "installer requires Python >=3.10 on PATH")
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
            self.assertTrue((target / ".claude" / "agents" / "context-scout.md").exists())

            preflight = target / "blackboard" / "17-capability-preflight.md"
            text = preflight.read_text(encoding="utf-8")
            self.assertIn("Local GraphOS helper found: memory/build_graph.py", text)
            self.assertIn("graph artifact not built yet", text)
            self.assertIn("Local OSVec helper found: memory/osvec_adapter.py", text)
            self.assertIn("vector store not populated yet", text)

    @unittest.skipUnless(_path_has_python310(), "installer requires Python >=3.10 on PATH")
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

    @unittest.skipUnless(_path_has_python310(), "installer requires Python >=3.10 on PATH")
    def test_install_script_force_reaches_full_engine_installer(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "project"
            existing = target / "memory" / "new_run.py"
            existing.parent.mkdir(parents=True)
            existing.write_text("# keep me until forced\n", encoding="utf-8")

            result = subprocess.run(
                ["sh", str(INSTALL), str(target), "--force", "--full-engine"],
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotEqual(existing.read_text(encoding="utf-8"), "# keep me until forced\n")
            self.assertIn("Project OS full engine add-on install complete.", result.stdout)

    @unittest.skipUnless(_path_has_python310(), "installer requires Python >=3.10 on PATH")
    def test_install_script_dry_run_does_not_create_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "dry-run-project"

            result = subprocess.run(
                [
                    "sh",
                    str(INSTALL),
                    str(target),
                    "--dry-run",
                    "--full-engine",
                    "--claude-engine",
                    "--check-tools",
                ],
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(target.exists())
            self.assertIn("Project OS setup dry run complete.", result.stdout)
            self.assertIn("would write", result.stdout)
            self.assertIn("Project OS full engine add-on dry run complete.", result.stdout)
            self.assertIn("Dry run: would run optional tool check", result.stdout)

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

    def test_public_onboarding_uses_live_repo_name_and_ci(self):
        files = [
            ROOT / "README.md",
            ROOT / "docs" / "install-from-github.md",
            ROOT / "docs" / "github-publishing.md",
        ]
        combined = "\n".join(path.read_text(encoding="utf-8") for path in files)
        workflow = (ROOT / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")

        self.assertIn("github.com/Zaidm6574/project-os.git", combined)
        self.assertIn("github.com/YOUR-USERNAME/project-os.git", combined)
        self.assertNotIn("project-os-template.git", combined)
        self.assertIn("python3 -m unittest discover -s tests -v", workflow)

    def test_python_310_minimum_is_documented_checked_and_tested_in_ci(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        publishing = (ROOT / "docs" / "github-publishing.md").read_text(encoding="utf-8")
        installer = INSTALL.read_text(encoding="utf-8")
        workflow = (ROOT / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")

        self.assertIn("Python 3.10+", readme)
        self.assertIn("Python 3.10+", publishing)
        self.assertIn("sys.version_info", installer)
        self.assertIn("Python 3.10 or newer", installer)
        self.assertIn('"3.10"', workflow)
        self.assertIn('"3.x"', workflow)
        self.assertIn("matrix.python-version", workflow)

    def test_direct_python_clis_reject_blank_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            for script in (SETUP, FULL_ENGINE):
                result = subprocess.run(
                    [sys.executable, str(script), "--target", "", "--dry-run"],
                    cwd=tmp,
                    capture_output=True,
                    text=True,
                )

                self.assertNotEqual(result.returncode, 0, script.name)
                self.assertIn("target must be a non-empty path", result.stderr)

    def test_claude_friend_review_matches_public_privacy_contract(self):
        claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")

        self.assertIn("Friend Review Mode", claude)
        self.assertIn("local paths", claude)
        self.assertIn("personal names", claude)
        self.assertIn("raw chats", claude)
        self.assertIn("secrets", claude)
        self.assertIn("blank test install", claude)
        self.assertIn("delivery reports", claude)

    def test_friend_review_tool_check_uses_an_explicit_target(self):
        friend_review = (ROOT / "docs" / "friend-review.md").read_text(encoding="utf-8")

        self.assertNotIn("`./install.sh --check-tools`", friend_review)
        self.assertIn("`./install.sh ../demo-project --check-tools`", friend_review)

    def test_ai_reviewer_doc_is_part_of_sharing_bundle(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        friend_review = (ROOT / "docs" / "friend-review.md").read_text(encoding="utf-8")
        reviewer_doc = (ROOT / "docs" / "for-ai-reviewers.md").read_text(encoding="utf-8")

        self.assertIn("docs/for-ai-reviewers.md", readme)
        self.assertIn("docs/for-ai-reviewers.md", friend_review)
        self.assertIn("Starter Vs Full Engine", reviewer_doc)
        self.assertIn("Implemented Now", reviewer_doc)
        self.assertIn("Optional Or External", reviewer_doc)
        self.assertIn("GitHub About Settings", reviewer_doc)
        self.assertIn("Privacy-first AI project workflow", reviewer_doc)

    def test_readme_includes_trust_building_demo_and_dry_run(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("5-Minute Demo", readme)
        self.assertIn("./install.sh ../demo-project --dry-run", readme)
        self.assertIn("Project OS setup dry run complete.", readme)
        self.assertIn("Before", readme)
        self.assertIn("After", readme)

    def test_discipline_hardening_guidance_is_available(self):
        files = [
            ROOT / "AGENTS.md",
            ROOT / "CLAUDE.md",
            ROOT / "prompts" / "project-os-kickoff.md",
            ROOT / "addons" / "full-engine" / "staged" / "commands" / "kickoff.md",
            ROOT / "addons" / "full-engine" / "staged" / "commands" / "status.md",
            ROOT / "addons" / "full-engine" / "staged" / "commands" / "deliver.md",
            ROOT / "addons" / "full-engine" / "staged" / "commands" / "ui-review.md",
            ROOT / "addons" / "full-engine" / "staged" / "agents" / "builder.md",
            ROOT / "addons" / "full-engine" / "staged" / "agents" / "evaluator.md",
            ROOT / "addons" / "full-engine" / "staged" / "agents" / "project-os-ceo.md",
        ]
        combined = "\n".join(path.read_text(encoding="utf-8") for path in files)

        self.assertIn("Blackboard Read Gate", combined)
        self.assertIn("context-scout", combined)
        self.assertIn("smallest available model", combined)
        self.assertIn("Context Used", combined)
        self.assertIn("Do not act from memory", combined)
        self.assertIn("append-only", combined)
        self.assertIn("superseded", combined)

    def test_context_cache_hygiene_guidance_is_available(self):
        files = [
            ROOT / "AGENTS.md",
            ROOT / "CLAUDE.md",
            ROOT / "README.md",
            ROOT / "prompts" / "project-os-kickoff.md",
            ROOT / "blackboard-template" / "09-cost-estimate.md",
            ROOT / "blackboard-template" / "11-model-routing.md",
            ROOT / "addons" / "full-engine" / "README.md",
            ROOT / "addons" / "full-engine" / "staged" / "commands" / "cost-check.md",
            ROOT / "addons" / "full-engine" / "staged" / "agents" / "project-os-cfo.md",
            ROOT / "addons" / "full-engine" / "staged" / "agents" / "project-os-ceo.md",
            ROOT / "addons" / "full-engine" / "staged" / "agents" / "context-scout.md",
        ]
        combined = "\n".join(path.read_text(encoding="utf-8") for path in files)

        self.assertIn("Context Cache Hygiene", combined)
        self.assertIn("cache writes", combined)
        self.assertIn("cached writes", combined)
        self.assertIn("fresh-session trigger", combined)
        self.assertIn("handoff packet", combined)
        self.assertIn("context-scout", combined)
        self.assertIn("Cached Write", combined)

    def test_cost_actuals_reports_cache_write_pressure(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "addons" / "full-engine" / "memory" / "cost_actuals.py"), "--selftest"],
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("cost_actuals selftest: OK", result.stdout)

    def test_codex_session_rollup_guidance_is_available(self):
        files = [
            ROOT / "AGENTS.md",
            ROOT / "CLAUDE.md",
            ROOT / "blackboard-template" / "09-cost-estimate.md",
            ROOT / "addons" / "full-engine" / "README.md",
            ROOT / "addons" / "full-engine" / "staged" / "commands" / "cost-check.md",
            ROOT / "addons" / "full-engine" / "staged" / "agents" / "project-os-cfo.md",
        ]
        combined = "\n".join(path.read_text(encoding="utf-8") for path in files)

        # Docs only. The CLI flag itself is covered behaviourally by
        # test_codex_session_rollup_cli_counts_real_session_files — asserting
        # "--codex-sessions" against cost_actuals.py source proved nothing,
        # because the module docstring and the --sessions-dir help string both
        # keep the substring alive after the whole feature is deleted.
        self.assertIn("--codex-sessions", combined)
        self.assertIn("last_token_usage", combined)
        self.assertIn("total_token_usage", combined)
        self.assertIn("cumulative", combined)
        self.assertIn("cached_input_tokens", combined)
        self.assertIn("cache writes", combined)
        self.assertIn("cache_creation_input_tokens", combined)

    @staticmethod
    def _write_codex_session(path, events):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event) + "\n")

    @staticmethod
    def _codex_usage(input_tokens, cached, output_tokens, reasoning, total):
        return {
            "input_tokens": input_tokens,
            "cached_input_tokens": cached,
            "output_tokens": output_tokens,
            "reasoning_output_tokens": reasoning,
            "total_tokens": total,
        }

    def test_codex_session_rollup_cli_counts_real_session_files(self):
        # Behavioural cover for --codex-sessions: build a fake ~/.codex/sessions
        # tree and assert the rendered numbers, so deleting the flag, the
        # dispatch, the file collector, or the parser fails this test.
        with tempfile.TemporaryDirectory() as tmp:
            sessions = Path(tmp) / "sessions"
            nested = sessions / "2026" / "07" / "session-a.jsonl"
            self._write_codex_session(
                nested,
                [
                    {
                        "type": "event_msg",
                        "payload": {
                            "info": {
                                "last_token_usage": self._codex_usage(100, 40, 10, 3, 110),
                                "total_token_usage": self._codex_usage(100, 40, 10, 3, 110),
                            }
                        },
                    },
                    {
                        "type": "event_msg",
                        "payload": {
                            "info": {
                                "last_token_usage": self._codex_usage(200, 50, 20, 6, 220),
                                "total_token_usage": self._codex_usage(300, 90, 30, 9, 330),
                            }
                        },
                    },
                ],
            )
            self._write_codex_session(
                sessions / "session-b.jsonl",
                [
                    {
                        "type": "event_msg",
                        "payload": {
                            "info": {
                                "last_token_usage": self._codex_usage(50, 0, 5, 1, 55),
                                "total_token_usage": self._codex_usage(50, 0, 5, 1, 55),
                            }
                        },
                    }
                ],
            )
            # Noise the collector must ignore: a non-jsonl file and a corrupt line.
            (sessions / "notes.txt").write_text("not a session log\n", encoding="utf-8")
            with open(sessions / "session-b.jsonl", "a", encoding="utf-8") as handle:
                handle.write("{ this is not json\n")

            result = subprocess.run(
                [sys.executable, str(COST_ACTUALS), "--codex-sessions", "--sessions-dir", str(sessions)],
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            out = result.stdout
            self.assertIn("## Codex local session token rollup", out)
            self.assertIn(str(sessions), out)
            self.assertIn("| Files scanned | 2 |", out)
            self.assertIn("| Sessions with usage | 2 |", out)
            self.assertIn("| Usage events | 3 |", out)
            # sum(last_token_usage) = 350 input / 90 cached / 35 output / 385 total
            self.assertIn("| input_tokens | 350 | 350 | 450 |", out)
            self.assertIn("| cached_input_tokens | 90 | 90 | 130 |", out)
            self.assertIn("| output_tokens | 35 | 35 | 45 |", out)
            self.assertIn("| total_tokens | 385 | 385 | 495 |", out)
            self.assertIn("| uncached_input_tokens | 260 | 260 | 320 |", out)
            self.assertIn("| Cached-input share of input | 25.7% |", out)
            self.assertIn("| Wrong cumulative-row overcount | 1.3x |", out)
            self.assertIn("| Final-session cross-check | matches |", out)

    def test_codex_session_rollup_refuses_a_missing_sessions_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "no-such-sessions"
            result = subprocess.run(
                [sys.executable, str(COST_ACTUALS), "--codex-sessions", "--sessions-dir", str(missing)],
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            combined = result.stdout + result.stderr
            self.assertIn(str(missing), combined)
            self.assertIn("not found", combined)
            self.assertNotIn("Codex local session token rollup", result.stdout)

    def test_installer_refuses_python_39_even_when_it_is_the_only_interpreter(self):
        # Behavioural cover for the >=3.10 boundary in install.sh. The shim is a
        # REAL interpreter that reports 3.9.6, so relaxing the gate to (3, 0)
        # re-admits it and this test fails. A shim that just `exit 1`s would
        # pass no matter where the boundary sits.
        real_python = _find_python310() or sys.executable
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            fake_bin = base / "bin"
            fake_bin.mkdir()
            shim = (
                "#!/bin/sh\n"
                'if [ "$1" = "-c" ]; then\n'
                "  shift\n"
                "  exec %s -c 'import sys, platform\n"
                'sys.version_info = (3, 9, 6, "final", 0)\n'
                'platform.python_version = lambda: "3.9.6"\n'
                "exec(sys.argv[1])\n"
                "' \"$1\"\n"
                "fi\n"
                "exec %s \"$@\"\n"
            ) % (shlex.quote(real_python), shlex.quote(real_python))
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

            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("found python3 = 3.9.6", result.stderr)
            self.assertIn("Python 3.10 or newer", result.stderr)
            self.assertNotIn("dry run complete", result.stdout)
            self.assertFalse(target.exists())

    def test_max_effort_auto_continuation_prompt_is_wired(self):
        files = [
            ROOT / "prompts" / "project-os-kickoff.md",
            ROOT / "CLAUDE.md",
            ROOT / "AGENTS.md",
            ROOT / "blackboard-template" / "09-cost-estimate.md",
            ROOT / "blackboard-template" / "11-model-routing.md",
        ]
        combined = "\n".join(path.read_text(encoding="utf-8") for path in files)

        # All three options must be offered somewhere in the public docs.
        self.assertIn("Auto", combined)
        self.assertIn("Ask first", combined)
        self.assertIn("Warn only", combined)
        # The feature name and the safe-degradation path must be present.
        self.assertIn("auto-continuation", combined.lower())
        self.assertIn("handoff packet", combined)
        self.assertIn("paste", combined.lower())
        # The Claude-facing file must carry the ask too, not just the kickoff prompt.
        claude_text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("auto-continuation", claude_text.lower())
        # The routing template must expose a slot to record the chosen mode.
        routing_text = (ROOT / "blackboard-template" / "11-model-routing.md").read_text(encoding="utf-8")
        self.assertIn("Max-effort auto-continuation", routing_text)

    def test_decision_and_risk_templates_are_append_only(self):
        decisions = (ROOT / "blackboard-template" / "03-decisions.md").read_text(encoding="utf-8")
        risks = (ROOT / "blackboard-template" / "04-risks.md").read_text(encoding="utf-8")

        self.assertIn("Append-only", decisions)
        self.assertIn("Do not delete", decisions)
        self.assertIn("Superseded", decisions)
        self.assertIn("Append-only", risks)
        self.assertIn("Do not delete", risks)
        self.assertIn("Superseded", risks)

    def test_ui_review_requires_real_qa_evidence_before_approval(self):
        command = (ROOT / "addons" / "full-engine" / "staged" / "commands" / "ui-review.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("Approved only with real build/browser QA evidence", command)
        self.assertIn("Draft when browser QA was not run", command)
        self.assertIn("Rejected when QA finds blocking UI issues", command)

    @unittest.skipUnless(_path_has_python310(), "installer requires Python >=3.10 on PATH")
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

    @unittest.skipUnless(_path_has_python310(), "installer requires Python >=3.10 on PATH")
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
            self.assertTrue((target / "memory" / "build_graph.py").exists())
            self.assertTrue((target / "memory" / "osvec_adapter.py").exists())
            self.assertFalse((target / "scripts" / "__pycache__").exists())
            self.assertTrue((target / "memory" / "self-improvement-loop.md").exists())
            self.assertIn("Self-Improvement Loop", (target / "memory" / "self-improvement-loop.md").read_text(encoding="utf-8"))
# The README advertises vector memory + graph adapters; the installer
            # must deliver them (regression: the strip once dropped this check).
            self.assertTrue((target / "memory" / "mneme_adapter.py").exists())
            self.assertTrue((target / "memory" / "build_graph.py").exists())
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
    def test_closure_validator_requires_a_nonempty_real_memory_artifact(self):
        validator = load_module(VALIDATE_RUN, "validate_run_real_artifact")
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            run = project / "runs" / "demo"
            run.mkdir(parents=True)
            (run / "13-delivery-report.md").write_text(
                "Graph rebuilt at graphify-out/graph.json; memory exported to brain/shared-brain.jsonl.\n",
                encoding="utf-8",
            )
            brain = project / "brain" / "shared-brain.jsonl"
            brain.parent.mkdir(parents=True)
            brain.write_text("", encoding="utf-8")

            self.assertFalse(validator._has_graph_or_memory(str(run)))

            brain.write_text('{"id":"lesson-1","type":"lesson","text":"Verified lesson"}\n', encoding="utf-8")
            self.assertTrue(validator._has_graph_or_memory(str(run)))

    def test_full_engine_installer_rejects_unbootstrapped_target(self):
        full_engine = load_module(FULL_ENGINE, "install_full_engine_requires_starter")
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "blank-project"

            with self.assertRaisesRegex(FileNotFoundError, "starter Project OS workspace"):
                full_engine.install_full_engine(target)

            self.assertFalse(target.exists())

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

        self.assertIn("Local GraphOS helper found: memory/build_graph.py", report)
        self.assertIn("graph artifact not built yet", report)
        self.assertIn("Local OSVec helper found: memory/osvec_adapter.py", report)
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
            (target / "blackboard").mkdir()
            (target / "AGENTS.md").write_text("# Project OS\n", encoding="utf-8")
            (target / "blackboard" / "00-project-goal.md").write_text("# Goal\n", encoding="utf-8")
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
                '{"id":"lesson-001","type":"lesson","text":"Use explicit opt-in before syncing central brain.","tags":["memory"],"approved":true,"summary_only":true,"raw_chat":false}\n',
                encoding="utf-8",
            )

            self.assertEqual(central_brain.push(central, source_project, "alpha"), (1, 0))
            self.assertEqual(central_brain.pull(central, receiving_project, "beta"), (1, 0))
            pulled = central_brain.read_jsonl(receiving_project / "brain" / "shared-brain.jsonl")
            self.assertEqual(len(pulled), 1)
            self.assertTrue(pulled[0]["central_import"])
            self.assertEqual(pulled[0]["source"], "central-brain")
            self.assertEqual(pulled[0]["origin_project_id"], "alpha")
            self.assertNotIn("project_path", pulled[0])

            self.assertEqual(central_brain.push(central, receiving_project, "beta"), (0, 0))
            count, projects = central_brain.status(central)
            self.assertEqual(count, 1)
            self.assertEqual(projects, ["alpha"])

    def test_central_brain_sync_rejects_raw_and_unapproved_chat_records(self):
        central_brain = load_module(CENTRAL_BRAIN, "central_brain_summary_boundary")
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            central = base / "central"
            source_project = base / "source"
            receiving_project = base / "receiving"
            (source_project / "brain").mkdir(parents=True)
            (receiving_project / "brain").mkdir(parents=True)
            records = [
                {
                    "id": "approved-summary",
                    "type": "lesson",
                    "source": "chat-summary",
                    "text": "Approved compact summary.",
                    "approved": True,
                    "summary_only": True,
                    "raw_chat": False,
                    "tags": ["chat-summary"],
                },
                {
                    "id": "raw-chat",
                    "type": "lesson",
                    "source": "chat-raw",
                    "text": "Raw personal conversation content.",
                    "approved": True,
                    "summary_only": False,
                    "raw_chat": True,
                    "tags": ["raw-chat"],
                },
                {
                    "id": "unapproved-summary",
                    "type": "lesson",
                    "source": "chat-summary",
                    "text": "Summary that was not approved.",
                    "approved": False,
                    "summary_only": True,
                    "raw_chat": False,
                    "tags": ["chat-summary"],
                },
            ]
            (source_project / "brain" / "shared-brain.jsonl").write_text(
                "".join(__import__("json").dumps(record) + "\n" for record in records),
                encoding="utf-8",
            )

            self.assertEqual(central_brain.push(central, source_project, "alpha"), (1, 2))
            self.assertEqual(central_brain.pull(central, receiving_project, "beta"), (1, 0))
            pulled = central_brain.read_jsonl(receiving_project / "brain" / "shared-brain.jsonl")
            self.assertEqual([record["origin_id"] for record in pulled], ["approved-summary"])

    def test_central_brain_sync_fails_closed_for_malformed_or_unapproved_records(self):
        central_brain = load_module(CENTRAL_BRAIN, "central_brain_fail_closed")
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            central = base / "central"
            source_project = base / "source"
            (source_project / "brain").mkdir(parents=True)
            records = [
                {
                    "id": "approved-summary",
                    "type": "lesson",
                    "source": "chat-summary",
                    "text": "Approved compact summary.",
                    "approved": True,
                    "summary_only": True,
                    "raw_chat": False,
                    "tags": ["chat-summary"],
                },
                {
                    "id": "missing-approval",
                    "type": "lesson",
                    "text": "This must not leave the project.",
                    "summary_only": True,
                    "raw_chat": False,
                    "tags": [],
                },
                {
                    "id": "private-summary",
                    "type": "lesson",
                    "source": "private-notes",
                    "text": "Private material must stay local.",
                    "approved": True,
                    "summary_only": True,
                    "raw_chat": False,
                    "tags": ["private"],
                },
                {
                    "id": "bad-tags",
                    "type": "lesson",
                    "text": "Tags must have the expected shape.",
                    "approved": True,
                    "summary_only": True,
                    "raw_chat": False,
                    "tags": "chat-summary",
                },
                {
                    "id": "bad-privacy-boolean",
                    "type": "lesson",
                    "text": "Privacy fields must be booleans.",
                    "approved": "true",
                    "summary_only": True,
                    "raw_chat": False,
                    "tags": [],
                },
                {
                    "id": "bad-raw-boolean",
                    "type": "lesson",
                    "text": "Raw-chat status must be a boolean.",
                    "approved": True,
                    "summary_only": True,
                    "raw_chat": "false",
                    "tags": [],
                },
            ]
            (source_project / "brain" / "shared-brain.jsonl").write_text(
                "".join(__import__("json").dumps(record) + "\n" for record in records),
                encoding="utf-8",
            )

            self.assertEqual(central_brain.push(central, source_project, "alpha"), (1, 5))
            synced = central_brain.read_jsonl(central / "shared-brain.jsonl")
            self.assertEqual([record["origin_id"] for record in synced], ["approved-summary"])

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
