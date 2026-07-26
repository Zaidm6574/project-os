#!/usr/bin/env python3
"""harvest — memory-harvest automation: run closeout -> shared-brain lessons.

The manual protocol (each run's 19-memory-harvest.md) stays the source of truth;
this automates the mechanical half: extract candidate lessons, dedupe against the
shared brain, and stage them as ready-to-append JSONL for human/agent approval.
Proposals are DATA (like plans) — nothing enters the brain without `apply`.

Usage:
  python3 scripts/harvest.py status                 # which done runs are unharvested
  python3 scripts/harvest.py scan  <run>            # extract + dedupe -> proposals JSONL
  python3 scripts/harvest.py apply <proposals.jsonl>  # append via brain_append, mark run

Extraction sources, in order:
  1. runs/<run>/19-memory-harvest.md   (## Lessons / User preferences / Project patterns / Safeguards)
  2. runs/<run>/12-evaluation-log.md   (table rows mentioning fail/revise — fallback only)

Dedupe: normalized (lowercase alnum) containment either way vs every shared-brain line.
"""
import os, re, sys, json, glob, datetime, subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # project-os/
HOME = os.path.expanduser("~")
SHARED_BRAIN = os.environ.get(
    "PROJECT_OS_SHARED_BRAIN",
    os.path.join(HOME, ".project-os", "central-brain", "shared-brain.jsonl"))
RUNS = os.path.join(ROOT, "runs")
PACKETS = os.path.join(ROOT, "blackboard", "packets")
MARKER = ".harvested"

SECTION_TYPES = {  # 19-memory-harvest.md heading prefix -> shared-brain type
    # Three heading dialects exist in the wild: plural ("## Lessons"), singular
    # kebab ("## lesson"), and the public template's long forms ("## User
    # Preferences Observed", "## Next-Kickoff Safeguards"). Match all of them —
    # a missed heading silently drops lessons (bit us twice on 2026-07-02).
    "lesson": "lesson",
    "user preference": "preference",
    "user-preference": "preference",
    "project pattern": "pattern",
    "project-pattern": "pattern",
    "safeguard": "safeguard",
    "next-kickoff safeguard": "safeguard",
}
# Deciding "is this a rejection marker or a lesson that says the word reject?"
# by text alone is not solvable, and three successive regexes proved it:
#   v1 `\breject|private[- ]only\b`  -> dropped every lesson containing "reject"
#   v2 fully end-anchored               -> let "Rejected (see note)" harvest through
#   v3 continuation-word allowlist      -> still missed "Rejected because ...",
#                                          and swallowed "Reject-first workflow ..."
# So stop guessing and use the STRUCTURE instead (adversarial verify 2026-07-25):
#
#   * In a TABLE, the lesson is the FIRST cell and a verdict lives in a LATER
#     column. Marker matching therefore runs on cells[1:] only, and may be
#     permissive there — a status cell can say anything after the marker.
#   * A BULLET has no status column, so only an EXPLICIT annotation counts:
#     either a directive that is never ordinary prose ("do not harvest",
#     "private-only"), or a marker followed by real annotation punctuation
#     ("Rejected: dupe", "[Rejected] ...").
#
# `(?![-\w])` keeps the marker a standalone word, so the compound
# "Reject-first workflow ..." is not treated as a rejection.
_MARKER = (r"(?:rejected|rejection|reject|private[\s-]?only|"
           r"do[\s-]?not[\s-]?harvest)")

# For table cells past the first: marker at the start, anything after it.
REJECT_CELL = re.compile(r"^[\W_]*" + _MARKER + r"(?![-\w])", re.I)

# Directives that are never ordinary prose — safe to honour anywhere.
REJECT_DIRECTIVE = re.compile(
    r"^[\W_]*(?:private[\s-]?only|do[\s-]?not[\s-]?harvest)(?![-\w])", re.I)

# For free-text bullets: require explicit annotation punctuation after the
# marker, so an ordinary sentence like "Rejection criteria belong in the
# rubric" is kept while "Rejected: dupe" is dropped.
REJECT_BULLET = re.compile(
    r"^[\W_]*" + _MARKER + r"(?![-\w])[\s*_`]*[:\]\)\-–—]", re.I)


def _is_reject_bullet(text):
    return bool(REJECT_DIRECTIVE.match(text) or REJECT_BULLET.match(text))


# Kept as an alias so existing callers/tests that reference REJECT_ROW keep
# working; it is the TABLE-cell rule.
REJECT_ROW = REJECT_CELL

# Rows skipped because a cell carried a rejection/private marker. Reported at
# the end of a harvest so the filter is auditable instead of invisible.
DROPPED = []


def norm(t):
    return re.sub(r"[^a-z0-9]+", "", (t or "").lower())


def brain_norms():
    out = []
    if os.path.isfile(SHARED_BRAIN):
        with open(SHARED_BRAIN, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(norm(json.loads(line).get("text", "")))
                except ValueError:
                    continue
    return [n for n in out if n]


def is_dupe(text, norms):
    n = norm(text)
    if len(n) < 20:
        return True  # too short to be a lesson; treat as noise
    # Dupe = the new lesson is contained in an existing entry (or equal).
    # Deliberately NOT bidirectional: a short old entry contained inside a
    # longer new lesson must not silently drop the richer new one.
    return any(n in b for b in norms)


def bullets_by_section(md):
    """Yield (type, text) from ## sections of a 19-memory-harvest.md.
    Accepts BOTH content shapes: bullet lists and markdown tables (the public
    template uses tables; a row is text = first cell, and any row marked
    Rejected/Private-only is never harvested)."""
    cur = None
    for line in md.splitlines():
        h = re.match(r"^##\s+(.+?)\s*(?:\(.*\))?\s*$", line)
        if h:
            key = h.group(1).strip().lower()
            cur = next((v for k, v in SECTION_TYPES.items() if key.startswith(k)), None)
            continue
        if not cur:
            continue
        b = re.match(r"^[-*]\s+(.+)$", line)
        if b:
            text = re.sub(r"\*\*(.+?)\*\*", r"\1", b.group(1)).strip()
            # The bullet branch used to yield unconditionally while the table
            # branch enforced REJECT_ROW per cell -- a bullet like "Private-only:
            # ..." harvested straight through with no exclusion at all
            # (audit 2026-07-25). Apply the same check here.
            if _is_reject_bullet(text):
                DROPPED.append(line.strip()[:120])
                continue
            if text:
                yield cur, text
            continue
        if line.strip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            first = cells[0] if cells else ""
            # Check CELLS, not the raw line: a status cell of "Rejected" skips
            # the row, but the same word inside the lesson text does not.
            if (not first or set(first) <= {"-", ":", " "}  # separator row
                    or first.lower() in ("lesson", "preference", "pattern", "safeguard")):
                continue
            # cells[1:] only: cells[0] IS the lesson text, so matching a
            # marker there dropped real lessons (adversarial verify 2026-07-25).
            if any(REJECT_CELL.match(c) for c in cells[1:]):
                # Record it. Silent filtering is why the old over-broad pattern
                # went unnoticed: a harvest that drops rows must say how many.
                DROPPED.append(line.strip()[:120])
                continue
            text = re.sub(r"\*\*(.+?)\*\*", r"\1", first).strip()
            extra = next((c for c in cells[1:] if c and not REJECT_CELL.match(c)), "")
            if text:
                yield cur, (text + (f" — {extra}" if extra else ""))[:400]


def eval_log_candidates(md):
    """Fallback: fail/revise table rows from 12-evaluation-log.md."""
    for line in md.splitlines():
        if not line.strip().startswith("|"):
            continue
        if re.search(r"\bfail(ed)?\b|\brevise[d]?\b|\bwrong\b|\bbug\b", line, re.I):
            cells = [c.strip() for c in line.split("|") if c.strip() and set(c.strip()) != {"-"}]
            if len(cells) >= 2:
                yield "lesson", " — ".join(cells)[:400]


def run_dir(run):
    d = run if os.path.isdir(run) else os.path.join(RUNS, run)
    if not os.path.isdir(d):
        sys.exit(f"no such run: {run}")
    return d


def read(p):
    try:
        with open(p, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def unharvested():
    out = []
    for d in sorted(glob.glob(os.path.join(RUNS, "*"))):
        if not os.path.isdir(d):
            continue
        done = os.path.isfile(os.path.join(d, "13-delivery-report.md"))
        if done and not os.path.isfile(os.path.join(d, MARKER)):
            out.append(os.path.basename(d))
    return out


def cmd_status():
    u = unharvested()
    print("unharvested done runs: " + (", ".join(u) if u else "none"))
    return 1 if u else 0


def cmd_scan(run):
    d = run_dir(run)
    slug = os.path.basename(d)
    harvest_md = read(os.path.join(d, "19-memory-harvest.md"))
    cands = list(bullets_by_section(harvest_md)) if harvest_md else \
        list(eval_log_candidates(read(os.path.join(d, "12-evaluation-log.md"))))
    norms = brain_norms()
    today = datetime.date.today().isoformat()
    fresh, skipped = [], 0
    for i, (typ, text) in enumerate(cands, 1):
        if is_dupe(text, norms):
            skipped += 1
            continue
        fresh.append({
            "id": f"harvest-{slug}-{today}-{i:02d}", "origin_id": f"{slug}/19-memory-harvest.md",
            "project_id": slug, "project_name": slug, "source": "harvest.py",
            "tags": [typ, "harvest"], "text": text, "ts": today, "type": typ,
        })
    if not fresh:
        why = f"all {skipped} already in the brain" if cands else \
            "no harvest sources found (no 19-memory-harvest.md sections, no eval-log hits)"
        print(f"{slug}: {why} — nothing to stage")
        # nothing new is still a completed harvest
        open(os.path.join(d, MARKER), "w", encoding="utf-8").write(today + "\n")
        return 0
    os.makedirs(PACKETS, exist_ok=True)
    out = os.path.join(PACKETS, f"harvest-{slug}-{today}.jsonl")
    with open(out, "w", encoding="utf-8") as f:
        for o in fresh:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    print(f"{slug}: staged {len(fresh)} new (skipped {skipped} dupes) -> {out}")
    if DROPPED:
        print(f"  filtered {len(DROPPED)} row(s) marked rejected/private-only:")
        for row in DROPPED[:5]:
            print(f"    - {row}")
        if len(DROPPED) > 5:
            print(f"    ... and {len(DROPPED) - 5} more")
    print(f"review the file, then: python3 scripts/harvest.py apply {out}")
    return 0


def cmd_apply(path):
    if not os.path.isfile(path):
        sys.exit(f"no such proposals file: {path}")
    ba = os.path.join(ROOT, "scripts", "brain_append.py")
    # Parse EVERY line before appending any of them. The old loop parsed and
    # appended in the same pass, so a malformed line N left lines 1..N-1
    # already committed to the brain despite the docstring's promise to
    # "refuse malformed before touching the brain" (audit 2026-07-25).
    lines, slugs = [], []
    with open(path, encoding="utf-8") as f:
        for i, raw in enumerate(f, 1):
            line = raw.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except ValueError as e:
                sys.exit(f"malformed proposals line {i}, nothing appended: {e}")
            lines.append(line)
            slugs.append(o.get("project_id"))
    n = 0
    for line in lines:
        r = subprocess.run([sys.executable, ba, "--line", line, "--agent", "harvest",
                            "--no-reindex"], capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit(f"brain_append failed on line {n + 1}: "
                     f"{(r.stderr or r.stdout).strip()[:300]}")
        n += 1
    # one reindex for the whole batch
    subprocess.run([sys.executable, os.path.join(ROOT, "memory", "mneme_adapter.py"), "build"],
                   check=True)
    # Mark EVERY contributing run harvested, not just the last project_id seen:
    # a batch spanning proja/projb/projc used to leave proja and projb
    # unmarked even though their lessons were appended (audit 2026-07-25).
    marked = []
    for slug in dict.fromkeys(s for s in slugs if s):
        if os.path.isdir(os.path.join(RUNS, slug)):
            with open(os.path.join(RUNS, slug, MARKER), "w", encoding="utf-8") as f:
                f.write(datetime.date.today().isoformat() + "\n")
            marked.append(slug)
    print(f"appended {n} lessons + reindexed; marked {', '.join(marked) if marked else '?'} harvested")
    return 0


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a or a[0] not in ("status", "scan", "apply"):
        print(__doc__)
        sys.exit(2)
    if a[0] == "status":
        sys.exit(cmd_status())
    if len(a) < 2:
        sys.exit(f"{a[0]} needs an argument")
    sys.exit(cmd_scan(a[1]) if a[0] == "scan" else cmd_apply(a[1]))
