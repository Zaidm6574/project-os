"""Regression tests for bb_lock lease ownership and concurrent expiry."""

import fcntl
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "bb_lock.py"


def run_lock(*args, env):
    merged = dict(os.environ)
    merged.update(env)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)],
        capture_output=True,
        text=True,
        env=merged,
        cwd=ROOT,
        timeout=10,
    )


def popen_lock(*args, env, **kwargs):
    merged = dict(os.environ)
    merged.update(env)
    return subprocess.Popen(
        [sys.executable, str(SCRIPT), *map(str, args)],
        env=merged,
        cwd=ROOT,
        **kwargs,
    )


def wait_for_lock(lock_dir, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        locks = list(Path(lock_dir).glob("*.lock"))
        if locks:
            return locks[0]
        time.sleep(0.01)
    raise AssertionError("lockfile was not created")


class TestBBLockHardening(unittest.TestCase):
    def test_run_renews_lease_while_long_command_is_alive(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "shared.md"
            env = {"BB_LOCK_DIR": str(Path(td) / "locks"), "BB_LOCK_STALE": "0.15"}
            holder = popen_lock(
                "run",
                target,
                "--agent",
                "slow-holder",
                "--wait",
                "1",
                "--",
                sys.executable,
                "-c",
                "import time; time.sleep(0.8)",
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                wait_for_lock(env["BB_LOCK_DIR"])
                time.sleep(0.30)  # twice the TTL: only renewal can keep it live
                contender = run_lock(
                    "acquire", target, "--agent", "contender", "--wait", "0.1", env=env
                )
                self.assertEqual(
                    contender.returncode,
                    1,
                    "a live long-running holder was incorrectly reaped: "
                    + contender.stderr,
                )
            finally:
                out, err = holder.communicate(timeout=3)
            self.assertEqual(holder.returncode, 0, out + err)

    def test_agent_label_and_force_cannot_release_fenced_lock(self):
        # the acquisition token is the ONLY proof of ownership: a reusable
        # agent label must not release someone else's lease, and --force
        # must not bypass the fence either (2026-07-17)
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "shared.md"
            env = {"BB_LOCK_DIR": str(Path(td) / "locks")}
            r1 = run_lock("acquire", target, "--agent", "plan", env=env)
            self.assertEqual(r1.returncode, 0, r1.stderr)
            token = r1.stdout.strip()
            self.assertTrue(token)
            by_label = run_lock("release", target, "--agent", "plan", env=env)
            self.assertNotEqual(by_label.returncode, 0)
            self.assertIn("token required", by_label.stderr)
            by_force = run_lock("release", target, "--agent", "plan",
                                "--force", env=env)
            self.assertNotEqual(by_force.returncode, 0)
            by_token = run_lock("release", target, "--token", token, env=env)
            self.assertEqual(by_token.returncode, 0, by_token.stderr)

    def test_lost_lease_terminates_running_command(self):
        # a holder whose lease was reaped and re-acquired must stop its
        # child command instead of running alongside the new owner
        # (2026-07-17)
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "shared.md"
            lock_dir = Path(td) / "locks"
            env = {"BB_LOCK_DIR": str(lock_dir), "BB_LOCK_STALE": "0.15"}
            holder = popen_lock(
                "run", target, "--agent", "old", "--wait", "1", "--",
                sys.executable, "-c", "import time; time.sleep(30)",
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            stopped = False
            try:
                wait_for_lock(lock_dir)
                os.kill(holder.pid, signal.SIGSTOP)
                stopped = True
                time.sleep(0.4)  # let the suspended holder's lease expire
                thief = run_lock("acquire", target, "--agent", "new",
                                 "--wait", "2", env=env)
                self.assertEqual(thief.returncode, 0, thief.stderr)
                os.kill(holder.pid, signal.SIGCONT)
                stopped = False
                out, err = holder.communicate(timeout=10)
                self.assertEqual(holder.returncode, 1, out + err)
                self.assertIn("lease lost", err)
            finally:
                if stopped:
                    os.kill(holder.pid, signal.SIGCONT)
                if holder.poll() is None:
                    holder.kill()
                    holder.communicate()

    def test_suspended_old_holder_cannot_release_replacement_lock(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "plan.json"
            lock_dir = Path(td) / "locks"
            env = {"BB_LOCK_DIR": str(lock_dir), "BB_LOCK_STALE": "0.12"}
            helper = (
                "import sys,time; "
                f"sys.path.insert(0, {str(SCRIPT.parent)!r}); "
                "import bb_lock; "
                f"target={str(target)!r}; "
                "token=bb_lock.acquire(target, agent='plan', wait=1); "
                "print('READY', flush=True); "
                "time.sleep(0.2); "
                "released=bb_lock.release(target, agent='plan', force=True); "
                "print(f'RELEASED={released}', flush=True)"
            )
            merged = dict(os.environ)
            merged.update(env)
            old_holder = subprocess.Popen(
                [sys.executable, "-c", helper],
                env=merged,
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            stopped = False
            try:
                self.assertEqual(old_holder.stdout.readline().strip(), "READY")
                os.kill(old_holder.pid, signal.SIGSTOP)
                stopped = True
                time.sleep(0.25)
                replacement = run_lock(
                    "acquire", target, "--agent", "plan", "--wait", "1", env=env
                )
                self.assertEqual(replacement.returncode, 0, replacement.stderr)
                lockfile = wait_for_lock(lock_dir)
                replacement_info = json.loads(lockfile.read_text(encoding="utf-8"))

                os.kill(old_holder.pid, signal.SIGCONT)
                stopped = False
                out, err = old_holder.communicate(timeout=3)
                self.assertEqual(old_holder.returncode, 0, out + err)

                self.assertTrue(lockfile.exists(), "old holder deleted the replacement lease")
                current_info = json.loads(lockfile.read_text(encoding="utf-8"))
                self.assertEqual(current_info.get("token"), replacement_info.get("token"))
            finally:
                if old_holder.poll() is None:
                    if stopped:
                        os.kill(old_holder.pid, signal.SIGCONT)
                    old_holder.kill()
                    old_holder.communicate(timeout=3)

    def test_cli_tokens_are_unique_and_required_token_is_owner_checked(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "shared.md"
            env = {"BB_LOCK_DIR": str(Path(td) / "locks")}

            first = run_lock("acquire", target, "--agent", "cli", env=env)
            self.assertEqual(first.returncode, 0, first.stderr)
            first_token = first.stdout.strip()
            self.assertRegex(first_token, r"^[0-9a-f]{32}$")
            lockfile = wait_for_lock(env["BB_LOCK_DIR"])
            self.assertEqual(json.loads(lockfile.read_text())["token"], first_token)

            wrong = run_lock(
                "release", target, "--agent", "cli", "--token", "0" * 32, "--force", env=env
            )
            self.assertEqual(wrong.returncode, 1)
            self.assertTrue(lockfile.exists(), "force bypassed an explicit ownership token")

            forced_without_token = run_lock(
                "release", target, "--agent", "cli", "--force", env=env
            )
            self.assertEqual(forced_without_token.returncode, 1)
            self.assertTrue(lockfile.exists(), "force bypassed fencing-token ownership")

            released = run_lock(
                "release", target, "--agent", "cli", "--token", first_token, env=env
            )
            self.assertEqual(released.returncode, 0, released.stderr)
            second = run_lock("acquire", target, "--agent", "cli", env=env)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertRegex(second.stdout.strip(), r"^[0-9a-f]{32}$")
            self.assertNotEqual(second.stdout.strip(), first_token)

    def test_reaper_serializes_with_stable_guard_before_unlink(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "shared.md"
            lock_dir = Path(td) / "locks"
            env = {"BB_LOCK_DIR": str(lock_dir), "BB_LOCK_STALE": "0.05"}
            acquired = run_lock("acquire", target, "--agent", "gone", env=env)
            self.assertEqual(acquired.returncode, 0, acquired.stderr)
            lockfile = wait_for_lock(lock_dir)
            time.sleep(0.10)

            guard_path = Path(str(lockfile) + ".guard")
            guard_path.touch(exist_ok=True)
            with guard_path.open("r+") as guard:
                fcntl.flock(guard.fileno(), fcntl.LOCK_EX)
                reaper = popen_lock(
                    "reap",
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                try:
                    time.sleep(0.12)
                    self.assertIsNone(
                        reaper.poll(),
                        "reaper mutated the lease without taking its serialization guard",
                    )
                finally:
                    fcntl.flock(guard.fileno(), fcntl.LOCK_UN)
                    out, err = reaper.communicate(timeout=3)
            self.assertEqual(reaper.returncode, 0, out + err)
            self.assertIn("reaped 1 stale lock", out)

    def test_missing_flag_values_and_invalid_wait_are_usage_errors(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "shared.md"
            env = {"BB_LOCK_DIR": str(Path(td) / "locks")}
            cases = (
                ("missing agent", ("acquire", target, "--agent")),
                ("missing agent before flag", ("acquire", target, "--agent", "--wait", "1")),
                ("missing wait", ("acquire", target, "--wait")),
                ("invalid wait", ("acquire", target, "--wait", "not-a-number")),
                ("non-finite wait", ("acquire", target, "--wait", "nan")),
                ("negative wait", ("acquire", target, "--wait", "-1")),
            )
            for label, args in cases:
                with self.subTest(label=label):
                    result = run_lock(*args, env=env)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertNotIn("Traceback", result.stderr)
                    self.assertIn("usage", result.stderr.lower())

    def test_line_value_starting_with_dashes_is_appended(self):
        # '--- separator ---' is a legitimate line, not a missing value:
        # only actual known flag names may be rejected as values
        # (audit finding F3, 2026-07-17)
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "log.md"
            env = {"BB_LOCK_DIR": str(Path(td) / "locks")}
            r = run_lock("append", target, "--agent", "sep",
                         "--line", "--- separator ---", env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(target.read_text(), "--- separator ---\n")

    def test_concurrent_append_processes_preserve_every_line(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "events.jsonl"
            env = {"BB_LOCK_DIR": str(Path(td) / "locks")}
            processes = [
                popen_lock(
                    "append",
                    target,
                    "--agent",
                    f"writer-{i}",
                    "--line",
                    f"line-{i}",
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                for i in range(8)
            ]
            outputs = [process.communicate(timeout=5) for process in processes]
            for process, (out, err) in zip(processes, outputs):
                self.assertEqual(process.returncode, 0, out + err)
            self.assertCountEqual(target.read_text().splitlines(), [f"line-{i}" for i in range(8)])


if __name__ == "__main__":
    unittest.main()
