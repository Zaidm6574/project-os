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
import subprocess
import sys
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
        backup = BRAIN + ".pre-archive-" + time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(BRAIN, backup)
        move_ids = {id(o) for o in move}
        keep = [o for o in rows if id(o) not in move_ids]
        stamp = dt.date.today().isoformat()
        with open(ARCHIVE, "a", encoding="utf-8") as f:
            for o in move:
                o["archived"] = stamp
                f.write(json.dumps(o, ensure_ascii=False) + "\n")
        tmp = BRAIN + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for o in keep:
                f.write(json.dumps(o, ensure_ascii=False) + "\n")
        os.replace(tmp, BRAIN)
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
