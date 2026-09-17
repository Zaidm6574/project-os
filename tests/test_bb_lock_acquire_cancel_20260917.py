"""Acquisition cancellation must leave no unpublished lease or spawned child."""

import errno
import importlib.util
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


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "bb_lock.py"
DRIVER = r"""
import importlib.util, os, pathlib, signal, sys
from unittest import mock
script, directory, number, mode = sys.argv[1:]
root = pathlib.Path(directory)
signum = int(number)
spec = importlib.util.spec_from_file_location('acquire_cancel_driver', script)
lock = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lock)
lock.LOCK_DIR = str(root / 'locks')
signal.signal(signal.SIGINT, signal.default_int_handler)
signal.signal(signal.SIGTERM, signal.SIG_DFL)
write, open_fd, unlink = lock.os.write, lock.os.open, lock.os.unlink

def interrupt():
    (root / 'signal.sent').touch()
    os.kill(os.getpid(), signum)
    if mode in ('repeat', 'rollback', 'rollback_denied'):
        os.kill(os.getpid(), signal.SIGTERM)
        os.kill(os.getpid(), signal.SIGINT)

def written(fd, data):
    count = write(fd, data)
    (root / 'payload.written').write_bytes(data)
    if mode in ('rollback', 'rollback_denied'):
        raise OSError('fixture write failed after publication')
    interrupt()
    return count

def opened(*args, **kwargs):
    fd = open_fd(*args, **kwargs)
    interrupt()
    return fd

def removed(path, *args, **kwargs):
    if mode in ('rollback', 'rollback_denied'):
        interrupt()
    if mode == 'rollback_denied':
        raise PermissionError('fixture denied cleanup')
    return unlink(path, *args, **kwargs)

def waiting(delay):
    interrupt()
    raise AssertionError('acquisition wait ignored cancellation')

def spawned(*args, **kwargs):
    (root / 'child.spawned').touch()
    raise AssertionError('a cancelled acquire must never spawn a child')

sys.argv = ['bb_lock', 'run', str(root / 'target'), '--wait', '30', '--', 'fixture']
with mock.patch.object(lock.subprocess, 'Popen', side_effect=spawned):
    if mode == 'waiting':
        with mock.patch.object(lock.time, 'sleep', side_effect=waiting):
            lock.main()
    elif mode == 'opened':
        with mock.patch.object(lock.os, 'open', side_effect=opened):
            lock.main()
    else:
        with mock.patch.object(lock.os, 'write', side_effect=written), \
                mock.patch.object(lock.os, 'unlink', side_effect=removed):
            lock.main()
"""


def load_lock(root):
    spec = importlib.util.spec_from_file_location("acquire_cancel_lock", SCRIPT)
    lock = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lock)
    lock.LOCK_DIR = str(root / "locks")
    return lock


@unittest.skipUnless(os.name == "posix", "requires POSIX signals and file guards")
class TestAcquireCancellation(unittest.TestCase):
    def assert_reacquires(self, lock, target):
        path = Path(lock.lock_path(str(target)))
        self.assertFalse(path.exists(), "cancelled acquisition left its lease")
        self.assertNotIn(str(path), lock._HELD_TOKENS)
        token = lock.acquire(str(target), wait=0)
        self.assertTrue(token, "replacement could not acquire immediately")
        self.assertEqual(json.loads(path.read_text())["token"], token)
        self.assertTrue(lock.release(str(target), token=token))
        self.assertFalse(path.exists())

    def cancelled_wrapper(self, signum, mode="written", held=False, guard=False):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "target"
            target.write_text("unchanged")
            lock = load_lock(root)
            path = Path(lock.lock_path(str(target)))
            owner = lock.acquire(str(target), agent="healthy owner", wait=0) if held else None
            before = path.read_bytes() if held else None
            handle = None
            try:
                if guard:
                    path.parent.mkdir(exist_ok=True)
                    handle = Path(str(path) + ".guard").open("a+")
                    fcntl.flock(handle, fcntl.LOCK_EX)
                started = time.monotonic()
                result = subprocess.run(
                    [sys.executable, "-c", DRIVER, str(SCRIPT), str(root), str(int(signum)), mode],
                    capture_output=True, text=True, timeout=5,
                )
                self.assertLess(time.monotonic() - started, 4, "long wait delayed cancellation")
                self.assertTrue((root / "signal.sent").exists(), result.stderr)
                self.assertFalse((root / "child.spawned").exists(), result.stderr)
                self.assertEqual(result.returncode, -signum, result.stdout + result.stderr)
                self.assertEqual(target.read_text(), "unchanged")
                if mode in ("written", "repeat", "rollback"):
                    self.assertIn("token", json.loads((root / "payload.written").read_bytes()))
                if held:
                    self.assertEqual(path.read_bytes(), before, "cancellation changed the existing owner")
                else:
                    self.assertFalse(path.exists(), "cancelled wrapper left its acquisition behind")
            finally:
                if handle is not None:
                    fcntl.flock(handle, fcntl.LOCK_UN)
                    handle.close()
                if owner is not None:
                    self.assertTrue(lock.release(str(target), token=owner))
            self.assert_reacquires(lock, target)

    def test_sigint_after_real_payload_write_releases_before_exit(self):
        self.cancelled_wrapper(signal.SIGINT)

    def test_sigterm_after_real_payload_write_releases_before_exit(self):
        self.cancelled_wrapper(signal.SIGTERM)

    def test_repeated_signals_preserve_first_signal_and_complete_cleanup(self):
        self.cancelled_wrapper(signal.SIGINT, mode="repeat")

    def test_signal_during_open_to_fd_handoff_does_not_orphan_empty_lease(self):
        self.cancelled_wrapper(signal.SIGINT, mode="opened")

    def test_repeated_signals_during_write_error_rollback_cannot_skip_unlink(self):
        self.cancelled_wrapper(signal.SIGINT, mode="rollback")

    def test_failed_rollback_reports_unconfirmed_cleanup_even_when_cancelled(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            lock = load_lock(root)
            target = root / "target"
            result = subprocess.run(
                [sys.executable, "-c", DRIVER, str(SCRIPT), str(root),
                 str(int(signal.SIGINT)), "rollback_denied"],
                capture_output=True, text=True, timeout=5,
            )
            self.assertEqual(result.returncode, -signal.SIGINT, result.stderr)
            self.assertFalse((root / "child.spawned").exists())
            self.assertIn("FAILED: acquisition rollback unconfirmed (PermissionError)", result.stderr)
            path = Path(lock.lock_path(str(target)))
            self.assertTrue(path.exists(), "fixture should have denied removal")
            owner = json.loads(path.read_text())["token"]
            self.assertFalse(lock.acquire(str(target), wait=0))
            self.assertTrue(lock.release(str(target), token=owner))
            self.assert_reacquires(lock, target)

    def test_sigint_while_waiting_preserves_existing_owner(self):
        self.cancelled_wrapper(signal.SIGINT, mode="waiting", held=True)

    def test_sigterm_while_waiting_preserves_existing_owner(self):
        self.cancelled_wrapper(signal.SIGTERM, mode="waiting", held=True)

    def test_wait_for_busy_guard_remains_interruptible(self):
        self.cancelled_wrapper(signal.SIGINT, mode="waiting", held=True, guard=True)

    def test_direct_acquire_sigint_rolls_back_while_guard_is_held(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            lock = load_lock(root)
            write, unlink = lock.os.write, lock.os.unlink
            old = signal.signal(signal.SIGINT, signal.default_int_handler)
            checked = []

            def interrupted(fd, data):
                write(fd, data)
                os.kill(os.getpid(), signal.SIGINT)
                self.fail("actual SIGINT was ignored")

            def guarded_unlink(path):
                with open(path + ".guard", "a+") as probe:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                checked.append(path)
                return unlink(path)

            try:
                with mock.patch.object(lock.os, "write", side_effect=interrupted), \
                        mock.patch.object(lock.os, "unlink", side_effect=guarded_unlink):
                    with self.assertRaises(KeyboardInterrupt):
                        lock.acquire(str(root / "target"), wait=0)
            finally:
                signal.signal(signal.SIGINT, old)
            self.assertEqual(checked, [lock.lock_path(str(root / "target"))])
            self.assert_reacquires(lock, root / "target")

    def test_failed_or_partial_write_rolls_back_own_object(self):
        for partial in (False, True):
            with self.subTest(partial=partial), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                lock = load_lock(root)
                write = lock.os.write

                def failed(fd, data):
                    if partial:
                        write(fd, data[:7])
                    raise OSError(errno.ENOSPC, "fixture full")

                with mock.patch.object(lock.os, "write", side_effect=failed):
                    with self.assertRaises(OSError) as caught:
                        lock.acquire(str(root / "target"), wait=0)
                self.assertEqual(caught.exception.errno, errno.ENOSPC)
                self.assert_reacquires(lock, root / "target")

    def test_zero_progress_write_fails_and_releases(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            lock = load_lock(root)
            with mock.patch.object(lock.os, "write", return_value=0):
                with self.assertRaises(OSError) as caught:
                    lock.acquire(str(root / "target"), wait=0)
            self.assertEqual(caught.exception.errno, errno.EIO)
            self.assert_reacquires(lock, root / "target")

    def test_short_writes_complete_a_healthy_renewable_fenced_lease(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "target"
            lock = load_lock(root)
            write = lock.os.write
            with mock.patch.object(lock.os, "write", side_effect=lambda fd, data: write(fd, data[:7])) as writes:
                token = lock.acquire(str(target), wait=0)
            self.assertGreater(writes.call_count, 1)
            self.assertEqual(json.loads(Path(lock.lock_path(str(target))).read_text())["token"], token)
            self.assertTrue(lock.renew(str(target), token=token))
            with lock.fenced(str(target), token):
                target.write_text("healthy publication")
            self.assertEqual(target.read_text(), "healthy publication")
            self.assertFalse(lock.acquire(str(target), agent="contender", wait=0))
            self.assertTrue(lock.release(str(target), token=token))

    def test_write_error_never_unlinks_a_replacement_object(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "target"
            lock = load_lock(root)
            path = Path(lock.lock_path(str(target)))
            replacement = json.dumps({"token": "replacement-owner", "agent": "other"}).encode()

            def replaced(fd, data):
                # Controlled replacement is a negative control for identity;
                # cooperative owners normally serialize on this same guard.
                other = root / "replacement"
                other.write_bytes(replacement)
                os.replace(str(other), str(path))
                raise OSError(errno.EIO, "fixture replacement")

            with mock.patch.object(lock.os, "write", side_effect=replaced):
                with self.assertRaises(OSError):
                    lock.acquire(str(target), wait=0)
            self.assertEqual(path.read_bytes(), replacement)
            self.assertNotIn(str(path), lock._HELD_TOKENS)
            self.assertFalse(lock.acquire(str(target), wait=0))
            self.assertTrue(lock.release(str(target), token="replacement-owner"))


if __name__ == "__main__":
    unittest.main()
