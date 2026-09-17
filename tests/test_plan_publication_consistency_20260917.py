"""Create/compile must publish one exclusive plan revision.

Pipe handshakes stop real CLI processes at specific read/write boundaries.
Contenders use the real bb_lock protocol with a zero wait, not a mocked lock.
All runtimes and artifacts are copied into temporary directories. Running this
module against the pre-repair runtime reproduces an overwritten approval and
packets compiled from a different revision than the final running plan.
"""
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import sys
import tempfile
import time
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"

# Instrument scheduling only; loading, validation, locking, and publication
# all run the selected runtime. No sleeps decide whether a contender wins.
WORKER = r'''
import os, sys, time
sys.path.insert(0, sys.argv[1])
import plan_artifact as p
mode, args = sys.argv[2], sys.argv[3:]
def pause():
    print("READY", flush=True)
    if sys.stdin.readline().strip() != "continue":
        raise RuntimeError("coordination pipe closed")
if mode.startswith("pause-"):
    stage = mode[len("pause-"):]
    if stage == "save":
        original = p.save
        def saving(*a, **kw):
            pause()
            return original(*a, **kw)
        p.save = saving
    elif stage == "load":
        original = p.load
        fired = False
        def loading(*a, **kw):
            global fired
            value = original(*a, **kw)
            if not fired:
                fired = True
                pause()
            return value
        p.load = loading
    else:
        original = p.tempfile.mkstemp
        fired = False
        def staging(*a, **kw):
            global fired
            if not fired and kw.get("prefix", "").endswith("-build.md."):
                fired = True
                pause()
            return original(*a, **kw)
        p.tempfile.mkstemp = staging
elif mode in ("contend", "revise"):
    original = p.bb_lock.acquire
    def acquire(path, agent="unknown", wait=10):
        return original(path, agent=agent, wait=0)
    p.bb_lock.acquire = acquire
    if mode == "revise":
        target = args[0]
        def revise(plan):
            plan["steps"][0]["task"] = "NEW APPROVED TASK"
        p.locked_update(target, revise)
        args = ["approve", target]
elif mode == "expire-before-fence":
    original = p.bb_lock.acquire
    def acquire(*a, **kw):
        token = original(*a, **kw)
        if token:
            old = time.time() - 120
            os.utime(p.bb_lock.lock_path(a[0]), (old, old))
        return token
    p.bb_lock.acquire = acquire
elif mode == "fail-save":
    def fail(*a, **kw):
        raise OSError("synthetic plan publication failure")
    p.save = fail
elif mode == "fail-packet":
    original = p.tempfile.mkstemp
    def staging(*a, **kw):
        if kw.get("prefix", "").endswith("-build.md."):
            raise OSError("synthetic first packet failure")
        return original(*a, **kw)
    p.tempfile.mkstemp = staging
sys.argv = ["plan_artifact.py", *args]
p.main()
'''


def steps():
    return [
        {"id": "build", "role": "builder", "task": "ORIGINAL APPROVED TASK"},
        {"id": "check", "role": "checker", "task": "check the fixture",
         "depends_on": ["build"],
         "verification": {"method": "compare the fixture bytes",
                          "expected": "all fixture bytes match"}},
    ]


class PlanPublicationConsistency(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.scripts = self.root / "scripts"
        self.scripts.mkdir()
        for name in ("plan_artifact.py", "bb_lock.py"):
            shutil.copy2(SCRIPTS / name, self.scripts / name)
        self.path = self.root / "blackboard" / "plans" / "fixture.json"
        self.packets = self.path.parent.parent / "packets"
        self.step_file = self.root / "steps.json"
        self.step_file.write_text(json.dumps(steps()), encoding="utf-8")
        self.env = dict(os.environ, BB_LOCK_DIR=str(self.root / "locks"),
                        BB_LOCK_STALE="60")
        self.sentinel = self.root / "unrelated.md"
        self.sentinel.write_bytes(b"unrelated artifact\n")
        self.addCleanup(self.assert_sentinel)

    def assert_sentinel(self):
        self.assertEqual(self.sentinel.read_bytes(), b"unrelated artifact\n")

    def command(self, mode, *args):
        return [sys.executable, "-B", "-c", WORKER, str(self.scripts), mode,
                *map(str, args)]

    def cli(self, *args, mode="normal"):
        return subprocess.run(self.command(mode, *args), cwd=self.root,
                              env=self.env, capture_output=True, text=True,
                              timeout=15)

    def ok(self, *args, **kwargs):
        result = self.cli(*args, **kwargs)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def create_args(self, goal="first revision", path=None):
        return ("create", "--id", path or self.path, "--goal", goal,
                "--steps-file", self.step_file)

    def approved(self):
        self.ok(*self.create_args())
        self.ok("approve", self.path)
        return self.read()

    def read(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def paused(self, stage, *args):
        process = subprocess.Popen(self.command("pause-" + stage, *args),
                                   cwd=self.root, env=self.env,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        def cleanup():
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)
        self.addCleanup(cleanup)
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            self.assertTrue(selector.select(10), "writer did not reach scheduling seam")
        self.assertEqual(process.stdout.readline().strip(), "READY")
        return process

    def finish(self, process):
        out, err = process.communicate("continue\n", timeout=15)
        self.assertEqual(process.returncode, 0, out + err)

    def age_lease(self):
        locks = list((self.root / "locks").glob("*.lock"))
        self.assertEqual(len(locks), 1, "operation must already hold its plan lease")
        old = time.time() - 120
        os.utime(locks[0], (old, old))

    def assert_released(self):
        self.assertEqual(list((self.root / "locks").glob("*.lock")), [])

    def test_concurrent_create_preserves_the_winners_approval(self):
        first = self.paused("save", *self.create_args("first creator"))
        second = self.cli(*self.create_args("second creator"), mode="contend")
        prior = None
        if second.returncode == 0:
            # Baseline: B creates and really approves while A is paused after
            # its absence check. Resume A to observe the lost approval itself.
            self.ok("approve", self.path)
            prior = self.path.read_bytes()
        self.finish(first)
        if prior is not None:
            self.assertEqual(self.path.read_bytes(), prior,
                             "paused creator overwrote an intervening approved plan")
        self.assertNotEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertIn("could not lock plan file", second.stderr)
        self.assertEqual(self.read()["goal"], "first creator")
        self.ok("approve", self.path)
        prior = self.path.read_bytes()
        serial = self.cli(*self.create_args("serial contender"))
        self.assertNotEqual(serial.returncode, 0)
        self.assertIn("already exists", serial.stderr)
        self.assertEqual(self.path.read_bytes(), prior)
        self.assert_released()

    def test_create_fence_survives_lease_age_during_publication(self):
        first = self.paused("save", *self.create_args())
        self.age_lease()
        second = self.cli(*self.create_args("replacement"), mode="contend")
        self.assertNotEqual(second.returncode, 0)
        self.assertFalse(self.path.exists())
        self.finish(first)
        self.assertEqual(self.read()["goal"], "first revision")
        self.assert_released()

    def compile_interleaving(self, stage, age=False):
        original = self.approved()
        first = self.paused(stage, "compile", self.path)
        if age:
            self.age_lease()
        contender = self.cli(self.path, mode="revise")
        self.finish(first)
        final = self.read()
        packet = (self.packets / "fixture-build.md").read_text(encoding="utf-8")
        self.assertIn("Task: " + final["steps"][0]["task"] + "\n", packet,
                      "published packets and running plan name different revisions")
        self.assertNotEqual(contender.returncode, 0, contender.stdout + contender.stderr)
        self.assertIn("could not lock plan file", contender.stderr)
        self.assertEqual(final["status"], "running")
        self.assertEqual(final["approved_digest"], original["approved_digest"])
        self.assertEqual(final["steps"], original["steps"])
        self.ok("validate", self.path)
        self.assert_released()

    def test_compile_excludes_revision_change_after_initial_load(self):
        self.compile_interleaving("load")

    def test_compile_excludes_reapproval_at_first_packet_write(self):
        self.compile_interleaving("packet")

    def test_compile_fence_survives_lease_age_during_publication(self):
        self.compile_interleaving("packet", age=True)

    def test_other_plan_can_compile_while_first_plan_is_paused(self):
        original = self.approved()
        first = self.paused("packet", "compile", self.path)
        other = self.path.with_name("other.json")
        self.ok(*self.create_args("independent plan", other))
        self.ok("approve", other)
        self.ok("compile", other)
        other_bytes = other.read_bytes()
        self.finish(first)
        self.assertEqual(other.read_bytes(), other_bytes)
        self.assertEqual(self.read()["approved_digest"], original["approved_digest"])
        self.assertEqual(len(list(self.packets.glob("*.md"))), 4)
        self.assert_released()

    def test_healthy_workflow_preserves_digest_packets_and_structural_completion(self):
        original = self.approved()
        result = self.ok("compile", self.path)
        self.assertIn("compiled 2 worker packets", result.stdout)
        running = self.read()
        self.assertEqual(running["status"], "running")
        self.assertEqual(running["approved_digest"], original["approved_digest"])
        checker = (self.packets / "fixture-check.md").read_text(encoding="utf-8")
        self.assertIn("Verification method: compare the fixture bytes\n", checker)
        self.assertIn("Expected: all fixture bytes match\n", checker)
        self.ok("complete", self.path, "--step", "build")
        self.ok("complete", self.path, "--step", "check")
        self.assertEqual(self.read()["status"], "done")
        self.ok("validate", self.path)
        self.assert_released()

    def test_rejected_changed_approval_preserves_files_even_with_force(self):
        self.approved()
        changed = self.read()
        changed["steps"][0]["task"] = "NEW APPROVED TASK"
        self.path.write_text(json.dumps(changed), encoding="utf-8")
        before = self.path.read_bytes()
        for options in ((), ("--force",)):
            result = self.cli("compile", self.path, *options)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("content changed after approval", result.stderr)
            self.assertEqual(self.path.read_bytes(), before)
            self.assertFalse(self.packets.exists())
            self.assertFalse(Path(str(self.path) + ".pre-force").exists())
            self.assert_released()
        self.ok("approve", self.path)
        self.ok("compile", self.path)
        packet = (self.packets / "fixture-build.md").read_text(encoding="utf-8")
        self.assertIn("Task: NEW APPROVED TASK\n", packet)

    def test_unapproved_refusal_and_force_recompile_keep_existing_contract(self):
        self.ok(*self.create_args())
        before = self.path.read_bytes()
        refusal = self.cli("compile", self.path)
        self.assertNotEqual(refusal.returncode, 0)
        self.assertIn("not approved", refusal.stderr)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(self.packets.exists())
        self.assert_released()
        self.ok("compile", self.path, "--force")
        self.assertEqual(Path(str(self.path) + ".pre-force").read_bytes(), before)
        self.ok("complete", self.path, "--step", "build")
        before = self.path.read_bytes()
        self.ok("compile", self.path, "--force")
        self.assertEqual(Path(str(self.path) + ".pre-force").read_bytes(), before)
        self.assertTrue(self.read()["steps"][0]["done"])
        self.assert_released()

    def test_failed_create_releases_lock_without_publishing(self):
        result = self.cli(*self.create_args(), mode="fail-save")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("synthetic plan publication failure", result.stderr)
        self.assertFalse(self.path.exists())
        self.assertFalse(self.packets.exists())
        self.assert_released()
        self.ok(*self.create_args())

    def test_expired_create_lease_refuses_before_publishing(self):
        result = self.cli(*self.create_args(), mode="expire-before-fence")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("lease lost", result.stderr)
        self.assertFalse(self.path.exists())
        self.assertFalse(self.packets.exists())
        self.assert_released()
        self.ok(*self.create_args())

    def test_expired_compile_lease_refuses_before_publishing(self):
        self.approved()
        before = self.path.read_bytes()
        result = self.cli("compile", self.path, mode="expire-before-fence")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("lease lost", result.stderr)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(self.packets.exists())
        self.assert_released()
        self.ok("compile", self.path)

    def test_failed_first_packet_releases_lock_preserving_approved_plan(self):
        self.approved()
        before = self.path.read_bytes()
        result = self.cli("compile", self.path, mode="fail-packet")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("synthetic first packet failure", result.stderr)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.packets.iterdir()), [])
        self.assert_released()
        self.ok("compile", self.path)


if __name__ == "__main__":
    unittest.main()
