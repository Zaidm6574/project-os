"""Cross-file contracts for run-scoped public workflow instructions.

These check operational documentation against shipped inputs/CLIs. They do not
claim a host followed a prompt or that structural validation proves completion.
"""
import ast
import json
from pathlib import Path
import re
import subprocess
import shlex
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def read(relative):
    return (ROOT / relative).read_text(encoding="utf-8")


def workflows_and_roles():
    workflows = sorted((ROOT / "prompts/workflows").glob("*.md"))
    roles = sorted((ROOT / "addons/full-engine/staged/agents").glob("*.md"))
    return [p for p in workflows if p.stem != "INDEX"] + roles


class RunScopeDocsTests(unittest.TestCase):
    def test_all_workflows_and_roles_receive_explicit_active_root(self):
        paths = workflows_and_roles()
        self.assertGreaterEqual(len(paths), 22, "do not pass an empty inventory")
        for path in paths:
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertIn("## Active workspace", text)
                self.assertIn("`<run-root>`", text)
                self.assertIn("do not choose the newest run automatically", text)
                self.assertIn("Shared `blackboard/` notes are read-only context", text)
                self.assertIn("Pass the same root to every delegated role", text)

    def test_run_local_output_recipes_do_not_target_global_notes(self):
        # These workflows previously wrote global state after new-run had
        # selected a run. Inspect their actual destination references.
        for name in ("kickoff", "board-review", "evaluate", "cost-check", "ui-review", "status"):
            text = read("prompts/workflows/%s.md" % name)
            with self.subTest(workflow=name):
                self.assertNotRegex(text, r"`blackboard/(?:\d\d-|packets/|plans/)")
                self.assertRegex(text, r"`<run-root>/(?:\d\d-|packets/)")

    def test_solo_prefix_documentation_matches_scaffold_source(self):
        tree = ast.parse(read("memory/new_run.py"))
        assignments = [n for n in tree.body if isinstance(n, ast.Assign)
                       and any(isinstance(t, ast.Name) and t.id == "TIER_FILES" for t in n.targets)]
        self.assertEqual(1, len(assignments))
        solo = ast.literal_eval(assignments[0].value)["solo"]
        doc = read("prompts/workflows/new-run.md")
        stated = re.search(r"Solo selects nine numbered prefixes: \*\*([0-9, ]+)\*\*", doc)
        self.assertIsNotNone(stated)
        self.assertEqual(solo, set(stated.group(1).split(", ")))
        self.assertIn("`PACKETS.md` with a solo waiver instead of a `packets/` directory", doc)
        self.assertIn("without the full-engine goal guard, do not assume the roster exists", doc)
        self.assertIn("plain starter installation stages the add-on sources but does not activate it", doc)
        self.assertIn("python3 scripts/install_full_engine.py --target .", doc)

    def test_named_run_packet_recipe_preserves_shared_index(self):
        # Execute the documented argument list in a disposable project. Merely
        # seeing --out-dir in prose does not prove shared notes stay untouched.
        line = next(line for line in read("AGENTS.md").splitlines()
                    if line.startswith("- prompt packets:"))
        recipe = re.search(r"`(python3 scripts/promptsmith\.py [^`]+)`", line).group(1)
        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            (project / "scripts").mkdir()
            for name in ("promptsmith.py", "bb_lock.py"):
                shutil.copyfile(ROOT / "scripts" / name, project / "scripts" / name)
            (project / "examples").mkdir()
            shutil.copyfile(ROOT / "examples/sample-brief.md",
                            project / "examples/sample-brief.md")
            (project / "blackboard").mkdir()
            shared = project / "blackboard/05-agent-packets.md"
            before = "# Shared packet index\n\n| ID | Owner | Task | Status | Link |\n"
            shared.write_text(before, encoding="utf-8")
            run = project / "runs/synthetic"
            args = [sys.executable] + [arg.replace("$run_root", str(run))
                                      for arg in shlex.split(recipe)[1:]]
            result = subprocess.run(args, cwd=project, capture_output=True,
                                    text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(shared.read_text(encoding="utf-8"), before)
            self.assertEqual(len(list((run / "packets").glob("*.md"))), 2)
            self.assertIn("--no-index", args)
            self.assertIn("<run-root>/05-agent-packets.md", line)
            self.assertIn("Draft", line)

    def test_documented_explicit_plan_path_is_supported_by_cli(self):
        # Exercise the actual create/validate interface on synthetic files;
        # passing a run path as --id must not silently use blackboard/plans.
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            plan = base / "runs/example-run/plans/wave-1.json"
            steps = base / "steps.json"
            steps.write_text(json.dumps([
                {"id": "build", "role": "builder", "task": "write fixture"},
                {"id": "check", "role": "reviewer", "task": "check fixture",
                 "depends_on": ["build"], "verification": {
                     "method": "compare fixture bytes", "expected": "fixture equals approved input"}}
            ]), encoding="utf-8")
            command = [sys.executable, str(ROOT / "scripts/plan_artifact.py")]
            create = subprocess.run(command + ["create", "--id", str(plan),
                "--goal", "synthetic run path", "--steps-file", str(steps)],
                cwd=base, text=True, capture_output=True, timeout=30)
            self.assertEqual(0, create.returncode, create.stdout + create.stderr)
            self.assertEqual("wave-1", json.loads(plan.read_text())["id"])
            valid = subprocess.run(command + ["validate", str(plan)], cwd=base,
                text=True, capture_output=True, timeout=30)
            self.assertEqual(0, valid.returncode, valid.stdout + valid.stderr)
            self.assertFalse((base / "blackboard/plans").exists())


class DeliveryAndSafetyDocsTests(unittest.TestCase):
    def test_delivery_persists_before_export_and_fills_receipt_before_validation(self):
        doc = read("prompts/workflows/deliver.md")
        self.assertLess(doc.index("python3 memory/osvec_adapter.py add"), doc.index("python3 brain/brain.py export"))
        self.assertLess(doc.index("**Fill `<run-root>/23-loop-closeout.md`"), doc.index("python3 memory/validate_run.py"))
        self.assertIn("structural", doc.lower())
        self.assertIn("does not execute evaluator evidence", doc)
        self.assertIn("primary task on the real artifact", doc)
        self.assertIn("resulting lesson IDs", doc)

    def test_delivery_cost_commands_are_runtime_specific_and_attributed(self):
        doc = read("prompts/workflows/deliver.md")
        commands = [block.replace("\\\n", " ") for block in re.findall(r"```bash\n(.*?)```", doc, re.S)]
        cost = [line for block in commands for line in block.splitlines() if "cost_actuals.py" in line]
        self.assertEqual(2, len(cost))
        claude = next(line for line in cost if "--codex-sessions" not in line)
        codex = next(line for line in cost if "--codex-sessions" in line)
        self.assertIn("--transcript", claude)
        self.assertIn('--target "$run_root/09-cost-estimate.md"', claude)
        self.assertIn("--sessions-dir", codex)
        self.assertNotIn("--write", codex)
        self.assertNotIn("--target", codex)
        self.assertIn("does **not** implement project/time filtering", doc)
        self.assertIn("Unmeasured", doc)

    def test_claude_tool_permissions_cover_mandated_shell_execution(self):
        paths = ["addons/full-engine/staged/agents/%s.md" % name
                 for name in ("evaluator", "project-os-ceo", "project-os-cfo")]
        paths += ["prompts/workflows/%s.md" % name for name in ("evaluate", "deliver", "cost-check", "kickoff")]
        for path in paths:
            doc = read(path)
            declaration = re.search(r"^(?:tools|claude-tools): (.+)$", doc, re.M)
            with self.subTest(path=path):
                self.assertIsNotNone(declaration)
                self.assertRegex(declaration.group(1), r"\bBash\b")

    def test_publishing_recipes_use_value_free_scanner(self):
        for path in ("docs/github-publishing.md", "docs/friend-review.md"):
            doc = read(path)
            with self.subTest(path=path):
                blocks = "\n".join(re.findall(r"```bash\n(.*?)```", doc, re.S))
                self.assertNotRegex(blocks, r"(?m)^rg\s.*--no-ignore")
                self.assertIn("python3 scripts/prepublish_check.py --tracked", blocks)
                self.assertIn("stage-0", doc)
                self.assertIn("history", doc)

    def test_no_subagents_preserves_user_tier_without_claiming_isolation(self):
        doc = read("addons/full-engine/staged/agents/project-os-ceo.md")
        self.assertNotIn("Auto-cap the tier at Solo", doc)
        self.assertIn("Keep the user-chosen tier", doc)
        self.assertIn("no independent context isolation", doc)

    def test_plain_install_does_not_claim_registered_slash_command(self):
        doc = read("docs/install-from-github.md")
        self.assertIn("does not register slash commands or host skills", doc)
        self.assertIn("--claude-engine", doc)
        self.assertIn("--codex-engine", doc)
        publishing = read("docs/github-publishing.md")
        self.assertIn("A plain install does not register `/project`", publishing)


if __name__ == "__main__":
    unittest.main()
