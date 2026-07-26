#!/usr/bin/env python3
"""brain_scale — instrument the shared brain against the flat-index ceiling.

Karpathy's LLM-wiki observation: a flat index works
to ~100 sources / ~200 pages, after which hybrid retrieval (BM25 + embeddings +
graph) must take over as PRIMARY retrieval. This counts what we actually have and
says how close we are, so the Mneme (formerly TurboVec/OSVec) cutover happens before retrieval
degrades — not after.

Counts:
  - shared-brain entries   (~/.project-os/central-brain/shared-brain.jsonl lines)
  - run pages              (runs/**/*.md)
  - blackboard pages       (blackboard/**/*.md)
  - mneme index entries    (memory/mneme_index.json)
  - taste-brain nodes      (PROJECT_OS_TASTE_INVENTORY json, if present)
  - archive entries + stale `interest` entries (>60d — archive candidates)

Calibration: with a NEURAL index live, agents retrieve top-k semantically and
never flat-read the file, so the active-entries ceiling relaxes to a soft 400
(quality/dedup pressure, relieved via scripts/brain_archive.py — archived
entries stay in the index). The md-pages ceiling stays at 200 regardless:
vectors don't shrink markdown sprawl.

Status: OK < 60% of ceiling · WATCH 60-85% · CUTOVER > 85%  (worst dimension wins)
Exit codes: 0 OK · 1 WATCH · 2 CUTOVER · 3 n/a (shared brain missing)

Usage: python3 scripts/brain_scale.py [--json]
"""
import datetime as _dt
import os, sys, json, glob

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # project-os/
HOME = os.path.expanduser("~")
SHARED_BRAIN = os.environ.get(
    "PROJECT_OS_SHARED_BRAIN",
    os.path.join(HOME, ".project-os", "central-brain", "shared-brain.jsonl"))
OSVEC = os.path.join(ROOT, "memory", "mneme_index.json")
# Optional taste-brain inventory (personal-brain integration). Point the env var
# at your own node inventory JSON, or leave unset — the dimension reports n/a.
TASTE_INV = os.environ.get(
    "PROJECT_OS_TASTE_INVENTORY",
    os.path.join(HOME, ".project-os", "brain", "data", "_node_inventory.json"))

SOURCES_CEIL = 100        # Karpathy: ~100 sources
PAGES_CEIL = 200          # Karpathy: ~hundreds of pages; plan at 200
ENTRIES_CEIL_NEURAL = 400 # soft ceiling for the ACTIVE brain once neural retrieval is primary
INTEREST_STALE_DAYS = 60  # interest entries older than this are archive candidates


def count_lines(p):
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            return sum(1 for ln in f if ln.strip())
    except FileNotFoundError:
        return None


def count_json_entries(p):
    try:
        d = json.load(open(p, encoding="utf-8"))
        for k in ("entries", "items", "vectors", "nodes"):
            if isinstance(d, dict) and isinstance(d.get(k), (list, dict)):
                return len(d[k])
        return len(d) if isinstance(d, (list, dict)) else None
    except Exception:
        return None


def status_for(n, ceil):
    if n is None:
        return "n/a"
    pct = n / ceil
    return "CUTOVER" if pct > 0.85 else "WATCH" if pct >= 0.60 else "OK"


def count_stale_interest(p, days):
    """interest-type entries older than `days` — archive candidates."""
    try:
        n = 0
        with open(p, encoding="utf-8", errors="replace") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    o = json.loads(ln)
                except ValueError:
                    continue
                # 2026-07-25: a non-dict JSONL line (e.g. a bare int) has no
                # .get() — crashed with an unhandled AttributeError and exit 1,
                # indistinguishable from a real WATCH status. Skip it instead.
                if not isinstance(o, dict):
                    continue
                if (o.get("type") or o.get("kind")) != "interest":
                    continue
                ts = str(o.get("ts") or o.get("date") or "")[:10]
                try:
                    then = _dt.datetime.strptime(ts, "%Y-%m-%d")
                except ValueError:
                    continue
                if (_dt.datetime.now() - then).days > days:
                    n += 1
        return n
    except FileNotFoundError:
        return None


def main():
    shared = count_lines(SHARED_BRAIN)
    archive = count_lines(SHARED_BRAIN.replace(".jsonl", "-archive.jsonl"))
    run_pages = len(glob.glob(os.path.join(ROOT, "runs", "**", "*.md"), recursive=True))
    bb_pages = len(glob.glob(os.path.join(ROOT, "blackboard", "**", "*.md"), recursive=True))
    pages = run_pages + bb_pages
    mneme = count_json_entries(OSVEC)
    taste = count_json_entries(TASTE_INV)
    stale_interest = count_stale_interest(SHARED_BRAIN, INTEREST_STALE_DAYS)

    # Neural retrieval changes the entries calibration: agents query top-k
    # instead of flat-reading, so the active-file ceiling relaxes to 400.
    embedder = ""
    try:
        with open(OSVEC, encoding="utf-8") as f:
            embedder = json.load(f).get("embedder", "")
    except (OSError, ValueError):
        pass
    neural = embedder.startswith("neural-")
    # 2026-07-25: flat mode must calibrate shared-brain entries against
    # SOURCES_CEIL (~100 sources), not PAGES_CEIL (~200 md pages) — the two
    # dimensions count different things and reusing PAGES_CEIL here silently
    # halved the reported retrieval-degradation risk in flat (non-neural) mode.
    entries_ceil = ENTRIES_CEIL_NEURAL if neural else SOURCES_CEIL

    dims = [
        ("shared-brain entries (active)", shared, entries_ceil, status_for(shared, entries_ceil)),
        ("pages (runs+blackboard md)", pages, PAGES_CEIL, status_for(pages, PAGES_CEIL)),
    ]
    # n/a (missing input) ranks WORST and exits distinctly (3): a missing
    # shared brain must never read as "healthy" to callers or the nightly log.
    rank = {"OK": 0, "WATCH": 1, "CUTOVER": 2, "n/a": 3}
    worst = max((d[3] for d in dims), key=lambda s: rank[s])

    out = {
        "dimensions": [{"name": n, "count": c, "ceiling": ceil, "status": s}
                       for n, c, ceil, s in dims],
        "context": {"mneme_index_entries": mneme, "taste_brain_nodes": taste,
                    "run_pages": run_pages, "blackboard_pages": bb_pages,
                    "archive_entries": archive or 0,
                    "stale_interest_gt%dd" % INTEREST_STALE_DAYS: stale_interest,
                    "embedder": embedder or "none"},
        "overall": worst,
        "rule": (("CUTOVER: past even the neural soft ceiling — archive with scripts/brain_archive.py (entries stay searchable) and split md sprawl. "
                  "WATCH: neural retrieval is live; relieve pressure via scripts/brain_archive.py and md cleanup. "
                  "OK: healthy.") if neural else
                 ("CUTOVER: make Mneme (or a neural upgrade behind the same interface) "
                  "the PRIMARY retrieval path and demote the flat index to fallback. "
                  "WATCH: schedule the cutover. OK: flat index is fine.")),
    }

    if "--json" in sys.argv:
        print(json.dumps(out, indent=2))
    else:
        ceil_note = f"active entries ceiling {entries_ceil} ({'neural' if neural else 'flat'} calibration)"
        print(f"brain scale — retrieval ceiling check ({ceil_note}; md pages ceiling {PAGES_CEIL})\n")
        for n, c, ceil, s in dims:
            bar = "" if c is None else f"  {c}/{ceil} ({100*c//ceil}%)"
            print(f"  [{s:>7}] {n}{bar}")
        print(f"\n  context: mneme entries={mneme} · taste-brain nodes={taste} "
              f"· run pages={run_pages} · blackboard pages={bb_pages} "
              f"· archived={archive or 0} · stale interest(>{INTEREST_STALE_DAYS}d)={stale_interest}")
        if stale_interest:
            print(f"  archive candidates: python3 scripts/brain_archive.py candidates")
        if worst == "n/a":
            print("\n  OVERALL: n/a — shared brain not found; initialize it "
                  "(scripts/brain_append.py creates the file) before trusting this gauge")
        else:
            print(f"\n  OVERALL: {worst} — {out['rule'].split('. ')[0 if worst=='CUTOVER' else 1 if worst=='WATCH' else 2]}")
    sys.exit(rank[worst])


if __name__ == "__main__":
    main()
