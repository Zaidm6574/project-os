#!/usr/bin/env python3
"""brain_archive — move entries from the active shared brain to the archive tier.

The active brain (shared-brain.jsonl) should stay small and high-signal; the
archive (shared-brain-archive.jsonl, same directory) holds everything moved out.
Archived entries STAY in the Mneme vector index (mneme_adapter indexes both
files, tagging archived entries "lesson-archived"), so nothing loses semantic
recall — archiving is a filing operation, not a deletion.

Usage:
  python3 scripts/brain_archive.py candidates [--interest-days N]
      List archive candidates: `interest`-type entries older than N days
      (default 60). Read-only.
  python3 scripts/brain_archive.py apply --ids id1 id2 ...
  python3 scripts/brain_archive.py apply --interest-days N
      Move the selected entries to the archive under bb_lock, with a timestamped
      backup of the active file, then rebuild the Mneme index.

Nothing is ever deleted: active-file backups are kept alongside, and the
archive file is append-only.
"""
import argparse
import datetime as dt
import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bb_lock
import brain_paths

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# An explicit path remains a command-level input so archive can reject a link
# with a clean no-mutation result.  The default, however, is project-local.
BRAIN = os.environ.get("PROJECT_OS_SHARED_BRAIN") or str(
    brain_paths.resolve_shared_brain(ROOT))


def archive_path(brain):
    p = pathlib.Path(brain)
    if p.suffix == ".jsonl":
        return str(p.with_name(p.stem + "-archive.jsonl"))
    return str(p.with_name(p.name + "-archive.jsonl"))


ARCHIVE = archive_path(BRAIN)


class _RawLine:
    """Malformed / non-object line kept verbatim; a class (not a dict shape)
    so no legitimate JSON entry can ever collide with the placeholder."""
    __slots__ = ("line",)

    def __init__(self, line):
        self.line = line


def _reject_non_finite(value):
    raise ValueError(f"non-finite number {value}")


def _rows(path):
    """Returns (rows, skipped). Malformed / non-object lines are kept as
    _RawLine placeholders (never candidates, rewritten verbatim) so a
    bad line can't crash or get dropped by an apply rewrite."""
    if not os.path.isfile(path):
        return [], 0
    out, skipped = [], 0
    with open(path, encoding="utf-8", newline="") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                o = json.loads(line, parse_constant=_reject_non_finite)
                # JSON accepts exponents such as 1e999 syntactically, but the
                # decoded float is infinite. Re-encoding with allow_nan=False
                # catches those overflow-to-infinity values at any depth.
                json.dumps(o, allow_nan=False)
            except (json.JSONDecodeError, ValueError):
                o = None
            if isinstance(o, dict):
                out.append(o)
            else:
                skipped += 1
                out.append(_RawLine(line))
    return out, skipped


def _rid(o):
    if not isinstance(o, dict):
        return ""
    return o.get("id") or o.get("name") or ""


def _entry_age_days(o):
    ts = o.get("ts") or o.get("date") or o.get("created") or ""
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            then = dt.datetime.strptime(str(ts)[:len(fmt.replace('%Y', '0000'))], fmt)
            return (dt.datetime.now() - then).days
        except ValueError:
            continue
    return None  # undated entries are never auto-candidates


def _interest_candidates(rows, days):
    out = []
    for o in rows:
        if not isinstance(o, dict):
            continue
        try:
            typ = brain_paths.record_type(o)
        except brain_paths.BrainRecordError:
            continue
        if typ != "interest":
            continue
        age = _entry_age_days(o)
        if age is not None and age > days:
            out.append((o, age))
    return out


def _copy_backup(src, dest_base):
    """Create a sibling backup without ever following a planted destination."""
    taken = None
    for number in range(1, 51):
        path = dest_base if number == 1 else "%s-%d" % (dest_base, number)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            taken = exc
            continue
        os.close(fd)
        if hasattr(bb_lock, "lock_path"):
            # The fenced runtime keeps the directory ownership boundary while
            # preserving the existing copy2 seam used by operational backups.
            shutil.copy2(src, path)
        else:
            with open(path, "wb") as out, open(src, "rb") as source:
                shutil.copyfileobj(source, out)
            shutil.copystat(src, path)
        return path
    raise taken


def _atomic_write_preserving_mode(dest, text):
    """Atomically replace a brain file without following a planted path."""
    try:
        dest_stat = os.stat(dest)
    except OSError:
        dest_stat = None
    fd, tmp = tempfile.mkstemp(
        dir=os.path.dirname(os.path.abspath(dest)) or ".",
        prefix=os.path.basename(dest) + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600 if dest_stat is None else stat.S_IMODE(dest_stat.st_mode))
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class _LeaseRenewer:
    """Keep a bb_lock lease alive and remember if its fencing token is lost."""

    def __init__(self, target, token):
        self.target = target
        self.token = token
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._error = None
        self._started = False
        self._interval = max(
            0.01, min(getattr(bb_lock, "STALE_AFTER_SEC", 60.0) / 3.0, 10.0))
        self._thread = threading.Thread(
            target=self._run, name="brain-archive-renew", daemon=True)

    def start(self):
        self._thread.start()
        self._started = True

    def _mark_lost(self, error=None):
        self._error = error
        self._lost.set()

    def _renew(self):
        # Compatibility seam for callers that supply the documented minimal
        # acquire/release lock adapter. Real bb_lock always has renew().
        if not hasattr(bb_lock, "renew"):
            return True
        if self._lost.is_set():
            return False
        try:
            owned = bb_lock.renew(self.target, self.token)
        except Exception as exc:
            self._mark_lost(exc)
            return False
        if not owned:
            self._mark_lost()
            return False
        return True

    def _run(self):
        while not self._stop.wait(self._interval):
            if not self._renew():
                return

    def verify(self):
        """Synchronously renew/fence immediately before a shared-file mutation."""
        return self._renew()

    def diagnostic(self):
        if self._error is not None:
            return f": {self._error}"
        return ""

    def stop(self):
        self._stop.set()
        if self._started:
            self._thread.join()


def cmd_candidates(args):
    rows, skipped = _rows(BRAIN)
    cands = _interest_candidates(rows, args.interest_days)
    if skipped:
        print(f"skipped {skipped} malformed lines in {os.path.basename(BRAIN)}")
    if not cands:
        print(f"no interest entries older than {args.interest_days}d "
              f"(active brain: {len(rows)} entries)")
        return 0
    print(f"{len(cands)} archive candidate(s) — interest > {args.interest_days}d:")
    for o, age in cands:
        text = (o.get("text") or o.get("lesson") or "")[:90]
        print(f"  {age:>4}d  {_rid(o) or '(no id)'}  :: {text}")
    print(f"\napply with: python3 scripts/brain_archive.py apply "
          f"--interest-days {args.interest_days}")
    return 0


def _append_archive(archive_lines, source_mode):
    """Append under a mode no more permissive than the active brain."""
    if os.path.islink(ARCHIVE):
        raise OSError("refusing symlink archive path")
    permitted_mode = source_mode & 0o666
    flags = os.O_RDWR | os.O_APPEND | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(ARCHIVE, flags, permitted_mode)
    try:
        if os.fstat(fd).st_nlink != 1:
            raise OSError("refusing hard-linked archive path")
        current_mode = stat.S_IMODE(os.fstat(fd).st_mode)
        tightened_mode = current_mode & permitted_mode
        if tightened_mode != current_mode:
            os.fchmod(fd, tightened_mode)
        # Inspect the same descriptor we append through. Preserve the damaged
        # bytes for recovery, but terminate their physical JSONL line before
        # adding a moved row (also preserves a valid record missing its newline).
        needs_separator = False
        if os.fstat(fd).st_size:
            os.lseek(fd, -1, os.SEEK_END)
            needs_separator = os.read(fd, 1) != b"\n"
        with os.fdopen(fd, "a", encoding="utf-8", newline="") as f:
            fd = None
            if needs_separator:
                f.write("\n")
            f.writelines(archive_lines)
            f.flush()
            os.fsync(f.fileno())
    finally:
        if fd is not None:
            os.close(fd)


def _commit_if_owned(token, tmp, archive_lines, source_mode):
    """Fence token verification and both shared-file mutations as one step."""
    if not all(hasattr(bb_lock, name) for name in ("lock_path", "_guard", "read_lock")):
        try:
            _append_archive(archive_lines, source_mode)
            os.replace(tmp, BRAIN)
            return True
        except OSError:
            return False
    try:
        with bb_lock.fenced(BRAIN, token):
            # A valid lease may age while the guarded publication runs. Keep
            # it fresh for the background renewer when this guard is released;
            # never resurrect an expired lease before entering fenced().
            lock_file = bb_lock.lock_path(BRAIN)
            os.utime(lock_file, None)
            _append_archive(archive_lines, source_mode)
            os.replace(tmp, BRAIN)
            os.utime(lock_file, None)
            return True
    except bb_lock.LockLeaseLost:
        return False


def _apply_locked(args, lease, token):
    """Perform archive work while the caller owns and renews the brain lock."""
    tmp = None
    try:
        if os.path.islink(BRAIN) or os.path.islink(ARCHIVE):
            print("REFUSED: brain or archive path is a symlink", file=sys.stderr)
            return 2, False
        rows, skipped = _rows(BRAIN)
        if skipped:
            print(f"skipped {skipped} malformed lines in {os.path.basename(BRAIN)} "
                  "(kept in place, never archived)")
        if args.ids:
            wanted = set(args.ids)
            move = [o for o in rows if _rid(o) in wanted]
            missing = wanted - {_rid(o) for o in move}
            if missing:
                print(f"REFUSED: unknown id(s): {sorted(missing)}", file=sys.stderr)
                return 2, False
        else:
            move = [o for o, _ in _interest_candidates(rows, args.interest_days)]
        if not move:
            print("nothing to archive")
            return 0, False

        source_mode = stat.S_IMODE(os.stat(BRAIN).st_mode)
        backup_base = BRAIN + ".pre-archive-" + time.strftime("%Y%m%d-%H%M%S")
        try:
            backup = _copy_backup(BRAIN, backup_base)
        except OSError as exc:
            print("REFUSED: could not safely create brain backup: %s" % exc,
                  file=sys.stderr)
            return 2, False
        if not lease.verify():
            print("FAILED: brain archive lock lease lost during apply; aborting "
                  "before archive/active mutation" + lease.diagnostic(),
                  file=sys.stderr)
            return 1, False
        move_ids = {id(o) for o in move}
        keep = [o for o in rows if id(o) not in move_ids]
        stamp = dt.date.today().isoformat()
        archive_lines = []
        for o in move:
            archived = brain_paths.canonicalize_record(o)
            archived["archived"] = stamp
            archive_lines.append(json.dumps(archived, ensure_ascii=False) + "\n")
        fd, tmp = tempfile.mkstemp(
            dir=os.path.dirname(os.path.abspath(BRAIN)) or ".",
            prefix=os.path.basename(BRAIN) + ".", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            for o in keep:
                if isinstance(o, _RawLine):
                    f.write(o.line)
                else:
                    f.write(json.dumps(o, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        shutil.copystat(BRAIN, tmp)
        if not lease.verify():
            print("FAILED: brain archive lock lease lost during apply; aborting "
                  "before archive/active mutation" + lease.diagnostic(),
                  file=sys.stderr)
            return 1, False
        if not _commit_if_owned(token, tmp, archive_lines, source_mode):
            if hasattr(bb_lock, "lock_path"):
                print("FAILED: brain archive lock lease lost before final commit; "
                      "active brain left unchanged", file=sys.stderr)
                return 1, False
            print("REFUSED: archive commit was not safe; active brain left unchanged",
                  file=sys.stderr)
            return 2, False
        tmp = None
        print(f"archived {len(move)} -> {ARCHIVE}")
        print(f"active brain: {len(rows)} -> {len(keep)} entries (backup: {backup})")
        return 0, True
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass


def cmd_apply(args):
    token = bb_lock.acquire(BRAIN, agent="brain-archive", wait=15)
    if not token:
        print("FAILED: could not lock shared brain", file=sys.stderr)
        return 1
    lease = None
    release_ok = False
    try:
        lease = _LeaseRenewer(BRAIN, token)
        lease.start()
        rc, changed = _apply_locked(args, lease, token)
    finally:
        try:
            if lease is not None:
                lease.stop()
        finally:
            release_ok = bb_lock.release(BRAIN, agent="brain-archive", token=token)
            if not release_ok:
                print("FAILED: could not release brain archive lock with its "
                      "fencing token", file=sys.stderr)

    if not release_ok:
        return 1
    if rc != 0 or not changed:
        return rc

    r = subprocess.run(
        [sys.executable, os.path.join(ROOT, "memory", "mneme_adapter.py"), "build"],
        capture_output=True, text=True)
    output = (r.stdout + r.stderr).strip()
    if getattr(r, "returncode", 0) != 0:
        detail = output.splitlines()[-1] if output else "no diagnostic output"
        print(f"FAILED: archive state updated, but Mneme rebuild failed "
              f"(exit {r.returncode}): {detail}", file=sys.stderr)
        return 1
    if output:
        print(output.splitlines()[-1])
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("candidates", help="list auto-archive candidates (read-only)")
    c.add_argument("--interest-days", type=int, default=60)
    a = sub.add_parser("apply", help="archive selected entries")
    a.add_argument("--ids", nargs="+", default=None)
    a.add_argument("--interest-days", type=int, default=60)
    args = ap.parse_args()
    if args.cmd == "candidates":
        sys.exit(cmd_candidates(args))
    if args.cmd == "apply" and not args.ids and args.interest_days is None:
        ap.error("apply needs --ids or --interest-days")
    sys.exit(cmd_apply(args))


if __name__ == "__main__":
    main()
