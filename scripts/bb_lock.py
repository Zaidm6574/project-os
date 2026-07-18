#!/usr/bin/env python3
"""bb_lock — multi-writer file locking for Project OS blackboards + the shared brain.

Pattern lifted from claude-obsidian's wiki-lock.sh (behavior pinned by
tests/test_bb_lock_hardening.py): atomic O_CREAT|O_EXCL lockfiles keyed by
SHA1(realpath), stale locks
auto-reaped after 60s. Zero-dependency, same-user cross-process — safe for
concurrent agent waves and Claude/Codex both appending to shared-brain.jsonl.

Usage:
  python3 scripts/bb_lock.py acquire <path> [--agent ID] [--wait SECS]
  python3 scripts/bb_lock.py release <path> [--token TOKEN] [--agent ID] [--force]
  python3 scripts/bb_lock.py append  <path> --line '<text>' [--agent ID]
  python3 scripts/bb_lock.py run     <path> [--agent ID] -- <cmd> [args...]
  python3 scripts/bb_lock.py status  [<path>]
  python3 scripts/bb_lock.py reap

Env: BB_LOCK_DIR (default ~/.project-os/locks), BB_LOCK_STALE (default 60 seconds)

Exit codes: 0 ok · 1 could not acquire / not held · 2 usage error
"""
import os, sys, json, time, hashlib, subprocess, fcntl, math, uuid
from contextlib import contextmanager

LOCK_DIR = os.environ.get("BB_LOCK_DIR", os.path.expanduser("~/.project-os/locks"))
STALE_AFTER_SEC = float(os.environ.get("BB_LOCK_STALE", "60"))
POLL_SEC = 0.25
_HELD_TOKENS = {}


def _key(target):
    return hashlib.sha1(os.path.realpath(target).encode()).hexdigest()


def lock_path(target):
    return os.path.join(LOCK_DIR, _key(target) + ".lock")


@contextmanager
def _guard(lp):
    """Serialize all transitions for one stable lock pathname."""
    os.makedirs(LOCK_DIR, exist_ok=True)
    with open(lp + ".guard", "a+", encoding="utf-8") as guard:
        fcntl.flock(guard.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(guard.fileno(), fcntl.LOCK_UN)


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


def _reap_locked(lp):
    try:
        st = os.stat(lp)
    except FileNotFoundError:
        return False
    if (time.time() - st.st_mtime) <= STALE_AFTER_SEC:
        return False
    try:
        os.unlink(lp)
        return True
    except FileNotFoundError:
        return True


def reap_one(lp):
    """Remove a stale lock while holding its stable serialization guard."""
    with _guard(lp):
        return _reap_locked(lp)


def acquire(target, agent="unknown", wait=10.0):
    """Atomically acquire the lock for target. Returns its fencing token."""
    os.makedirs(LOCK_DIR, exist_ok=True)
    lp = lock_path(target)
    deadline = time.monotonic() + wait
    while True:
        with _guard(lp):
            _reap_locked(lp)
            try:
                fd = os.open(lp, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                token = uuid.uuid4().hex
                with os.fdopen(fd, "w") as f:
                    json.dump({"path": os.path.realpath(target), "agent": agent,
                               "pid": os.getpid(), "ts": time.time(),
                               "token": token}, f)
                _HELD_TOKENS[lp] = token
                return token
            except FileExistsError:
                pass
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(POLL_SEC, remaining))


def renew(target, token=None):
    """Renew a held lease only when its fencing token still owns the lock."""
    lp = lock_path(target)
    expected = token or _HELD_TOKENS.get(lp)
    if expected is None:
        return False
    with _guard(lp):
        info = read_lock(lp)
        if info is None or info.get("token") != expected:
            return False
        try:
            os.utime(lp, None)
            return True
        except FileNotFoundError:
            return False


def release(target, agent=None, force=False, token=None):
    """Release the lock only when its fencing token still owns the lease."""
    lp = lock_path(target)
    expected = token or _HELD_TOKENS.get(lp)
    with _guard(lp):
        info = read_lock(lp)
        if info is None:
            _HELD_TOKENS.pop(lp, None)
            return False
        current = info.get("token")
        # The token is the ONLY proof of ownership: agent labels are reusable
        # strings and must never bypass the fence, and --force must not
        # either — a wedged lock exits via stale reaping, not override
        # (independent review finding, 2026-07-17).
        if current is not None and expected != current:
            print(f"held by {info.get('agent')} (pid {info.get('pid')}); ownership token required",
                  file=sys.stderr)
            return False
        if current is None and not force and agent is not None \
                and info.get("agent") not in (agent, "unknown"):
            print(f"held by {info.get('agent')} (pid {info.get('pid')}); use --force to override",
                  file=sys.stderr)
            return False
        try:
            os.unlink(lp)
        except FileNotFoundError:
            return False
    if _HELD_TOKENS.get(lp) == expected:
        _HELD_TOKENS.pop(lp, None)
    return True


def _flag_limit(args):
    return args.index("--") if "--" in args else len(args)


# only these exact tokens may be rejected as "missing value" — arbitrary
# values starting with '--' (e.g. --line '--- separator ---') are legitimate
# (audit finding F3, 2026-07-17)
KNOWN_FLAGS = frozenset({"--agent", "--token", "--force", "--wait", "--line"})


def _flag(args, name, default=None):
    limit = _flag_limit(args)
    try:
        i = args.index(name, 0, limit)
    except ValueError:
        return default
    if i + 1 >= limit or args[i + 1] in KNOWN_FLAGS:
        raise ValueError(f"{name} requires a value")
    v = args[i + 1]
    del args[i:i + 2]
    return v


def _switch(args, name):
    limit = _flag_limit(args)
    try:
        i = args.index(name, 0, limit)
    except ValueError:
        return False
    del args[i]
    return True


def _usage_error(message):
    print(f"usage error: {message}", file=sys.stderr)
    return 2


def _run_with_renewal(command, target, token):
    proc = subprocess.Popen(command)
    interval = max(0.01, min(STALE_AFTER_SEC / 3.0, 10.0))
    while True:
        try:
            return proc.wait(timeout=interval)
        except subprocess.TimeoutExpired:
            if not renew(target, token):
                # Lease lost (reaped + re-acquired while we were stalled).
                # Continuing would run alongside the new owner, so stop the
                # child instead (independent review finding, 2026-07-17).
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                print("FAILED: lock lease lost while command was running; "
                      "command terminated", file=sys.stderr)
                return 1


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

    try:
        agent = _flag(rest, "--agent", "unknown")
        token = _flag(rest, "--token")
        force = _switch(rest, "--force")
        wait = float(_flag(rest, "--wait", "10"))
        line = _flag(rest, "--line")
        if not math.isfinite(wait) or wait < 0:
            raise ValueError("--wait must be a non-negative finite number")
    except ValueError as exc:
        sys.exit(_usage_error(str(exc)))

    if not rest:
        print("missing <path>", file=sys.stderr)
        sys.exit(2)
    target = rest[0]

    if cmd == "acquire":
        acquired_token = acquire(target, agent, wait)
        if not acquired_token:
            info = read_lock(lock_path(target)) or {}
            print(f"FAILED: locked by {info.get('agent','?')} (pid {info.get('pid','?')})",
                  file=sys.stderr)
        else:
            print(acquired_token)
        sys.exit(0 if acquired_token else 1)

    if cmd == "release":
        sys.exit(0 if release(target, agent, force, token) else 1)

    if cmd == "append":
        if line is None:
            line = sys.stdin.read().rstrip("\n")
        acquired_token = acquire(target, agent, wait)
        if not acquired_token:
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
            release(target, agent, force=True, token=acquired_token)
        sys.exit(0)

    if cmd == "run":
        if "--" not in rest:
            print("run requires: run <path> -- <cmd> [args...]", file=sys.stderr)
            sys.exit(2)
        sub = rest[rest.index("--") + 1:]
        if not sub:
            sys.exit(_usage_error("run requires a command after --"))
        acquired_token = acquire(target, agent, wait)
        if not acquired_token:
            print("FAILED: could not acquire lock", file=sys.stderr)
            sys.exit(1)
        try:
            rc = _run_with_renewal(sub, target, acquired_token)
        finally:
            release(target, agent, force=True, token=acquired_token)
        sys.exit(rc)

    print(f"unknown command: {cmd}", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
