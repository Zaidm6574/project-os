#!/usr/bin/env python3
"""Context & cost budget check for Project OS runs (added 2026-07-05).

Why: live billing analysis (2026-07-05) confirmed the audit's #1 cost finding --
the orchestrator's CONTEXT, not the subagents, dominates spend. A fat session
re-reads its whole prefix every turn and re-WRITES it on every cache expiry.

Usage:
  python3 memory/context_budget.py                 # newest session transcript
  python3 memory/context_budget.py --session X.jsonl
  python3 memory/context_budget.py --quiet         # one-line verdict (for hooks)

Verdicts (exit code):
  0 OK          < 120K live context
  1 WATCH       120-200K -- plan a checkpoint at the next wave boundary
  2 CHECKPOINT  > 200K -- close the wave to the blackboard, continue FRESH
Also flags a fat harness (>25 enabled plugins): fix BEFORE wave 1.
"""
import argparse, glob, json, os, sys

FABLE = {"in": 10e-6, "out": 50e-6, "cr": 1e-6, "cw": 12.5e-6}  # $/token, list

def newest_transcript():
    pats = glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl"))
    pats = [p for p in pats if "/memory/" not in p and "agent-" not in os.path.basename(p)]
    return max(pats, key=os.path.getmtime) if pats else None

def last_usage(path):
    usage = None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"usage"' not in line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            u = d.get("message", {}).get("usage")
            if u and (u.get("input_tokens") or u.get("cache_read_input_tokens")):
                usage = u
    return usage

def plugin_count():
    try:
        s = json.load(open(os.path.expanduser("~/.claude/settings.json")))
        return sum(1 for v in s.get("enabledPlugins", {}).values() if v)
    except Exception:
        return -1

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    path = a.session or newest_transcript()
    if not path or not os.path.exists(path):
        print("context_budget: no session transcript found"); return 0
    u = last_usage(path)
    if not u:
        print("context_budget: no usage entries yet"); return 0

    ctx = (u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
           + u.get("cache_creation_input_tokens", 0))
    read_cost = ctx * FABLE["cr"]
    cold_cost = ctx * FABLE["cw"]
    plugins = plugin_count()

    if ctx > 200_000:   verdict, code = "CHECKPOINT", 2
    elif ctx > 120_000: verdict, code = "WATCH", 1
    else:               verdict, code = "OK", 0

    line = (f"context_budget: {verdict} | live context {ctx:,} tok | "
            f"~${read_cost:.2f}/turn cached, ~${cold_cost:.2f} per cold write | "
            f"plugins on: {plugins}")
    print(line)
    if not a.quiet:
        if plugins > 25:
            print("  !! fat harness (>25 plugins) -- trim enabledPlugins before wave 1")
        if code == 2:
            print("  -> close the wave (packets + decisions to blackboard), then continue in a FRESH session")
        elif code == 1:
            print("  -> plan a blackboard checkpoint + fresh session at the next wave boundary")
    return code

if __name__ == "__main__":
    sys.exit(main())
