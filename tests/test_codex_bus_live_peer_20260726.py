#!/usr/bin/env python3
"""Regression tests for the two b4cc70f hunks in scripts/codex_bus.py.

Both hunks were flagged in the 2026-07-26 handoff as "never reached, so the
surface is honestly unknown" -- written on the assumption they needed a live
websocket peer. Neither actually does:

  * Hunk A (cmd_serve): after Popen, poll ~2s so a server that crashes on
    startup (e.g. port already in use) is reported as RC=1 with the new log
    tail on stderr, instead of a silent RC=0 "server starting". This is a
    pure subprocess seam: it is driven here through the REAL CLI
    (`codex_bus.py serve`) with HOME pointed at a sandbox that contains the
    plugin directory layout plugin_root() actually globs for. The child
    server script is a stand-in for "any bin/server.py that exits fast" --
    the hunk's contract is about process exit behaviour, not server content.

  * Hunk B (_hello): the welcome recv() is wrapped in a 5s asyncio.wait_for
    so a server that accepts the connection but never replies produces
    sys.exit("hello timed out ...") instead of hanging list/send/listen
    forever. _hello only touches ws.send/ws.recv, so an async peer object
    that never answers drives the exact guarded condition -- no websockets
    library needed (the CI floor has none).

CAUTION encoded below: importing codex_bus without INTER_SESSION_NO_REEXEC=1
set would os.execv the test process into the plugin venv (which exists on
dev machines). The env var MUST be set before the import.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# MUST happen before importing codex_bus: the module re-execs itself under the
# plugin venv at import time when argv[1] is not setup/serve and the venv
# exists. Without this guard the unittest process is replaced wholesale.
os.environ["INTER_SESSION_NO_REEXEC"] = "1"

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import codex_bus  # noqa: E402  (env guard above is load-bearing)


# The exact directory layout plugin_root() globs for (its first pattern),
# relative to HOME. shared.py only has to exist -- plugin_root() checks
# is_file(), nothing imports it on the serve path.
_PLUGIN_REL = Path(".claude/plugins/marketplaces/inter-session/skills/inter-session")

_CRASH_SENTINEL = "SENTINEL_BIND_FAILURE_20260726"
_CRASH_SERVER = (
    "import sys\n"
    f"sys.stderr.write('{_CRASH_SENTINEL}: address already in use\\n')\n"
    "sys.exit(7)\n"
)
# Outlives cmd_serve's ~2s poll window, then exits on its own (it is spawned
# start_new_session=True, so the test cannot reap it -- keep it short-lived).
_SLOW_SERVER = "import time\ntime.sleep(4)\n"


class ServeEarlyCrashTest(unittest.TestCase):
    """Hunk A: cmd_serve must notice a fast-crashing server, not report success."""

    def _run_serve(self, server_body: str, pre_log: str = "") -> subprocess.CompletedProcess:
        home = Path(tempfile.mkdtemp(prefix="codex-bus-home-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(home, ignore_errors=True))
        bindir = home / _PLUGIN_REL / "bin"
        bindir.mkdir(parents=True)
        (bindir / "shared.py").write_text("# existence probe only\n")
        (bindir / "server.py").write_text(server_body)
        if pre_log:
            logdir = home / ".claude" / "data" / "inter-session"
            logdir.mkdir(parents=True)
            (logdir / "server.log").write_text(pre_log)
        env = dict(os.environ)
        env["HOME"] = str(home)
        env["INTER_SESSION_NO_REEXEC"] = "1"
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "codex_bus.py"), "serve"],
            capture_output=True, text=True, env=env, timeout=30,
        )

    def test_fast_crash_is_reported_as_failure_with_log_tail(self):
        r = self._run_serve(_CRASH_SERVER, pre_log="OLD_LOG_LINE_MUST_NOT_REAPPEAR\n")
        self.assertEqual(
            r.returncode, 1,
            "a server that died within the poll window must yield RC=1, "
            f"got RC={r.returncode}; stdout={r.stdout!r} stderr={r.stderr!r}",
        )
        self.assertIn("server failed to start (exit 7)", r.stderr)
        # The NEW log tail (the crash reason) must be surfaced ...
        self.assertIn(_CRASH_SENTINEL, r.stderr)
        # ... but only text appended after this launch (log_pos seek), never
        # stale content from a previous run.
        self.assertNotIn("OLD_LOG_LINE_MUST_NOT_REAPPEAR", r.stderr)
        self.assertNotIn("server starting", r.stdout)

    def test_surviving_server_still_reports_success(self):
        # Control: the fix must not break the healthy path.
        r = self._run_serve(_SLOW_SERVER)
        self.assertEqual(
            r.returncode, 0,
            f"healthy server must keep RC=0; stderr={r.stderr!r}",
        )
        self.assertIn("server starting (pid", r.stdout)


class _SilentPeer:
    """A peer that completes the send but never answers -- the exact condition
    hunk B guards (server accepted the hello, never replied)."""

    def __init__(self):
        self.sent = []

    async def send(self, data):
        self.sent.append(data)

    async def recv(self):
        await asyncio.Event().wait()  # never set: resolves only via cancellation


class _ReplyPeer:
    def __init__(self, frame: dict):
        self._frame = frame
        self.sent = []

    async def send(self, data):
        self.sent.append(data)

    async def recv(self):
        return json.dumps(self._frame)


def _drive_hello(ws, name: str, token: str):
    """Run _hello with a hard 8s ceiling; fold every outcome into a tuple.

    SystemExit is caught INSIDE the same coroutine stack as _hello (before any
    Task boundary): if it reached Task.__step it would be re-raised straight
    into the event loop and logged as an unretrieved task exception.

    Outcomes:
      ("exit", message)   _hello called sys.exit (timeout guard or rejection)
      ("ok",   welcome)   _hello returned a welcome frame
      ("hang", note)      _hello neither returned nor exited within 8s --
                          i.e. the b4cc70f timeout guard is GONE; the outer
                          wait_for is what keeps a reverted build from wedging
                          the whole suite.
    """
    async def shielded():
        try:
            return ("ok", await codex_bus._hello(ws, name, token))
        except SystemExit as e:
            return ("exit", str(e.code))

    async def ceiling():
        return await asyncio.wait_for(shielded(), timeout=8)

    try:
        return asyncio.run(ceiling())
    except asyncio.TimeoutError:
        return ("hang", "recv() never resolved and no timeout guard fired")


class HelloWelcomeTimeoutTest(unittest.TestCase):
    """Hunk B: _hello must time out on a mute server instead of hanging."""

    def test_mute_server_times_out_with_message(self):
        ws = _SilentPeer()
        outcome = _drive_hello(ws, "codex-test", "tok-test")
        self.assertEqual(
            "exit", outcome[0],
            f"expected _hello to sys.exit on a mute server, got {outcome!r}",
        )
        self.assertIn("hello timed out", outcome[1])
        # The module really got as far as sending its hello frame -- the real
        # protocol payload, produced by the code under test.
        self.assertEqual(len(ws.sent), 1)
        hello = json.loads(ws.sent[0])
        self.assertEqual(hello.get("op"), "hello")
        self.assertEqual(hello.get("token"), "tok-test")
        self.assertEqual(hello.get("role"), "agent")

    def test_prompt_welcome_still_returned(self):
        # Control: a server that answers promptly must be unaffected.
        ws = _ReplyPeer({"op": "welcome", "sessions": []})
        outcome = _drive_hello(ws, "codex-test", "tok")
        self.assertEqual("ok", outcome[0], f"got {outcome!r}")
        self.assertEqual(outcome[1].get("op"), "welcome")

    def test_error_welcome_still_rejects(self):
        # Control: the pre-existing error branch after the guarded recv.
        ws = _ReplyPeer({"op": "error", "code": "bad_token", "message": "nope"})
        outcome = _drive_hello(ws, "codex-test", "tok")
        self.assertEqual("exit", outcome[0], f"got {outcome!r}")
        self.assertIn("hello rejected", outcome[1])


if __name__ == "__main__":
    unittest.main()
