#!/usr/bin/env python3
"""brain_scale — instrument the shared brain against the flat-index ceiling.

Karpathy's LLM-wiki finding (verified, July 2026 radar run): a flat index works
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

Status: OK < 60% of ceiling · WATCH 60-85% · CUTOVER > 85%  (worst dimension wins)
Exit codes: 0 OK · 1 WATCH · 2 CUTOVER

Usage: python3 scripts/brain_scale.py [--json]
"""
import os, sys, json, glob

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # project-os/
HOME = os.path.expanduser("~")
SHARED_BRAIN = os.path.join(HOME, ".project-os", "central-brain", "shared-brain.jsonl")
OSVEC = os.path.join(ROOT, "memory", "mneme_index.json")
# Optional taste-brain inventory (personal-brain integration). Point the env var
# at your own node inventory JSON, or leave unset — the dimension reports n/a.
TASTE_INV = os.environ.get(
    "PROJECT_OS_TASTE_INVENTORY",
    os.path.join(HOME, ".project-os", "brain", "data", "_node_inventory.json"))

SOURCES_CEIL = 100   # Karpathy: ~100 sources
PAGES_CEIL = 200     # Karpathy: ~hundreds of pages; plan at 200


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


def main():
    shared = count_lines(SHARED_BRAIN)
    run_pages = len(glob.glob(os.path.join(ROOT, "runs", "**", "*.md"), recursive=True))
    bb_pages = len(glob.glob(os.path.join(ROOT, "blackboard", "**", "*.md"), recursive=True))
    pages = run_pages + bb_pages
    mneme = count_json_entries(OSVEC)
    taste = count_json_entries(TASTE_INV)

    dims = [
        ("shared-brain entries", shared, PAGES_CEIL, status_for(shared, PAGES_CEIL)),
        ("pages (runs+blackboard md)", pages, PAGES_CEIL, status_for(pages, PAGES_CEIL)),
    ]
    rank = {"OK": 0, "WATCH": 1, "CUTOVER": 2, "n/a": 0}
    worst = max((d[3] for d in dims), key=lambda s: rank[s])

    # Cutover-aware advice: once the index reports a neural embedder, "schedule
    # the cutover" is stale — the pressure valve becomes pruning/harvest hygiene.
    embedder = ""
    try:
        with open(OSVEC, encoding="utf-8") as f:
            embedder = json.load(f).get("embedder", "")
    except (OSError, ValueError):
        pass
    neural = embedder.startswith("neural-")

    out = {
        "dimensions": [{"name": n, "count": c, "ceiling": ceil, "status": s}
                       for n, c, ceil, s in dims],
        "context": {"mneme_index_entries": mneme, "taste_brain_nodes": taste,
                    "run_pages": run_pages, "blackboard_pages": bb_pages,
                    "embedder": embedder or "none"},
        "overall": worst,
        "rule": (("CUTOVER: neural retrieval is live — prune/archive aggressively; ceiling pressure is now about md sprawl, not retrieval. "
                  "WATCH: neural retrieval live (cutover done 2026-07-02); keep harvest/prune hygiene. "
                  "OK: healthy.") if neural else
                 ("CUTOVER: make Mneme (or a neural upgrade behind the same interface) "
                  "the PRIMARY retrieval path and demote the flat index to fallback. "
                  "WATCH: schedule the cutover. OK: flat index is fine.")),
    }

    if "--json" in sys.argv:
        print(json.dumps(out, indent=2))
    else:
        print(f"brain scale — flat-index ceiling check (Karpathy ~{SOURCES_CEIL} sources / ~{PAGES_CEIL} pages)\n")
        for n, c, ceil, s in dims:
            bar = "" if c is None else f"  {c}/{ceil} ({100*c//ceil}%)"
            print(f"  [{s:>7}] {n}{bar}")
        print(f"\n  context: mneme entries={mneme} · taste-brain nodes={taste} "
              f"· run pages={run_pages} · blackboard pages={bb_pages}")
        print(f"\n  OVERALL: {worst} — {out['rule'].split('. ')[0 if worst=='CUTOVER' else 1 if worst=='WATCH' else 2]}")
    sys.exit(rank[worst])


if __name__ == "__main__":
    main()
