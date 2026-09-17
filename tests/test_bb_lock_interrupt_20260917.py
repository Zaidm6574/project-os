"""The run wrapper must retire its process group before releasing its lease."""

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


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "bb_lock.py"
WORKER = r"""
import json, os, pathlib, signal, sys, time
root = pathlib.Path(sys.argv[1])
if sys.argv[2] == 'ignore':
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
(root / 'worker.ready').write_text(json.dumps({'pid': os.getpid(), 'pgid': os.getpgrp()}))
while not (root / 'go').exists():
    time.sleep(.01)
(root / 'published').write_text('completed')
"""
LEADER = r"""
import json, os, pathlib, signal, subprocess, sys
root = pathlib.Path(sys.argv[1])
# Do not keep the wrapper's output pipes open after it exits. The assertions
# must observe surviving writers, rather than time out waiting for pipe EOF.
os.close(1)
os.close(2)
def term(sig, frame):
    (root / 'term.received').touch()
    if sys.argv[2] != 'ignore':
        raise SystemExit(15)
signal.signal(signal.SIGTERM, term)
child = subprocess.Popen([sys.executable, '-c', sys.argv[3], str(root), sys.argv[2]],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
(root / 'leader.ready').write_text(json.dumps({'pid': os.getpid(), 'pgid': os.getpgrp()}))
child.wait()
raise SystemExit(7)
"""


def wait_until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError("fixture did not reach the expected state")


def stop_pid(pid):
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def load_lock():
    spec = importlib.util.spec_from_file_location("interrupt_lock", SCRIPT)
    lock = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lock)
    return lock


@unittest.skipUnless(os.name == "posix", "requires POSIX process groups")
class TestWrapperCancellation(unittest.TestCase):
    def run_tree(self, signum=None, resistant=False, repeat=False):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "target"
            target.write_text("unchanged")
            lock_dir = root / "locks"
            env = dict(os.environ, BB_LOCK_DIR=str(lock_dir), BB_LOCK_STALE="60")
            holder = subprocess.Popen(
                [sys.executable, str(SCRIPT), "run", str(target), "--",
                 sys.executable, "-c", LEADER, str(root),
                 "ignore" if resistant else "default", WORKER],
                env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                start_new_session=True,
            )
            announced = []
            try:
                for name in ("leader", "worker"):
                    path = root / (name + ".ready")
                    wait_until(path.exists)
                    announced.append(json.loads(path.read_text()))
                self.assertEqual(announced[0]["pgid"], announced[1]["pgid"])
                lockfile, = lock_dir.glob("*.lock")
                started = time.monotonic()
                if signum is None:
                    (root / "go").touch()
                else:
                    os.kill(holder.pid, signum)
                    if repeat:
                        wait_until(lambda: (root / "term.received").exists() or holder.poll() is not None)
                        self.assertTrue((root / "term.received").exists(), "wrapper skipped group cleanup")
                        self.assertTrue(lockfile.exists(), "lease released during cleanup")
                        os.kill(holder.pid, signal.SIGINT)
                        os.kill(holder.pid, signal.SIGTERM)
                out, err = holder.communicate(timeout=13)
                self.assertLess(time.monotonic() - started, 12)
                if signum is None:
                    self.assertEqual(holder.returncode, 7, out + err)
                    self.assertEqual((root / "published").read_text(), "completed")
                    self.assertNotIn("FAILED", err)
                else:
                    # Test behavior before diagnostics: a surviving old worker
                    # can still write even after a new owner acquires the lease.
                    replacement = subprocess.run(
                        [sys.executable, str(SCRIPT), "acquire", str(target), "--wait", "0"],
                        env=env, text=True, capture_output=True, timeout=3)
                    self.assertEqual(replacement.returncode, 0, replacement.stderr)
                    replacement_bytes = lockfile.read_bytes()
                    (root / "go").touch()
                    deadline = time.monotonic() + .5
                    while time.monotonic() < deadline and not (root / "published").exists():
                        time.sleep(.01)
                    self.assertFalse((root / "published").exists(),
                                     "old command wrote after wrapper cancellation and replacement acquire")
                    self.assertEqual(lockfile.read_bytes(), replacement_bytes)
                    self.assertEqual(holder.returncode, -signum, out + err)
                    self.assertIn("FAILED: command wrapper interrupted;", err)
                    self.assertTrue(
                        "command process group cancelled" in err or
                        "command exited; process group cancellation unconfirmed (signal denied)" in err,
                        err)
                    released = subprocess.run(
                        [sys.executable, str(SCRIPT), "release", str(target),
                         "--token", replacement.stdout.strip()],
                        env=env, text=True, capture_output=True, timeout=3)
                    self.assertEqual(released.returncode, 0, released.stderr)
                self.assertFalse(lockfile.exists())
                self.assertEqual(target.read_text(), "unchanged")
            finally:
                for process in announced:
                    stop_pid(process["pid"])
                if holder.poll() is None:
                    holder.kill()
                holder.communicate(timeout=5)

    def test_sigint_stops_command_before_replacement_can_publish(self):
        self.run_tree(signal.SIGINT)

    def test_sigterm_stops_command_before_replacement_can_publish(self):
        self.run_tree(signal.SIGTERM)

    def test_repeated_signals_do_not_skip_term_resistant_cleanup(self):
        self.run_tree(signal.SIGINT, resistant=True, repeat=True)

    def test_healthy_command_publishes_and_preserves_exit_status(self):
        self.run_tree()

    def test_keyboard_interrupt_and_renewal_error_cleanup_then_propagate(self):
        for error in (KeyboardInterrupt(), RuntimeError("renewal failed")):
            with self.subTest(error=type(error).__name__):
                lock = load_lock()
                proc = mock.Mock(pid=12345)
                proc.wait.side_effect = [subprocess.TimeoutExpired("fixture", 1), -15]
                with mock.patch.object(lock.subprocess, "Popen", return_value=proc), \
                        mock.patch.object(lock, "renew", side_effect=error), \
                        mock.patch.object(lock.os, "killpg") as killpg, \
                        mock.patch.object(lock.time, "sleep"), \
                        contextlib.redirect_stderr(io.StringIO()) as errors:
                    with self.assertRaises(type(error)):
                        lock._run_with_renewal(["fixture"], "target", "token")
                self.assertEqual(killpg.call_args_list,
                                 [mock.call(proc.pid, signal.SIGTERM), mock.call(proc.pid, signal.SIGKILL)])
                self.assertEqual(proc.wait.call_count, 2)
                self.assertIn("command process group cancelled", errors.getvalue())

    def test_signal_during_spawn_is_cleaned_before_release_and_original_handler(self):
        lock = load_lock()
        events = []
        proc = mock.Mock(pid=12345, returncode=None)

        def spawned(*args, **kwargs):
            os.kill(os.getpid(), signal.SIGINT)
            events.append("spawned")
            return proc

        def reaped(**kwargs):
            self.assertGreater(kwargs["timeout"], 0)
            events.append("reaped")
            return -15

        def original(signum, frame):
            self.assertEqual(signum, signal.SIGINT)
            events.append("original handler")

        old = signal.signal(signal.SIGINT, original)
        old_term = signal.getsignal(signal.SIGTERM)
        proc.wait.side_effect = reaped
        try:
            with mock.patch.object(lock.sys, "argv", ["bb_lock", "run", "target", "--", "fixture"]), \
                    mock.patch.object(lock, "acquire", return_value="token"), \
                    mock.patch.object(lock, "release", side_effect=lambda *a, **kw: events.append("released")), \
                    mock.patch.object(lock.subprocess, "Popen", side_effect=spawned), \
                    mock.patch.object(lock.os, "killpg", side_effect=lambda pid, sig: events.append(sig.name)), \
                    mock.patch.object(lock.time, "sleep"), \
                    contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as exit_context:
                    lock.main()
            self.assertEqual(exit_context.exception.code, 128 + signal.SIGINT)
            self.assertEqual(events, ["spawned", "SIGTERM", "SIGKILL", "reaped", "released", "original handler"])
            self.assertIs(signal.getsignal(signal.SIGINT), original)
            self.assertEqual(signal.getsignal(signal.SIGTERM), old_term)
        finally:
            signal.signal(signal.SIGINT, old)
            signal.signal(signal.SIGTERM, old_term)

    def test_ignored_signal_does_not_cancel_a_healthy_command(self):
        lock = load_lock()
        proc = mock.Mock(pid=12345, returncode=7)
        proc.wait.return_value = 7

        def spawned(*args, **kwargs):
            os.kill(os.getpid(), signal.SIGINT)
            return proc

        old = signal.signal(signal.SIGINT, signal.SIG_IGN)
        old_term = signal.getsignal(signal.SIGTERM)
        try:
            with mock.patch.object(lock.sys, "argv", ["bb_lock", "run", "target", "--", "fixture"]), \
                    mock.patch.object(lock, "acquire", return_value="token"), \
                    mock.patch.object(lock, "release") as release, \
                    mock.patch.object(lock.subprocess, "Popen", side_effect=spawned), \
                    mock.patch.object(lock.os, "killpg") as killpg:
                with self.assertRaises(SystemExit) as exit_context:
                    lock.main()
            self.assertEqual(exit_context.exception.code, 7)
            self.assertEqual(release.call_count, 1)
            killpg.assert_not_called()
            self.assertEqual(signal.getsignal(signal.SIGINT), signal.SIG_IGN)
            self.assertEqual(signal.getsignal(signal.SIGTERM), old_term)
        finally:
            signal.signal(signal.SIGINT, old)
            signal.signal(signal.SIGTERM, old_term)

    def test_keyboard_interrupt_cleanup_timeout_is_bounded_and_truthful(self):
        lock = load_lock()
        proc = mock.Mock(pid=12345, returncode=None)
        proc.wait.side_effect = [KeyboardInterrupt(), subprocess.TimeoutExpired("fixture", 5)]
        with mock.patch.object(lock.subprocess, "Popen", return_value=proc), \
                mock.patch.object(lock.os, "killpg"), \
                mock.patch.object(lock.time, "sleep"), \
                contextlib.redirect_stderr(io.StringIO()) as errors:
            with self.assertRaises(KeyboardInterrupt):
                lock._run_with_renewal(["fixture"], "target", "token")
        self.assertEqual(proc.wait.call_count, 2)
        self.assertEqual(proc.wait.call_args.kwargs, {"timeout": 5})
        self.assertIn("command exit not observed", errors.getvalue())
        self.assertNotIn("group cancelled", errors.getvalue())

    def test_already_reaped_leader_never_signals_a_reusable_group_id(self):
        lock = load_lock()
        proc = mock.Mock(pid=12345, returncode=0)
        proc.wait.side_effect = KeyboardInterrupt()
        with mock.patch.object(lock.subprocess, "Popen", return_value=proc), \
                mock.patch.object(lock.os, "killpg") as killpg, \
                contextlib.redirect_stderr(io.StringIO()) as errors:
            with self.assertRaises(KeyboardInterrupt):
                lock._run_with_renewal(["fixture"], "target", "token")
        killpg.assert_not_called()
        self.assertIn("unconfirmed (leader already reaped)", errors.getvalue())

    def test_waiting_for_a_lease_remains_interruptible_without_spawning(self):
        lock = load_lock()

        def waiting(*args, **kwargs):
            os.kill(os.getpid(), signal.SIGINT)
            self.fail("cancellation was deferred while waiting for another owner's lease")

        old = signal.signal(signal.SIGINT, signal.default_int_handler)
        old_term = signal.getsignal(signal.SIGTERM)
        try:
            with mock.patch.object(lock.sys, "argv", ["bb_lock", "run", "target", "--", "fixture"]), \
                    mock.patch.object(lock, "acquire", side_effect=waiting), \
                    mock.patch.object(lock, "release") as release, \
                    mock.patch.object(lock.subprocess, "Popen") as spawn:
                with self.assertRaises(KeyboardInterrupt):
                    lock.main()
            spawn.assert_not_called()
            release.assert_not_called()
            self.assertIs(signal.getsignal(signal.SIGINT), signal.default_int_handler)
            self.assertEqual(signal.getsignal(signal.SIGTERM), old_term)
        finally:
            signal.signal(signal.SIGINT, old)
            signal.signal(signal.SIGTERM, old_term)


if __name__ == "__main__":
    unittest.main()
