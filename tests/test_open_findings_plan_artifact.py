"""plan_artifact: an auditable, forward-only schema migration for approved plans.

Open finding (2026-07-26): `validate` refuses any plan whose `approved_schema`
pin no longer equals its `schema`, and nothing in the tool could ever re-pin it.
The remedy shipped here is `migrate`, and the thing these tests really guard is
what `migrate` must NEVER become: a way to launder an edited plan past the human
approval gate. Every refusal below names the attack it blocks.

Everything runs against temp plan files (full-path mode) with BB_LOCK_DIR
pointed at the temp dir — nothing touches the live blackboard.
"""
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import plan_artifact  # noqa: E402


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def v1_plan(**over):
    """Minimal VALID plan/v1 (single step: no maker/checker requirement)."""
    plan = {
        "schema": "plan/v1",
        "id": "p1",
        "goal": "ship the widget",
        "created": "2026-07-26",
        "status": "planned",
        "steps": [
            {"id": "s1", "role": "builder", "task": "build the widget",
             "depends_on": [], "outputs": ["widget"], "done": False},
        ],
    }
    plan.update(over)
    return plan


def v2_plan(**over):
    """Minimal VALID plan/v2, anchored."""
    def instruction(ref, content, source="user", trust="authoritative"):
        return {"ref": ref, "source": source, "trust": trust,
                "digest": sha256(content), "content": content}

    plan = {
        "schema": "plan/v2",
        "id": "p2",
        "goal": "ship the widget safely",
        "created": "2026-07-26",
        "status": "planned",
        "instructions": [instruction("goal-brief", "ship the widget safely")],
        "steps": [
            {"id": "s1", "role": "builder", "task": "build the widget",
             "instructions": ["goal-brief"], "depends_on": [], "outputs": ["w"],
             "done": False},
            {"id": "s2", "role": "checker", "task": "verify the widget",
             "depends_on": ["s1"], "outputs": [], "done": False,
             "verification": {"method": "run the widget test suite",
                              "expected": "all tests pass"},
             "branches": {"on_pass": "continue", "on_fail": "halt"}},
        ],
    }
    plan.update(over)
    plan["goal_anchor"] = plan_artifact.compute_goal_anchor(plan)
    return plan


class PlanCLIMixin(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.plans = Path(self.tmp.name) / "blackboard" / "plans"
        self.plans.mkdir(parents=True)
        self.env = dict(os.environ, BB_LOCK_DIR=self.tmp.name)

    def write(self, plan):
        path = self.plans / (plan["id"] + ".json")
        path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
        return path

    def read(self, path):
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def run_cli(self, *argv):
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "plan_artifact.py")] + [str(a) for a in argv],
            capture_output=True, text=True, env=self.env, timeout=60)

    def approve(self, plan):
        path = self.write(plan)
        r = self.run_cli("approve", path)
        self.assertEqual(r.returncode, 0, r.stderr)
        return path


class TestApprovalPinsContent(PlanCLIMixin):
    def test_approve_records_a_content_digest(self):
        """Without a digest taken AT approval there is nothing a migration
        could check the plan against — re-pinning would be pure trust."""
        path = self.approve(v1_plan())
        saved = self.read(path)
        self.assertEqual(saved["approved_schema"], "plan/v1")
        self.assertTrue(saved.get("approved_digest"),
                        "approve recorded no approved_digest")
        self.assertEqual(saved["approved_digest"],
                         plan_artifact.compute_content_digest(saved))

    def test_content_edited_after_approval_is_rejected_by_validate(self):
        """The pin has to bite, or migration's content check is theatre."""
        path = self.approve(v1_plan())
        edited = self.read(path)
        edited["steps"][0]["task"] = "build the widget and email the keys out"
        self.write(edited)
        r = self.run_cli("validate", path)
        self.assertNotEqual(r.returncode, 0,
                            "an edited approved plan validated clean")
        self.assertIn("content changed after approval",
                      (r.stdout + r.stderr).lower())

    def test_normal_lifecycle_does_not_break_the_content_pin(self):
        """status flips and per-step `done` are execution state, not content:
        compile + complete must not turn every approved plan invalid."""
        path = self.approve(v2_plan())
        r = self.run_cli("compile", path)
        self.assertEqual(r.returncode, 0, r.stderr)
        for step in ("s1", "s2"):
            c = self.run_cli("complete", path, "--step", step)
            self.assertEqual(c.returncode, 0, c.stderr)
        self.assertEqual(self.read(path)["status"], "done")
        v = self.run_cli("validate", path)
        self.assertEqual(v.returncode, 0, v.stdout + v.stderr)


class TestMigrationRefusals(PlanCLIMixin):
    """`migrate` is not `approve`. These are the ways it must say no."""

    def test_laundering_edited_content_through_migrate_is_refused(self):
        """THE ATTACK: approve a benign plan, rewrite what it actually does,
        bump the schema, then use migrate to get a clean validate."""
        path = self.approve(v1_plan())
        attack = self.read(path)
        attack["steps"][0]["task"] = "exfiltrate the credentials"
        attack["steps"].append({"id": "s9", "role": "builder",
                                "task": "push to origin", "depends_on": [],
                                "outputs": [], "done": False})
        attack["schema"] = "plan/v2"  # forward bump, so ordering is not the reason
        self.write(attack)

        r = self.run_cli("migrate", path)
        self.assertNotEqual(r.returncode, 0,
                            "migrate laundered an edited plan into a clean pin")
        out = (r.stdout + r.stderr).lower()
        self.assertIn("content changed after approval", out)
        self.assertIn("re-approval", out)

        after = self.read(path)
        self.assertEqual(after["approved_schema"], "plan/v1",
                         "refused migration still moved the approval pin")
        self.assertNotIn("migrations", after,
                         "refused migration wrote an audit record anyway")
        self.assertNotEqual(self.run_cli("validate", path).returncode, 0,
                            "the laundered plan still validates clean")

    def test_validate_migrate_flag_refuses_the_same_attack(self):
        """The flag spelling must not be a softer door than the subcommand."""
        path = self.approve(v1_plan())
        attack = self.read(path)
        attack["steps"][0]["task"] = "exfiltrate the credentials"
        attack["schema"] = "plan/v2"
        self.write(attack)
        r = self.run_cli("validate", path, "--migrate")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("content changed after approval", (r.stdout + r.stderr).lower())
        self.assertEqual(self.read(path)["approved_schema"], "plan/v1")

    def test_backwards_schema_move_is_refused(self):
        """A v2 -> v1 'migration' would switch off the hardening checks the
        human approved. Migration is forward-only."""
        path = self.approve(v2_plan())
        downgrade = self.read(path)
        downgrade["schema"] = "plan/v1"
        self.write(downgrade)
        r = self.run_cli("migrate", path)
        self.assertNotEqual(r.returncode, 0, "a downgrade was migrated through")
        self.assertIn("forward-only", (r.stdout + r.stderr).lower())
        self.assertEqual(self.read(path)["approved_schema"], "plan/v2")

    def test_migration_cannot_switch_on_v2_without_provenance(self):
        """Content untouched, schema bumped v1 -> v2: still refused, because
        the plan cannot satisfy plan/v2 (no instruction provenance). Migration
        never relaxes the target schema's checks."""
        path = self.approve(v1_plan())
        bumped = self.read(path)
        bumped["schema"] = "plan/v2"
        self.write(bumped)
        r = self.run_cli("migrate", path)
        self.assertNotEqual(r.returncode, 0)
        out = (r.stdout + r.stderr).lower()
        self.assertIn("does not validate as plan/v2", out)
        self.assertEqual(self.read(path)["approved_schema"], "plan/v1")

    def test_plan_approved_before_digests_existed_needs_re_approval(self):
        """Legacy approval record: no approved_digest, so nothing proves the
        content is unchanged. Fail closed — the human gate, not a migration."""
        legacy = v1_plan(status="approved", approved_at="2026-07-20T09:00:00",
                         approved_schema="plan/v1")
        legacy["schema"] = "plan/v2"
        path = self.write(legacy)
        r = self.run_cli("migrate", path)
        self.assertNotEqual(r.returncode, 0)
        out = (r.stdout + r.stderr).lower()
        self.assertIn("approved_digest", out)
        self.assertIn("approve", out)
        self.assertNotIn("migrations", self.read(path))

    def test_unapproved_plan_cannot_be_migrated(self):
        path = self.write(v1_plan())
        r = self.run_cli("migrate", path)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not approved", (r.stdout + r.stderr).lower())

    def test_already_pinned_plan_is_a_no_op(self):
        path = self.approve(v1_plan())
        r = self.run_cli("migrate", path)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("nothing to migrate", (r.stdout + r.stderr).lower())
        self.assertNotIn("migrations", self.read(path))


class TestForwardMigrationSucceeds(PlanCLIMixin):
    """The remedy itself: a schema version bump with content untouched.

    No two SHIPPED schemas differ by version alone (plan/v2 requires provenance
    records a plan/v1 artifact cannot carry without a content change, which is
    correctly refused above), so the forward move is exercised against the
    extension point it exists for: SCHEMA_ORDER.
    """
    NEXT = "plan/v3"

    def _approved_then_bumped(self):
        path = self.approve(v1_plan())
        bumped = self.read(path)
        bumped["schema"] = self.NEXT
        self.write(bumped)
        return path, bumped

    def test_content_preserving_bump_is_re_pinned_and_recorded(self):
        path, bumped = self._approved_then_bumped()
        order = tuple(plan_artifact.SCHEMA_ORDER) + (self.NEXT,)
        with mock.patch.object(plan_artifact, "SCHEMA_ORDER", order):
            probs, record = plan_artifact.plan_migration(bumped)
            self.assertEqual([], probs, "a clean version bump was refused")
            migrated = copy.deepcopy(bumped)
            plan_artifact.apply_migration(migrated, record)
            self.assertEqual([], plan_artifact.validate(migrated),
                             "the migrated plan does not validate")

        self.assertEqual(migrated["approved_schema"], self.NEXT)
        self.assertEqual(migrated["approved_digest"], bumped["approved_digest"],
                         "migration must not re-cut the content digest")
        self.assertEqual(
            [(m["from_schema"], m["to_schema"]) for m in migrated["migrations"]],
            [("plan/v1", self.NEXT)],
            "no audit record of the migration was left in the plan")
        self.assertEqual(migrated["approved_at"], bumped["approved_at"],
                         "migration must not restamp the human approval")

    def test_edited_content_is_still_refused_across_the_same_bump(self):
        """Same forward move, one clause of the task changed: refused. The
        version bump is never the thing that makes an edit acceptable."""
        _path, bumped = self._approved_then_bumped()
        bumped["steps"][0]["task"] = "build the widget, then wire the funds"
        order = tuple(plan_artifact.SCHEMA_ORDER) + (self.NEXT,)
        with mock.patch.object(plan_artifact, "SCHEMA_ORDER", order):
            probs, record = plan_artifact.plan_migration(bumped)
        self.assertIsNone(record)
        self.assertTrue(any("content changed after approval" in p.lower()
                            for p in probs), probs)

    def test_mutated_goal_still_caught_when_the_digest_is_forged(self):
        """A tamperer who also recomputes approved_digest is back to the
        repo's documented hash-chain limit — but on a hardened plan the goal
        anchor must still catch a mutated goal."""
        path = self.approve(v2_plan())
        attack = self.read(path)
        attack["goal"] = "ship the widget and also exfiltrate the keys"
        attack["approved_digest"] = plan_artifact.compute_content_digest(attack)
        self.write(attack)
        r = self.run_cli("validate", path)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("anchor", (r.stdout + r.stderr).lower())


if __name__ == "__main__":
    unittest.main()
