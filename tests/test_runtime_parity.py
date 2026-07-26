"""Runtime capability parity — tests written RED-first (2026-07-25).

The doc unification (08449fa) made AGENTS.md the single source of DOCTRINE for
every runtime. It did not touch CAPABILITY: the 11 slash commands and 10 agent
roles in addons/full-engine/staged/ are Claude-only assets, so a Codex, Cursor,
or Antigravity session reads the same rules but has no way to invoke the same
workflows.

This closes that gap the same way the doc gap was closed — one canonical
source plus generated thin adapters, with a fail-closed drift check:

  prompts/workflows/<name>.md        canonical, runtime-neutral
    -> staged/commands/<name>.md     Claude adapter (generated)
    -> prompts/workflows/INDEX.md    universal discovery (generated)

A workflow that exists canonically but has no adapter, or an adapter edited by
hand so it no longer matches its source, is a PARITY BREACH and fails here
rather than silently leaving one runtime behind.
"""
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SYNC = ROOT / "scripts" / "sync_runtime_assets.py"
CANON = ROOT / "prompts" / "workflows"
CLAUDE_COMMANDS = ROOT / "addons" / "full-engine" / "staged" / "commands"
CODEX_SKILLS = ROOT / "addons" / "full-engine" / "staged" / "codex-skills"
INDEX = CANON / "INDEX.md"


def load_sync():
    spec = importlib.util.spec_from_file_location("sync_runtime_assets", SYNC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sync_runtime_assets"] = mod
    spec.loader.exec_module(mod)
    return mod


class TestCanonicalWorkflows(unittest.TestCase):
    def test_canonical_workflow_directory_exists_and_is_populated(self):
        self.assertTrue(CANON.is_dir(), "prompts/workflows/ must exist")
        names = {p.stem for p in CANON.glob("*.md")} - {"INDEX"}
        self.assertGreaterEqual(
            len(names), 11,
            "every staged Claude command needs a runtime-neutral canonical source")

    def test_canonical_workflows_carry_no_claude_only_syntax(self):
        """$ARGUMENTS and allowed-tools are Claude slash-command syntax. The
        canonical file must be readable and followable by ANY runtime."""
        for path in sorted(CANON.glob("*.md")):
            if path.stem == "INDEX":
                continue
            text = path.read_text(encoding="utf-8")
            with self.subTest(workflow=path.stem):
                self.assertNotIn("$ARGUMENTS", text)
                self.assertNotIn("allowed-tools:", text)

    def test_workflow_naming_a_capability_emits_degradation_guidance(self):
        """Silence is how Codex ends up behind: any workflow that wants a
        capability must tell a runtime lacking it what to do instead."""
        sync = load_sync()
        for wf in sync.load_workflows(ROOT):
            with self.subTest(workflow=wf.name):
                self.assertTrue(wf.description, "needs a description")
                self.assertIsInstance(wf.capabilities, list)
                for cap in wf.capabilities:
                    self.assertIn(cap, sync.KNOWN_CAPABILITIES)
                note = sync.capability_note(wf)
                if wf.capabilities:
                    self.assertIn("do the work inline", note)
                    self.assertIn("17-capability-preflight", note)
                else:
                    self.assertEqual(note, "", "no capabilities => no noise")

    def test_capabilities_are_not_over_declared(self):
        """`board` is a substring of `blackboard`. Substring inference made
        every workflow claim it needed subagents, which trains runtimes to log
        gaps they never hit."""
        sync = load_sync()
        by_name = {wf.name: wf for wf in sync.load_workflows(ROOT)}
        for name in ("new-run", "adopt-project", "save-chat"):
            with self.subTest(workflow=name):
                self.assertEqual(
                    by_name[name].capabilities, [],
                    f"{name} runs a script and needs no roles")


class TestGeneratedAdapters(unittest.TestCase):
    def test_every_canonical_workflow_has_a_claude_adapter(self):
        canon = {p.stem for p in CANON.glob("*.md")} - {"INDEX"}
        adapters = {p.stem for p in CLAUDE_COMMANDS.glob("*.md")}
        self.assertEqual(
            canon - adapters, set(),
            "canonical workflow with no Claude adapter — Claude would lose a command")
        self.assertEqual(
            adapters - canon, set(),
            "Claude command with no canonical source — other runtimes cannot see it")

    def test_adapters_are_byte_identical_to_a_fresh_render(self):
        """Fail-closed drift check, same doctrine as the FTS mirror: a
        hand-edited adapter silently diverges from every other runtime."""
        sync = load_sync()
        drift = sync.check(ROOT)
        self.assertEqual(drift, [], "generated assets are stale; run sync_runtime_assets.py")

    def test_every_canonical_workflow_has_a_codex_skill(self):
        """Codex reads the canonical file fine, so this is a DISCOVERABILITY
        adapter, not a capability one — without it a Codex session never learns
        the workflows exist, which is the actual 'feels behind' complaint.

        Path verified empirically against codex-cli 0.144.1 with a marker-file
        probe: .agents/skills/<name>/SKILL.md is auto-loaded at startup."""
        canon = {p.stem for p in CANON.glob("*.md")} - {"INDEX"}
        skills = {p.parent.name for p in CODEX_SKILLS.glob("*/SKILL.md")}
        self.assertEqual(canon - skills, set(),
                         "canonical workflow with no Codex skill adapter")
        self.assertEqual(skills - canon, set(),
                         "Codex skill with no canonical source")

    def test_codex_skills_carry_the_argument_token_codex_actually_expands(self):
        sync = load_sync()
        for wf in sync.load_workflows(ROOT):
            if "{{ARGUMENTS}}" not in wf.body:
                continue
            rendered = sync.render_codex_skill(wf)
            with self.subTest(workflow=wf.name):
                self.assertIn("$ARGUMENTS", rendered)
                self.assertNotIn("{{ARGUMENTS}}", rendered)

    def test_index_lists_every_workflow_for_non_claude_runtimes(self):
        self.assertTrue(INDEX.is_file(), "prompts/workflows/INDEX.md must exist")
        text = INDEX.read_text(encoding="utf-8")
        for path in sorted(CANON.glob("*.md")):
            if path.stem == "INDEX":
                continue
            with self.subTest(workflow=path.stem):
                self.assertIn(path.stem, text)


class TestDoctrineRouting(unittest.TestCase):
    def test_no_staged_asset_routes_doctrine_to_claude_md(self):
        """After the unification CLAUDE.md is a thin pointer. An asset telling
        an agent to 'follow CLAUDE.md' now sends it to the pointer, not the
        doctrine — and is meaningless to Codex."""
        offenders = []
        staged = ROOT / "addons" / "full-engine" / "staged"
        for path in sorted(staged.rglob("*.md")):
            if "CLAUDE.md" in path.read_text(encoding="utf-8"):
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [], "these must point at AGENTS.md instead")

    def test_agents_md_tells_a_non_claude_runtime_how_to_run_a_workflow(self):
        text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("prompts/workflows/", text,
                      "AGENTS.md must document the neutral invocation path")


if __name__ == "__main__":
    unittest.main()
