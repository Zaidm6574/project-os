"""Public audit repairs: approval recovery, packet identity, and publication fences.

All state is synthetic. Pipe handshakes select the exact writer boundary; aging
the fixture lease's mtime models expiry without scheduler-dependent sleeps.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import selectors
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import bb_lock
import plan_artifact


def fixture_plan(hardened=False):
    plan = {
        "schema": "plan/v1", "id": "fixture", "goal": "build a fixture",
        "status": "planned", "steps": [
            {"id": "build", "role": "builder", "task": "write a fixture",
             "depends_on": [], "outputs": [], "done": False},
            {"id": "check", "role": "checker", "task": "check the fixture",
             "depends_on": ["build"], "outputs": [], "done": False,
             "verification": {"method": "compare the fixture to the specification",
                              "expected": "the fixture matches the specification"}},
        ],
    }
    if hardened:
        content = "build a fixture"
        plan["schema"] = "plan/v2"
        plan["instructions"] = [{
            "ref": "brief", "source": "user", "trust": "authoritative",
            "content": content, "digest": hashlib.sha256(content.encode()).hexdigest(),
        }]
        plan["steps"][0]["instructions"] = ["brief"]
        plan["steps"][1]["branches"] = {"on_pass": "continue", "on_fail": "halt"}
        plan["goal_anchor"] = plan_artifact.compute_goal_anchor(plan)
    return plan


class PlanWorkflowRepairs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.scripts = self.root / "scripts"
        self.scripts.mkdir()
        for name in ("plan_artifact.py", "bb_lock.py"):
            shutil.copy2(SCRIPTS / name, self.scripts / name)
        self.env = dict(os.environ, BB_LOCK_DIR=str(self.root / "locks"))
        self.path = self.root / "blackboard" / "plans" / "fixture.json"
        self.write(fixture_plan())

    def write(self, plan, path=None):
        path = self.path if path is None else path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(plan), encoding="utf-8")

    def read(self, path=None):
        return json.loads((self.path if path is None else path).read_text(encoding="utf-8"))

    def cli(self, *args):
        return subprocess.run(
            [sys.executable, str(self.scripts / "plan_artifact.py"), *map(str, args)],
            env=self.env, cwd=self.root, capture_output=True, text=True, timeout=15)

    def ok(self, *args):
        result = self.cli(*args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def test_reapproval_accepts_valid_edit_but_other_gates_keep_rejecting_it(self):
        for hardened in (False, True):
            with self.subTest(hardened=hardened):
                self.write(fixture_plan(hardened))
                self.ok("approve", self.path)
                first = self.read()
                edited = copy.deepcopy(first)
                edited["steps"][0]["task"] = "write the revised fixture"
                self.write(edited)
                before = self.path.read_bytes()
                for operation in (("validate",), ("compile",),
                                  ("compile", "--force")):
                    result = self.cli(operation[0], self.path, *operation[1:])
                    self.assertNotEqual(result.returncode, 0, operation)
                    self.assertIn("content changed after approval", result.stdout + result.stderr)
                    self.assertEqual(self.path.read_bytes(), before)
                # Same-schema migrate is an intentional no-op; it must not
                # refresh the digest or turn the edited content valid.
                self.ok("migrate", self.path)
                self.assertEqual(self.path.read_bytes(), before)
                self.assertNotEqual(self.cli("validate", self.path).returncode, 0)
                self.ok("approve", self.path)
                approved = self.read()
                self.assertNotEqual(first["approved_digest"], approved["approved_digest"])
                self.assertEqual(approved["approved_digest"],
                                 plan_artifact.compute_content_digest(approved))
                self.assertEqual(approved["approved_schema"], edited["schema"])
                self.assertEqual(approved["steps"], edited["steps"])
                self.ok("validate", self.path)
                self.ok("compile", self.path)

    def test_reapproval_keeps_structural_provenance_and_anchor_validation(self):
        self.write(fixture_plan(True))
        self.ok("approve", self.path)
        baseline = self.read()
        changes = (
            ("verification", lambda p: p["steps"][1]["verification"].update(method="todo")),
            ("coverage", lambda p: p["steps"][1].update(depends_on=[])),
            ("digest", lambda p: p["instructions"][0].update(content="changed source")),
            ("anchor", lambda p: p.update(goal="changed goal")),
            ("branch", lambda p: p["steps"][1]["branches"].update(on_fail="continue")),
            ("downgrade", lambda p: p.update(schema="plan/v1")),
        )
        for label, change in changes:
            with self.subTest(case=label):
                candidate = copy.deepcopy(baseline)
                change(candidate)
                self.write(candidate)
                before = self.path.read_bytes()
                result = self.cli("approve", self.path)
                self.assertNotEqual(result.returncode, 0, label)
                self.assertIn("cannot approve, INVALID", result.stderr)
                self.assertEqual(self.path.read_bytes(), before,
                                 "failed approval must preserve the old pins and content")

    def test_missing_schema_pin_can_be_reestablished_only_by_approval(self):
        self.ok("approve", self.path)
        edited = self.read()
        edited.pop("approved_schema")
        self.write(edited)
        self.assertNotEqual(self.cli("validate", self.path).returncode, 0)
        self.ok("approve", self.path)
        self.assertEqual(self.read()["approved_schema"], "plan/v1")
        self.ok("validate", self.path)

    def test_complete_generated_command_targets_original_explicit_plan(self):
        # Exercise actual shell quoting, including apostrophes, spaces, $ and ;.
        # The directory text is a fixed synthetic fixture, never user input.
        external = self.root / "space ' quote $literal; text" / "blackboard" / "plans" / "fixture.json"
        self.write(fixture_plan(), external)
        # A same-id default plan must remain untouched by external completion.
        default_before = self.path.read_bytes()
        relative = os.path.relpath(external, self.root)
        self.ok("approve", relative)
        self.ok("compile", relative)
        packet = external.parent.parent / "packets" / "fixture-build.md"
        line = next(line for line in packet.read_text().splitlines()
                    if line.startswith("On completion run: "))
        command = line[len("On completion run: "):]
        self.assertEqual(shlex.split(command), [
            "python3", "scripts/plan_artifact.py", "complete", str(external),
            "--step", "build"])
        result = subprocess.run(["/bin/sh", "-c", command], cwd=self.root,
                                env=self.env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.read(external)["steps"][0]["done"])
        self.assertEqual(self.path.read_bytes(), default_before)
        self.assertFalse(self.read(external)["steps"][1]["done"])

    def test_slug_completion_still_works(self):
        self.ok("approve", "fixture")
        self.ok("compile", "fixture")
        packet = self.path.parent.parent / "packets" / "fixture-build.md"
        command = next(line[len("On completion run: "):]
                       for line in packet.read_text().splitlines()
                       if line.startswith("On completion run: "))
        self.assertEqual(shlex.split(command)[3], "fixture")
        result = subprocess.run(["/bin/sh", "-c", command], cwd=self.root,
                                env=self.env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.read()["steps"][0]["done"])


# Every subprocess loads the actual checked-out runtime. Only the scheduling
# seam is instrumented. Parent/child stdin/stdout coordinate exactly once.
WRITER = r'''
import json, os, sys
sys.path.insert(0, sys.argv[1])
import bb_lock, plan_artifact as plan
target, mode = sys.argv[2:]
def pause():
    print("READY", flush=True)
    if sys.stdin.readline().strip() != "continue":
        raise RuntimeError("coordination pipe closed")
if mode == "replace":
    original = plan.os.replace
    def replacement(src, dst):
        if os.path.abspath(dst) == target:
            pause()
        return original(src, dst)
    plan.os.replace = replacement
elif mode == "renew":
    original = bb_lock.renew
    def renewal(path, token):
        result = original(path, token)
        if not result:
            raise RuntimeError("precondition: initial renewal failed")
        pause()
        return result
    bb_lock.renew = renewal
elif mode == "append":
    import builtins
    original = builtins.open
    def opening(path, access="r", *args, **kwargs):
        if os.fspath(path) == target and access == "a":
            pause()
        return original(path, access, *args, **kwargs)
    builtins.open = opening
    sys.argv = ["bb_lock.py", "append", target, "--line", "tail"]
    bb_lock.main()
elif mode == "new":
    token = bb_lock.acquire(target, agent="new", wait=0)
    if not token:
        print("CONTENDED", flush=True)
        sys.exit(3)
    try:
        value = plan.load(target)
        value["updates"].append("new")
        # This is a correctly cooperating writer on old AND new runtimes.
        # Its brief update is synchronous while the old owner is paused.
        plan.save(value, target)
    finally:
        bb_lock.release(target, token=token)
    print("SAVED", flush=True)
    sys.exit(0)
def mutate(value):
    value["updates"].append("old")
    if mode == "mutate":
        pause()
plan.locked_update(target, mutate)
print("SAVED", flush=True)
'''


class PublicationFenceRepairs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.target = self.root / "plan.json"
        self.target.write_text(json.dumps({"id": "fixture", "updates": []}))
        self.env = dict(os.environ, BB_LOCK_DIR=str(self.root / "locks"),
                        BB_LOCK_STALE="60")
        patcher = mock.patch.object(bb_lock, "LOCK_DIR", self.env["BB_LOCK_DIR"])
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(bb_lock._HELD_TOKENS.clear)

    def expire(self):
        stamp = time.time() - max(bb_lock.STALE_AFTER_SEC, 60) - 100
        os.utime(bb_lock.lock_path(str(self.target)), (stamp, stamp))

    def read(self):
        return json.loads(self.target.read_text())["updates"]

    def command(self, mode):
        return [sys.executable, "-c", WRITER, str(SCRIPTS), str(self.target), mode]

    def paused_writer(self, mode):
        process = subprocess.Popen(self.command(mode), env=self.env,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        def cleanup():
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)
        self.addCleanup(cleanup)
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            self.assertTrue(selector.select(10), "writer never reached scheduling seam")
        self.assertEqual(process.stdout.readline().strip(), "READY")
        return process

    def resume(self, process):
        stdout, stderr = process.communicate("continue\n", timeout=10)
        return process.returncode, stdout, stderr

    def contender(self):
        return subprocess.run(self.command("new"), env=self.env,
                              capture_output=True, text=True, timeout=10)

    def test_guard_spans_replacement_even_after_lease_ages(self):
        process = self.paused_writer("replace")
        self.expire()
        contender = self.contender()
        code, stdout, stderr = self.resume(process)
        self.assertEqual(contender.returncode, 3, contender.stdout + contender.stderr)
        self.assertEqual(contender.stdout.strip(), "CONTENDED")
        self.assertEqual(code, 0, stdout + stderr)
        self.assertEqual(self.read(), ["old"])
        # Once the guard exits, the next writer reads and preserves the update.
        next_writer = self.contender()
        self.assertEqual(next_writer.returncode, 0, next_writer.stderr)
        self.assertEqual(self.read(), ["old", "new"])

    def test_takeover_after_renewal_aborts_stale_publication(self):
        process = self.paused_writer("renew")
        self.expire()
        contender = self.contender()
        self.assertEqual(contender.returncode, 0, contender.stderr)
        self.assertEqual(self.read(), ["new"])
        code, stdout, stderr = self.resume(process)
        self.assertEqual(code, 1, stdout + stderr)
        self.assertIn("lease lost", stderr)
        self.assertEqual(self.read(), ["new"])

    def test_append_holds_fence_through_open_and_preserves_line_healing(self):
        self.target.write_text("head")
        process = self.paused_writer("append")
        self.expire()
        contender = bb_lock.acquire(str(self.target), agent="contender", wait=0)
        try:
            code, stdout, stderr = self.resume(process)
            self.assertFalse(contender, "append published without the stable guard")
            self.assertEqual(code, 0, stdout + stderr)
            self.assertEqual(self.target.read_text(), "head\ntail\n")
        finally:
            if contender:
                bb_lock.release(str(self.target), token=contender)

    def test_expired_lease_without_takeover_does_not_resurrect(self):
        process = self.paused_writer("mutate")
        self.expire()
        code, stdout, stderr = self.resume(process)
        self.assertEqual(code, 1, stdout + stderr)
        self.assertEqual(self.read(), [])
        self.assertEqual(self.contender().returncode, 0)
        self.assertEqual(self.read(), ["new"])

    def test_fence_rejects_missing_wrong_expired_and_tokenless_ownership(self):
        target = str(self.target)
        token = bb_lock.acquire(target, wait=0)
        self.assertTrue(token)
        for wrong in (None, "", "incorrect"):
            with self.subTest(token=wrong), self.assertRaises(RuntimeError):
                with bb_lock.fenced(target, wrong):
                    self.fail("unowned fence entered")
        self.expire()
        with self.assertRaises(RuntimeError):
            with bb_lock.fenced(target, token):
                self.fail("expired fence entered")
        self.assertFalse(bb_lock.renew(target, token))
        self.assertTrue(bb_lock.release(target, token=token))
        with self.assertRaises(RuntimeError):
            with bb_lock.fenced(target, token):
                self.fail("missing fence entered")
        lease = Path(bb_lock.lock_path(target))
        lease.write_text(json.dumps({"agent": "legacy"}))
        with self.assertRaises(RuntimeError):
            with bb_lock.fenced(target, token):
                self.fail("tokenless fence entered")
        # The new API must not alter the explicitly supported legacy release.
        self.assertTrue(bb_lock.release(target, agent="legacy"))

    def test_valid_fence_publishes_and_unwinds_on_body_error(self):
        target = str(self.target)
        token = bb_lock.acquire(target, wait=0)
        self.assertTrue(token)
        with self.assertRaisesRegex(ValueError, "fixture error"):
            with bb_lock.fenced(target, token):
                self.target.write_text(json.dumps({"updates": ["guarded"]}))
                raise ValueError("fixture error")
        self.assertEqual(self.read(), ["guarded"])
        self.assertTrue(bb_lock.release(target, token=token))
        next_token = bb_lock.acquire(target, wait=0)
        self.assertTrue(next_token, "exception leaked the kernel guard")
        self.assertTrue(bb_lock.release(target, token=next_token))

    def test_unrelated_save_runtime_error_is_not_reported_as_lease_loss(self):
        with mock.patch.object(plan_artifact, "save", side_effect=RuntimeError("disk fixture")):
            with self.assertRaisesRegex(RuntimeError, "disk fixture"):
                plan_artifact.locked_update(str(self.target), lambda p: p.update(marker=True))
        self.assertEqual(self.read(), [])


if __name__ == "__main__":
    unittest.main()
