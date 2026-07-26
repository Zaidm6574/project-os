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
# A row is skipped only when a cell IS a rejection/private marker -- not when a
# lesson happens to use the word. The old pattern was
# `\breject|private[- ]only\b`, whose alternation binds as (`\breject`) OR
# (`private[- ]only\b`), so the left branch matched any word starting with
# "reject". A genuine lesson like "Evaluator must reject on missing evidence"
# was silently dropped from every harvest, with no count reported
# (audit 2026-07-25).
# Anchored at the START of the cell but NOT at the end: a status cell reads
# "Rejected" but also "Rejected (see note)" / "REJECTED — superseded", and a
# fully-anchored pattern let every annotated rejection harvest through as an
# approved lesson (adversarial verify 2026-07-25). Starting-anchored still
# spares a real lesson like "Evaluator must reject on missing evidence",
# because that cell starts with "Evaluator".
REJECT_ROW = re.compile(
    r"^[\W_]*(reject(?:ed|ion)?|private[\s-]?only|do[\s-]?not[\s-]?harvest)"
    r"[\s*_`]*(?:$|[(\[\-–—:;,].*)",
    re.I,
)

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
            if any(REJECT_ROW.match(c) for c in cells):
                # Record it. Silent filtering is why the old over-broad pattern
                # went unnoticed: a harvest that drops rows must say how many.
                DROPPED.append(line.strip()[:120])
                continue
            text = re.sub(r"\*\*(.+?)\*\*", r"\1", first).strip()
            extra = next((c for c in cells[1:] if c and not REJECT_ROW.match(c)), "")
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
    n, slug = 0, None
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            o = json.loads(line)  # refuse malformed before touching the brain
            slug = o.get("project_id", slug)
            r = subprocess.run([sys.executable, ba, "--line", line, "--agent", "harvest",
                                "--no-reindex"], capture_output=True, text=True)
            if r.returncode != 0:
                sys.exit(f"brain_append failed on line {n + 1}: "
                         f"{(r.stderr or r.stdout).strip()[:300]}")
            n += 1
    # one reindex for the whole batch
    subprocess.run([sys.executable, os.path.join(ROOT, "memory", "mneme_adapter.py"), "build"],
                   check=True)
    if slug and os.path.isdir(os.path.join(RUNS, slug)):
        with open(os.path.join(RUNS, slug, MARKER), "w", encoding="utf-8") as f:
            f.write(datetime.date.today().isoformat() + "\n")
    print(f"appended {n} lessons + reindexed; marked {slug or '?'} harvested")
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
