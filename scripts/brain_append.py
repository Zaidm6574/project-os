#!/usr/bin/env python3
"""brain_append — locked append to the shared brain + immediate OSVec re-index.

Closes the audit gap (2026-07-02) where lessons appended to shared-brain.jsonl
left mneme_index.json stale until someone remembered to run
`memory/mneme_adapter.py build` — at one point only 13 of 138 entries were
indexed. Use THIS for shared-brain writes, not a raw bb_lock append.

Validates the line is well-formed JSON before appending (it is a JSONL file),
appends under bb_lock, then rebuilds the OSVec index.

Usage:
  python3 scripts/brain_append.py --line '{"kind":"lesson","text":"..."}' [--agent ID]
  echo '{"kind":"lesson","text":"..."}' | python3 scripts/brain_append.py
  ... [--no-reindex]   # skip the rebuild (batch mode: reindex once at the end)
"""
import os, sys, json, subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # project-os/
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import bb_lock

SHARED_BRAIN = os.environ.get(
    "PROJECT_OS_SHARED_BRAIN",
    os.path.expanduser("~/.project-os/central-brain/shared-brain.jsonl"))
MNEME = os.path.join(ROOT, "memory", "mneme_adapter.py")


def main():
    args = sys.argv[1:]

    def flag(name, default=None):
        if name in args:
            i = args.index(name)
            v = args[i + 1]
            del args[i:i + 2]
            return v
        return default

    line = flag("--line")
    agent = flag("--agent", "brain-append")
    no_reindex = "--no-reindex" in args
    if line is None:
        line = sys.stdin.read().strip()
    if not line:
        print(__doc__)
        sys.exit(2)

    try:
        json.loads(line)
    except json.JSONDecodeError as e:
        print(f"REFUSED: not valid JSON ({e}) — shared-brain.jsonl takes one JSON object per line",
              file=sys.stderr)
        sys.exit(2)

    _bp = os.path.dirname(os.path.abspath(SHARED_BRAIN))
    os.makedirs(_bp, exist_ok=True)
    if not bb_lock.acquire(SHARED_BRAIN, agent=agent, wait=10):
        print("FAILED: could not lock shared-brain.jsonl", file=sys.stderr)
        sys.exit(1)
    try:
        with open(SHARED_BRAIN, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
    finally:
        bb_lock.release(SHARED_BRAIN, agent=agent, force=True)

    if no_reindex:
        print("appended (reindex skipped — run memory/mneme_adapter.py build when done)")
        sys.exit(0)

    r = subprocess.run([sys.executable, MNEME, "build"], capture_output=True, text=True)
    if r.returncode == 0:
        print("appended + reindexed:", (r.stdout or "").strip())
        sys.exit(0)
    print("appended, but reindex FAILED — run memory/mneme_adapter.py build manually:\n"
          + (r.stderr or "")[:300], file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
