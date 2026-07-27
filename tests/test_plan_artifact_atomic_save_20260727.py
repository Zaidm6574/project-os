#!/usr/bin/env python3
"""plan_artifact.save() must never truncate the live plan in place (2026-07-27).

Defect (audit 2026-07-27, reproduced): save() was

    with open(dest, "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2)

open(dest, "w") EMPTIES the destination before a single new byte is flushed,
and json.dump writes incrementally. So anything that stops the write partway --
SIGKILL, a full disk, a serialization error on a hand-edited plan -- leaves the
only copy of that plan half-written, and from then on `complete` and `approve`
on that plan die with "invalid JSON in plan file". The plan artifact IS the
run's coordination state: there is no second copy.

The same module already writes worker packets with tempfile.mkstemp() +
os.replace() (see the `compile` branch), with a comment explaining exactly why,
so the disposable packets were crash-safe while the irreplaceable plan was not.

Mutation contract: revert save() to the truncating open()+json.dump above and
    test_interrupted_write_preserves_the_previous_plan
    test_unserializable_plan_cannot_destroy_the_previous_plan
    test_sigkill_mid_write_preserves_the_previous_plan
MUST all fail (the destination is left torn/empty and load() raises
PlanInputError). They pass only with the temp-file + os.replace() swap.

The remaining tests are near-miss guards on the FIX, not on the defect: they
pin that the atomic swap did not quietly change what save() writes, where it
writes it, or the destination's permissions (mkstemp() creates 0600 regardless
of umask and os.replace() carries that mode onto the destination -- exactly the
silent narrowing that cost_actuals.py was already burned by, audit 2026-07-26).

Everything below writes to temp directories: full-path mode (`pid` may be a
path) means the live blackboard/ is never touched.
"""
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import plan_artifact  # noqa: E402


def a_plan(pid="p1"):
    """A VALID plan/v1, big enough that a torn write is unmistakable."""
    return {
        "schema": "plan/v1",
        "id": pid,
        "goal": "ship the widget",
        "created": "2026-07-27",
        "status": "approved",
        "steps": [
            {"id": "s%d" % i, "role": "builder", "task": "build part %d" % i,
             "depends_on": [], "outputs": ["artifact-%d" % i], "done": False}
            for i in range(1, 40)
        ],
    }


def torn_dump(obj, fp, **kwargs):
    """A json.dump stand-in that TEARS the file it is handed, then crashes.

    Faithful to a real interrupted dump: json.dump writes incrementally, so a
    write that dies partway has already put bytes in whatever file object it
    was given.
    """
    fp.write('{\n  "torn": "' + "x" * 4096)
    fp.flush()
    raise RuntimeError("simulated crash mid plan write")


class PlanSaveIsAtomic(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="po-plan-atomic-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.dest = os.path.join(self.tmp, "p1.json")
        self.plan = a_plan()
        plan_artifact.save(self.plan, self.dest)
        self.before = self._read()
        self.assertEqual(json.loads(self.before), self.plan,
                         "precondition: save() did not round-trip a good plan")

    def _read(self):
        with open(self.dest, encoding="utf-8") as f:
            return f.read()

    def _strays(self):
        return sorted(f for f in os.listdir(self.tmp) if f != "p1.json")

    def assert_plan_survived(self, how):
        """The whole point: the PREVIOUS plan is intact and still loadable."""
        self.assertTrue(os.path.exists(self.dest),
                        "%s deleted the plan file outright" % how)
        try:
            recovered = plan_artifact.load(self.dest)
        except plan_artifact.PlanInputError as e:  # pragma: no cover - the bug
            self.fail("%s TORE the plan file (%d bytes on disk): %s"
                      % (how, os.path.getsize(self.dest), e))
        self.assertEqual(recovered, self.plan,
                         "%s changed the plan on disk" % how)
        self.assertEqual(self._read(), self.before,
                         "%s rewrote the plan file byte-for-byte differently"
                         % how)

    # ---------------- the defect ----------------

    def test_interrupted_write_preserves_the_previous_plan(self):
        real_dump = plan_artifact.json.dump
        plan_artifact.json.dump = torn_dump
        try:
            with self.assertRaises(RuntimeError):
                plan_artifact.save(a_plan(), self.dest)
        finally:
            plan_artifact.json.dump = real_dump
        self.assert_plan_survived("an interrupted save()")
        self.assertEqual(self._strays(), [],
                         "interrupted save() left a temp file behind: %r"
                         % (self._strays(),))

    def test_unserializable_plan_cannot_destroy_the_previous_plan(self):
        """No monkeypatching: json.dump itself dies partway through.

        A plan is hand-edited JSON pasted in from another agent, and callers
        mutate it in memory before save(); a value json cannot encode makes the
        dump raise AFTER it has already emitted the opening bytes.
        """
        doomed = a_plan()
        doomed["steps"][0]["outputs"] = ["ok", {"a", "set", "of", "strings"}]
        with self.assertRaises(TypeError):
            plan_artifact.save(doomed, self.dest)
        self.assert_plan_survived("a failed serialization")
        self.assertEqual(self._strays(), [],
                         "failed serialization left a temp file behind: %r"
                         % (self._strays(),))

    def test_sigkill_mid_write_preserves_the_previous_plan(self):
        """The real failure mode: SIGKILL, no unwinding, no cleanup."""
        child = os.path.join(self.tmp, "kill_mid_save.py")
        with open(child, "w", encoding="utf-8") as f:
            f.write(
                "import json, os, signal, sys\n"
                "sys.path.insert(0, %r)\n"
                "import plan_artifact\n"
                "def kill_mid_dump(obj, fp, **kw):\n"
                "    fp.write('{\\n  \"torn\": \"' + 'x' * 8192)\n"
                "    fp.flush()\n"
                "    os.kill(os.getpid(), signal.SIGKILL)\n"
                "json.dump = kill_mid_dump\n"
                "plan_artifact.save(json.loads(%r), %r)\n"
                % (str(SCRIPTS), json.dumps(a_plan()), self.dest))
        r = subprocess.run([sys.executable, child], capture_output=True,
                           text=True, timeout=60)
        # Non-vacuous: if the hook never fired the child would exit 0 and this
        # test would be asserting nothing about an interrupted write.
        self.assertEqual(
            r.returncode, -signal.SIGKILL,
            "the child was not killed inside save(); this test proved nothing "
            "(rc=%s, stderr=%s)" % (r.returncode, r.stderr))
        self.assert_plan_survived("a SIGKILL mid save()")
        # A SIGKILL cannot run cleanup, so a leftover temp file is expected and
        # acceptable -- it must simply not be, or clobber, the plan.
        for stray in self._strays():
            self.assertTrue(stray.endswith(".tmp") or stray.endswith(".py"),
                            "SIGKILL left an unexpected file: %r" % stray)

    # ---------------- near-miss guards on the fix ----------------

    def test_save_still_writes_indent_2_json_and_makes_the_directory(self):
        nested = os.path.join(self.tmp, "deeper", "plans", "p9.json")
        plan = a_plan("p9")
        plan_artifact.save(plan, nested)
        with open(nested, encoding="utf-8") as f:
            text = f.read()
        self.assertEqual(text, json.dumps(plan, indent=2),
                         "save() no longer writes the documented indent=2 JSON")
        self.assertEqual(plan_artifact.load(nested), plan)

    def test_save_without_a_pid_uses_the_id_derived_path(self):
        """save(plan) with no pid must still resolve through plan_path(id)."""
        self.assertEqual(plan_artifact.plan_path("p1"),
                         os.path.join(plan_artifact.PLANS, "p1.json"))

    def test_save_preserves_an_existing_plan_files_mode(self):
        """mkstemp() is 0600 and os.replace() carries the mode: don't narrow."""
        for mode in (0o644, 0o600, 0o664):
            with self.subTest(mode=oct(mode)):
                os.chmod(self.dest, mode)
                plan_artifact.save(a_plan(), self.dest)
                got = stat.S_IMODE(os.stat(self.dest).st_mode)
                self.assertEqual(
                    got, mode,
                    "save() changed the plan file's mode from %s to %s"
                    % (oct(mode), oct(got)))

    def test_a_new_plan_file_is_created_with_the_umask_default(self):
        """A fresh plan must not silently become owner-only (0600)."""
        umask = os.umask(0)
        os.umask(umask)
        fresh = os.path.join(self.tmp, "fresh.json")
        plan_artifact.save(a_plan("fresh"), fresh)
        self.assertEqual(
            stat.S_IMODE(os.stat(fresh).st_mode), 0o666 & ~umask,
            "a newly created plan file did not get the ordinary umask mode")

    def test_a_symlinked_plan_path_is_replaced_not_written_through(self):
        """Deliberate, and the same rule as scripts/brain_archive.py.

        dest is not realpath()ed. os.replace() does not follow a symlink, so a
        link planted at the plan path is REPLACED and whatever it pointed at is
        left alone -- rather than the rewrite landing on the link's target.
        """
        victim = os.path.join(self.tmp, "victim.json")
        with open(victim, "w", encoding="utf-8") as f:
            f.write("do not touch me\n")
        link = os.path.join(self.tmp, "linked.json")
        os.symlink(victim, link)

        plan_artifact.save(a_plan("linked"), link)

        with open(victim, encoding="utf-8") as f:
            self.assertEqual(f.read(), "do not touch me\n",
                             "save() wrote THROUGH a symlinked plan path")
        self.assertFalse(os.path.islink(link),
                         "save() left the plan path as a symlink")
        self.assertEqual(plan_artifact.load(link)["id"], "linked")


if __name__ == "__main__":
    unittest.main()
