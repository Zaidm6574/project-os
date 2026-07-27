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


# A child that announces its own pid before blocking. Waiting on this marker
# is an OBSERVABLE proof that `run` finished acquire() (guard released, lease
# fully written) and reached _run_with_renewal — unlike the lockfile appearing,
# which happens mid-critical-section inside acquire().
CHILD_ANNOUNCE = (
    "import os, pathlib, time; "
    "pathlib.Path({marker!r}).write_text(str(os.getpid())); "
    "{body}"
)


def wait_for_child_pid(marker, timeout=15):
    """Block until the child has published a complete pid, and return it."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            text = Path(marker).read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            text = ""
        if text.isdigit():
            return int(text)
        time.sleep(0.01)
    raise AssertionError("child command never announced its pid")


def revoke_lease(lockfile, agent="new"):
    """Put the lease into the exact state a reap+re-acquire leaves behind.

    Done while holding the SAME ``<lock>.guard`` flock bb_lock serializes every
    transition with, so no renew() can interleave with the rewrite. Returns the
    new owner's fencing token. This is a state transition, not a sleep: once it
    returns, every renew() by the previous owner must fail forever, so the
    outcome under test cannot depend on scheduling.
    """
    lockfile = Path(lockfile)
    stolen = "b" * 32
    guard = Path(str(lockfile) + ".guard")
    with guard.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            lockfile.write_text(
                json.dumps({
                    "path": str(lockfile),
                    "agent": agent,
                    "pid": os.getpid(),
                    "ts": time.time(),
                    "token": stolen,
                }),
                encoding="utf-8",
            )
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return stolen


def pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_until_dead(pid, timeout=30):
    """Poll until pid is gone. Generous bound: only the outcome is asserted."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.02)
    return False


def kill_pid(pid):
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, TypeError):
        pass


class TestBBLockHardening(unittest.TestCase):
    def test_run_renews_lease_while_long_command_is_alive(self):
        """A live holder must not be reapable -- proven by OBSERVING a renewal.

        2026-07-27: this slept 0.30s against a 0.15s TTL and assumed the renewal
        thread had run. Under load it sometimes had not, the lease went
        legitimately stale, the contender stole it, and the test failed on
        healthy code -- caught on a cold clone at 353s wall-clock where the same
        suite passes at 219s. Sleeping longer would only make the lie rarer; the
        assertion has to stop depending on the scheduler.

        The lease is renewed with os.utime, so a renewal is directly observable:
        wait until the lockfile's mtime ADVANCES past the value acquire() wrote.
        Once that is seen, renewal is demonstrably working and the contender must
        be refused -- unless renewals stop, which is the actual defect. The TTL
        is also widened from 150ms to 600ms, so the polling loop is not racing
        the very timer it is trying to observe.
        """
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "shared.md"
            env = {"BB_LOCK_DIR": str(Path(td) / "locks"), "BB_LOCK_STALE": "0.6"}
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
                "import time; time.sleep(6)",
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                wait_for_lock(env["BB_LOCK_DIR"])
                lockfiles = list(Path(env["BB_LOCK_DIR"]).glob("*.lock"))
                self.assertEqual(len(lockfiles), 1, "expected exactly one lockfile")
                lockfile = lockfiles[0]
                first = lockfile.stat().st_mtime

                # Wait for a renewal to actually land. Generous bound: this is a
                # timeout for a hung renewer, not a timing assumption.
                deadline = time.monotonic() + 20.0
                renewed = False
                reaped = False
                while time.monotonic() < deadline:
                    try:
                        if lockfile.stat().st_mtime > first:
                            renewed = True
                            break
                    except FileNotFoundError:
                        # The lease expired and something reaped it while its
                        # command was still running -- exactly the defect this
                        # test exists to catch. Report it as an assertion rather
                        # than letting stat() raise an opaque FileNotFoundError.
                        reaped = True
                        break
                    time.sleep(0.02)
                self.assertFalse(
                    reaped,
                    "the holder's lockfile was reaped while its command was still "
                    "running -- the lease was not being renewed",
                )
                self.assertTrue(
                    renewed,
                    "the holder never renewed its lease: mtime stayed at %r for 20s "
                    "while its command was still running" % first,
                )

                contender = run_lock(
                    "acquire", target, "--agent", "contender", "--wait", "0.1", env=env
                )
                self.assertEqual(
                    contender.returncode,
                    1,
                    "a live long-running holder was incorrectly reaped even though "
                    "its lease had just been observed renewing: " + contender.stderr,
                )
            finally:
                holder.kill()
                out, err = holder.communicate(timeout=10)

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
        # A holder whose lease was reaped and re-acquired must stop its child
        # command instead of running alongside the new owner (2026-07-17).
        #
        # Deterministic by construction (2026-07-27). The previous version
        # SIGSTOPped the holder and slept past the TTL so a real second
        # acquire could steal the lease. That was timing-dependent in a way no
        # longer sleep could fix: `run` takes the <lock>.guard flock inside
        # acquire() AND again on every renewal tick, so SIGSTOP regularly
        # froze the holder while it OWNED the guard -- the thief could then
        # never take the guard, timed out against --wait, and the test failed
        # with "FAILED: locked by ? (pid ?)". Measured 1/40 under load (a
        # direct probe caught the stopped holder owning the guard in 2 of ~30
        # runs), and it had already turned one real mutation escape into a
        # false "caught".
        #
        # So the transition is now DRIVEN, not awaited: synchronise on the
        # child's own pid marker (proof acquire() finished), then revoke the
        # lease under the guard. The end-to-end reap+steal path stays covered
        # by test_stress_reap_and_steal_terminates_running_command below.
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "shared.md"
            lock_dir = Path(td) / "locks"
            marker = Path(td) / "child.pid"
            env = {"BB_LOCK_DIR": str(lock_dir), "BB_LOCK_STALE": "0.3"}
            holder = popen_lock(
                "run", target, "--agent", "old", "--wait", "5", "--",
                sys.executable, "-c",
                CHILD_ANNOUNCE.format(marker=str(marker),
                                      body="time.sleep(300)"),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            child_pid = None
            try:
                lockfile = wait_for_lock(lock_dir)
                child_pid = wait_for_child_pid(marker)
                stolen = revoke_lease(lockfile)
                # Checked BEFORE the holder's exit status, and checked on the
                # child's real pid: a holder that merely reports "lease lost"
                # while its child keeps running is the defect, not the fix.
                self.assertTrue(
                    wait_until_dead(child_pid),
                    "child command kept running alongside the new owner after "
                    "the lease was revoked (pid %s)" % child_pid,
                )
                out, err = holder.communicate(timeout=30)
                self.assertEqual(holder.returncode, 1, out + err)
                self.assertIn("lease lost", err)
                # and the dispossessed holder must not delete the new owner's
                # lease on its way out
                self.assertTrue(lockfile.exists(),
                                "holder deleted the new owner's lease")
                self.assertEqual(
                    json.loads(lockfile.read_text(encoding="utf-8")).get("token"),
                    stolen,
                )
            finally:
                # the child inherits the holder's pipes, so it has to die too
                # or communicate() would block on a still-open stdout
                kill_pid(child_pid)
                if holder.poll() is None:
                    holder.kill()
                    holder.communicate(timeout=10)

    def test_live_lease_lets_command_run_to_completion(self):
        # mirror direction: renewal must NOT kill a child whose lease is
        # healthy, and `run` must propagate the child's real exit status.
        # Without this, "terminate on lost lease" could be satisfied by
        # terminating unconditionally.
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "shared.md"
            lock_dir = Path(td) / "locks"
            marker = Path(td) / "child.pid"
            done = Path(td) / "child.done"
            env = {"BB_LOCK_DIR": str(lock_dir), "BB_LOCK_STALE": "0.3"}
            body = (
                "time.sleep(0.9); "
                f"pathlib.Path({str(done)!r}).write_text('done'); "
                "raise SystemExit(7)"
            )
            holder = popen_lock(
                "run", target, "--agent", "steady", "--wait", "5", "--",
                sys.executable, "-c",
                CHILD_ANNOUNCE.format(marker=str(marker), body=body),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            child_pid = None
            out = err = ""
            timed_out = False
            try:
                wait_for_lock(lock_dir)
                child_pid = wait_for_child_pid(marker)
                try:
                    out, err = holder.communicate(timeout=60)
                except subprocess.TimeoutExpired:
                    timed_out = True
            finally:
                if holder.poll() is None:
                    holder.kill()
                    kill_pid(child_pid)
                    holder.communicate(timeout=10)
            self.assertFalse(timed_out, "holder never finished its child command")
            # the child outlives several renewal ticks (TTL 0.3s => renew
            # every 0.1s), so only working renewal gets it to the finish line
            self.assertNotIn("lease lost", err)
            self.assertTrue(done.exists(), "child was killed mid-run: " + out + err)
            self.assertEqual(holder.returncode, 7, out + err)

    @unittest.skipUnless(
        os.environ.get("BB_LOCK_STRESS") == "1",
        "opt-in stress probe: SIGSTOP can freeze the holder while it owns the "
        "lock guard, which wedges the thief's acquire for the whole --wait "
        "window (~2-6% under load). Deterministic coverage of the same "
        "behaviour lives in test_lost_lease_terminates_running_command; run "
        "with BB_LOCK_STRESS=1 to exercise the real reap+re-acquire path.",
    )
    def test_stress_reap_and_steal_terminates_running_command(self):
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
