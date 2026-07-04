#!/usr/bin/env python3
"""bb_lock — multi-writer file locking for Project OS blackboards + the shared brain.

Pattern lifted from claude-obsidian's wiki-lock.sh (verified in-code 3-0, July 2026
radar run): atomic O_CREAT|O_EXCL lockfiles keyed by SHA1(realpath), stale locks
auto-reaped after 60s. Zero-dependency, same-user cross-process — safe for
concurrent agent waves and Claude/Codex both appending to shared-brain.jsonl.

Usage:
  python3 scripts/bb_lock.py acquire <path> [--agent ID] [--wait SECS]
  python3 scripts/bb_lock.py release <path> [--agent ID] [--force]
  python3 scripts/bb_lock.py append  <path> --line '<text>' [--agent ID]
  python3 scripts/bb_lock.py run     <path> [--agent ID] -- <cmd> [args...]
  python3 scripts/bb_lock.py status  [<path>]
  python3 scripts/bb_lock.py reap

Env: BB_LOCK_DIR (default ~/.project-os/locks), BB_LOCK_STALE (default 60 seconds)

Exit codes: 0 ok · 1 could not acquire / not held · 2 usage error
"""
import os, sys, json, time, hashlib, subprocess

LOCK_DIR = os.environ.get("BB_LOCK_DIR", os.path.expanduser("~/.project-os/locks"))
STALE_AFTER_SEC = float(os.environ.get("BB_LOCK_STALE", "60"))
POLL_SEC = 0.25


def _key(target):
    return hashlib.sha1(os.path.realpath(target).encode()).hexdigest()


def lock_path(target):
    return os.path.join(LOCK_DIR, _key(target) + ".lock")


def read_lock(lp):
    try:
        with open(lp, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def is_stale(lp):
    try:
        return (time.time() - os.stat(lp).st_mtime) > STALE_AFTER_SEC
    except FileNotFoundError:
        return False


def reap_one(lp):
    """Remove a lockfile only if stale. Re-stats immediately before unlink so a
    lock released-and-reacquired by a fresh holder between the staleness check
    and the unlink isn't reaped (narrows the TOCTOU window; with a 60s TTL and
    0.25s polls the residual window is microseconds). Returns True if removed."""
    try:
        st = os.stat(lp)
    except FileNotFoundError:
        return False
    if (time.time() - st.st_mtime) <= STALE_AFTER_SEC:
        return False
    try:
        if os.stat(lp).st_mtime != st.st_mtime:
            return False  # replaced by a fresh holder mid-check
        os.unlink(lp)
        return True
    except FileNotFoundError:
        return True


def acquire(target, agent="unknown", wait=10.0):
    """Atomically acquire the lock for target. Returns True on success."""
    os.makedirs(LOCK_DIR, exist_ok=True)
    lp = lock_path(target)
    deadline = time.time() + wait
    while True:
        reap_one(lp)
        try:
            fd = os.open(lp, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w") as f:
                json.dump({"path": os.path.realpath(target), "agent": agent,
                           "pid": os.getpid(), "ts": time.time()}, f)
            return True
        except FileExistsError:
            if time.time() >= deadline:
                return False
            time.sleep(POLL_SEC)


def release(target, agent=None, force=False):
    """Release the lock. Owner-checked unless --force. Returns True if released."""
    lp = lock_path(target)
    info = read_lock(lp)
    if info is None:
        return False
    if not force and agent is not None and info.get("agent") not in (agent, "unknown"):
        print(f"held by {info.get('agent')} (pid {info.get('pid')}); use --force to override",
              file=sys.stderr)
        return False
    try:
        os.unlink(lp)
        return True
    except FileNotFoundError:
        return False


def _flag(args, name, default=None):
    if name in args:
        i = args.index(name)
        v = args[i + 1]
        del args[i:i + 2]
        return v
    return default


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(2)
    cmd, rest = args[0], args[1:]

    if cmd == "reap":
        n = 0
        if os.path.isdir(LOCK_DIR):
            for f in os.listdir(LOCK_DIR):
                if f.endswith(".lock") and reap_one(os.path.join(LOCK_DIR, f)):
                    n += 1
        print(f"reaped {n} stale lock(s)")
        sys.exit(0)

    if cmd == "status":
        target = rest[0] if rest else None
        found = 0
        if os.path.isdir(LOCK_DIR):
            for f in sorted(os.listdir(LOCK_DIR)):
                lp = os.path.join(LOCK_DIR, f)
                info = read_lock(lp)
                if not info:
                    continue
                if target and _key(target) + ".lock" != f:
                    continue
                age = time.time() - os.stat(lp).st_mtime
                found += 1
                print(f"{info.get('path')} — agent={info.get('agent')} pid={info.get('pid')} "
                      f"age={age:.0f}s{' STALE' if age > STALE_AFTER_SEC else ''}")
        if not found:
            print("no locks held" if not target else "not locked")
        sys.exit(0)

    agent = _flag(rest, "--agent", "unknown")
    force = "--force" in rest and (rest.remove("--force") or True)
    wait = float(_flag(rest, "--wait", "10"))
    line = _flag(rest, "--line")

    if not rest:
        print("missing <path>", file=sys.stderr)
        sys.exit(2)
    target = rest[0]

    if cmd == "acquire":
        ok = acquire(target, agent, wait)
        if not ok:
            info = read_lock(lock_path(target)) or {}
            print(f"FAILED: locked by {info.get('agent','?')} (pid {info.get('pid','?')})",
                  file=sys.stderr)
        sys.exit(0 if ok else 1)

    if cmd == "release":
        sys.exit(0 if release(target, agent, force) else 1)

    if cmd == "append":
        if line is None:
            line = sys.stdin.read().rstrip("\n")
        if not acquire(target, agent, wait):
            print("FAILED: could not acquire lock for append", file=sys.stderr)
            sys.exit(1)
        try:
            parent = os.path.dirname(os.path.abspath(target))
            os.makedirs(parent, exist_ok=True)
            with open(target, "a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
                os.fsync(f.fileno())
        finally:
            release(target, agent, force=True)
        sys.exit(0)

    if cmd == "run":
        if "--" not in rest:
            print("run requires: run <path> -- <cmd> [args...]", file=sys.stderr)
            sys.exit(2)
        sub = rest[rest.index("--") + 1:]
        if not acquire(target, agent, wait):
            print("FAILED: could not acquire lock", file=sys.stderr)
            sys.exit(1)
        try:
            rc = subprocess.call(sub)
        finally:
            release(target, agent, force=True)
        sys.exit(rc)

    print(f"unknown command: {cmd}", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
