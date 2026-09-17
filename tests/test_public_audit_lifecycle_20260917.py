"""Regression controls for approval, adoption, and exact run schema files.

Only temporary synthetic projects are modified. The accepted-tier and filename
oracles are literal workflow contracts, not copied from implementation constants.
"""
import hashlib
import importlib.util
import itertools
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "addons" / "full-engine" / "memory"
SCRIPTS = ("new_run.py", "adopt_project.py", "goal_guard.py", "validate_run.py")
SOLO_EXACT = (
    "00-project-goal.md", "07-approved-plan.md", "09-cost-estimate.md",
    "12-evaluation-log.md", "13-delivery-report.md", "14-artifact-manifest.md",
    "19-memory-harvest.md", "21-agent-roster.md", "23-loop-closeout.md",
)
LARGER_EXACT = ("03-decisions.md", "04-risks.md", "06-open-questions.md")


def load_script(filename):
    spec = importlib.util.spec_from_file_location("audit_" + filename[:-3], ENGINE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cli(script, *args):
    return subprocess.run([sys.executable, "-B", str(script)] + list(map(str, args)),
                          capture_output=True, text=True, timeout=20)


def install(base, full=True):
    project = base / "project"
    shutil.copytree(ROOT / "blackboard-template", project / "blackboard")
    shutil.copytree(ROOT / "blackboard-template", project / "blackboard-template")
    (project / "memory").mkdir()
    (project / "scripts").mkdir()
    shutil.copy2(ROOT / "scripts/bb_lock.py", project / "scripts/bb_lock.py")
    for name in SCRIPTS if full else ("new_run.py",):
        shutil.copy2(ENGINE / name, project / "memory" / name)
    if full:
        for directory in ("blackboard", "blackboard-template"):
            shutil.copy2(ROOT / "addons/full-engine/blackboard-addons/21-agent-roster.md",
                         project / directory / "21-agent-roster.md")
    return project


class ApprovalSyntaxTests(unittest.TestCase):
    def setUp(self):
        self.validator = load_script("validate_run.py")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run = Path(self.temp.name) / "project" / "runs" / "synthetic"
        self.validator._good_run(str(self.run))
        self.packet = self.run / "packets" / "3-builder-001.md"

    def test_every_accepted_full_spelling_uses_the_same_packet_gate(self):
        statuses = (
            ("Status: Approved\n", True),
            ("Status: Rejected\n", False),
            ("```text\nStatus: Approved\n```\n", False),
            ("~~~markdown\nStatus: Approved\n~~~\n", False),
            ("Status: Approved\nStatus:\n", False),
            ("```text\nStatus: Rejected\n```\nStatus: Approved\n", True),
        )
        count = 0
        for prefix, field, bold, tier in itertools.product(
            ("", "- ", "* ", "+ "), ("Tier", "Chosen Tier"),
            (False, True), ("Full", "Full Swarm", "fUlL (chosen by USER)",
                            "Full Swarm (chosen by USER)"),
        ):
            label = "**%s**" % field if bold else field
            goal = "%s%s: %s\nLocked: yes\n" % (prefix, label, tier)
            with self.subTest(goal=goal):
                self.assertTrue(self.validator._tier_locked(goal))
                self.assertTrue(self.validator._is_full_swarm(goal))
                for packet, expected in statuses:
                    with self.subTest(packet=packet):
                        self.packet.write_text(packet, encoding="utf-8")
                        self.assertEqual(expected, self.validator._has_packets(str(self.run), goal))
                        count += 1
        self.assertEqual(count, 384)

    def test_fence_variants_and_malformed_statuses_fail_closed(self):
        for marker in ("```", "````", "~~~", "~~~~"):
            for closing in (marker, marker + marker[0], ""):
                with self.subTest(marker=marker, closing=closing):
                    example = marker + "text\nStatus: Approved\n" + closing + "\n"
                    self.assertFalse(self.validator._packet_is_approved(example))
                    if closing:
                        self.assertTrue(self.validator._packet_is_approved(example + "Status: Approved\n"))
        for malformed in ("Status:", "Status:   ", "+ Status:", "**Status:**",
                          "Status: `Approved", "Status: Approved**", "***Status: Approved",
                          "Status: Approved / Draft", "+ Status: Rejected"):
            for text in (malformed, "Status: Approved\n" + malformed,
                         malformed + "\nStatus: Approved"):
                with self.subTest(text=text):
                    self.assertFalse(self.validator._packet_is_approved(text))
        for valid in ("Status: Approved", "+ Status: approved", "**Status:** Approved",
                      "* **Status**: Approved", "Status: **Approved**", "Status: `Approved`",
                      "Status: Approved\nStatus: Approved"):
            with self.subTest(valid=valid):
                self.assertTrue(self.validator._packet_is_approved(valid))

    def test_empty_or_duplicate_tier_metadata_is_not_a_locked_tier(self):
        for field in ("Tier:", "Chosen Tier:", "+ Tier:", "Locked:"):
            with self.subTest(field=field):
                self.assertFalse(self.validator._tier_locked("Tier: Full\nLocked: yes\n" + field))
        for tier in ("Solo", "Solo Agent Loop", "Mini", "Mini Swarm"):
            goal = "Tier: %s\nLocked: true\n" % tier
            self.assertTrue(self.validator._tier_locked(goal))
            self.assertFalse(self.validator._is_full_swarm(goal))
            self.packet.write_text("Status: Draft\n", encoding="utf-8")
            self.assertTrue(self.validator._has_packets(str(self.run), goal))
        goal = "```text\nTier: Full\nLocked: no\n```\nTier: Solo\nLocked: yes\n"
        self.assertTrue(self.validator._tier_locked(goal))
        self.assertFalse(self.validator._is_full_swarm(goal))

    def test_real_validate_changes_only_with_the_approval(self):
        for field in ("Tier: Full", "Chosen Tier: Full Swarm", "+ Tier: Full Swarm"):
            (self.run / "00-project-goal.md").write_text(
                "## Definition of Done\n- [x] Exercise the synthetic fixture\n\n"
                "## Execution Level\n" + field + "\nLocked: yes\n", encoding="utf-8")
            for status, expected in (("Status: Rejected", 1), ("Status: Approved", 0),
                                     ("```text\nStatus: Approved\n```", 1)):
                with self.subTest(field=field, status=status):
                    self.packet.write_text(status, encoding="utf-8")
                    result = cli(ENGINE / "validate_run.py", self.run)
                    self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
                    self.assertIn("Approved packet present", result.stdout)


class GoalCommentTests(unittest.TestCase):
    def test_all_comment_tails_apply_placeholder_rejection(self):
        guard = load_script("goal_guard.py")
        for prefix in ("", "<!-- explanation --> ", "<!-- explanation\n--> ",
                       "<!-- first\n--> <!-- second --> "):
            for placeholder in ("TBD", "`TBD`", "replace this line with a goal",
                                "TODO: write the one-sentence canonical goal", "one sentence"):
                with self.subTest(prefix=prefix, placeholder=placeholder):
                    with self.assertRaises(guard.GoalAnchorMissing):
                        guard.canonical_goal("## Canonical Goal\n" + prefix + placeholder + "\n")
            self.assertEqual(guard.canonical_goal("## Canonical Goal\n" + prefix +
                                                 "Build a local widget tracker.\n"),
                             "Build a local widget tracker.")

    def test_real_hash_cli_does_not_emit_a_placeholder_hash(self):
        with tempfile.TemporaryDirectory() as td:
            goal = Path(td) / "goal.md"
            goal.write_text("## Canonical Goal\n<!--\n--> TBD\n", encoding="utf-8")
            result = cli(ENGINE / "goal_guard.py", "--hash", goal)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertEqual(result.stdout, "")
            self.assertIn("UNANCHORED", result.stderr)
            sentence = "Build a local widget tracker."
            goal.write_text("## Canonical Goal\n<!--\n--> " + sentence + "\n", encoding="utf-8")
            result = cli(ENGINE / "goal_guard.py", "--hash", goal)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), hashlib.sha256(sentence.encode()).hexdigest()[:12])


class AdoptionTruthTests(unittest.TestCase):
    def test_adoption_provides_pending_criteria_and_fails_the_dod_gate(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            project = install(base)
            source = base / "widget"
            source.mkdir()
            readme = b"# Widget Tracker\n\nNo implementation exists in this fixture.\n"
            (source / "README.md").write_bytes(readme)
            for slug, custom in (("fresh", False), ("custom", True)):
                if custom:
                    (project / "blackboard/00-project-goal.md").write_text(
                        "# Goal\n## Canonical Goal\nTBD\n\n## Success Criteria\n"
                        "- [ ] Preserve custom acceptance requirement\n\n## Definition of Done\n"
                        "- [x] Historical work already recorded\n", encoding="utf-8")
                result = cli(project / "memory/adopt_project.py", source, "--slug", slug)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                run = project / "runs" / slug
                text = (run / "00-project-goal.md").read_text(encoding="utf-8")
                for section in ("Success Criteria", "Definition of Done"):
                    body = text.split("## " + section, 1)[1].split("\n## ", 1)[0]
                    self.assertIn("- [ ] Verify adopted project", body)
                    self.assertIn("- [ ] Complete /deliver and record the actual closure validation result", body)
                    self.assertNotIn("- [ ] TBD", body)
                    self.assertNotIn("VALIDATE: PASS", body)
                if custom:
                    self.assertIn("Preserve custom acceptance requirement", text)
                    self.assertIn("- [x] Historical work already recorded", text)
                else:
                    self.assertNotIn("- [x]", text)
                result = cli(project / "memory/validate_run.py", run)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("[ ] DoD has no remaining TBD", result.stdout)
                self.assertEqual((source / "README.md").read_bytes(), readme)


class CanonicalSchemaTests(unittest.TestCase):
    def test_each_exact_schema_name_is_filled_despite_occupied_prefix(self):
        for tier in ("solo", "mini", "full"):
            with tempfile.TemporaryDirectory() as td:
                project = install(Path(td))
                required = SOLO_EXACT + (LARGER_EXACT if tier != "solo" else ())
                for name in required:
                    with self.subTest(tier=tier, name=name):
                        original = project / "blackboard" / name
                        archive = original.with_name(name[:-3] + ".archived.md")
                        before = original.read_bytes()
                        original.rename(archive)
                        result = cli(project / "memory/new_run.py", "run-" + name[:2], "--tier", tier)
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                        run = project / "runs" / ("run-" + name[:2])
                        self.assertTrue((run / name).is_file(), name)
                        self.assertEqual((run / name).read_bytes(),
                                         (project / "blackboard-template" / name).read_bytes())
                        self.assertEqual((run / archive.name).read_bytes(), before)
                        archive.rename(original)

    def test_unavailable_exact_names_refuse_without_creating_a_run(self):
        with tempfile.TemporaryDirectory() as td:
            project = install(Path(td))
            for name in SOLO_EXACT + LARGER_EXACT:
                with self.subTest(name=name):
                    renamed = []
                    for directory in ("blackboard", "blackboard-template"):
                        original = project / directory / name
                        archive = original.with_name(name[:-3] + ".archived.md")
                        original.rename(archive)
                        renamed.append((archive, original))
                    result = cli(project / "memory/new_run.py", "absent-" + name[:2], "--tier", "full")
                    self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                    self.assertIn(name, result.stderr)
                    self.assertFalse((project / "runs" / ("absent-" + name[:2])).exists())
                    for archive, original in renamed:
                        archive.rename(original)

    def test_live_exact_names_win_and_starter_does_not_require_optional_roster(self):
        with tempfile.TemporaryDirectory() as td:
            project = install(Path(td), full=False)
            cost = project / "blackboard/09-cost-estimate.md"
            cost.write_text("# Live cost notes\nPreserve this content.\n", encoding="utf-8")
            result = cli(project / "memory/new_run.py", "starter", "--tier", "solo")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            run = project / "runs/starter"
            self.assertEqual((run / cost.name).read_bytes(), cost.read_bytes())
            self.assertTrue((run / "21-evolution-records.md").is_file())
            self.assertFalse((run / "21-agent-roster.md").exists())
            self.assertTrue((run / "PACKETS.md").is_file())
            self.assertFalse((run / "03-decisions.md").exists())

    def test_core_and_full_engine_scaffold_sources_remain_identical(self):
        self.assertEqual((ROOT / "memory/new_run.py").read_bytes(), (ENGINE / "new_run.py").read_bytes())


if __name__ == "__main__":
    unittest.main()
