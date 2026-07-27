"""--wait must be a REAL bound on bb_lock acquire, even against a hung holder.

Defect (handoff 2026-07-26, "Still open" item 1): ``_guard()`` did an
unbounded ``fcntl.flock(..., LOCK_EX)`` and ``acquire()`` only checked its
``--wait`` deadline AFTER the guard block, so one stopped or hung process
inside a guard critical section wedged every other agent indefinitely and
``--wait N`` did not bound the wait.

These tests assert on observed behaviour only (exit codes, wall time against
generous CI-load slack, lockfiles on disk) — never on source text. The hung
holder is a real subprocess holding the real flock the shipped module uses;
the guard pathname (``<lock>.lock.guard``) is the same on-disk contract
already pinned by test_bb_lock_hardening's reaper-serialization test.
"""

import importlib.util
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "bb_lock.py"

# Hard ceiling for a waiter given --wait 1. Generous CI-load slack: a bounded
# waiter finishes in ~1s; only an unbounded flock ever reaches this.
WEDGE_CEILING_SEC = 20

# A python subprocess that takes the guard flock and never releases it,
# exactly what a SIGSTOPped agent inside a _guard critical section looks like.
HOLD_GUARD_FOREVER = (
    "import fcntl, sys, time\n"
    "f = open(sys.argv[1], 'a+')\n"
    "fcntl.flock(f.fileno(), fcntl.LOCK_EX)\n"
    "print('HELD', flush=True)\n"
    "time.sleep(600)\n"
)

# Same, but the holder exits (releasing the flock) after a short hold, so a
# correct waiter with budget left MUST eventually get through.
HOLD_GUARD_BRIEFLY = (
    "import fcntl, sys, time\n"
    "f = open(sys.argv[1], 'a+')\n"
    "fcntl.flock(f.fileno(), fcntl.LOCK_EX)\n"
    "print('HELD', flush=True)\n"
    "time.sleep(1.0)\n"
)


def load_bb_lock(lock_dir):
    """Load a private bb_lock module instance bound to lock_dir."""
    spec = importlib.util.spec_from_file_location("bb_lock_wait_bound", str(SCRIPT))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.LOCK_DIR = str(lock_dir)
    os.makedirs(str(lock_dir), exist_ok=True)
    return module


def run_acquire(target, lock_dir, wait, timeout):
    env = dict(os.environ)
    env["BB_LOCK_DIR"] = str(lock_dir)
    return subprocess.run(
        [sys.executable, str(SCRIPT), "acquire", str(target),
         "--agent", "waiter", "--wait", str(wait)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(ROOT),
        timeout=timeout,
    )


def hold_guard(script_body, guard_path):
    holder = subprocess.Popen(
        [sys.executable, "-c", script_body, str(guard_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    ready = holder.stdout.readline().strip()
    if ready != "HELD":
        holder.kill()
        holder.communicate()
        raise AssertionError("guard holder never took the flock: %r" % ready)
    return holder


class TestWaitBoundsAcquire(unittest.TestCase):
    def test_hung_guard_holder_cannot_wedge_waiter_past_wait_budget(self):
        with tempfile.TemporaryDirectory() as td:
            lock_dir = Path(td) / "locks"
            target = Path(td) / "shared.md"
            bb = load_bb_lock(lock_dir)
            guard_path = bb.lock_path(str(target)) + ".guard"

            holder = hold_guard(HOLD_GUARD_FOREVER, guard_path)
            try:
                start = time.monotonic()
                try:
                    waiter = run_acquire(target, lock_dir, wait=1,
                                         timeout=WEDGE_CEILING_SEC)
                except subprocess.TimeoutExpired:
                    self.fail(
                        "acquire --wait 1 was still blocked after "
                        "%ss behind a hung guard holder: --wait is not a "
                        "real bound" % WEDGE_CEILING_SEC)
                elapsed = time.monotonic() - start
            finally:
                holder.kill()
                holder.communicate()

            self.assertEqual(
                waiter.returncode, 1,
                "waiter exited %s (stdout=%r stderr=%r)" % (
                    waiter.returncode, waiter.stdout, waiter.stderr))
            self.assertIn("FAILED", waiter.stderr)
            self.assertLess(
                elapsed, WEDGE_CEILING_SEC,
                "waiter took %.1fs against a 1s --wait budget" % elapsed)
            self.assertEqual(
                list(lock_dir.glob("*.lock")), [],
                "a timed-out waiter must not leave a lockfile behind")

    def test_uncontended_acquire_succeeds_fast_and_does_not_burn_the_budget(self):
        with tempfile.TemporaryDirectory() as td:
            lock_dir = Path(td) / "locks"
            target = Path(td) / "shared.md"

            start = time.monotonic()
            result = run_acquire(target, lock_dir, wait=30, timeout=25)
            elapsed = time.monotonic() - start

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertRegex(result.stdout.strip(), r"^[0-9a-f]{32}$")
            self.assertEqual(len(list(lock_dir.glob("*.lock"))), 1)
            # Far below the 30s budget: an implementation that sleeps
            # before/instead of trying cannot pass, while CI-grade load
            # still has ~15s of slack for interpreter startup.
            self.assertLess(
                elapsed, 15,
                "uncontended acquire took %.1fs" % elapsed)

    def test_waiter_still_acquires_when_guard_frees_before_deadline(self):
        # Mirror direction of the wedge fix (2026-07-25 lesson: verify BOTH
        # failure directions): bounding the guard wait must not turn into
        # "fail on the first busy poll". A holder that releases with budget
        # to spare must not cost the waiter its acquisition.
        with tempfile.TemporaryDirectory() as td:
            lock_dir = Path(td) / "locks"
            target = Path(td) / "shared.md"
            bb = load_bb_lock(lock_dir)
            guard_path = bb.lock_path(str(target)) + ".guard"

            holder = hold_guard(HOLD_GUARD_BRIEFLY, guard_path)
            try:
                result = run_acquire(target, lock_dir, wait=30, timeout=40)
            finally:
                if holder.poll() is None:
                    holder.kill()
                holder.communicate()

            self.assertEqual(
                result.returncode, 0,
                "waiter failed although the guard freed well inside its "
                "budget (stdout=%r stderr=%r)" % (result.stdout, result.stderr))
            self.assertRegex(result.stdout.strip(), r"^[0-9a-f]{32}$")
            self.assertEqual(len(list(lock_dir.glob("*.lock"))), 1)


if __name__ == "__main__":
    unittest.main()
