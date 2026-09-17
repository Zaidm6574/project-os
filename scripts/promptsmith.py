#!/usr/bin/env python3
"""promptsmith — compile a Worker prompt AND a matching Evaluator rubric from the
second brain, so maker and checker share the same taste priors.

The pipeline stage this implements:
  task -> brain brief (mcp__brain__brief or brain_query.py CLI)
       -> <id>-worker-prompt.md  (task + brief embedded + hard constraints)
       -> <id>-rubric.md         (weighted criteria from the SAME brief; DON'Ts = auto-fail)

In Claude sessions the orchestrator may call mcp__brain__brief itself and pass the
result via --brief-file. Standalone, this script shells to the personal-brain CLI (PROJECT_OS_BRAIN_DIR).
If the brain is unreachable it emits placeholder packets marked BRAIN-UNAVAILABLE
rather than pretending it ran (Reality Check rule).

Usage:
  python3 scripts/promptsmith.py --task "build the hero section" [--query "landing page hero"]
         [--packet-id ID] [--out-dir blackboard/packets] [--brief-file path] [--no-index]

Try it without any brain configured (uses the bundled sample brief):
  python3 scripts/promptsmith.py --task "build the hero section" \
      --brief-file examples/sample-brief.md --out-dir demo-packets --no-index

Without --no-index, the shared blackboard index records a path relative to its
own directory (URL-escaped where needed), even with a custom --out-dir.

Rejections feed back: the rubric instructs the Evaluator to write a lesson line to
memory/self-improvement-loop.md and record the variant via scripts/evolution.py.
"""
import os, re, sys, json, subprocess, datetime, tempfile
from urllib.parse import quote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # project-os/
# Personal-brain CLI location. Point PROJECT_OS_BRAIN_DIR at any directory
# containing a brain_query.py (optionally with its own .venv); without one,
# pass --brief-file instead — the script degrades to BRAIN-UNAVAILABLE, never
# invents taste.
BRAIN_DIR = os.environ.get("PROJECT_OS_BRAIN_DIR",
                           os.path.expanduser("~/.project-os/brain"))
BRAIN_PY = os.path.join(BRAIN_DIR, ".venv", "bin", "python")
BRAIN_QUERY = os.path.join(BRAIN_DIR, "brain_query.py")


def fetch_brief(query):
    """Shell to the brain CLI. Returns (brief_md, source_note) or (None, reason)."""
    py = BRAIN_PY if os.path.exists(BRAIN_PY) else sys.executable
    if not os.path.exists(BRAIN_QUERY):
        return None, f"brain_query.py not found at {BRAIN_QUERY}"
    try:
        r = subprocess.run([py, BRAIN_QUERY, "brief", query],
                           capture_output=True, text=True, timeout=90)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip(), f"brain_query.py brief \"{query}\""
        return None, (r.stderr or "empty output").strip()[:300]
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def extract_donts(brief_md):
    """Pull the DON'T bullet lines out of a brief (heuristic: bullets in a DO/DON'T
    section, or any bullet starting with a negation)."""
    donts, in_dont = [], False
    for ln in brief_md.splitlines():
        if re.match(r"^#{1,4}\s", ln):
            in_dont = bool(re.search(r"don'?t|avoid|never", ln, re.I))
            continue
        b = re.match(r"^\s*[-*]\s+(.*)", ln)
        if not b:
            continue
        text = b.group(1).strip()
        if in_dont or re.match(r"(?i)(don'?t|never|no |avoid )", text):
            donts.append(text)
    return donts


def slug(s, n=32):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:n].strip("-") or "task"


def _publish_pair(worker_path, worker_text, rubric_path, rubric_text):
    """Publish both new packet files or neither, never overwriting a path."""
    parent = os.path.dirname(os.path.abspath(worker_path))
    if parent != os.path.dirname(os.path.abspath(rubric_path)):
        raise OSError("worker and rubric outputs must share one directory")
    staged = []
    published = []
    try:
        for label, text in (("worker", worker_text), ("rubric", rubric_text)):
            fd, temp_path = tempfile.mkstemp(
                prefix=f".promptsmith-{label}-", dir=parent, text=True)
            staged.append(temp_path)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
        for temp_path, target in zip(staged, (worker_path, rubric_path)):
            os.link(temp_path, target)
            published.append((temp_path, target))
    except Exception:
        for temp_path, target in reversed(published):
            try:
                if os.path.samestat(os.lstat(temp_path), os.lstat(target)):
                    os.unlink(target)
            except FileNotFoundError:
                pass
        raise
    finally:
        for temp_path in staged:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass


def _preflight_output(out_dir, worker_path, rubric_path, index_path, no_index):
    if os.path.lexists(out_dir) and os.path.islink(out_dir):
        raise ValueError(f"--out-dir must not be a symlink: {out_dir}")
    if os.path.exists(out_dir) and not os.path.isdir(out_dir):
        raise ValueError(f"--out-dir is not a directory: {out_dir}")
    for path in (worker_path, rubric_path):
        if os.path.lexists(path):
            kind = "symlink" if os.path.islink(path) else "existing path"
            raise ValueError(f"refusing to overwrite {kind}: {path}")
    if not no_index and os.path.lexists(index_path) and os.path.islink(index_path):
        raise ValueError(f"packet index must not be a symlink: {index_path}")


def _table_cell(value):
    """Render untrusted text as one Markdown table cell."""
    return " ".join(str(value).replace("|", " / ").split())


def _parse_args(args):
    """Validate the whole command before brief reads, helper calls or writes."""
    valued = {"--task", "--query", "--packet-id", "--out-dir", "--brief-file"}
    values = {}
    i = 0
    while i < len(args):
        name = args[i]
        if name not in valued and name != "--no-index":
            raise ValueError(f"unrecognized argument: {name!r}")
        if name in values:
            raise ValueError(f"duplicate option: {name}")
        if name == "--no-index":
            values[name] = True
            i += 1
            continue
        if i + 1 >= len(args) or args[i + 1].startswith("--"):
            raise ValueError(f"missing value for {name}")
        value = args[i + 1]
        if not value.strip():
            raise ValueError(f"empty value for {name}")
        values[name] = value
        i += 2
    if "--task" not in values:
        raise ValueError("--task is required")
    return values


def main():
    try:
        args = _parse_args(sys.argv[1:])
    except ValueError as exc:
        print(f"usage error: {exc}", file=sys.stderr)
        sys.exit(2)
    task = args["--task"]
    query = args.get("--query", task)
    today = datetime.date.today().isoformat()
    pid = args.get("--packet-id", f"psmith-{today}-{slug(task)}")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,119}", pid):
        print("usage error: refusing --packet-id: use 1–120 ASCII letters, digits, "
              "hyphens or underscores, starting with a letter or digit",
              file=sys.stderr)
        sys.exit(2)
    out_dir = args.get("--out-dir", os.path.join(ROOT, "blackboard", "packets"))
    brief_file = args.get("--brief-file")
    no_index = args.get("--no-index", False)
    wp = os.path.join(out_dir, f"{pid}-worker-prompt.md")
    rp = os.path.join(out_dir, f"{pid}-rubric.md")
    idx = os.path.join(ROOT, "blackboard", "05-agent-packets.md")

    if brief_file:
        try:
            with open(brief_file, encoding="utf-8") as brief_handle:
                brief_md = brief_handle.read().strip()
        except OSError as exc:
            detail = str(exc).replace("\n", " ")
            print(f"usage error: could not read --brief-file {brief_file!r}: {detail}",
                  file=sys.stderr)
            sys.exit(2)
        if not brief_md:
            print(f"usage error: --brief-file {brief_file!r} is empty; "
                  "provide a brief containing non-whitespace text", file=sys.stderr)
            sys.exit(2)
        source = f"pre-fetched: {brief_file}"
    else:
        brief_md, source = fetch_brief(query)

    available = brief_md is not None
    if not available:
        reason = source
        brief_md = ("> **BRAIN-UNAVAILABLE** — brief could not be fetched "
                    f"({reason}). Do NOT invent taste. Either re-run promptsmith "
                    "when the brain is reachable, or have the orchestrator call "
                    "mcp__brain__brief and pass --brief-file.")
        source = "unavailable"
    donts = extract_donts(brief_md) if available else []

    try:
        _preflight_output(out_dir, wp, rp, idx, no_index)
    except ValueError as exc:
        print(f"usage error: {exc}", file=sys.stderr)
        sys.exit(2)

    dont_block = ("\n".join(f"- [ ] {d}" for d in donts) if donts else
                  "- [ ] Extract every DON'T from the brief above; each is a hard constraint."
                  if available else
                  "- [ ] BRAIN-UNAVAILABLE — no DON'T list; human gate must review everything.")

    worker_text = f"""# Worker Prompt — {pid}

Packet ID: {pid}
Agent: (assign)
Status: Draft
Compiled: {today} by promptsmith (brief source: {source})

## Task

{task}

## Taste brief — build to this literally

{brief_md}

## Hard constraints

- Honor every DON'T in the brief. Each violation is an automatic Evaluator fail.
- Use the brief's palette values literally; do not invent adjacent colors.
- Match the aesthetic register and mood lines; when in doubt, quieter wins.
- Motion/animation work: capture a filmstrip (>= 6 frames across the motion), not a
  single still — one frame cannot verify motion.
- Do not claim taste approval. Your job ends at "candidate + evidence"; the human
  gate decides.

## Deliverables

- The artifact.
- Evidence pack: screenshots (filmstrip if anything moves) + one line per DON'T
  stating how it was checked.
"""

    rubric_text = f"""# Evaluator Rubric — {pid}

Packet ID: {pid}
Status: Draft
Compiled: {today} by promptsmith from the SAME brief as the worker prompt
(maker and checker share taste priors).

## Task

{task}

## Taste brief — evaluate against this literally

{brief_md}

## Scoring

**Pass bar:** >= 0.80 weighted, no criterion < 0.50.
**Auto-fail:** any DON'T violation rejects regardless of weighted score.

| Criterion | Weight | Score | Notes |
|---|---|---|---|
| Task completion (the worker task, fully) | 0.35 | | |
| Palette fidelity (literal brief values) | 0.20 | | |
| Register & mood match (brief language) | 0.20 | | |
| Craft: spacing, type, motion quality | 0.25 | | |

## Auto-fail checks (from the brief's DON'Ts)

{dont_block}

## Evaluator instructions

1. Score against the embedded brief, not personal taste. Cite file/pixel evidence
   in every Notes cell.
2. Any DON'T violated -> verdict **Reject**, regardless of weighted score.
3. On Reject: write one lesson line to `memory/self-improvement-loop.md`
   (what was rejected, why, future behavior) and record the variant + score via
   `python3 scripts/evolution.py record`.
4. On Approve: forward to the human taste gate with the evidence pack. Approve is
   a recommendation, not a final pass — only the human gate closes taste.
"""

    try:
        os.makedirs(out_dir, exist_ok=True)
        _preflight_output(out_dir, wp, rp, idx, no_index)
        _publish_pair(wp, worker_text, rp, rubric_text)
    except (OSError, ValueError) as exc:
        detail = str(exc).replace("\n", " ")
        print(f"FAILED: could not publish promptsmith packet pair: {detail}",
              file=sys.stderr)
        sys.exit(2 if isinstance(exc, ValueError) or isinstance(exc, FileExistsError) else 1)

    if not no_index:
        if os.path.exists(idx):
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import bb_lock
            # Preserve the destination while keeping whitespace and delimiters
            # from changing the Markdown table. Default paths stay unchanged.
            packet_ref = quote(os.path.relpath(os.path.abspath(wp),
                                              os.path.dirname(idx)), safe="/")
            cells = (pid, "promptsmith", _table_cell(task)[:60], "Draft",
                     packet_ref)
            row = "| " + " | ".join(_table_cell(cell) for cell in cells) + " |"
            token = bb_lock.acquire(idx, agent="promptsmith", wait=10)
            if token:
                try:
                    flags = os.O_WRONLY | os.O_APPEND
                    if hasattr(os, "O_NOFOLLOW"):
                        flags |= os.O_NOFOLLOW
                    fd = os.open(idx, flags)
                    with os.fdopen(fd, "a", encoding="utf-8") as f:
                        f.write(row + "\n")
                        f.flush()
                        os.fsync(f.fileno())
                finally:
                    bb_lock.release(idx, agent="promptsmith", token=token)
            else:
                print(f"WARN: could not lock packet index; add row manually:\n{row}",
                      file=sys.stderr)

    print(json.dumps({"packet_id": pid, "worker_prompt": wp, "rubric": rp,
                      "brief_source": source, "donts_extracted": len(donts),
                      "brain_available": available}, indent=2))
    sys.exit(0 if available else 1)


if __name__ == "__main__":
    main()
