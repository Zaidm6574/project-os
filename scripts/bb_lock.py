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

On POSIX, run starts a separate session and cancels the command's process
group on lease loss or wrapper cancellation, before releasing the lease.
SIGINT/SIGTERM are deferred during spawn and bounded cleanup, then delivered
to the caller's original handler after lease release. Descendants that leave
the group are outside this cooperative lifecycle contract; fenced() is still
required for publication.

Exit codes: 0 ok · 1 could not acquire / not held · 2 usage error
"""
import errno, os, sys, json, time, hashlib, subprocess, fcntl, math, uuid, signal, threading
from contextlib import contextmanager

LOCK_DIR = os.environ.get("BB_LOCK_DIR", os.path.expanduser("~/.project-os/locks"))
STALE_AFTER_SEC = float(os.environ.get("BB_LOCK_STALE", "60"))
POLL_SEC = 0.25
_HELD_TOKENS = {}


def _key(target):
    return hashlib.sha1(os.path.realpath(target).encode()).hexdigest()


def lock_path(target):
    return os.path.join(LOCK_DIR, _key(target) + ".lock")


class _GuardTimeout(Exception):
    """Deadline expired while waiting for a lock's serialization guard."""


class LockLeaseLost(RuntimeError):
    """The supplied token no longer owns a live lease for publication."""


@contextmanager
def _guard(lp, deadline=None):
    """Serialize all transitions for one stable lock pathname.

    With ``deadline=None`` (release/renew/reap) this blocks until the guard
    is ours — those transitions must not fail spuriously. ``acquire()``
    passes its ``--wait`` deadline (``time.monotonic()`` based): the guard is
    then polled with LOCK_NB and ``_GuardTimeout`` is raised once the
    deadline passes. Before 2026-07-26 this was an unbounded LOCK_EX and
    acquire() only checked its deadline AFTER the guard block, so one
    stopped/hung process inside a guard critical section wedged every other
    agent indefinitely and ``--wait N`` did not actually bound the wait.
    """
    os.makedirs(LOCK_DIR, exist_ok=True)
    with open(lp + ".guard", "a+", encoding="utf-8") as guard:
        if deadline is None:
            fcntl.flock(guard.fileno(), fcntl.LOCK_EX)
        else:
            while True:
                try:
                    fcntl.flock(guard.fileno(),
                                fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EAGAIN, errno.EACCES,
                                         errno.EWOULDBLOCK):
                        raise
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise _GuardTimeout(lp) from None
                    time.sleep(min(POLL_SEC, remaining))
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


def acquire(target, agent="unknown", wait=10.0, cancellation=None):
    """Atomically acquire the lock for target. Returns its fencing token.

    A command wrapper keeps signals deferred from creation until it receives
    the token. Contended waits remain interruptible; on success the wrapper
    must restore cancellation only after storing the returned token.
    """
    os.makedirs(LOCK_DIR, exist_ok=True)
    lp = lock_path(target)
    deadline = time.monotonic() + wait
    while True:
        try:
            with _guard(lp, deadline=deadline):
                _reap_locked(lp)
                acquired = False
                if cancellation is not None:
                    cancellation.interruptible = False
                try:
                    token = uuid.uuid4().hex
                    # Keep lock acquisition independent of content-writer
                    # wrappers: a stalled writer may instrument os.fdopen,
                    # but that must not stall the fencing transition itself.
                    payload = json.dumps({
                        "path": os.path.realpath(target), "agent": agent,
                        "pid": os.getpid(), "ts": time.time(), "token": token,
                    }).encode("utf-8")
                    try:
                        fd = os.open(lp, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    except FileExistsError:
                        pass
                    else:
                        try:
                            offset = 0
                            while offset < len(payload):
                                written = os.write(fd, payload[offset:])
                                if written <= 0:
                                    raise OSError(errno.EIO, "lock payload write made no progress")
                                offset += written
                            _HELD_TOKENS[lp] = token
                        except BaseException:
                            # Partial JSON has no usable token. The open
                            # object, while still guarded, is our proof of
                            # ownership; never unlink a replacement file.
                            try:
                                if os.path.samestat(os.fstat(fd), os.stat(lp)):
                                    os.unlink(lp)
                            except FileNotFoundError:
                                pass
                            except OSError as cleanup_error:
                                print("FAILED: acquisition rollback unconfirmed (%s)" %
                                      type(cleanup_error).__name__, file=sys.stderr)
                                raise
                            finally:
                                if _HELD_TOKENS.get(lp) == token:
                                    _HELD_TOKENS.pop(lp, None)
                            raise
                        finally:
                            os.close(fd)
                        acquired = True
                        return token
                finally:
                    if cancellation is not None and not acquired:
                        cancellation.interruptible = True
                        cancellation.check()
        except _GuardTimeout:
            # Same timeout failure shape as a lock held past the deadline:
            # a wedged guard holder must not block us beyond --wait.
            return False
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
        if not isinstance(info, dict) or info.get("token") != expected or is_stale(lp):
            return False
        try:
            os.utime(lp, None)
            return True
        except FileNotFoundError:
            return False


@contextmanager
def fenced(target, token):
    """Hold the stable guard from lease validation through publication.

    A token check followed by an unguarded write leaves a window in which a
    replacement owner can publish and then be overwritten by the old owner.
    Use ``with fenced(target, token):`` around the entire publication. For
    read-modify-write operations, acquire the lease before reading the state.
    Missing, replaced, tokenless, or expired leases raise RuntimeError before
    entering the block; no implicit token lookup or renewal is performed.

    Once entered, the guard prevents takeover even if the lease ages during
    the block. Keep it short and do not call acquire/renew/release/reap for the
    same target inside it: those operations need this non-reentrant guard.
    This is cooperative same-user locking, not protection from arbitrary file
    editors. The guard file must never be unlinked while participants run.
    """
    lp = lock_path(target)
    with _guard(lp):
        info = read_lock(lp)
        if (not token or not isinstance(info, dict)
                or info.get("token") != token or is_stale(lp)):
            raise LockLeaseLost("lock lease lost or expired before publication")
        yield


def release(target, agent="unknown", force=False, token=None):
    """Release the lock only when its fencing token still owns the lease."""
    # agent must default to "unknown" (matching acquire()'s default), not
    # None: a caller that omits agent entirely used to sail through the
    # tokenless-legacy-lock ownership guard below, since `agent is not None`
    # was False and the whole check short-circuited (2026-07-25).
    # A caller that forwards an optional variable can also pass agent=None
    # EXPLICITLY, which reopened the same bypass, so an absent agent is
    # normalized to the untrusted default here rather than skipping the
    # guard (2026-07-26). Behaviour pinned by
    # tests/test_bb_lock_ownership_regression.py.
    if agent is None:
        agent = "unknown"
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
        if current is None and not force \
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


class _CommandCancelled(BaseException):
    """Unwind a command wait without Popen's KeyboardInterrupt reaping wait."""


class _CommandSignals:
    """Keep cancellation pending through spawn, cleanup, and lease release.

    Popen.wait handles KeyboardInterrupt by briefly waiting for/reaping the
    leader. A separate exception preserves the group id until our final group
    signal. Restore and invoke the original handler only after the lease's
    finally block, preserving SIGINT/SIGTERM exit status and caller handlers.
    Ignored signals remain ignored; repeated signals cannot skip cleanup.
    """

    def __init__(self):
        self.handlers = {}
        self.pending = None
        self.interruptible = False

    def _receive(self, signum, frame):
        if self.pending is None:
            self.pending = (signum, frame)
        self.check()

    def check(self):
        if self.interruptible and self.pending is not None:
            raise _CommandCancelled()

    def __enter__(self):
        if threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGINT, signal.SIGTERM):
                handler = signal.getsignal(signum)
                if handler != signal.SIG_IGN:
                    self.handlers[signum] = handler
                    signal.signal(signum, self._receive)
        return self

    def __exit__(self, exc_type, exc, tb):
        self.interruptible = False
        for signum, handler in self.handlers.items():
            signal.signal(signum, handler)
        if self.pending is not None:
            signum, frame = self.pending
            handler = self.handlers[signum]
            if callable(handler):
                handler(signum, frame)
            else:
                os.kill(os.getpid(), signum)
            # A custom handler may return. The command was still cancelled;
            # the CLI reports 128 + signal instead of a successful exit.
            return isinstance(exc, _CommandCancelled)
        return False


def _cancel_command_group(proc):
    """Stop ordinary POSIX workers, then reap our direct child within a bound.

    The session leader's pid is also its process-group id. Keep that child
    unreaped until the final group signal, pinning its pid against reuse even
    when it exits before a TERM-resistant descendant. Non-child descendants
    are reaped by their own parents (or init), not by this wrapper.
    """
    if isinstance(proc.returncode, int):
        # A signal can race successful wait() completion. A reaped leader no
        # longer pins the group id: do not risk signalling a reused id.
        return "command exited; process group cancellation unconfirmed (leader already reaped)"
    signal_denied = False
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            break
        except PermissionError:
            # Some POSIX hosts also report EPERM for a group containing only
            # zombies. It is not proof of absence: retain the failure and
            # still reap our child, rather than claiming successful cleanup.
            signal_denied = True
        if sig == signal.SIGTERM:
            # The leader exiting is not proof that its workers have exited.
            # Give the whole group its grace period before escalation.
            time.sleep(5)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        # Even SIGKILL can be delayed by uninterruptible kernel work. Do not
        # hang forever or claim that an unreaped command has terminated.
        return "cancellation requested; command exit not observed"
    if signal_denied:
        return "command exited; process group cancellation unconfirmed (signal denied)"
    return "command process group cancelled"


def _run_with_renewal(command, target, token, cancellation=None):
    proc = None
    cleanup_started = False
    interval = max(0.01, min(STALE_AFTER_SEC / 3.0, 10.0))
    try:
        if cancellation is not None:
            cancellation.interruptible = False
        proc = subprocess.Popen(command, start_new_session=True)
        if cancellation is not None:
            cancellation.interruptible = True
            cancellation.check()
        while True:
            try:
                return proc.wait(timeout=interval)
            except subprocess.TimeoutExpired:
                if not renew(target, token):
                    # Lease lost (reaped + re-acquired while we were stalled).
                    # Continuing would run alongside the new owner, so stop the
                    # command's group instead, including ordinary workers.
                    if cancellation is not None:
                        cancellation.interruptible = False
                    cleanup_started = True
                    outcome = _cancel_command_group(proc)
                    print("FAILED: lock lease lost while command was running; "
                          + outcome, file=sys.stderr)
                    return 1
    except BaseException:
        if cancellation is not None:
            cancellation.interruptible = False
        if proc is not None:
            outcome = "cancellation unconfirmed (cleanup interrupted)"
            if not cleanup_started:
                try:
                    outcome = _cancel_command_group(proc)
                except Exception as cleanup_error:
                    outcome = "cancellation unconfirmed (%s)" % type(cleanup_error).__name__
            print("FAILED: command wrapper interrupted; " + outcome, file=sys.stderr)
        raise


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

    # "--" is a reserved separator (only meaningful for `run <path> -- <cmd>`),
    # never a legitimate <path>; without this check an omitted <path> silently
    # keyed the lock off the literal string "--" instead of erroring
    # (2026-07-25).
    if not rest or rest[0] == "--":
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
            with fenced(target, acquired_token):
                parent = os.path.dirname(os.path.abspath(target))
                os.makedirs(parent, exist_ok=True)
                with open(target, "a", encoding="utf-8") as f:
                    # Never weld onto a crash-truncated last line. This is the
                    # generic append the module docstring advertises, and it is
                    # named in brain_append.py's own heal as a cause of the
                    # truncation it repairs -- yet it reproduced the defect
                    # itself (adversarial verify 2026-07-26).
                    if f.tell():
                        with open(target, "rb") as probe:
                            probe.seek(-1, os.SEEK_END)
                            if probe.read(1) != b"\n":
                                f.write("\n")
                    f.write(line + "\n")
                    f.flush()
                    os.fsync(f.fileno())
        except LockLeaseLost as exc:
            print("FAILED: %s" % exc, file=sys.stderr)
            sys.exit(1)
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
        with _CommandSignals() as cancellation:
            acquired_token = None
            try:
                # Waits remain interruptible. acquire defers only publication
                # and the handoff until this frame owns the returned token.
                cancellation.interruptible = True
                cancellation.check()
                acquired_token = acquire(target, agent, wait, cancellation=cancellation)
                cancellation.interruptible = True
                cancellation.check()
                if not acquired_token:
                    print("FAILED: could not acquire lock", file=sys.stderr)
                    sys.exit(1)
                rc = _run_with_renewal(sub, target, acquired_token, cancellation)
            finally:
                cancellation.interruptible = False
                if acquired_token:
                    release(target, agent, force=True, token=acquired_token)
        sys.exit(128 + cancellation.pending[0] if cancellation.pending else rc)

    print(f"unknown command: {cmd}", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
