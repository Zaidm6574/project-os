"""Behaviour regressions for bb_lock's release ownership guard and `--` path guard.

Both defects guarded here were fixed in b4cc70f but shipped without a test:

1. ``release()`` used to default ``agent=None``. The tokenless-legacy-lock
   ownership guard reads ``... and agent is not None and ...``, so any caller
   that omitted ``agent`` short-circuited the guard entirely and silently stole
   (deleted) another agent's lock.
2. ``main()`` only rejected an EMPTY argument list as a missing ``<path>``, so
   ``bb_lock <cmd> --`` keyed a real lock off the literal string ``"--"`` and,
   for ``run``, executed the sub-command while holding it.
3. That first fix changed only the DEFAULT, so passing ``agent=None``
   EXPLICITLY still hit the ``agent is not None`` clause and skipped the guard
   (fixed 2026-07-26 by normalizing an absent agent to "unknown").

These assert on observed behaviour (return value, lockfile on disk, exit code,
whether a child command ran), never on source text or --help output.
"""

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "bb_lock.py"


def load_bb_lock(lock_dir):
    """Load a private bb_lock module instance bound to lock_dir."""
    spec = importlib.util.spec_from_file_location("bb_lock_regression", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.LOCK_DIR = str(lock_dir)
    os.makedirs(str(lock_dir), exist_ok=True)
    return module


def write_tokenless_lock(module, target, agent):
    """Simulate a legacy (pre-fencing-token) lockfile held by `agent`."""
    lp = module.lock_path(target)
    with open(lp, "w", encoding="utf-8") as f:
        json.dump({"path": os.path.realpath(target), "agent": agent,
                   "pid": 12345}, f)
    return lp


def run_lock(*args, **kwargs):
    env = dict(os.environ)
    env.update(kwargs.pop("env", {}))
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + [str(a) for a in args],
        capture_output=True,
        text=True,
        env=env,
        input="",
        timeout=30,
        **kwargs
    )


class TestReleaseOwnershipGuard(unittest.TestCase):
    def test_release_with_omitted_agent_cannot_steal_a_tokenless_lock(self):
        with tempfile.TemporaryDirectory() as td:
            bb = load_bb_lock(Path(td) / "locks")
            target = Path(td) / "shared.md"
            target.write_text("", encoding="utf-8")
            lp = write_tokenless_lock(bb, str(target), "agentA")

            # The whole point: agent is OMITTED, exactly as a library caller
            # doing `bb_lock.release(path)` would write it.
            with contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertFalse(bb.release(str(target)))
            self.assertIn("held by agentA", err.getvalue())
            self.assertTrue(os.path.exists(lp),
                            "agentA's lock was deleted by a caller that never named itself")

            # Same refusal when another agent names itself explicitly.
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertFalse(bb.release(str(target), agent="agentB"))
            self.assertTrue(os.path.exists(lp))

            # Positive control: the rightful holder still gets its lock back,
            # so the guard is refusing impostors, not everybody.
            self.assertTrue(bb.release(str(target), agent="agentA"))
            self.assertFalse(os.path.exists(lp))

    def test_release_with_agent_explicitly_none_cannot_steal_a_tokenless_lock(self):
        """agent=None passed EXPLICITLY must be as untrusted as an omitted one.

        A library caller forwarding an optional variable (``release(p,
        agent=cfg.get("agent"))``) lands here. Defaulting agent to "unknown"
        alone did not close this: the guard also had an ``agent is not None``
        clause that skipped ownership checking outright for a None agent.
        """
        with tempfile.TemporaryDirectory() as td:
            bb = load_bb_lock(Path(td) / "locks")
            target = Path(td) / "shared.md"
            target.write_text("", encoding="utf-8")
            lp = write_tokenless_lock(bb, str(target), "agentA")

            with contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertFalse(bb.release(str(target), agent=None))
            self.assertIn("held by agentA", err.getvalue())
            self.assertTrue(os.path.exists(lp),
                            "agentA's lock was deleted by an explicit agent=None caller")

            # Positive control: the rightful holder is still unaffected.
            self.assertTrue(bb.release(str(target), agent="agentA"))
            self.assertFalse(os.path.exists(lp))

    def test_anonymous_caller_cannot_match_an_anonymous_tokenless_lock(self):
        """A null agent label must never be treated as an identity that matches.

        Without normalizing an absent agent to "unknown", ``agent=None``
        compares equal to a lockfile whose ``agent`` field is JSON ``null``
        (written by e.g. ``acquire(p, agent=cfg.get("agent"))``), so one
        unidentified caller could tokenlessly delete another's lock. ``--force``
        and stale reaping remain the escape hatches, so nothing is wedged.
        """
        with tempfile.TemporaryDirectory() as td:
            bb = load_bb_lock(Path(td) / "locks")
            target = Path(td) / "shared.md"
            target.write_text("", encoding="utf-8")
            lp = write_tokenless_lock(bb, str(target), None)

            with contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertFalse(bb.release(str(target), agent=None))
            self.assertIn("use --force", err.getvalue())
            self.assertTrue(os.path.exists(lp))

            with contextlib.redirect_stderr(io.StringIO()):
                self.assertFalse(bb.release(str(target)))
            self.assertTrue(os.path.exists(lp))

            # Not wedged: an operator can still take it with --force.
            self.assertTrue(bb.release(str(target), agent="ops", force=True))
            self.assertFalse(os.path.exists(lp))

    def test_release_with_omitted_agent_still_frees_an_unlabelled_lock(self):
        """A lock left by the acquire() default must not become unreleasable."""
        with tempfile.TemporaryDirectory() as td:
            bb = load_bb_lock(Path(td) / "locks")
            target = Path(td) / "shared.md"
            target.write_text("", encoding="utf-8")
            lp = write_tokenless_lock(bb, str(target), "unknown")
            self.assertTrue(bb.release(str(target)))
            self.assertFalse(os.path.exists(lp))

    def test_named_agent_may_still_free_an_unlabelled_tokenless_lock(self):
        """The legacy allowance covers NAMED callers too, not just omitted ones.

        Tightening the guard to ``info.get("agent") != agent`` would strand
        every pre-fencing-token lock written under acquire()'s "unknown"
        default behind an agent label nobody can reproduce. The omitted-agent
        control above cannot catch that regression, because an omitted agent
        already equals "unknown".
        """
        with tempfile.TemporaryDirectory() as td:
            bb = load_bb_lock(Path(td) / "locks")
            target = Path(td) / "shared.md"
            target.write_text("", encoding="utf-8")
            lp = write_tokenless_lock(bb, str(target), "unknown")
            with contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertTrue(bb.release(str(target), agent="agentB"))
            self.assertFalse(os.path.exists(lp))
            self.assertEqual(err.getvalue(), "")


class TestSeparatorIsNotAPath(unittest.TestCase):
    def test_bare_separator_is_a_usage_error_for_every_locking_command(self):
        for cmd in ("acquire", "release", "append", "run"):
            with tempfile.TemporaryDirectory() as td:
                lock_dir = Path(td) / "locks"
                proc = run_lock(cmd, "--", cwd=td,
                                env={"BB_LOCK_DIR": str(lock_dir)})
                self.assertEqual(
                    proc.returncode, 2,
                    "{0} -- exited {1}: {2}{3}".format(
                        cmd, proc.returncode, proc.stdout, proc.stderr))
                self.assertIn("missing <path>", proc.stderr)
                self.assertEqual(
                    sorted(p.name for p in lock_dir.glob("*.lock")), [],
                    "{0} -- created a lock keyed off the literal separator".format(cmd))
                self.assertFalse(
                    (Path(td) / "--").exists(),
                    "{0} -- created a file literally named '--'".format(cmd))

    def test_run_without_a_path_does_not_execute_the_command(self):
        with tempfile.TemporaryDirectory() as td:
            lock_dir = Path(td) / "locks"
            sentinel = Path(td) / "ran.txt"
            proc = run_lock(
                "run", "--", sys.executable, "-c",
                "open({0!r}, 'w').close()".format(str(sentinel)),
                cwd=td, env={"BB_LOCK_DIR": str(lock_dir)})
            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
            self.assertFalse(sentinel.exists(),
                             "sub-command ran under a lock keyed off '--'")
            self.assertEqual(sorted(p.name for p in lock_dir.glob("*.lock")), [])


if __name__ == "__main__":
    unittest.main()
