#!/usr/bin/env python3
"""codex_bus — Codex-side peer client for the inter-session bus (Claude<->Codex).

The inter-session plugin (claude-code-inter-session) gives Claude Code sessions a
localhost WebSocket bus (127.0.0.1:9473, bearer token, 0600). Codex has no client —
this is that client. It imports the PLUGIN'S OWN modules (shared/server constants),
so the protocol stays correct when the plugin updates; nothing is reimplemented.

Commands:
  python3 scripts/codex_bus.py setup                  # create venv + install websockets (one-time)
  python3 scripts/codex_bus.py serve                  # start the bus server standalone (Codex-first)
  python3 scripts/codex_bus.py list                   # who's on the bus
  python3 scripts/codex_bus.py send --to NAME --text "..."   (--all to broadcast)
  python3 scripts/codex_bus.py listen [--name codex]  # join as a peer; incoming msgs -> stdout JSONL

Security note (docs/inter-session.md): the token is readable by any same-user
process. This is a convenience bus, NOT a security boundary.
"""
from __future__ import annotations

import os
import sys
import glob
import json
import time
import uuid
import subprocess
from pathlib import Path

# ---- locate the installed plugin (its bin/ is the protocol source of truth) ----
_PLUGIN_GLOBS = [
    str(Path.home() / ".claude" / "plugins" / "marketplaces" / "inter-session"
        / "skills" / "inter-session"),
    str(Path.home() / ".claude" / "plugins" / "*" / "inter-session*" / "skills" / "inter-session"),
]


def plugin_root() -> Path:
    for pat in _PLUGIN_GLOBS:
        for hit in sorted(glob.glob(pat)):
            if (Path(hit) / "bin" / "shared.py").is_file():
                return Path(hit)
    sys.exit("inter-session plugin not found — install it first:\n"
             "  claude plugin marketplace add https://github.com/yilunzhang/claude-code-inter-session\n"
             "  claude plugin install inter-session")


# ---- re-exec under the plugin's isolated venv (same pattern as the plugin's bin/) ----
_VENV = Path.home() / ".claude" / "data" / "inter-session" / "venv"
_VENV_PY = _VENV / "bin" / "python"
if (not os.environ.get("INTER_SESSION_NO_REEXEC")
        and sys.argv[1:2] not in (["setup"], ["serve"])
        and _VENV_PY.is_file()
        and Path(sys.prefix).resolve() != _VENV.resolve()):
    os.execv(str(_VENV_PY), [str(_VENV_PY), *sys.argv])


def cmd_setup() -> int:
    """Create the plugin's venv and install websockets (mirrors /inter-session install-deps)."""
    if _VENV_PY.is_file():
        r = subprocess.run([str(_VENV_PY), "-c", "import websockets"], capture_output=True)
        if r.returncode == 0:
            print(f"already set up: {_VENV}")
            return 0
    _VENV.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, "-m", "venv", str(_VENV)], check=True)
    subprocess.run([str(_VENV_PY), "-m", "pip", "install", "--quiet", "websockets"], check=True)
    print(f"venv ready: {_VENV} (websockets installed)")
    return 0


def cmd_serve() -> int:
    """Start the bus server standalone so Codex can bring the bus up without Claude."""
    root = plugin_root()
    py = str(_VENV_PY) if _VENV_PY.is_file() else sys.executable
    log = Path.home() / ".claude" / "data" / "inter-session" / "server.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a") as lf:
        p = subprocess.Popen([py, str(root / "bin" / "server.py")],
                             stdout=lf, stderr=lf, start_new_session=True)
    print(f"server starting (pid {p.pid}); log: {log}")
    print("note: it auto-exits after the configured idle window with no clients")
    return 0


# ---- bus client (agent role — a real peer, unlike the plugin's control-role send.py) ----
def _bus_setup():
    root = plugin_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        import websockets  # noqa: F401
    except ImportError:
        sys.exit("websockets missing — run: python3 scripts/codex_bus.py setup")
    from bin import shared  # the plugin's own constants/helpers
    tok_path = shared.token_path()
    if not tok_path.is_file():
        sys.exit(f"no bus token at {tok_path} — the server has never started.\n"
                 "Start it: python3 scripts/codex_bus.py serve  (or open a Claude session "
                 "with /inter-session)")
    token = tok_path.read_text().strip()
    host, port = "127.0.0.1", int(os.environ.get("INTER_SESSION_PORT", shared.DEFAULT_PORT))
    if not shared.verify_server_identity(host, port):
        sys.exit(f"server identity check failed — nothing on {host}:{port} that is bin/server.py. "
                 "Start it: python3 scripts/codex_bus.py serve")
    return shared, token, host, port


async def _hello(ws, name: str, token: str):
    await ws.send(json.dumps({
        "op": "hello", "session_id": str(uuid.uuid4()), "name": name,
        "label": "codex bridge", "cwd": os.getcwd(), "pid": os.getpid(),
        "role": "agent", "token": token,
    }))
    welcome = json.loads(await ws.recv())
    if welcome.get("op") == "error":
        sys.exit(f"hello rejected: {welcome.get('code')} {welcome.get('message', '')}")
    return welcome


async def _run_list(name: str) -> int:
    import asyncio, websockets
    shared, token, host, port = _bus_setup()
    async with websockets.connect(f"ws://{host}:{port}/", max_size=shared.WS_FRAME_CAP) as ws:
        await _hello(ws, name, token)
        await ws.send(json.dumps({"op": "list"}))
        while True:
            frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            if frame.get("op") == "list_ok":
                for s in frame.get("sessions", []):
                    print(json.dumps(s))
                break
        await ws.send(json.dumps({"op": "bye"}))
    return 0


async def _run_send(name: str, to: str | None, text: str, broadcast: bool) -> int:
    import asyncio, websockets
    shared, token, host, port = _bus_setup()
    async with websockets.connect(f"ws://{host}:{port}/", max_size=shared.WS_FRAME_CAP) as ws:
        await _hello(ws, name, token)
        payload = {"op": "broadcast", "text": text} if broadcast else \
                  {"op": "send", "to": to, "text": text}
        await ws.send(json.dumps(payload))
        try:  # success is silence; errors come back fast
            frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=1.0))
            if frame.get("op") == "error":
                print(f"error: {frame.get('code')}: {frame.get('message', '')}", file=sys.stderr)
                return 1
        except asyncio.TimeoutError:
            pass
        await ws.send(json.dumps({"op": "bye"}))
    print("sent")
    return 0


async def _run_listen(name: str) -> int:
    import asyncio, websockets
    shared, token, host, port = _bus_setup()
    async with websockets.connect(f"ws://{host}:{port}/", max_size=shared.WS_FRAME_CAP) as ws:
        await _hello(ws, name, token)
        print(json.dumps({"op": "listening", "name": name, "port": port}), flush=True)

        async def keepalive():
            while True:
                await asyncio.sleep(shared.PING_INTERVAL_S)
                await ws.send(json.dumps({"op": "ping"}))

        ka = asyncio.ensure_future(keepalive())
        try:
            async for raw in ws:
                frame = json.loads(raw)
                if frame.get("op") == "pong":
                    continue
                print(json.dumps(frame), flush=True)  # msg / peer_joined / peer_left ...
        finally:
            ka.cancel()
    return 0


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup")
    sub.add_parser("serve")
    p = sub.add_parser("list"); p.add_argument("--name", default="codex")
    p = sub.add_parser("send"); p.add_argument("--name", default="codex")
    p.add_argument("--to"); p.add_argument("--all", action="store_true")
    p.add_argument("--text", required=True)
    p = sub.add_parser("listen"); p.add_argument("--name", default="codex")
    a = ap.parse_args()

    if a.cmd == "setup":
        return cmd_setup()
    if a.cmd == "serve":
        return cmd_serve()
    import asyncio
    if a.cmd == "list":
        return asyncio.run(_run_list(a.name))
    if a.cmd == "send":
        if not a.all and not a.to:
            ap.error("--to required unless --all")
        return asyncio.run(_run_send(a.name + "-tx", a.to, a.text, a.all))
    if a.cmd == "listen":
        return asyncio.run(_run_listen(a.name))
    return 2


if __name__ == "__main__":
    sys.exit(main())
