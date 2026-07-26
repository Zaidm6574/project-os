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

    # only these exact tokens may be rejected as a flag's "missing value" —
    # a --line value that itself starts with -- is legitimate JSON-ish text,
    # but a bare trailing flag must not IndexError (audit finding, 2026-07-25;
    # same fix already landed in bb_lock.py/plan_artifact.py).
    known_flags = {"--line", "--agent", "--no-reindex"}

    def flag(name, default=None):
        if name in args:
            i = args.index(name)
            if i + 1 >= len(args) or args[i + 1] in known_flags:
                print(f"usage error: missing value for {name}", file=sys.stderr)
                sys.exit(2)
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
        record = json.loads(line)
    except json.JSONDecodeError as e:
        print(f"REFUSED: not valid JSON ({e}) — shared-brain.jsonl takes one JSON object per line",
              file=sys.stderr)
        sys.exit(2)

    # THE privacy gate (audit 2026-07-25). This is the doctrine-mandated
    # cross-runtime write path and it had ZERO secret screening, so anything
    # `brain.py save-chat` refuses could be appended here instead — and
    # `central_brain.py pull` then redistributed it to other projects.
    # One gate, all writers: reuse brain.py's rather than forking the patterns.
    _brain_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "addons", "full-engine", "brain")
    _brain = None
    if os.path.isdir(_brain_dir):
        sys.path.insert(0, _brain_dir)
        try:
            import brain as _brain  # noqa: E402
        except Exception:
            _brain = None
        finally:
            sys.path.pop(0)
    if _brain is not None and hasattr(_brain, "record_secret_hit"):
        field = _brain.record_secret_hit(record)
        if field:
            print(
                f"REFUSED: field '{field}' looks like it contains a secret. "
                "Redact it and retry — the shared brain syncs to the central "
                "brain and must never carry credentials.",
                file=sys.stderr,
            )
            sys.exit(2)
    else:
        # Fail closed on ALL failure shapes, including the addon simply not
        # being present. The prior version guarded this whole else-branch
        # behind `if os.path.isdir(_brain_dir)`, so a fresh clone / any
        # environment missing addons/full-engine/brain skipped the privacy
        # gate ENTIRELY and appended unscreened secrets with exit 0
        # (audit finding, 2026-07-25).
        why = ("the brain addon is not present at addons/full-engine/brain"
               if not os.path.isdir(_brain_dir) else
               "could not be imported" if _brain is None
               else "has no record_secret_hit(); it may be a different "
                    "module named 'brain' that shadowed it on sys.path")
        print(f"REFUSED: the brain privacy gate {why}; refusing to append "
              "unscreened. Check addons/full-engine/brain/brain.py.",
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
