#!/usr/bin/env python3
"""Regression tests for the 2026-07-25 line-comb audit fixes.

Every test here encodes a defect that shipped green: the suite passed while the
behaviour was wrong, because nothing asserted the behaviour. Each one fails if
its fix is reverted.

Grouped by the root cause the comb kept finding:

  * A gate that matches VOCABULARY instead of reading a FIELD
    (validate_run tier lock / manifest, harvest reject rows, score_rubric's
    first-match User Need Gate).
  * A writer that does not VERIFY ITS OWN WRITE (adopt_project's dead regex).
  * A second COPY of a security list that quietly drifted (osvec_adapter vs
    brain.py secret patterns).
  * An input trusted because it is "ours" (workflow names, plan schema).
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE_MEMORY = ROOT / "addons" / "full-engine" / "memory"
BRAIN_DIR = ROOT / "addons" / "full-engine" / "brain"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class ValidatorReadsFieldsNotProse(unittest.TestCase):
    """Run-closure gates must read fields, not scan for words."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ENGINE_MEMORY))
        cls.v = load("vr_fix", ENGINE_MEMORY / "validate_run.py")

    def test_tier_lock_rejects_prose_that_merely_contains_the_words(self):
        """'locked' in low and 'tier' in low passed on unrelated sentences."""
        self.assertFalse(
            self.v._tier_locked("The door is locked.\nWe shipped the beta tier."),
            "unrelated prose satisfied the tier-lock gate",
        )

    def test_tier_lock_reads_the_locked_field(self):
        self.assertTrue(self.v._tier_locked("Chosen tier: solo\nLocked: yes"))
        self.assertTrue(self.v._tier_locked("**Locked**: yes"))
        self.assertFalse(self.v._tier_locked("Locked: no"))
        self.assertFalse(self.v._tier_locked("Chosen tier: solo"))

    def test_manifest_gate_rejects_a_denial_of_a_manifest(self):
        """"No artifact manifest was produced" used to CLOSE the run."""
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "14-artifact-manifest.md").write_text(
                "# Artifact Manifest\n\nNo artifact manifest was produced.\n",
                encoding="utf-8",
            )
            self.assertFalse(
                self.v._has_manifest(tmp),
                "a report DENYING a manifest satisfied the manifest gate",
            )

    def test_manifest_gate_requires_entries_not_just_the_word(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "14-artifact-manifest.md"
            p.write_text("# Artifact Manifest\n\n(manifest pending)\n", encoding="utf-8")
            self.assertFalse(self.v._has_manifest(tmp), "empty manifest passed")
            p.write_text(
                "# Artifact Manifest\n\n- site/index.html — shipped\n", encoding="utf-8"
            )
            self.assertTrue(self.v._has_manifest(tmp), "real manifest was rejected")


class HarvestDropsRowsNotWords(unittest.TestCase):
    """`\\breject|private[- ]only\\b` matched any word starting with 'reject'."""

    @classmethod
    def setUpClass(cls):
        cls.h = load("harvest_fix", ROOT / "scripts" / "harvest.py")

    def test_a_lesson_that_mentions_rejecting_is_kept(self):
        for text in (
            "Evaluator must reject on missing evidence",
            "The plan was rejected by the board, so we revised it",
            "Rejection criteria belong in the rubric",
        ):
            with self.subTest(text=text[:40]):
                self.assertIsNone(
                    self.h.REJECT_ROW.match(text),
                    f"a real lesson was silently dropped: {text!r}",
                )

    def test_a_status_cell_marking_rejection_is_dropped(self):
        for cell in ("Rejected", "rejected", "private-only", "do-not-harvest"):
            with self.subTest(cell=cell):
                self.assertIsNotNone(self.h.REJECT_ROW.match(cell))

    def test_dropped_rows_are_recorded_for_reporting(self):
        """Silent filtering is why the over-broad pattern went unnoticed."""
        self.assertTrue(
            hasattr(self.h, "DROPPED"),
            "harvest no longer tracks what it filtered out",
        )


class SecretPatternsStayInSync(unittest.TestCase):
    """Two doors into one store must use the same lock."""

    def _patterns_from_source(self, path: Path) -> list:
        src = path.read_text(encoding="utf-8")
        block = src.split("_SECRET_PATTERNS = [", 1)[1].split("\n]", 1)[0]
        return re.findall(r'^\s*r"(.*?)",\s*(?:#.*)?$', block, re.M)

    def test_osvec_patterns_match_brain_patterns_exactly(self):
        brain = load("brain_parity", BRAIN_DIR / "brain.py")
        want = [p.pattern for p in brain.SECRET_PATTERNS]
        got = self._patterns_from_source(ENGINE_MEMORY / "osvec_adapter.py")
        self.assertEqual(
            got,
            want,
            "osvec_adapter's secret list drifted from brain.py's -- vector "
            "memory would accept keys the brain refuses",
        )

    def test_osvec_detects_modern_key_formats(self):
        osvec = load("osvec_fix", ENGINE_MEMORY / "osvec_adapter.py")
        for name, secret in {
            "anthropic": "sk-ant-api03-" + "A" * 40,
            "openai_proj": "sk-proj-" + "B" * 40,
            "stripe": "sk_live_" + "C" * 24,
            "figma": "figd_" + "E" * 30,
            "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc123",
            "pg_dsn": "postgres://admin:hunter2@db.example.com:5432/prod",
        }.items():
            with self.subTest(kind=name):
                self.assertIsNotNone(
                    osvec.looks_like_secret("my key is " + secret),
                    f"{name} was NOT detected by vector memory",
                )

    def test_osvec_does_not_false_positive_on_ordinary_text(self):
        osvec = load("osvec_fp", ENGINE_MEMORY / "osvec_adapter.py")
        for text in (
            "risk-001-d885ad348d19 was closed",
            "The sk- prefix identifies OpenAI keys; never paste one here.",
            "Deployed revision teas-prep-00003-czf to Cloud Run.",
            "AC power draw dropped after the fix.",
        ):
            with self.subTest(text=text[:40]):
                self.assertIsNone(
                    osvec.looks_like_secret(text), f"false positive on {text!r}"
                )

    def test_osvec_scans_tags_and_source_not_only_text(self):
        """The exact hole brain.py had: a secret in a non-body field."""
        osvec = load("osvec_fields", ENGINE_MEMORY / "osvec_adapter.py")
        secret = "sk-ant-api03-" + "A" * 40
        src = (ENGINE_MEMORY / "osvec_adapter.py").read_text(encoding="utf-8")
        body = src.split("def add(", 1)[1].split("\n    def ", 1)[0]
        for field in ("source_file", "memory_id", "tags"):
            with self.subTest(field=field):
                self.assertIn(
                    field, body.split("secret pattern")[0],
                    f"add() does not scan '{field}' for secrets",
                )
        self.assertIsNotNone(osvec.looks_like_secret(secret))


class GeneratorsDistrustTheirOwnInputs(unittest.TestCase):
    def test_workflow_name_cannot_escape_the_repo(self):
        """`name:` frontmatter went straight into a path join."""
        sync = load("sync_fix", ROOT / "scripts" / "sync_runtime_assets.py")
        with tempfile.TemporaryDirectory() as tmp:
            wf = Path(tmp) / "prompts" / "workflows"
            wf.mkdir(parents=True)
            (wf / "evil.md").write_text(
                "---\nname: ../../../../tmp/PWNED\ndescription: probe\n---\nbody\n",
                encoding="utf-8",
            )
            with self.assertRaises(SystemExit) as ctx:
                sync.sync(Path(tmp))
            self.assertIn("unsafe workflow name", str(ctx.exception))

    def test_ordinary_workflow_names_still_load(self):
        sync = load("sync_ok", ROOT / "scripts" / "sync_runtime_assets.py")
        for good in ("kickoff", "ui-review", "new_run", "plan.v2"):
            with self.subTest(name=good):
                self.assertEqual(sync.safe_workflow_name(good, Path("x")), good)

    def test_plan_schema_downgrade_is_refused(self):
        """Rewriting plan/v2 -> plan/v1 disabled every hardening check."""
        plan_mod = load("plan_fix", ROOT / "scripts" / "plan_artifact.py")
        plan = {
            "schema": "plan/v2",
            "id": "P1",
            "created": "2026-07-25",
            "goal": "Ship the thing",
            "status": "planned",
            "steps": [{"id": "s1", "role": "builder", "task": "do it"}],
            "instructions": [
                {"ref": "user-1", "trust": "authoritative", "digest": "abc"}
            ],
        }
        plan["goal_anchor"] = plan_mod.compute_goal_anchor(plan)
        downgraded = dict(plan, schema="plan/v1")
        probs = plan_mod.validate(downgraded)
        self.assertTrue(
            any("schema downgrade" in p for p in probs),
            f"plan/v2 -> plan/v1 downgrade was accepted: {probs}",
        )

    def test_goal_anchor_binds_the_schema(self):
        plan_mod = load("plan_anchor", ROOT / "scripts" / "plan_artifact.py")
        base = {
            "id": "P1",
            "created": "2026-07-25",
            "goal": "Ship the thing",
            "instructions": [],
        }
        self.assertNotEqual(
            plan_mod.compute_goal_anchor(dict(base, schema="plan/v2")),
            plan_mod.compute_goal_anchor(dict(base, schema="plan/v1")),
            "the anchor does not cover `schema`, so a downgrade keeps matching",
        )


class ScaffolderWorksFromAFreshClone(unittest.TestCase):
    def test_new_run_finds_a_template_and_includes_the_goal_roster(self):
        """`blackboard/` is gitignored; a clone has only blackboard-template/.

        And goal_guard compares against `21-agent-roster.md`, which lived only
        in the addon dir and never reached a scaffolded run.
        """
        new_run = load("new_run_fix", ENGINE_MEMORY / "new_run.py")
        self.assertTrue(
            os.path.isdir(new_run.BLACKBOARD),
            f"no blackboard template resolved (looked under {new_run.ROOT})",
        )
        names = new_run.numbered_templates("solo")
        self.assertIn(
            "21-agent-roster.md",
            names,
            "a solo run scaffolds without the roster goal_guard reads",
        )

    def test_installer_does_not_downgrade_build_graph(self):
        """--force copied the older full-engine helper over the canonical one."""
        with tempfile.TemporaryDirectory() as tmp:
            for args in ([], ["--force"]):
                subprocess.run(
                    [sys.executable, str(ROOT / "scripts" / "setup_project_os.py"),
                     "--target", tmp] + args,
                    capture_output=True, text=True, check=False,
                )
            installed = Path(tmp) / "memory" / "build_graph.py"
            self.assertTrue(installed.is_file(), "installer did not deliver build_graph.py")
            self.assertEqual(
                installed.read_text(encoding="utf-8"),
                (ROOT / "memory" / "build_graph.py").read_text(encoding="utf-8"),
                "installer replaced the canonical build_graph.py with the "
                "older full-engine helper",
            )


class AdoptVerifiesItsOwnWrite(unittest.TestCase):
    def test_two_adopted_projects_get_different_goal_hashes(self):
        """The dead stub regex left every adopted run on one placeholder goal,
        so they all hashed identically and goal_guard could never see drift."""
        proc = subprocess.run(
            [sys.executable, str(ENGINE_MEMORY / "adopt_project.py"), "--selftest"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(
            proc.returncode, 0,
            f"adopt_project selftest failed:\n{proc.stdout}\n{proc.stderr}",
        )

    def test_stub_writer_replaces_the_canonical_goal_line(self):
        adopt = load("adopt_fix", ENGINE_MEMORY / "adopt_project.py")
        goal_guard = load("gg_fix", ENGINE_MEMORY / "goal_guard.py")
        template = (
            "# Project Goal\n\n## Canonical Goal\n\n"
            "<!-- ONE sentence. -->\n\n"
            "Replace this line with the one-sentence canonical goal.\n\n"
            "## Summary\n\nDescribe it.\n"
        )
        out = adopt._replace_canonical_goal(template, "Track widgets locally", "README.md")
        self.assertEqual(goal_guard.canonical_goal(out), "Track widgets locally")

    def test_stub_writer_adds_the_anchor_when_it_is_missing(self):
        adopt = load("adopt_anchor", ENGINE_MEMORY / "adopt_project.py")
        goal_guard = load("gg_anchor", ENGINE_MEMORY / "goal_guard.py")
        out = adopt._replace_canonical_goal(
            "# Project Goal\n\n## Summary\n\nSomething.\n", "Reconcile ledgers", "README.md"
        )
        self.assertEqual(goal_guard.canonical_goal(out), "Reconcile ledgers")


class Round2AdversarialBypasses(unittest.TestCase):
    """Every case below defeated the FIRST version of that day's fix.

    An independent skeptic pass broke 6 of 8 fix clusters. These are its
    reproductions, kept so the second version cannot regress to the first.
    """

    def test_tier_lock_ignores_a_rejected_option_block_above_the_real_one(self):
        v = load("vr_r2", ENGINE_MEMORY / "validate_run.py")
        doc = (
            "## Options Considered\n### Option B (rejected)\n"
            "Tier: Full Swarm\nLocked: yes\n\n"
            "## Current Execution Level\nTier: Solo\nLocked: no\n"
        )
        self.assertFalse(
            v._tier_locked(doc),
            "a rejected-option 'Locked: yes' above the real 'Locked: no' "
            "satisfied the gate (first-match-wins)",
        )

    def test_manifest_table_with_only_header_and_alignment_row_is_empty(self):
        v = load("vr_r2b", ENGINE_MEMORY / "validate_run.py")
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "14-artifact-manifest.md").write_text(
                "# Artifact Manifest\n\n| path | what |\n| :--- | ---: |\n",
                encoding="utf-8",
            )
            self.assertFalse(
                v._has_manifest(tmp),
                "a table with a header + alignment separator and NO data rows "
                "counted as a populated manifest",
            )

    def test_manifest_denial_wording_omitted_and_skipped(self):
        v = load("vr_r2c", ENGINE_MEMORY / "validate_run.py")
        for wording in (
            "Artifact manifest: omitted for this run.",
            "Artifact manifest skipped this run to save time.",
        ):
            with tempfile.TemporaryDirectory() as tmp:
                (Path(tmp) / "13-delivery-report.md").write_text(
                    f"## Notes\n{wording}\n- item one\n- item two\n", encoding="utf-8"
                )
                with self.subTest(wording=wording[:30]):
                    self.assertFalse(
                        v._has_manifest(tmp),
                        "an explicit denial still passed because unrelated "
                        "bullets existed later in the file",
                    )

    def test_annotated_rejection_cells_are_still_dropped(self):
        h = load("harvest_r2", ROOT / "scripts" / "harvest.py")
        for cell in ("Rejected (see note)", "REJECTED — superseded", "**Rejected**",
                     "REJECTED ", "private only"):
            with self.subTest(cell=cell):
                self.assertIsNotNone(
                    h.REJECT_ROW.match(cell),
                    f"annotated rejection harvested through as a lesson: {cell!r}",
                )

    def test_bare_modern_credentials_are_detected(self):
        """Pasted with no `KEY=` label — the realistic accidental-leak shape."""
        osvec = load("osvec_r2", ENGINE_MEMORY / "osvec_adapter.py")
        for name, text in {
            "huggingface": "clone with hf_" + "A" * 34 + " as the auth",
            "vercel": "deploy hook uses vercel_" + "b" * 24 + " in the script",
            "linear": "automation calls lin_api_" + "C" * 36 + " directly",
            "notion": "token ntn_" + "d" * 44 + " is in the config",
            "sentry_dsn": "endpoint is https://" + "a" * 32 + "@o123456.ingest.sentry.io/1234567",
            "azure": "AccountKey = " + "Zm9vYmFy" * 6 + "==",
            "slack_webhook": "post to https://hooks.slack.com/services/T00000000/B00000000/" + "X" * 24,
        }.items():
            with self.subTest(kind=name):
                self.assertIsNotNone(
                    osvec.looks_like_secret(text), f"{name} passed the gate"
                )

    def test_plan_downgrade_survives_stripping_both_hardened_fields(self):
        """The bypass: delete goal_anchor AND instructions, then set plan/v1."""
        plan_mod = load("plan_r2", ROOT / "scripts" / "plan_artifact.py")
        approved = {
            "schema": "plan/v2",
            "id": "P1",
            "created": "2026-07-25",
            "goal": "Ship it",
            "status": "approved",
            "approved_at": "2026-07-25T10:00:00",
            "approved_schema": "plan/v2",
            "steps": [{"id": "s1", "role": "builder", "task": "build",
                       "depends_on": [], "outputs": []}],
        }
        attack = dict(approved)
        attack.pop("goal_anchor", None)
        attack.pop("instructions", None)
        attack["schema"] = "plan/v1"
        probs = plan_mod.validate(attack)
        self.assertTrue(
            any("after approval" in p for p in probs),
            f"stripped-and-downgraded plan validated clean: {probs}",
        )

    def test_a_single_user_need_lookalike_does_not_satisfy_the_gate(self):
        """One decoy named 'User Needs Documented' shipped a Pass verdict."""
        src = (Path("/Users/zaidmartinez/Claude/Projects/project-os")
               / "memory" / "score_rubric.py")
        if not src.is_file():
            self.skipTest("private fork not present")
        body = src.read_text(encoding="utf-8")
        self.assertIn(
            'CANONICAL = "user need gate"', body,
            "the gate no longer requires the canonical criterion name",
        )
        self.assertIn(
            "decoys and not ung_matches", body,
            "a single look-alike criterion can still satisfy the gate",
        )

    def test_sync_refuses_to_write_through_a_symlink(self):
        """A legit name + a planted symlink still redirected the write."""
        sync = load("sync_r2", ROOT / "scripts" / "sync_runtime_assets.py")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "prompts" / "workflows").mkdir(parents=True)
            (root / "prompts" / "workflows" / "normal.md").write_text(
                "---\nname: normal-workflow\ndescription: legit\n---\nbody\n",
                encoding="utf-8",
            )
            cmds = root / "addons" / "full-engine" / "staged" / "commands"
            cmds.mkdir(parents=True)
            evil = Path(tmp) / "evil_target"
            evil.write_text("", encoding="utf-8")
            (cmds / "normal-workflow.md").symlink_to(evil)
            with self.assertRaises(SystemExit) as ctx:
                sync.sync(root)
            self.assertIn("symlink", str(ctx.exception).lower())
            self.assertEqual(evil.read_text(encoding="utf-8"), "",
                             "content was written through the symlink")

    def test_goal_guard_skips_whole_multiline_comment_blocks(self):
        """The template ships a 4-line HTML comment.

        canonical_goal() skipped only lines STARTING with '<!--', so line 2 of
        that comment was returned as the goal: every project hashed the same
        sentence and editing the real goal did not change the hash at all.
        Caught only by running the documented install flow end-to-end -- the
        unit selftest used a one-line comment and passed throughout.
        """
        gg = load("gg_comment", ENGINE_MEMORY / "goal_guard.py")
        text = (
            "# Project Goal\n\n## Canonical Goal\n\n"
            "<!-- ONE sentence. This exact line is hashed by goal_guard.py and\n"
            "     the hash is recorded in 21-agent-roster.md, so wave-boundary\n"
            "     drift checks can tell whether the goal changed. -->\n\n"
            "Build a credit-card tracker that flags overspend.\n\n"
            "## Summary\n\nDescribe it.\n"
        )
        self.assertEqual(
            gg.canonical_goal(text),
            "Build a credit-card tracker that flags overspend.",
            "a line from inside the multi-line comment was read as the goal",
        )

    def test_goal_guard_refuses_the_untouched_template_placeholder(self):
        """A placeholder hashes identically for every run -- same blindness."""
        gg = load("gg_placeholder", ENGINE_MEMORY / "goal_guard.py")
        for placeholder in (
            "Replace this line with the one-sentence canonical goal.",
            "TODO: write the one-sentence canonical goal.",
            "TBD",
        ):
            text = "# Project Goal\n\n## Canonical Goal\n\n%s\n" % placeholder
            with self.subTest(placeholder=placeholder[:30]):
                with self.assertRaises(gg.GoalAnchorMissing):
                    gg.canonical_goal(text)

    def test_shipped_template_produces_a_usable_goal_after_editing(self):
        """Guard the REAL template, not a hand-written fixture."""
        gg = load("gg_tmpl", ENGINE_MEMORY / "goal_guard.py")
        template = (ROOT / "blackboard-template" / "00-project-goal.md").read_text(
            encoding="utf-8"
        )
        with self.assertRaises(gg.GoalAnchorMissing):
            gg.canonical_goal(template)  # untouched template must refuse
        edited = template.replace(
            "Replace this line with the one-sentence canonical goal.",
            "Build a credit-card tracker that flags overspend.",
        )
        self.assertEqual(
            gg.canonical_goal(edited),
            "Build a credit-card tracker that flags overspend.",
        )
        other = template.replace(
            "Replace this line with the one-sentence canonical goal.",
            "Build a budgeting app for freelancers.",
        )
        self.assertNotEqual(
            gg.goal_hash(gg.canonical_goal(edited)),
            gg.goal_hash(gg.canonical_goal(other)),
            "two different goals hash identically -- drift is undetectable",
        )

    def test_selftests_do_not_touch_the_live_runs_index(self):
        """Both selftests scaffolded into the REAL runs/ and rewrote INDEX.md.

        That made the suite intermittently RED under concurrency (two runs
        collided on the same slug) and left debris in a user's live runs dir.
        Caught only by running suites in parallel.
        """
        index = ROOT / "runs" / "INDEX.md"
        before = index.read_text(encoding="utf-8") if index.is_file() else None
        for script in ("new_run.py", "adopt_project.py"):
            proc = subprocess.run(
                [sys.executable, str(ENGINE_MEMORY / script), "--selftest"],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(proc.returncode, 0,
                             f"{script} selftest failed: {proc.stderr[-400:]}")
        if before is not None:
            self.assertEqual(
                index.read_text(encoding="utf-8"), before,
                "a selftest rewrote the live runs/INDEX.md",
            )
        stray = [p.name for p in (ROOT / "runs").glob("*selftest*")] if (ROOT / "runs").is_dir() else []
        self.assertEqual(stray, [], f"selftest left debris in live runs/: {stray}")

    def test_index_reports_run_dirs_it_could_not_index(self):
        """A run dir with no goal file used to vanish from INDEX silently."""
        new_run = load("nr_skip", ENGINE_MEMORY / "new_run.py")
        with new_run.isolated_root():
            os.makedirs(os.path.join(new_run.RUNS, "ghost-run"), exist_ok=True)
            skipped = new_run.regenerate_index()
            body = Path(new_run.INDEX).read_text(encoding="utf-8")
        self.assertIn("ghost-run", skipped)
        self.assertIn("ghost-run", body,
                      "an unindexable run directory is invisible in INDEX.md")

    def test_index_goal_skips_multiline_comment_blocks(self):
        """Same comment bug as goal_guard, in the INDEX summariser."""
        new_run = load("nr_goal", ENGINE_MEMORY / "new_run.py")
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "00-project-goal.md"
            p.write_text(
                "# Project Goal\n\n## Canonical Goal\n\n"
                "<!-- ONE sentence. This exact line is hashed by goal_guard.py\n"
                "     and recorded in 21-agent-roster.md. -->\n\n"
                "Build a credit-card tracker that flags overspend.\n",
                encoding="utf-8",
            )
            goal, _tier = new_run._read_goal(str(p))
        self.assertEqual(goal, "Build a credit-card tracker that flags overspend.")

    def test_adopt_verifies_the_file_on_disk_not_its_own_string(self):
        src = (ENGINE_MEMORY / "adopt_project.py").read_text(encoding="utf-8")
        verify_block = src.split("Verify the write", 1)[1].split("def ", 1)[0]
        self.assertIn(
            "fh.read()", verify_block,
            "the write-verification still re-parses the in-memory string, so a "
            "corrupted disk write would report success",
        )


if __name__ == "__main__":
    unittest.main()
