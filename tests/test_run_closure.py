import json
import builtins
import contextlib
import io
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "blackboard-template"
MEMORY_SCRIPTS = ROOT / "addons" / "full-engine" / "memory"
ROSTER_ADDON = ROOT / "addons" / "full-engine" / "blackboard-addons" / "21-agent-roster.md"
SCRIPTS = ("new_run.py", "adopt_project.py", "validate_run.py", "goal_guard.py", "cost_actuals.py")

MARK_START = "<!-- ACTUALS:START -->"
MARK_END = "<!-- ACTUALS:END -->"


def make_project(base: Path) -> Path:
    """Mirror the installed layout: blackboard/ from the shipped template,
    memory/ from the full-engine scripts, roster addon merged in."""
    project = base / "project"
    shutil.copytree(str(TEMPLATE), str(project / "blackboard"))
    shutil.copy2(str(ROSTER_ADDON), str(project / "blackboard" / "21-agent-roster.md"))
    (project / "scripts").mkdir()
    shutil.copy2(str(ROOT / "scripts" / "bb_lock.py"), str(project / "scripts" / "bb_lock.py"))
    (project / "memory").mkdir()
    for name in SCRIPTS:
        shutil.copy2(str(MEMORY_SCRIPTS / name), str(project / "memory" / name))
    return project


def run_script(project: Path, script: str, *args: str) -> "subprocess.CompletedProcess":
    return subprocess.run(
        [sys.executable, str(project / "memory" / script)] + list(args),
        capture_output=True,
        text=True,
    )


def write_transcript(base: Path) -> Path:
    transcript = base / "session.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "model": "claude-opus-4-8",
                "usage": {
                    "input_tokens": 1000,
                    "output_tokens": 200,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return transcript


def load_module_path(module_path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load %s" % module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_memory_module(script: str, module_name: str):
    return load_module_path(MEMORY_SCRIPTS / script, module_name)


def fill_loop_closeout(closeout_path: Path) -> None:
    closeout_path.write_text(
        "# Project OS Loop Closeout\n\n"
        "Loop ID: `L-test`\nDate: `2026-07-29`\nOwner: test\nAgent: unittest\n\n"
        "## Objective\nExercise verified closeout.\n\n"
        "## Scope Proof\nScratch project, local branch, fixed HEAD, boundaries preserved.\n\n"
        "## Source Packet\nTest fixture.\n\n"
        "## Verified Evidence\n- Focused tests passed.\n\n"
        "## Completed\n- Closure fixture completed.\n\n"
        "## Open Work\n- None.\n\n"
        "## Blocker / Dependency\nNone.\n\n"
        "## Next Action\nRetain the receipt.\n\n"
        "## Disposition\nCLOSED\n\n"
        "## Close Condition\nAll test gates passed.\n\n"
        "## Memory Harvest\nLesson reviewed; no promotion required.\n\n"
        "## External Effects\nNone; scratch-only.\n\n"
        "## Final Artifact Links\n- outputs/demo-tracker\n\n"
        "## Updated\n2026-07-29 by unittest.\n",
        encoding="utf-8",
    )


def fill_goal_file(goal_path: Path, sentence: str) -> None:
    """Minimal user edits a real run would make: goal sentence, DoD ticks, tier lock."""
    text = goal_path.read_text(encoding="utf-8")
    text = text.replace(
        "- [ ] TBD\n- [ ] TBD",
        "- [x] Demo tracker works end to end\n- [x] Run closed with VALIDATE: PASS",
        1,
    )
    text = text.replace("Tier: TBD", "Tier: Solo (chosen by USER)", 1)
    text = text.replace("Locked: no", "Locked: yes", 1)
    lines = [sentence if line.strip() == "TBD" else line for line in text.splitlines()]
    goal_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class TemplateClosureContractTests(unittest.TestCase):
    def test_shipped_templates_carry_the_closure_contract(self):
        goal = (TEMPLATE / "00-project-goal.md").read_text(encoding="utf-8")
        cost = (TEMPLATE / "09-cost-estimate.md").read_text(encoding="utf-8")

        self.assertIn("## Canonical Goal (one sentence)", goal)
        self.assertIn("TBD", goal)
        self.assertIn("## Definition of Done", goal)
        self.assertIn("- [ ] TBD\n- [ ] TBD", goal)
        self.assertIn("Tier: TBD", goal)
        self.assertIn("Locked: no", goal)
        self.assertIn("## Summary", goal)
        self.assertIn("## Success Criteria", goal)
        self.assertIn("## Execution Level", goal)
        self.assertIn(MARK_START, cost)
        self.assertIn(MARK_END, cost)
        self.assertLess(cost.find(MARK_START), cost.find(MARK_END))

    def test_loop_closeout_template_is_in_every_closable_tier(self):
        closeout = (TEMPLATE / "23-loop-closeout.md").read_text(encoding="utf-8")
        for field in (
            "Loop ID:", "## Objective", "## Scope Proof", "## Source Packet",
            "## Verified Evidence", "## Completed", "## Open Work",
            "## Blocker / Dependency", "## Next Action", "## Disposition",
            "## Close Condition", "## Memory Harvest", "## External Effects",
            "## Final Artifact Links", "## Updated",
        ):
            self.assertIn(field, closeout)

        new_run = load_memory_module("new_run.py", "new_run_closeout_tiers")
        for tier in ("solo", "mini"):
            with self.subTest(tier=tier):
                self.assertIn("23", new_run.TIER_FILES[tier])

    def test_loop_closeout_requires_complete_closed_receipt(self):
        validate_run = load_memory_module("validate_run.py", "validate_run_closeout")
        complete = (
            "# Project OS Loop Closeout\n\n"
            "Loop ID: `L-42`\nDate: 2026-07-29\nOwner: evaluator\nAgent: verifier\n\n"
            "## Objective\nShip verified local behavior.\n\n"
            "## Scope Proof\nrepo, branch, HEAD, dirty boundary\n\n"
            "## Source Packet\npacket.md\n\n"
            "## Verified Evidence\n- tests passed\n\n"
            "## Completed\n- implementation\n\n"
            "## Open Work\n- None\n\n"
            "## Blocker / Dependency\nNone.\n\n"
            "## Next Action\nArchive the receipt.\n\n"
            "## Disposition\nCLOSED\n\n"
            "## Close Condition\nAll required gates passed.\n\n"
            "## Memory Harvest\nLesson reviewed; not promoted.\n\n"
            "## External Effects\nNone; local-only.\n\n"
            "## Final Artifact Links\n- outputs/final-report.pdf\n\n"
            "## Updated\n2026-07-29 by evaluator.\n"
        )
        self.assertTrue(validate_run._loop_closeout_complete(complete))
        self.assertFalse(validate_run._loop_closeout_complete(
            complete.replace("CLOSED", "OPEN", 1)
        ))
        self.assertFalse(validate_run._loop_closeout_complete(
            complete.replace("- tests passed", "- TBD", 1)
        ))

    def test_loop_closeout_rejects_duplicate_sections_and_placeholder_metadata(self):
        validate_run = load_memory_module("validate_run.py", "validate_run_closeout_adversarial")
        complete = (
            "# Project OS Loop Closeout\n\n"
            "Loop ID: `L-42`\nDate: 2026-07-29\nOwner: evaluator\nAgent: verifier\n\n"
            "## Objective\nShip verified local behavior.\n\n"
            "## Scope Proof\nrepo, branch, HEAD, dirty boundary\n\n"
            "## Source Packet\npacket.md\n\n"
            "## Verified Evidence\n- tests passed\n\n"
            "## Completed\n- implementation\n\n"
            "## Open Work\n- None\n\n"
            "## Blocker / Dependency\nNone.\n\n"
            "## Next Action\nArchive the receipt.\n\n"
            "## Disposition\nCLOSED\n\n"
            "## Close Condition\nAll required gates passed.\n\n"
            "## Memory Harvest\nLesson reviewed; not promoted.\n\n"
            "## External Effects\nNone; local-only.\n\n"
            "## Final Artifact Links\n- outputs/final-report.pdf\n\n"
            "## Updated\n2026-07-29 by evaluator.\n"
        )
        self.assertTrue(validate_run._loop_closeout_complete(complete))
        self.assertFalse(validate_run._loop_closeout_complete(
            complete + "\n## Disposition\nCLOSED\n"
        ))
        self.assertFalse(validate_run._loop_closeout_complete(
            complete.replace("Owner: evaluator", "Owner: TBD")
        ))
        self.assertFalse(validate_run._loop_closeout_complete(
            complete.replace("Agent: verifier", "Agent:")
        ))
        for artifact in ("- TBD", "- None", "- pending", "- N/A"):
            with self.subTest(artifact=artifact):
                self.assertFalse(validate_run._loop_closeout_complete(
                    complete.replace("- outputs/final-report.pdf", artifact)
                ))
        self.assertFalse(validate_run._loop_closeout_complete(
            complete.replace("## Final Artifact Links\n- outputs/final-report.pdf\n\n", "")
        ))
        for replacement in ("CLOSED later", "CLOSED / OPEN", "CLOSED.", "`CLOSED` note"):
            with self.subTest(disposition=replacement):
                self.assertFalse(validate_run._loop_closeout_complete(
                    complete.replace("## Disposition\nCLOSED", "## Disposition\n" + replacement)
                ))

    def test_tier_lock_requires_an_exact_boolean_token(self):
        validate_run = load_memory_module("validate_run.py", "validate_run_under_test")

        for locked in ("yes", "YES", "true", " True "):
            with self.subTest(locked=locked):
                self.assertTrue(validate_run._tier_locked(f"Tier: Solo\nLocked: {locked}"))

        for locked in ("yesterday", "yesman", "trueish", "true-ish", "1", "on"):
            with self.subTest(locked=locked):
                self.assertFalse(validate_run._tier_locked(f"Tier: Solo\nLocked: {locked}"))

        self.assertFalse(validate_run._tier_locked("Tier: TBD\nLocked: yes"))

    def test_tier_lock_accepts_only_canonical_execution_tiers(self):
        validate_run = load_memory_module("validate_run.py", "validate_run_tiers")

        for tier in ("Solo", "Mini", "Full", "Mini (chosen by USER)"):
            with self.subTest(tier=tier):
                self.assertTrue(validate_run._tier_locked(
                    "Tier: %s\nLocked: yes" % tier
                ))

        for tier in ("Premium", "Soloist", "Mini/Full", "TBD"):
            with self.subTest(tier=tier):
                self.assertFalse(validate_run._tier_locked(
                    "Tier: %s\nLocked: yes" % tier
                ))

    def test_definition_of_done_requires_checked_checkboxes(self):
        validate_run = load_memory_module("validate_run.py", "validate_run_dod")

        self.assertFalse(validate_run._dod_no_tbd(
            "## Definition of Done\nNo checkbox here\n"
        ))
        self.assertFalse(validate_run._dod_no_tbd(
            "## Definition of Done\n- [ ] Real unfinished outcome\n"
        ))
        self.assertFalse(validate_run._dod_no_tbd(
            "## Definition of Done\n- [x] Done\n- [ ] Still open\n"
        ))
        self.assertTrue(validate_run._dod_no_tbd(
            "## Definition of Done\n- [x] Done\n- [X] Also done\n"
        ))

    def test_definition_of_done_requires_exact_unfenced_h2(self):
        validate_run = load_memory_module("validate_run.py", "validate_run_exact_dod")

        for heading in (
            "## Definition of Done Later",
            "### Definition of Done",
            "## Definition of Done-ish",
        ):
            with self.subTest(heading=heading):
                self.assertFalse(validate_run._dod_no_tbd(
                    heading + "\n- [x] Ship the verified result\n"
                ))

        self.assertFalse(validate_run._dod_no_tbd(
            "```markdown\n## Definition of Done\n- [x] Example only\n```\n"
        ))
        self.assertTrue(validate_run._dod_no_tbd(
            "```markdown\n## Definition of Done Later\n- [x] Example only\n```\n"
            "## Definition of Done\n- [x] Ship the verified result\n"
        ))

    def test_definition_of_done_rejects_checked_placeholder_text(self):
        validate_run = load_memory_module("validate_run.py", "validate_run_dod_placeholders")

        for placeholder in ("TBD", "tbd later", "TODO: define it", "N/A", "none yet",
                            "pending", "(none)", "**TBD**", "—", "...", "",
                            "Implement TBD later", "Ship now and TODO polish later"):
            with self.subTest(placeholder=placeholder):
                self.assertFalse(validate_run._dod_no_tbd(
                    "## Definition of Done\n- [x] %s\n" % placeholder
                ))

    def test_tier_lock_ignores_fenced_examples(self):
        validate_run = load_memory_module("validate_run.py", "validate_run_fenced_lock")
        self.assertFalse(validate_run._tier_locked(
            "```text\nTier: Full\nLocked: yes\n```\n"
        ))
        self.assertTrue(validate_run._tier_locked(
            "```text\nTier: Full\nLocked: no\n```\n"
            "Tier: Solo\nLocked: yes\n"
        ))

    def test_duplicate_or_conflicting_tier_and_lock_lines_fail(self):
        validate_run = load_memory_module("validate_run.py", "validate_run_duplicate_locks")
        cases = (
            "Tier: Solo\nTier: Full\nLocked: yes\n",
            "Tier: Solo\nTier: Solo\nLocked: yes\n",
            "Tier: Solo\nLocked: yes\nLocked: no\n",
            "Tier: Solo\nLocked: yes\nLocked: true\n",
        )
        for text in cases:
            with self.subTest(text=text):
                self.assertFalse(validate_run._tier_locked(text))

    def test_measured_actual_must_be_finite_nonnegative_number(self):
        validate_run = load_memory_module("validate_run.py", "validate_run_actuals")

        def cost_block(value):
            return (
                MARK_START + "\n"
                "| Model | Est $ | Measured $ | Variance |\n"
                "|---|---|---|---|\n"
                "| main | — | %s | — |\n" % value
                + MARK_END + "\n"
            )

        for value in ("$0", "0.125", "$1,234.50", "**$2.00**"):
            with self.subTest(valid=value):
                self.assertTrue(validate_run._actuals_populated(cost_block(value)))
        for value in ("measured", "$nan", "NaN", "$Infinity", "-1", "$-1"):
            with self.subTest(invalid=value):
                self.assertFalse(validate_run._actuals_populated(cost_block(value)))

    def test_actuals_ignore_fenced_tables_but_accept_unfenced_evidence(self):
        validate_run = load_memory_module("validate_run.py", "validate_run_fenced_actuals")

        def cost_block(value):
            return (
                MARK_START + "\n"
                "| Model | Est $ | Measured $ | Variance |\n"
                "|---|---|---|---|\n"
                "| main | — | %s | — |\n" % value
                + MARK_END + "\n"
            )

        fenced_example = "```markdown\n" + cost_block("$9.99") + "```\n"
        self.assertFalse(validate_run._actuals_populated(fenced_example))
        self.assertTrue(
            validate_run._actuals_populated(fenced_example + cost_block("$0"))
        )

    def test_artifact_manifest_requires_a_substantive_current_entry(self):
        validate_run = load_memory_module("validate_run.py", "validate_run_manifest")
        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td)
            manifest = run_dir / "14-artifact-manifest.md"
            manifest.write_text(
                "# Artifact Manifest\n\n"
                "| Artifact | Type | Status | Owner | Verification | Notes |\n"
                "|---|---|---|---|---|---|\n\n"
                "## Current Artifacts\n\n- \n",
                encoding="utf-8",
            )
            self.assertFalse(validate_run._has_manifest(str(run_dir)))

            manifest.write_text(
                "# Artifact Manifest\n\n"
                "## Current Artifacts\n\n"
                "- [ ] TBD\n",
                encoding="utf-8",
            )
            self.assertFalse(validate_run._has_manifest(str(run_dir)))

            manifest.write_text(
                "# Artifact Manifest\n\n"
                "| Artifact | Type | Status | Owner | Verification | Notes |\n"
                "|---|---|---|---|---|---|\n"
                "| Artifact | Type | Current | Owner | Verification | Notes |\n",
                encoding="utf-8",
            )
            self.assertFalse(validate_run._has_manifest(str(run_dir)))

            manifest.write_text(
                "# Artifact Manifest\n\n"
                "## Current Artifacts\n\n"
                "- [x] outputs/final-report.pdf\n",
                encoding="utf-8",
            )
            self.assertTrue(validate_run._has_manifest(str(run_dir)))

            for placeholder in ("TBD later", "todo - replace", "N/A pending",
                                "None yet", "Pending artifact", "— later"):
                with self.subTest(placeholder=placeholder):
                    manifest.write_text(
                        "# Artifact Manifest\n\n"
                        "| Artifact | Type | Status | Owner | Verification | Notes |\n"
                        "|---|---|---|---|---|---|\n"
                        "| %s | File | Current | builder | checked | final |\n"
                        % placeholder,
                        encoding="utf-8",
                    )
                    self.assertFalse(validate_run._has_manifest(str(run_dir)))

            (run_dir / "13-delivery-report.md").write_text(
                "The manifest will be completed later.\n\n"
                "| Capability | Status | Evidence |\n"
                "|---|---|---|\n"
                "| Browser QA | Not used | Current |\n",
                encoding="utf-8",
            )
            self.assertFalse(validate_run._has_manifest(str(run_dir)))

            manifest.write_text(
                "# Artifact Manifest\n\n"
                "| Artifact | Type | Status | Owner | Verification | Notes |\n"
                "|---|---|---|---|---|---|\n"
                "| outputs/final-report.pdf | PDF | Current | builder | opened | final |\n",
                encoding="utf-8",
            )
            self.assertTrue(validate_run._has_manifest(str(run_dir)))

    def test_artifact_manifest_ignores_fenced_examples(self):
        validate_run = load_memory_module("validate_run.py", "validate_run_fenced_manifest")
        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td)
            manifest = run_dir / "14-artifact-manifest.md"
            fenced_example = (
                "```markdown\n"
                "# Artifact Manifest\n\n"
                "| Artifact | Type | Status | Owner | Verification | Notes |\n"
                "|---|---|---|---|---|---|\n"
                "| outputs/example.pdf | PDF | Current | demo | none | example |\n"
                "```\n"
            )
            manifest.write_text(fenced_example, encoding="utf-8")
            self.assertFalse(validate_run._has_manifest(str(run_dir)))

            manifest.write_text(
                fenced_example
                + "# Artifact Manifest\n\n"
                + "## Current Artifacts\n\n"
                + "- [x] outputs/final-report.pdf\n",
                encoding="utf-8",
            )
            self.assertTrue(validate_run._has_manifest(str(run_dir)))

    def test_packets_require_real_regular_files_inside_the_run(self):
        validate_run = load_memory_module("validate_run.py", "validate_run_packets")
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            run_dir = base / "run"
            packets = run_dir / "packets"
            packets.mkdir(parents=True)

            (packets / "nested-only").mkdir()
            self.assertFalse(validate_run._has_packets(str(run_dir)))
            shutil.rmtree(str(packets / "nested-only"))

            outside_file = base / "outside-packet.md"
            outside_file.write_text("outside packet\n", encoding="utf-8")
            (packets / "escaped.md").symlink_to(outside_file)
            self.assertFalse(validate_run._has_packets(str(run_dir)))
            (packets / "escaped.md").unlink()

            (packets / "builder.md").write_text("real packet\n", encoding="utf-8")
            self.assertTrue(validate_run._has_packets(str(run_dir)))

        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            run_dir = base / "run"
            run_dir.mkdir()
            outside_packets = base / "outside-packets"
            outside_packets.mkdir()
            (outside_packets / "builder.md").write_text("outside packet\n", encoding="utf-8")
            (run_dir / "packets").symlink_to(outside_packets, target_is_directory=True)
            self.assertFalse(validate_run._has_packets(str(run_dir)))

        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            run_dir = base / "run"
            run_dir.mkdir()
            outside_waiver = base / "outside-waiver.md"
            outside_waiver.write_text("no-packets: solo run\n", encoding="utf-8")
            (run_dir / "PACKETS.md").symlink_to(outside_waiver)
            self.assertFalse(validate_run._has_packets(str(run_dir)))

    def test_solo_packet_waiver_ignores_fenced_examples(self):
        validate_run = load_memory_module("validate_run.py", "validate_run_fenced_waiver")
        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td)
            note = run_dir / "06-open-questions.md"
            fenced_example = "```text\nno-packets: solo run\n```\n"
            note.write_text(fenced_example, encoding="utf-8")
            self.assertFalse(validate_run._has_packets(str(run_dir)))

            note.write_text(
                fenced_example + "\nno-packets: solo run\n",
                encoding="utf-8",
            )
            self.assertTrue(validate_run._has_packets(str(run_dir)))


class RunClosureKeystoneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.project = make_project(self.base)
        self.env_patch = mock.patch.dict(
            os.environ, {"BB_LOCK_DIR": str(self.base / "locks")})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def test_fresh_scaffold_fails_validation_with_specific_checks_not_a_crash(self):
        scaffold = run_script(self.project, "new_run.py", "demo", "--tier", "solo")
        self.assertEqual(scaffold.returncode, 0, scaffold.stderr)
        run_dir = self.project / "runs" / "demo"

        result = run_script(self.project, "validate_run.py", str(run_dir))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("VALIDATE: FAIL", result.stdout)
        self.assertIn("[ ] DoD has no remaining TBD", result.stdout)
        self.assertIn("[ ] Actuals populated (not placeholder)", result.stdout)
        self.assertIn("[ ] Graph/memory artifact present", result.stdout)
        self.assertIn("[ ] Tier line present and Locked", result.stdout)
        self.assertIn("[x] Packets present (or solo-run waiver)", result.stdout)
        self.assertIn("[ ] Artifact manifest present", result.stdout)
        self.assertIn("[ ] Loop closeout receipt complete", result.stdout)

    def test_cost_actuals_write_missing_target_is_clean_error(self):
        transcript = write_transcript(self.base)
        result = run_script(
            self.project,
            "cost_actuals.py",
            "--transcript",
            str(transcript),
            "--write",
            "--target",
            str(self.project / "runs" / "nope" / "09-cost-estimate.md"),
        )
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("error:", result.stderr)
        self.assertEqual(result.stdout, "", "missing write targets must fail before rendering a report")
        self.assertEqual(len(result.stderr.splitlines()), 1)

    def test_scaffolded_run_can_reach_validate_pass(self):
        scaffold = run_script(self.project, "new_run.py", "closable", "--tier", "solo")
        self.assertEqual(scaffold.returncode, 0, scaffold.stderr)
        run_dir = self.project / "runs" / "closable"

        fill_goal_file(
            run_dir / "00-project-goal.md",
            "Ship the demo tracker with local-only storage.",
        )
        fill_loop_closeout(run_dir / "23-loop-closeout.md")

        transcript = write_transcript(self.base)
        wrote = run_script(
            self.project,
            "cost_actuals.py",
            "--transcript",
            str(transcript),
            "--write",
            "--target",
            str(run_dir / "09-cost-estimate.md"),
        )
        self.assertEqual(wrote.returncode, 0, wrote.stderr)
        self.assertNotIn("markers not found", wrote.stderr.lower())
        cost_text = (run_dir / "09-cost-estimate.md").read_text(encoding="utf-8")
        self.assertEqual(cost_text.count(MARK_START), 1)
        self.assertIn("Main loop / orchestrator (Opus)", cost_text)

        brain = self.project / "brain"
        brain.mkdir()
        (brain / "shared-brain.jsonl").write_text(
            '{"id":"lesson-1","type":"lesson","text":"Verified closure lesson"}\n',
            encoding="utf-8",
        )
        manifest = run_dir / "14-artifact-manifest.md"
        manifest.write_text(
            manifest.read_text(encoding="utf-8")
            + "\n| outputs/demo-tracker | App | Current | builder | tests passed | final |\n",
            encoding="utf-8",
        )

        result = run_script(self.project, "validate_run.py", str(run_dir))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("VALIDATE: PASS", result.stdout)
        self.assertNotIn("[ ]", result.stdout)

    def test_fresh_run_index_shows_placeholder_goal_and_tier(self):
        run_script(self.project, "new_run.py", "indexed", "--tier", "solo")
        index = (self.project / "runs" / "INDEX.md").read_text(encoding="utf-8")
        self.assertIn("| indexed | TBD | TBD | active |", index)

    def test_new_run_selftest_preserves_user_dirs_and_cleans_only_its_own(self):
        runs = self.project / "runs"
        sentinels = {}
        for name in ("_selftest_tmp_run", "selftestprobe"):
            sentinel = runs / name / "user-sentinel.bin"
            sentinel.parent.mkdir(parents=True, exist_ok=True)
            sentinel.write_bytes(("user-owned:" + name).encode("utf-8"))
            sentinels[name] = sentinel.read_bytes()

        result = run_script(self.project, "new_run.py", "--selftest")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        for name, before in sentinels.items():
            self.assertEqual((runs / name / "user-sentinel.bin").read_bytes(), before)
        self.assertEqual(
            {p.name for p in runs.iterdir() if p.is_dir()},
            set(sentinels),
            "selftest left a unique scratch run behind",
        )


class AdoptProjectStubTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.project = make_project(self.base)
        self.source = self.base / "widget-app"
        self.source.mkdir()
        (self.source / "README.md").write_text(
            "# Widget Tracker\n\nTrack widgets locally.\n", encoding="utf-8"
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_adopt_selftest_preserves_preexisting_fixed_slug_content(self):
        runs = self.project / "runs"
        sentinel = runs / "adoptselftest" / "user-sentinel.bin"
        sentinel.parent.mkdir(parents=True)
        sentinel.write_bytes(b"user-owned-adopt-selftest")
        before_dirs = {p.name for p in runs.iterdir() if p.is_dir()}
        before_bytes = sentinel.read_bytes()

        result = run_script(self.project, "adopt_project.py", "--selftest")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(sentinel.read_bytes(), before_bytes)
        self.assertEqual(
            {p.name for p in runs.iterdir() if p.is_dir()},
            before_dirs,
            "selftest deleted a user directory or left a scratch run behind",
        )

    def test_adopt_writes_goal_and_dod_into_template_copied_run(self):
        result = run_script(self.project, "adopt_project.py", str(self.source), "--slug", "adopted")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("appending goal/DoD block", result.stderr)

        body = (self.project / "runs" / "adopted" / "00-project-goal.md").read_text(encoding="utf-8")
        self.assertIn("<!-- inferred from README.md -->\nWidget Tracker", body)
        self.assertNotIn("- [ ] TBD\n- [ ] TBD", body)
        self.assertIn("meets its stated purpose", body)
        self.assertIn("- [ ] Complete /deliver and record the actual closure validation result", body)
        self.assertNotIn("VALIDATE: PASS", body)
        validator = load_memory_module("validate_run.py", "adoption_pending_validator")
        self.assertFalse(validator._dod_no_tbd(body), "adoption cannot mark verification complete")

        index = (self.project / "runs" / "INDEX.md").read_text(encoding="utf-8")
        self.assertIn("adopted", index)
        self.assertIn("Widget Tracker", index)

    def test_adopt_appends_marked_block_and_warns_when_anchors_missing(self):
        (self.project / "blackboard" / "00-project-goal.md").write_text(
            "# Project Goal\n\n## Summary\n\nDescribe the project.\n", encoding="utf-8"
        )

        result = run_script(self.project, "adopt_project.py", str(self.source), "--slug", "fallback")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("appending goal/DoD block", result.stderr)
        self.assertIn("canonical-goal anchor", result.stderr)
        self.assertIn("definition-of-done anchor", result.stderr)

        body = (self.project / "runs" / "fallback" / "00-project-goal.md").read_text(encoding="utf-8")
        self.assertIn("## Summary", body)
        self.assertIn("template anchors were missing", body)
        self.assertIn("## Canonical Goal (one sentence)", body)
        self.assertIn("Widget Tracker", body)
        self.assertIn("## Definition of Done", body)
        self.assertIn("- [ ] Complete /deliver and record the actual closure validation result", body)
        self.assertNotIn("VALIDATE: PASS", body)
        validator = load_memory_module("validate_run.py", "adoption_fallback_pending_validator")
        self.assertFalse(validator._dod_no_tbd(body), "adoption cannot mark verification complete")

    def test_adopt_ignores_readme_symlink_outside_the_project(self):
        (self.source / "README.md").unlink()
        outside = self.base / "outside-secret.md"
        outside.write_text("# OUTSIDE SECRET GOAL\n", encoding="utf-8")
        (self.source / "README.md").symlink_to(outside)

        result = run_script(
            self.project, "adopt_project.py", str(self.source), "--slug", "safe-readme"
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        body = (self.project / "runs" / "safe-readme" / "00-project-goal.md").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("OUTSIDE SECRET GOAL", body)
        self.assertIn("Adopt and improve widget-app", body)

    def test_adopt_ignores_package_symlink_outside_the_project(self):
        (self.source / "README.md").unlink()
        outside = self.base / "outside-package.json"
        outside.write_text(
            json.dumps({"description": "OUTSIDE PACKAGE SECRET"}), encoding="utf-8"
        )
        (self.source / "package.json").symlink_to(outside)

        result = run_script(
            self.project, "adopt_project.py", str(self.source), "--slug", "safe-package"
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        body = (self.project / "runs" / "safe-package" / "00-project-goal.md").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("OUTSIDE PACKAGE SECRET", body)
        self.assertIn("Adopt and improve widget-app", body)

    def test_adopt_nonobject_package_json_uses_safe_fallback(self):
        (self.source / "README.md").unlink()
        for index, package_value in enumerate(([], "package-name", 17)):
            with self.subTest(package_value=package_value):
                (self.source / "package.json").write_text(
                    json.dumps(package_value), encoding="utf-8"
                )
                slug = "nonobject-package-%s" % index
                result = run_script(
                    self.project, "adopt_project.py", str(self.source), "--slug", slug
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                body = (self.project / "runs" / slug / "00-project-goal.md").read_text(
                    encoding="utf-8"
                )
                self.assertIn("Adopt and improve widget-app", body)


class RunSlugValidationTests(unittest.TestCase):
    BAD_SLUGS = ("", ".", "..", "a/b", "a\\b")

    def test_new_run_rejects_unsafe_slugs_before_creating_any_run(self):
        for bad in self.BAD_SLUGS:
            with self.subTest(slug=bad), tempfile.TemporaryDirectory() as td:
                base = Path(td)
                project = make_project(base)
                result = run_script(project, "new_run.py", bad, "--tier", "solo")
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("Traceback", result.stderr)
                self.assertIn("invalid run slug", result.stderr.lower())
                runs = project / "runs"
                created = [] if not runs.exists() else [
                    p for p in runs.iterdir() if p.name != "INDEX.md"
                ]
                self.assertEqual(created, [], "unsafe slug created run content")

        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            project = make_project(base / "installed")
            escaped = base / "absolute-escape"
            result = run_script(project, "new_run.py", str(escaped), "--tier", "solo")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("invalid run slug", result.stderr.lower())
            self.assertFalse(escaped.exists())

    def test_adopt_rejects_unsafe_explicit_slugs_before_scaffolding(self):
        for bad in self.BAD_SLUGS:
            with self.subTest(slug=bad), tempfile.TemporaryDirectory() as td:
                base = Path(td)
                project = make_project(base / "installed")
                source = base / "source"
                source.mkdir()
                result = run_script(
                    project, "adopt_project.py", str(source), "--slug", bad
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("Traceback", result.stderr)
                self.assertIn("invalid run slug", result.stderr.lower())
                runs = project / "runs"
                created = [] if not runs.exists() else [
                    p for p in runs.iterdir() if p.name != "INDEX.md"
                ]
                self.assertEqual(created, [], "unsafe adopt slug created run content")

        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            project = make_project(base / "installed")
            source = base / "source"
            source.mkdir()
            escaped = base / "absolute-adopt-escape"
            result = run_script(
                project, "adopt_project.py", str(source), "--slug", str(escaped)
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("invalid run slug", result.stderr.lower())
            self.assertFalse(escaped.exists())

    def test_new_run_rejects_a_symlinked_runs_root_before_writing_outside(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            project = make_project(base / "installed")
            outside = base / "outside-runs"
            outside.mkdir()
            (project / "runs").symlink_to(outside, target_is_directory=True)

            result = run_script(project, "new_run.py", "escaped", "--tier", "solo")
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("Traceback", result.stderr)
            self.assertEqual(list(outside.iterdir()), [], "scaffold wrote through runs symlink")

class CostActualsWriteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.project = make_project(self.base)
        self.transcript = write_transcript(self.base)
        self.env_patch = mock.patch.dict(
            os.environ, {"BB_LOCK_DIR": str(self.base / "locks")})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, target: Path) -> "subprocess.CompletedProcess":
        return run_script(
            self.project,
            "cost_actuals.py",
            "--transcript",
            str(self.transcript),
            "--write",
            "--target",
            str(target),
        )

    def test_write_succeeds_against_a_fresh_template_cost_file(self):
        target = self.base / "09-cost-estimate.md"
        shutil.copy2(str(TEMPLATE / "09-cost-estimate.md"), str(target))

        result = self._write(target)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("markers not found", result.stderr.lower())
        text = target.read_text(encoding="utf-8")
        self.assertEqual(text.count(MARK_START), 1)
        self.assertIn("Main loop / orchestrator (Opus)", text)

    def test_no_subagent_transcripts_are_not_reported_as_measured_zero(self):
        result = run_script(
            self.project,
            "cost_actuals.py",
            "--transcript",
            str(self.transcript),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        subagent_row = next(
            line for line in result.stdout.splitlines()
            if "| **Subtotal — subagents** |" in line
        )
        total_row = next(
            line for line in result.stdout.splitlines()
            if "| **Total** |" in line
        )
        self.assertEqual(
            subagent_row,
            "| **Subtotal — subagents** | — | Not measured | — |",
        )
        self.assertNotIn("$0.0000", subagent_row)
        self.assertIn("Not fully measured", total_row)

    def test_missing_main_usage_is_not_reported_as_measured_zero(self):
        self.transcript.write_text('{"type":"status","message":"no main usage"}\n', encoding="utf-8")
        subagents = self.base / "subagents"
        subagents.mkdir()
        (subagents / "worker.jsonl").write_text(
            json.dumps({
                "model": "claude-opus-4-8",
                "usage": {"input_tokens": 100},
            }) + "\n",
            encoding="utf-8",
        )

        result = run_script(
            self.project,
            "cost_actuals.py",
            "--transcript",
            str(self.transcript),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        main_row = next(
            line for line in result.stdout.splitlines()
            if "| **Subtotal — orchestrator** |" in line
        )
        subagent_row = next(
            line for line in result.stdout.splitlines()
            if "| **Subtotal — subagents** |" in line
        )
        total_row = next(
            line for line in result.stdout.splitlines()
            if "| **Total** |" in line
        )
        self.assertEqual(
            main_row,
            "| **Subtotal — orchestrator** | — | Not measured | — |",
        )
        self.assertEqual(
            subagent_row,
            "| **Subtotal — subagents** | — | $0.0005 | — |",
        )
        self.assertIn("Not fully measured", total_row)

    def test_explicit_zero_subagent_usage_is_measured_zero(self):
        subagents = self.base / "subagents"
        subagents.mkdir()
        (subagents / "worker.jsonl").write_text(
            json.dumps({
                "model": "claude-opus-4-8",
                "usage": {"input_tokens": 0},
            }) + "\n",
            encoding="utf-8",
        )

        result = run_script(
            self.project,
            "cost_actuals.py",
            "--transcript",
            str(self.transcript),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        subagent_row = next(
            line for line in result.stdout.splitlines()
            if "| **Subtotal — subagents** |" in line
        )
        total_row = next(
            line for line in result.stdout.splitlines()
            if "| **Total** |" in line
        )
        self.assertEqual(
            subagent_row,
            "| **Subtotal — subagents** | — | $0.0000 | — |",
        )
        self.assertNotIn("Not fully measured", total_row)

    def test_write_appends_markers_and_warns_when_they_are_missing(self):
        target = self.base / "no-markers.md"
        target.write_text("# Cost Estimate\n\nNo markers here.\n", encoding="utf-8")

        result = self._write(target)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ACTUALS markers not found", result.stderr)
        text = target.read_text(encoding="utf-8")
        self.assertIn("No markers here.", text)
        self.assertEqual(text.count(MARK_START), 1)
        self.assertEqual(text.count(MARK_END), 1)
        self.assertIn("Main loop / orchestrator (Opus)", text)

        again = self._write(target)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertNotIn("ACTUALS markers not found", again.stderr)
        text = target.read_text(encoding="utf-8")
        self.assertEqual(text.count(MARK_START), 1)
        self.assertEqual(text.count(MARK_END), 1)

    def test_lone_reversed_or_duplicate_markers_refuse_without_modifying(self):
        cases = {
            "lone-start": "# Cost\n" + MARK_START + "\nold\n",
            "lone-end": "# Cost\nold\n" + MARK_END + "\n",
            "reversed": MARK_END + "\nold\n" + MARK_START + "\n",
            "duplicate-start": MARK_START + "\n" + MARK_START + "\n" + MARK_END + "\n",
            "duplicate-end": MARK_START + "\n" + MARK_END + "\n" + MARK_END + "\n",
        }
        for name, original in cases.items():
            with self.subTest(name=name):
                target = self.base / (name + ".md")
                target.write_text(original, encoding="utf-8")
                result = self._write(target)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("Traceback", result.stderr)
                self.assertIn("ACTUALS markers", result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertEqual(target.read_text(encoding="utf-8"), original)

    def test_fenced_actuals_examples_are_ignored_as_live_markers(self):
        target = self.base / "fenced-example.md"
        fenced_example = (
            "# Cost documentation\n\n"
            "```markdown\n"
            + MARK_START + "\n"
            + "example only\n"
            + MARK_END + "\n"
            + "```\n"
        )
        target.write_text(fenced_example, encoding="utf-8")

        first = self._write(target)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("ACTUALS markers not found", first.stderr)
        first_text = target.read_text(encoding="utf-8")
        self.assertTrue(first_text.startswith(fenced_example))
        self.assertEqual(first_text.count(MARK_START), 2)
        self.assertEqual(first_text.count(MARK_END), 2)

        second = self._write(target)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertNotIn("ACTUALS markers not found", second.stderr)
        second_text = target.read_text(encoding="utf-8")
        self.assertTrue(second_text.startswith(fenced_example))
        self.assertEqual(second_text.count(MARK_START), 2)
        self.assertEqual(second_text.count(MARK_END), 2)

    def test_marker_update_normalizes_generated_table_to_detected_crlf(self):
        target = self.base / "crlf-markers.md"
        prefix = b"# Cost\r\n\r\nmanual prefix  \r\n" + MARK_START.encode()
        suffix = MARK_END.encode() + b"\r\nmanual suffix\r\n"
        target.write_bytes(prefix + b"\r\nold table\r\n" + suffix)

        result = self._write(target)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = target.read_bytes()
        self.assertTrue(data.startswith(prefix + b"\r\n"))
        self.assertTrue(data.endswith(suffix))
        self.assertNotIn(b"\n", data.replace(b"\r\n", b""))

    def test_marker_append_normalizes_generated_block_to_detected_crlf(self):
        target = self.base / "crlf-no-markers.md"
        original = b"# Cost\r\n\r\nmanual legacy text  \r\n\r\n"
        target.write_bytes(original)

        result = self._write(target)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = target.read_bytes()
        self.assertTrue(data.startswith(original))
        self.assertNotIn(b"\n", data.replace(b"\r\n", b""))
        self.assertIn(MARK_START.encode(), data)
        self.assertIn(MARK_END.encode(), data)

    def test_unterminated_markdown_fence_fails_closed_without_modifying(self):
        target = self.base / "unterminated-fence.md"
        original = (
            "# Cost documentation\n\n"
            "```markdown\n"
            + MARK_START + "\n"
            + "hidden example\n"
            + MARK_END + "\n"
        ).encode("utf-8")
        target.write_bytes(original)

        result = self._write(target)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("fence", result.stderr.lower())
        self.assertEqual(result.stdout, "")
        self.assertEqual(target.read_bytes(), original)

    def test_stale_cost_writer_cannot_overwrite_replacement_owner(self):
        cost_actuals = load_module_path(
            self.project / "memory" / "cost_actuals.py", "installed_cost_stale")
        lock_module = load_module_path(
            self.project / "scripts" / "bb_lock.py", "installed_cost_lock_stale")
        cost_actuals.bb_lock = lock_module
        target = self.base / "stale-cost.md"
        target.write_text(MARK_START + "\nold\n" + MARK_END + "\n", encoding="utf-8")
        reached_stage = threading.Event()
        resume_stale = threading.Event()
        old_result = []
        real_fdopen = cost_actuals.os.fdopen

        def gated_fdopen(fd, mode="r", *args, **kwargs):
            if (threading.current_thread().name == "stale-cost-writer"
                    and "w" in mode):
                reached_stage.set()
                if not resume_stale.wait(5):
                    raise RuntimeError("test timed out waiting to resume stale cost writer")
            return real_fdopen(fd, mode, *args, **kwargs)

        with mock.patch.object(lock_module, "LOCK_DIR", str(self.base / "stale-locks")), \
                mock.patch.object(lock_module, "STALE_AFTER_SEC", 0.08), \
                mock.patch.object(lock_module, "POLL_SEC", 0.005), \
                mock.patch.object(cost_actuals.os, "fdopen", side_effect=gated_fdopen), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            stale = threading.Thread(
                target=lambda: old_result.append(
                    cost_actuals._update_markers(target, "stale table")),
                name="stale-cost-writer",
            )
            stale.start()
            self.assertTrue(reached_stage.wait(2), "stale writer never staged")
            time.sleep(0.12)
            replacement_result = cost_actuals._update_markers(
                target, "replacement owner table")
            resume_stale.set()
            stale.join(3)

        self.assertFalse(stale.is_alive())
        self.assertTrue(replacement_result)
        self.assertEqual(old_result, [False])
        text = target.read_text(encoding="utf-8")
        self.assertIn("replacement owner table", text)
        self.assertNotIn("stale table", text)
        self.assertIn("lease lost", err.getvalue().lower())

    def test_cost_release_failure_is_reported_and_run_fails(self):
        cost_actuals = load_module_path(
            self.project / "memory" / "cost_actuals.py", "installed_cost_release")
        lock_module = load_module_path(
            self.project / "scripts" / "bb_lock.py", "installed_cost_lock_release")
        cost_actuals.bb_lock = lock_module
        target = self.base / "release-cost.md"
        target.write_text(MARK_START + "\nold\n" + MARK_END + "\n", encoding="utf-8")
        real_release = lock_module.release

        def release_but_report_failure(*args, **kwargs):
            real_release(*args, **kwargs)
            return False

        with mock.patch.object(lock_module, "LOCK_DIR", str(self.base / "release-locks")), \
                mock.patch.object(
                    lock_module, "release", side_effect=release_but_report_failure
                ) as release, contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            with self.assertRaises(cost_actuals.CostActualsError):
                cost_actuals.run(
                    self.transcript, cost_actuals.DEFAULT_PRICES, True, target)

        release.assert_called_once()
        self.assertIn("release", err.getvalue().lower())
        self.assertIn("FAILED", err.getvalue())
        self.assertIn("Main loop / orchestrator", target.read_text(encoding="utf-8"))

    def test_cost_acquire_failure_refuses_without_writing(self):
        cost_actuals = load_module_path(
            self.project / "memory" / "cost_actuals.py", "installed_cost_acquire")
        lock_module = load_module_path(
            self.project / "scripts" / "bb_lock.py", "installed_cost_lock_acquire")
        cost_actuals.bb_lock = lock_module
        target = self.base / "held-cost.md"
        original = MARK_START + "\nold\n" + MARK_END + "\n"
        target.write_text(original, encoding="utf-8")

        with mock.patch.object(lock_module, "LOCK_DIR", str(self.base / "held-locks")), \
                mock.patch.object(cost_actuals, "LOCK_WAIT_SEC", 0.05):
            holder = lock_module.acquire(str(target), agent="someone-else")
            self.assertTrue(holder)
            try:
                with self.assertRaises(cost_actuals.CostActualsError) as caught:
                    cost_actuals._update_markers(target, "usurper table")
            finally:
                lock_module.release(str(target), token=holder)

        self.assertIn("lock", str(caught.exception).lower())
        self.assertEqual(target.read_text(encoding="utf-8"), original,
                         "a refused writer must not modify the target")

    def test_marker_update_uses_atomic_replace(self):
        cost_actuals = load_memory_module("cost_actuals.py", "cost_actuals_atomic")
        target = self.base / "atomic.md"
        target.write_text(MARK_START + "\nold\n" + MARK_END + "\n", encoding="utf-8")
        with mock.patch.object(
            cost_actuals.bb_lock, "LOCK_DIR", str(self.base / "atomic-locks")
        ), mock.patch.object(
            cost_actuals.os, "replace", wraps=cost_actuals.os.replace
        ) as replace:
            cost_actuals._update_markers(target, "new table")
        replace.assert_called_once()
        self.assertIn("new table", target.read_text(encoding="utf-8"))

    def test_invalid_usage_records_fail_before_render_or_write(self):
        token_fields = (
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        )
        cases = [
            ("record-not-object", "[]\n"),
            ("usage-not-object", json.dumps({"model": "claude-opus", "usage": [1]}) + "\n"),
            ("model-not-string", json.dumps({"model": 7, "usage": {"input_tokens": 1}}) + "\n"),
            ("bool-token", json.dumps({"model": "claude-opus", "usage": {"input_tokens": True}}) + "\n"),
            ("string-token", json.dumps({"model": "claude-opus", "usage": {"input_tokens": "1"}}) + "\n"),
            ("nan-token", '{"model":"claude-opus","usage":{"input_tokens":NaN}}\n'),
            ("infinity-token", '{"model":"claude-opus","usage":{"input_tokens":Infinity}}\n'),
            ("overflow-token", '{"model":"claude-opus","usage":{"input_tokens":1e999}}\n'),
        ]
        for field in token_fields:
            cases.append((
                "negative-" + field,
                json.dumps({"model": "claude-opus", "usage": {field: -1}}) + "\n",
            ))

        for name, record in cases:
            with self.subTest(name=name):
                transcript = self.base / (name + ".jsonl")
                transcript.write_text(record, encoding="utf-8")
                target = self.base / (name + "-cost.md")
                target.write_text(
                    MARK_START + "\nold actuals\n" + MARK_END + "\n",
                    encoding="utf-8",
                )
                original = target.read_text(encoding="utf-8")
                result = run_script(
                    self.project,
                    "cost_actuals.py",
                    "--transcript",
                    str(transcript),
                    "--write",
                    "--target",
                    str(target),
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("Traceback", result.stderr)
                self.assertEqual(result.stdout, "", "invalid usage must fail before rendering")
                self.assertEqual(target.read_text(encoding="utf-8"), original)

    def test_bad_or_unusable_transcripts_never_render_or_rewrite_actuals(self):
        malformed = self.base / "malformed.jsonl"
        malformed.write_text("{not json\n", encoding="utf-8")
        no_usage = self.base / "no-usage.jsonl"
        no_usage.write_text('{"type":"status","message":"no usage here"}\n', encoding="utf-8")
        missing = self.base / "missing.jsonl"
        unreadable = self.base / "unreadable.jsonl"
        unreadable.mkdir()

        for name, transcript in (
            ("malformed", malformed),
            ("no-usage", no_usage),
            ("missing", missing),
            ("unreadable", unreadable),
        ):
            with self.subTest(name=name):
                target = self.base / (name + "-actuals.md")
                target.write_bytes(
                    ("# Costs\n" + MARK_START + "\noriginal bytes — keep\n"
                     + MARK_END + "\n").encode("utf-8")
                )
                before = target.read_bytes()
                result = run_script(
                    self.project,
                    "cost_actuals.py",
                    "--transcript",
                    str(transcript),
                    "--write",
                    "--target",
                    str(target),
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("Traceback", result.stderr)
                self.assertIn("error:", result.stderr.lower())
                self.assertEqual(result.stdout, "", "bad transcripts must fail before rendering")
                self.assertNotIn("$0.0000", result.stdout)
                self.assertEqual(target.read_bytes(), before)

    def test_empty_usage_object_fails_before_render_or_write(self):
        transcript = self.base / "empty-usage.jsonl"
        transcript.write_text(
            json.dumps({"model": "claude-opus", "usage": {}}) + "\n",
            encoding="utf-8",
        )
        target = self.base / "empty-usage-cost.md"
        target.write_bytes((MARK_START + "\noriginal\n" + MARK_END + "\n").encode("utf-8"))
        before = target.read_bytes()

        result = run_script(
            self.project, "cost_actuals.py", "--transcript", str(transcript),
            "--write", "--target", str(target),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(len(result.stderr.splitlines()), 1)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(target.read_bytes(), before)

    def test_unknown_model_requires_explicit_pricing_for_nonzero_usage(self):
        transcript = self.base / "unknown-model.jsonl"
        transcript.write_text(
            json.dumps({
                "model": "mystery-model",
                "usage": {"input_tokens": 100_000, "output_tokens": 0},
            }) + "\n",
            encoding="utf-8",
        )
        target = self.base / "unknown-model-cost.md"
        target.write_bytes((MARK_START + "\noriginal\n" + MARK_END + "\n").encode("utf-8"))
        before = target.read_bytes()

        missing_price = run_script(
            self.project, "cost_actuals.py", "--transcript", str(transcript),
            "--write", "--target", str(target),
        )
        self.assertNotEqual(missing_price.returncode, 0)
        self.assertEqual(missing_price.stdout, "")
        self.assertEqual(len(missing_price.stderr.splitlines()), 1)
        self.assertEqual(target.read_bytes(), before)

        priced = run_script(
            self.project, "cost_actuals.py", "--transcript", str(transcript),
            "--prices", json.dumps({"mystery-model": {"in": 2, "out": 4}}),
            "--write", "--target", str(target),
        )
        self.assertEqual(priced.returncode, 0, priced.stdout + priced.stderr)
        self.assertIn("Mystery-model", priced.stdout)
        self.assertNotEqual(target.read_bytes(), before)

    def test_invalid_custom_prices_fail_cleanly_without_touching_target(self):
        cases = {
            "non-object-table": "[]",
            "malformed-json": "{not json",
            "empty-model-key": json.dumps({"": {"in": 1, "out": 2}}),
            "entry-not-object": json.dumps({"opus": []}),
            "missing-in": json.dumps({"opus": {"out": 2}}),
            "missing-out": json.dumps({"opus": {"in": 1}}),
            "bool": json.dumps({"opus": {"in": True, "out": 2}}),
            "string": json.dumps({"opus": {"in": "1", "out": 2}}),
            "negative": json.dumps({"opus": {"in": -1, "out": 2}}),
            "nan": '{"opus":{"in":NaN,"out":2}}',
            "infinity": '{"opus":{"in":Infinity,"out":2}}',
            "overflow": '{"opus":{"in":1e999,"out":2}}',
        }
        for name, prices in cases.items():
            with self.subTest(name=name):
                target = self.base / (name + "-prices-cost.md")
                target.write_bytes(
                    (MARK_START + "\noriginal\n" + MARK_END + "\n").encode("utf-8")
                )
                before = target.read_bytes()
                result = run_script(
                    self.project, "cost_actuals.py", "--transcript", str(self.transcript),
                    "--prices", prices, "--write", "--target", str(target),
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertEqual(len(result.stderr.splitlines()), 1, result.stderr)
                self.assertNotIn("traceback", result.stderr.lower())
                self.assertNotIn("$nan", (result.stdout + result.stderr).lower())
                self.assertEqual(target.read_bytes(), before)

    def test_price_file_and_auto_detection_errors_use_one_line_boundary(self):
        empty_home = self.base / "empty-home"
        empty_home.mkdir()
        env = dict(os.environ)
        env["HOME"] = str(empty_home)
        commands = (
            [sys.executable, str(self.project / "memory" / "cost_actuals.py")],
            [
                sys.executable, str(self.project / "memory" / "cost_actuals.py"),
                "--transcript", str(self.transcript),
                "--prices", str(self.base / "missing-prices.json"),
            ],
        )
        for command in commands:
            with self.subTest(command=command):
                result = subprocess.run(command, capture_output=True, text=True, env=env)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertEqual(len(result.stderr.splitlines()), 1, result.stderr)
                self.assertNotIn("traceback", result.stderr.lower())


class GoalGuardTemplateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.project = make_project(self.base)
        run_script(self.project, "new_run.py", "guarded", "--tier", "solo")
        self.run_dir = self.project / "runs" / "guarded"
        self.goal = self.run_dir / "00-project-goal.md"
        self.roster = self.run_dir / "21-agent-roster.md"

    def tearDown(self):
        self.tmp.cleanup()

    def test_fresh_template_goal_reports_not_locked_not_vacuous_match(self):
        self.assertTrue(self.roster.exists(), "solo scaffold should include the roster")
        result = run_script(self.project, "goal_guard.py", str(self.goal), str(self.roster))
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("NOT-LOCKED", result.stdout)
        self.assertNotIn("MATCH", result.stdout)

    def test_filled_goal_still_reaches_match_against_recorded_hash(self):
        fill_goal_file(self.goal, "Ship the guarded demo tracker.")
        hashed = run_script(self.project, "goal_guard.py", "--hash", str(self.goal))
        self.assertEqual(hashed.returncode, 0, hashed.stderr)
        digest = hashed.stdout.strip()
        self.assertEqual(len(digest), 12)

        roster_text = self.roster.read_text(encoding="utf-8")
        self.roster.write_text(
            roster_text.replace("Goal hash:", "Goal hash: %s" % digest, 1),
            encoding="utf-8",
        )
        result = run_script(self.project, "goal_guard.py", str(self.goal), str(self.roster))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("MATCH", result.stdout)

    def test_goal_guard_selftest_covers_not_locked(self):
        result = run_script(self.project, "goal_guard.py", "--selftest")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("goal_guard selftest: OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
