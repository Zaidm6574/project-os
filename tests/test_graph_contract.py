import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE_BUILDER = ROOT / "memory" / "build_graph.py"
TOOL_CHECK = ROOT / "scripts" / "check_optional_tools.py"
VALIDATE_RUN = ROOT / "addons" / "full-engine" / "memory" / "validate_run.py"
CLAUDE = ROOT / "CLAUDE.md"
AGENTS = ROOT / "AGENTS.md"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class GraphContractTests(unittest.TestCase):
    def test_core_builder_output_is_recognized_by_public_graph_consumers(self):
        tool_check = load_module(TOOL_CHECK, "graph_contract_tool_check")
        validator = load_module(VALIDATE_RUN, "graph_contract_validator")

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            memory = project / "memory"
            blackboard = project / "blackboard"
            run = project / "runs" / "demo"
            memory.mkdir(parents=True)
            blackboard.mkdir()
            run.mkdir(parents=True)
            shutil.copy2(CORE_BUILDER, memory / "build_graph.py")
            (blackboard / "00-project-goal.md").write_text(
                "# Fresh Project OS Workspace\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [sys.executable, str(memory / "build_graph.py"), "--root", "blackboard"],
                cwd=project,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            graph_path = project / "graphify-out" / "graph.json"
            self.assertTrue(graph_path.exists(), result.stdout)
            graph = json.loads(graph_path.read_text(encoding="utf-8"))
            self.assertTrue(graph["nodes"])

            status, detail = tool_check.local_graphos_status(project)
            # The builder really ran above, but local_graphos_status itself only
            # stats the file — it cannot tell this genuine graph from a
            # `touch graphify-out/graph.json`. Per audit 2026-07-26 that check no
            # longer calls itself "Verified" (a word this script reserves for an
            # executable on PATH or an importable package), matching the sibling
            # local_osvec_status. Recognition — the substance of this contract —
            # is still asserted via `detail` below.
            self.assertEqual(status, tool_check.PRESENT_UNVERIFIED)
            self.assertIn("graph artifact found", detail)
            self.assertTrue(validator._has_graph_or_memory(str(run)))

    def test_claude_instructions_use_public_safe_path_wording(self):
        text = CLAUDE.read_text(encoding="utf-8")

        self.assertIn("personal or local paths", text)
        self.assertNotIn("Zaid's paths", text)
        self.assertNotIn("Zaid’s paths", text)

    def test_agents_instructions_match_installed_graph_output_contract(self):
        text = AGENTS.read_text(encoding="utf-8")
        naming_note = next(
            line for line in text.splitlines() if line.startswith("> **Naming:**")
        )

        self.assertIn("knowledge graph **Arachne**", naming_note)
        self.assertIn("output `graphify-out/graph.json`", naming_note)
        self.assertNotIn("`arachne-out/`", naming_note)
        self.assertNotIn("compat symlink", naming_note)


if __name__ == "__main__":
    unittest.main()
