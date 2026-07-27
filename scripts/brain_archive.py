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
import shutil
import stat
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bb_lock

HOME = os.path.expanduser("~")
BRAIN = os.environ.get(
    "PROJECT_OS_SHARED_BRAIN",
    os.path.join(HOME, ".project-os", "central-brain", "shared-brain.jsonl"))
# 2026-07-25: BRAIN.replace(".jsonl", "-archive.jsonl") collapsed ARCHIVE ==
# BRAIN whenever the configured brain path lacked a .jsonl suffix, so
# archiving silently deleted entries from the live file. Derive the archive
# path by appending a suffix instead of relying on a substring replace, and
# fail closed if it ever still collides with BRAIN.
ARCHIVE = (BRAIN[: -len(".jsonl")] if BRAIN.endswith(".jsonl") else BRAIN) + "-archive.jsonl"
assert ARCHIVE != BRAIN, "archive path must never collide with the active brain path"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _within(real_root, real_path):
    if real_path == real_root:
        return True
    try:
        return os.path.commonpath([real_root, real_path]) == real_root
    except ValueError:
        # Different drives, or an embedded NUL: not provably contained.
        return False


def _escapes_brain_dir(path):
    """Real target when `path` is a symlink -- ANY symlink.

    This was once a containment check: a link that still resolved inside the
    brain directory was allowed, on the theory that pointing the brain at a
    sibling store is a supported layout. An adversary took both halves of that
    apart (2026-07-26). A link is refused outright now:

      * a link INSIDE the directory is not safe either -- pointing the archive
        at the brain made the moved entry get appended to the brain and then
        overwritten by the rewrite, so it survived in NEITHER file while the
        tool printed "archived 1" and the docstring promised nothing is ever
        deleted;
      * `os.path.islink()` cannot see a HARD link, so this check can never be
        the only guard. The sinks below open with O_NOFOLLOW and verify the
        FILE DESCRIPTOR they actually hold (st_nlink), which is what closes
        the hardlink and the swap-after-the-check race.

    A path check alone is advisory: it reports a clear reason before any side
    effect. It is the fd checks that are load-bearing.
    """
    if not os.path.islink(path):
        return ""
    return os.path.realpath(path)


def _mode_or_private(path):
    """Permissions of `path`, or 0600 when it does not exist yet.

    The brain holds lesson text harvested from every project, so when there is
    no existing file to copy permissions from we pick the restrictive answer
    instead of the umask default (0644 on a stock umask 022 account).
    """
    try:
        return stat.S_IMODE(os.stat(path).st_mode)
    except OSError:
        return 0o600


def _copy_backup(src, dest_base):
    """Copy `src` to `dest_base` (or a numbered sibling) without following links.

    2026-07-26 (audit): the backup name is "<brain>.pre-archive-<stamp>" and
    the stamp is only second-resolution, so it is guessable; shutil.copy2()
    FOLLOWS a symlink and truncates, which let a link planted at that name
    redirect the copy on top of an arbitrary file the user can write.
    O_CREAT|O_EXCL is what closes that: POSIX requires it to fail with EEXIST
    when the path already exists, and to fail *even when the path is a
    symlink*, including a dangling one -- so this never writes through
    anything that was already there.

    A genuinely taken name (a second archive inside the same second) falls
    through to a numbered sibling rather than overwriting: this module
    promises backups are kept, so clobbering one would be its own data loss.
    The path actually written is returned, and that is the one apply prints.
    """
    taken = None
    for n in range(1, 51):
        path = dest_base if n == 1 else "%s-%d" % (dest_base, n)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            taken = exc
            continue
        with os.fdopen(fd, "wb") as out, open(src, "rb") as f:
            shutil.copyfileobj(f, out)
        shutil.copystat(src, path)  # what copy2() carried over: mode + times
        return path
    raise taken


def _atomic_write_preserving_mode(dest, text):
    """Replace dest's contents atomically, never writing through a planted path.

    2026-07-26 (audit): this rewrite went through a fixed, guessable
    "<brain>.tmp" name opened with plain open(..., "w"). Anyone able to create
    a file in the brain directory could pre-plant a symlink there; open()
    follows it, so the run truncated whatever the link pointed at, and
    os.replace() -- which does NOT follow links -- then renamed the symlink
    itself over the brain, leaving the active brain aliased to the file it had
    just destroyed. Reproduced end-to-end before this fix.

    tempfile.mkstemp() closes the hole, and the property doing the work is
    O_EXCL, not the unpredictable name: mkstemp opens with O_CREAT|O_EXCL
    (plus O_NOFOLLOW where the platform has it), and O_CREAT|O_EXCL fails with
    EEXIST on any pre-existing path, symlinks included. Randomising the name
    alone would only make pre-planting harder; O_EXCL makes writing through an
    existing path impossible. os.replace() then swaps the finished file in as
    one atomic rename, so no reader ever sees a half-written brain.

    Mode is carried across the swap exactly as in
    addons/full-engine/memory/cost_actuals.py: mkstemp always creates 0600
    regardless of umask and rename carries the temp file's mode onto the
    destination, so a naive temp-file rewrite REPLACES the brain's own
    permissions with whatever the writer happened to create (the old
    open(..., "w") handed a deliberately 0600 brain back as 0644, publishing
    every project's lesson text to every other account on the machine). Stat
    the destination and reproduce what was already there -- we never widen and
    never narrow. Only when the destination is absent do we choose, and there
    we choose 0600 rather than cost_actuals's umask default, which is right
    for a cost report meant to be read by teammates and wrong for the brain.

    dest is NOT resolved. Resolving it re-followed a symlink at write time,
    which handed the whole guard back: an attacker who swapped the brain for a
    link AFTER the caller's check still had the rewrite land on the link's
    target, destroying it and re-aliasing the brain to it (adversary
    2026-07-26). os.replace() does not follow a symlink -- it replaces the link
    itself -- so writing to the literal path is both the safe behaviour and the
    one that keeps an operator's deliberate layout from silently redirecting a
    rewrite.
    """
    real_dest = os.path.abspath(dest)
    try:
        dest_stat = os.stat(real_dest)
    except OSError:
        dest_stat = None

    fd, tmp_name = tempfile.mkstemp(
        dir=os.path.dirname(real_dest) or ".",
        prefix=os.path.basename(real_dest) + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        if dest_stat is not None:
            wanted_mode = stat.S_IMODE(dest_stat.st_mode)
            if hasattr(os, "chown"):
                # Best-effort: only root can hand a file to another uid, and a
                # non-root run that merely has write access should still get
                # the atomic write. It must run BEFORE the chmod -- POSIX has
                # a non-root chown() clear setuid/setgid, even a same-owner
                # no-op one, which would undo the mode we just restored.
                try:
                    os.chown(tmp_name, dest_stat.st_uid, dest_stat.st_gid)
                except OSError:
                    pass
            # Not best-effort: swallowing this would silently re-introduce the
            # permission change this function exists to prevent.
            os.chmod(tmp_name, wanted_mode)
            final_mode = stat.S_IMODE(os.stat(tmp_name).st_mode)
            if final_mode != wanted_mode:
                # chmod() may drop setuid/setgid without privilege. We cannot
                # restore those, but we refuse to change the mode *quietly*.
                print("WARNING: could not preserve mode %s on %s; wrote it as %s"
                      % (oct(wanted_mode), real_dest, oct(final_mode)),
                      file=sys.stderr)
        else:
            os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, real_dest)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _rows(path):
    if not os.path.isfile(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                out.append({"_raw": line})
    return out


def _rid(o):
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
        if (o.get("type") or o.get("kind")) != "interest":
            continue
        age = _entry_age_days(o)
        if age is not None and age > days:
            out.append((o, age))
    return out


def cmd_candidates(args):
    rows = _rows(BRAIN)
    cands = _interest_candidates(rows, args.interest_days)
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


def cmd_apply(args):
    rows = _rows(BRAIN)
    if args.ids:
        wanted = set(args.ids)
        move = [o for o in rows if _rid(o) in wanted]
        missing = wanted - {_rid(o) for o in move}
        if missing:
            print(f"REFUSED: unknown id(s): {sorted(missing)}", file=sys.stderr)
            return 2
    else:
        move = [o for o, _ in _interest_candidates(rows, args.interest_days)]
    if not move:
        print("nothing to archive")
        return 0

    if not bb_lock.acquire(BRAIN, agent="brain-archive", wait=15):
        print("FAILED: could not lock shared brain", file=sys.stderr)
        return 1
    try:
        # Checked INSIDE the lock. It used to run before acquire(), and
        # acquire() waits up to 15s for a concurrent agent -- an attacker-sized
        # window to swap the brain for a symlink after the check had passed
        # (adversary 2026-07-26). Holding the lock first shrinks the window to
        # the writes themselves, which is why each sink below re-verifies its
        # own file descriptor rather than trusting this.
        for label, path in (("shared brain", BRAIN), ("archive", ARCHIVE)):
            target = _escapes_brain_dir(path)
            if target:
                print(f"REFUSED: the {label} path {path} is a symlink -> "
                      f"{target}; refusing to write through it",
                      file=sys.stderr)
                return 2
        # 2026-07-25: rows/move were computed from a pre-lock read; a
        # concurrent writer could append between that read and lock
        # acquisition, and the stale in-memory `keep` snapshot written back
        # below would silently drop it. Re-read under the lock and re-derive
        # move/keep from the freshly-locked file.
        rows = _rows(BRAIN)
        if args.ids:
            move = [o for o in rows if _rid(o) in wanted]
        else:
            move = [o for o, _ in _interest_candidates(rows, args.interest_days)]
        # The mode the brain is wearing right now: the archive holds the very
        # same lesson text, so a brain that is private must not spawn a
        # world-readable archive on its first append.
        brain_mode = _mode_or_private(BRAIN)
        try:
            backup = _copy_backup(
                BRAIN, BRAIN + ".pre-archive-" + time.strftime("%Y%m%d-%H%M%S"))
        except FileExistsError:
            print("REFUSED: every candidate backup name next to the shared "
                  "brain is already taken; refusing to archive without a "
                  "backup", file=sys.stderr)
            return 2
        move_ids = {id(o) for o in move}
        keep = [o for o in rows if id(o) not in move_ids]
        stamp = dt.date.today().isoformat()
        archive_existed = os.path.exists(ARCHIVE)
        # O_NOFOLLOW refuses a symlink at the final component atomically -- no
        # check-then-open window -- and st_nlink on the OPEN descriptor is the
        # only way to see a hard link, which os.path.islink() is blind to. A
        # pre-planted hardlink at the archive name otherwise appended every
        # archived lesson into an arbitrary file with no race at all
        # (adversary 2026-07-26).
        try:
            fd = os.open(ARCHIVE,
                         os.O_WRONLY | os.O_CREAT | os.O_APPEND
                         | getattr(os, "O_NOFOLLOW", 0), 0o600)
        except OSError as e:
            print(f"REFUSED: cannot open the archive {ARCHIVE} safely "
                  f"({e.strerror or e}); refusing to write through it",
                  file=sys.stderr)
            return 2
        if os.fstat(fd).st_nlink > 1:
            os.close(fd)
            print(f"REFUSED: the archive {ARCHIVE} has more than one hard link;"
                  f" writing would also write the other name", file=sys.stderr)
            return 2
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            for o in move:
                o["archived"] = stamp
                f.write(json.dumps(o, ensure_ascii=False) + "\n")
        if not archive_existed:
            # Created just now (0600, umask cannot widen it): match the active
            # brain instead of the umask default. An archive that already
            # exists keeps whatever mode its owner chose.
            os.chmod(ARCHIVE, brain_mode)
        _atomic_write_preserving_mode(
            BRAIN,
            "".join(json.dumps(o, ensure_ascii=False) + "\n" for o in keep))
        print(f"archived {len(move)} -> {ARCHIVE}")
        print(f"active brain: {len(rows)} -> {len(keep)} entries (backup: {backup})")
    finally:
        bb_lock.release(BRAIN, agent="brain-archive", force=True)

    r = subprocess.run(
        [sys.executable, os.path.join(ROOT, "memory", "mneme_adapter.py"), "build"],
        capture_output=True, text=True)
    print((r.stdout + r.stderr).strip().splitlines()[-1] if (r.stdout or r.stderr) else "")
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
