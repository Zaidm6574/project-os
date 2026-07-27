#!/usr/bin/env python3
"""brain_append — locked append to the shared brain + immediate OSVec re-index.

Closes the audit gap (2026-07-02) where lessons appended to shared-brain.jsonl
left mneme_index.json stale until someone remembered to run
`memory/mneme_adapter.py build` — at one point only 13 of 138 entries were
indexed. Use THIS for shared-brain writes, not a raw bb_lock append.

Validates the line is well-formed JSON before appending (it is a JSONL file),
appends under bb_lock, then rebuilds the OSVec index.

Record shape: nonempty "id", "type" and "text" are what canon actually READS.
The field is "type" even though brain.py's CLI flag is `--kind` — brain.py
writes `"type": args.kind`. This docstring (which is also --help) used to name
the field "kind" and omit "id", a row NOTHING in canon understands: it is
dropped by central_brain.py and mneme_adapter.py, and it makes
memory/validate_run.py reject the WHOLE brain file, flipping an otherwise
closable run's "Graph/memory artifact present" to [ ] (audit 2026-07-27).
Every JSON example below must therefore stay copy-pasteable as-is.

Usage:
  python3 scripts/brain_append.py --line '{"id":"...","type":"lesson","text":"..."}' [--agent ID]
  echo '{"id":"...","type":"lesson","text":"..."}' | python3 scripts/brain_append.py
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

# Mirror of memory/validate_run.py:_valid_jsonl_record — the only fields canon
# actually reads off a shared-brain row.
CANON_FIELDS = ("id", "type", "text")


def canon_gaps(record):
    """Which canon-required fields this record is missing (nonempty strings)."""
    return [f for f in CANON_FIELDS
            if not (isinstance(record.get(f), str) and record.get(f).strip())]


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
    _gate = getattr(_brain, "record_secret_path", None) or getattr(
        _brain, "record_secret_hit", None)
    if _gate is not None and getattr(_brain, "SECRET_SCAN_EXHAUSTIVE", False):
        field = _gate(record)
        if field:
            print(
                f"REFUSED: field '{field}' looks like it contains a secret. "
                "Redact it and retry — the shared brain syncs to the central "
                "brain and must never carry credentials.",
                file=sys.stderr,
            )
            sys.exit(2)
        if not isinstance(record, dict):
            # shared-brain.jsonl is one JSON OBJECT per line: central_brain.py
            # and mneme_adapter.py both call .get() on every line they read.
            # A bare string/list/number used to be appended anyway (and, before
            # 2026-07-26, skipped the secret gate outright because
            # record_secret_hit early-returned None for a non-dict).
            print("REFUSED: shared-brain.jsonl takes one JSON OBJECT per line; "
                  f"got a bare {type(record).__name__}.", file=sys.stderr)
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
                    "module named 'brain' that shadowed it on sys.path"
               if _gate is None
               else "does not advertise SECRET_SCAN_EXHAUSTIVE, so it screens "
                    "an allowlist of field names instead of the whole payload "
                    "and would pass a secret hidden in any other field "
                    "(audit finding, 2026-07-26)")
        print(f"REFUSED: the brain privacy gate {why}; refusing to append "
              "unscreened. Check addons/full-engine/brain/brain.py.",
              file=sys.stderr)
        sys.exit(2)

    # Say so BEFORE the row disappears. central_brain.py/mneme_adapter.py skip
    # a row canon cannot read without a word, and validate_run.py then fails
    # the whole brain file over it — the operator's only signal was
    # central_brain's "skipped by privacy/type gate" tally, which reads like a
    # privacy event rather than a schema typo (audit 2026-07-27). A WARNING,
    # not a refusal: harvest.py aborts an entire batch on a nonzero
    # brain_append, so tightening this into a gate would be a separate,
    # louder change.
    _missing = canon_gaps(record)
    if _missing:
        _hint = ""
        if "type" in _missing and record.get("kind"):
            _hint = (' — the record field is "type", not "kind"; brain.py\'s '
                     'CLI flag --kind writes "type"')
        print("WARNING: record has no nonempty %s%s. Appending anyway, but "
              "central_brain.py and mneme_adapter.py drop rows like this "
              "silently, and memory/validate_run.py rejects the whole brain "
              "file over one, failing the run's Graph/memory check."
              % (", ".join(_missing), _hint), file=sys.stderr)

    _bp = os.path.dirname(os.path.abspath(SHARED_BRAIN))
    os.makedirs(_bp, exist_ok=True)
    if not bb_lock.acquire(SHARED_BRAIN, agent=agent, wait=10):
        print("FAILED: could not lock shared-brain.jsonl", file=sys.stderr)
        sys.exit(1)
    try:
        with open(SHARED_BRAIN, "a", encoding="utf-8") as f:
            # Never weld onto a truncated tail. If a previous write died between
            # the buffered write and the flush (SIGKILL, full disk, an
            # interrupted `bb_lock append`), the last line is a partial JSON
            # fragment with no newline. Appending to it produces ONE unparseable
            # line, destroying both the truncated record and the one being saved
            # now — and every reader skips unparseable lines silently
            # (central_brain.read_jsonl, mneme_adapter._gather, the FTS mirror),
            # so the loss is invisible forever while this command prints
            # "appended" and exits 0. Start a new line instead; the damaged
            # fragment stays damaged, but it stays ALONE (audit 2026-07-26).
            if f.tell():
                with open(SHARED_BRAIN, "rb") as probe:
                    probe.seek(-1, os.SEEK_END)
                    if probe.read(1) != b"\n":
                        f.write("\n")
            # Write the object that was SCANNED, not the raw input text. JSON
            # allows duplicate keys and json.loads keeps only the last, so
            # `{"text":"<secret>","text":"clean"}` was screened as clean while
            # the verbatim line carrying the secret went into the brain
            # (audit finding, 2026-07-26). Re-serializing makes
            # scanned-bytes == written-bytes.
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
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
