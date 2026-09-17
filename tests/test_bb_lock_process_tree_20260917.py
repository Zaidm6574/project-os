"""POSIX lease-loss cancellation covers workers in the command's group."""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

if os.name == "posix":
    import fcntl


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "bb_lock.py"

WORKER = r"""
import json, os, pathlib, signal, sys, time
root = pathlib.Path(sys.argv[1])
if sys.argv[2] == 'ignore':
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
(root / 'worker.ready').write_text(json.dumps({'pid': os.getpid(), 'pgid': os.getpgrp()}))
deadline = time.monotonic() + 30
while time.monotonic() < deadline:
    if (root / 'go').exists():
        (root / 'published').write_text('worker completed')
        break
    time.sleep(.01)
"""

LEADER = r"""
import json, os, pathlib, signal, subprocess, sys
root = pathlib.Path(sys.argv[1])
if sys.argv[2] == 'ignore':
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
child = subprocess.Popen([sys.executable, '-c', sys.argv[4], str(root), sys.argv[3]],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
(root / 'leader.ready').write_text(json.dumps({'pid': os.getpid(), 'pgid': os.getpgrp()}))
child.wait()
raise SystemExit(7)
"""


def wait_for_json(path, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return json.loads(path.read_text())
        except (FileNotFoundError, ValueError):
            time.sleep(.01)
    raise AssertionError("process did not announce readiness: %s" % path)


def stop_pid(pid):
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


@unittest.skipUnless(os.name == "posix", "bb_lock requires POSIX flock and process groups")
class TestBBLockProcessTree(unittest.TestCase):
    def run_tree(self, revoke, leader_ignores=False, worker_ignores=False):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "target"
            target.write_text("existing target must stay unchanged")
            lock_dir = root / "locks"
            env = dict(os.environ, BB_LOCK_DIR=str(lock_dir), BB_LOCK_STALE="3")
            holder = subprocess.Popen(
                [sys.executable, str(SCRIPT), "run", str(target), "--",
                 sys.executable, "-c", LEADER, str(root),
                 "ignore" if leader_ignores else "default",
                 "ignore" if worker_ignores else "default", WORKER],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, start_new_session=True,
            )
            announced = []
            try:
                leader = wait_for_json(root / "leader.ready")
                announced.append(leader["pid"])
                worker = wait_for_json(root / "worker.ready")
                announced.append(worker["pid"])
                self.assertEqual(leader["pgid"], worker["pgid"])
                lockfile, = lock_dir.glob("*.lock")
                if revoke:
                    # Synchronize on actual workers, then make the same
                    # guarded token transition as a replacement owner.
                    with Path(str(lockfile) + ".guard").open("a+") as guard:
                        fcntl.flock(guard.fileno(), fcntl.LOCK_EX)
                        replacement = json.loads(lockfile.read_text())
                        replacement["token"] = "replacement-owner-fixture"
                        lockfile.write_text(json.dumps(replacement))
                        fcntl.flock(guard.fileno(), fcntl.LOCK_UN)
                    replacement_bytes = lockfile.read_bytes()
                    started = time.monotonic()
                    out, err = holder.communicate(timeout=13)
                    self.assertLess(time.monotonic() - started, 12)
                    self.assertEqual(holder.returncode, 1, out + err)
                    self.assertIn("lease lost", err)
                    self.assertEqual(lockfile.read_bytes(), replacement_bytes,
                                     "old owner modified the replacement lease")
                    # A worker still alive after the wrapper exits can now
                    # prove it by writing. No reliance on kill(pid, 0), which
                    # treats an orphan awaiting reaping as a live worker.
                    (root / "go").touch()
                    deadline = time.monotonic() + .5
                    while time.monotonic() < deadline and not (root / "published").exists():
                        time.sleep(.01)
                    self.assertFalse((root / "published").exists(),
                                     "descendant wrote after lease-loss cancellation")
                else:
                    # Observe real renewal before allowing success, covering
                    # the same code path as the lease-loss tests.
                    before = lockfile.stat().st_mtime_ns
                    deadline = time.monotonic() + 5
                    while lockfile.stat().st_mtime_ns == before and time.monotonic() < deadline:
                        time.sleep(.01)
                    self.assertNotEqual(lockfile.stat().st_mtime_ns, before)
                    (root / "go").touch()
                    out, err = holder.communicate(timeout=5)
                    self.assertEqual(holder.returncode, 7, out + err)
                    self.assertNotIn("lease lost", err)
                    self.assertEqual((root / "published").read_text(), "worker completed")
                    self.assertFalse(lockfile.exists(), "healthy owner did not release its lease")
                self.assertEqual(target.read_text(), "existing target must stay unchanged")
            finally:
                for pid in announced:
                    stop_pid(pid)
                if holder.poll() is None:
                    holder.kill()
                holder.communicate(timeout=5)

    def test_lease_loss_stops_ordinary_descendant(self):
        self.run_tree(revoke=True)

    def test_lease_loss_kills_term_resistant_worker_after_leader_exits(self):
        self.run_tree(revoke=True, worker_ignores=True)

    def test_lease_loss_kills_term_resistant_leader_and_worker(self):
        self.run_tree(revoke=True, leader_ignores=True, worker_ignores=True)

    def test_healthy_tree_survives_renewal_and_propagates_exit_status(self):
        self.run_tree(revoke=False, leader_ignores=True, worker_ignores=True)

    def test_uninterruptible_command_cleanup_is_bounded_and_reported(self):
        # Model a kernel-delayed exit without leaving a real unkillable child.
        # Every wait must have a deadline, including the wait after SIGKILL.
        spec = importlib.util.spec_from_file_location("lock_cleanup_probe", SCRIPT)
        lock = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(lock)
        proc = mock.Mock(pid=12345)
        waits = []

        def delayed_exit(*args, **kwargs):
            self.assertIn("timeout", kwargs, "cleanup attempted an unbounded wait")
            self.assertGreater(kwargs["timeout"], 0)
            self.assertLessEqual(kwargs["timeout"], 10)
            waits.append(kwargs["timeout"])
            if len(waits) > 2:
                self.fail("cleanup retried after its bounded kill wait")
            raise subprocess.TimeoutExpired("fixture", kwargs["timeout"])

        proc.wait.side_effect = delayed_exit
        errors = io.StringIO()
        with mock.patch.object(lock.subprocess, "Popen", return_value=proc), \
                mock.patch.object(lock, "renew", return_value=False), \
                mock.patch.object(lock.os, "killpg"), \
                mock.patch.object(lock.time, "sleep"), \
                contextlib.redirect_stderr(errors):
            self.assertEqual(lock._run_with_renewal(["fixture"], "target", "token"), 1)
        self.assertEqual(len(waits), 2)
        self.assertIn("command exit not observed", errors.getvalue())
        self.assertNotIn("command terminated", errors.getvalue())

    def test_signal_denial_is_reported_and_direct_child_is_still_reaped(self):
        spec = importlib.util.spec_from_file_location("lock_signal_probe", SCRIPT)
        lock = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(lock)
        proc = mock.Mock(pid=12345)
        proc.wait.side_effect = [subprocess.TimeoutExpired("fixture", 1), -15]
        errors = io.StringIO()
        with mock.patch.object(lock.subprocess, "Popen", return_value=proc), \
                mock.patch.object(lock, "renew", return_value=False), \
                mock.patch.object(lock.os, "killpg", side_effect=PermissionError), \
                mock.patch.object(lock.time, "sleep"), \
                contextlib.redirect_stderr(errors):
            self.assertEqual(lock._run_with_renewal(["fixture"], "target", "token"), 1)
        self.assertEqual(proc.wait.call_count, 2, "direct child was not reaped")
        self.assertIn("timeout", proc.wait.call_args.kwargs)
        self.assertIn("signal denied", errors.getvalue())
        self.assertIn("unconfirmed", errors.getvalue())
        self.assertNotIn("group cancelled", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
