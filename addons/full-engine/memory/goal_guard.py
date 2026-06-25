#!/usr/bin/env python3
"""Goal-drift guard: hash the canonical goal and compare to the roster's record.

The CEO records the canonical goal's hash in 21-agent-roster.md at Wave 0, then
re-checks at the start of every wave. This tool makes that check deterministic:
it hashes the canonical goal line in a 00-project-goal.md and compares it to the
`Goal hash:` value recorded in 21-agent-roster.md.

Usage:
  python3 memory/goal_guard.py <goal_md> <roster_md>   # MATCH/DRIFT, exit 0/1
  python3 memory/goal_guard.py --hash <goal_md>        # print the computed hash
  python3 memory/goal_guard.py --selftest

Standard library only. No network access.
"""
import argparse
import hashlib
import re
import sys


def canonical_goal(goal_md_text):
    """First real line after the '## Canonical Goal' heading (no comments/blanks)."""
    grab = False
    for line in goal_md_text.splitlines():
        s = line.strip()
        if s.startswith("## Canonical Goal"):
            grab = True
            continue
        if grab:
            if not s or s.startswith("<!--") or s.startswith("#"):
                continue
            return s
    return ""


def goal_hash(goal_line):
    """Short, stable hash of a goal line (whitespace-normalized, 12 hex chars)."""
    norm = re.sub(r"\s+", " ", goal_line).strip()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:12]


def recorded_hash(roster_text):
    """Pull the `Goal hash:` value recorded in 21-agent-roster.md (or '')."""
    for line in roster_text.splitlines():
        m = re.match(r"\s*Goal hash:\s*(.*)$", line, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return ""


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def compare(goal_path, roster_path):
    line = canonical_goal(_read(goal_path))
    want = goal_hash(line)
    have = recorded_hash(_read(roster_path))
    if have and have == want:
        print("MATCH: goal hash %s (goal: %r)" % (want, line[:60]))
        return 0
    print("DRIFT: computed %s != recorded %r (goal: %r)" % (want, have or "<none>", line[:60]))
    return 1


def selftest():
    import os
    import tempfile
    base = tempfile.mkdtemp()
    try:
        goal = ("# 00\n## Canonical Goal (one sentence)\n\n<!-- c -->\n"
                "Build a credit-card tracker that flags overspend.\n")
        gp = os.path.join(base, "00-project-goal.md")
        with open(gp, "w") as fh:
            fh.write(goal)
        h = goal_hash(canonical_goal(goal))

        # MATCH roster
        rp_ok = os.path.join(base, "roster_ok.md")
        with open(rp_ok, "w") as fh:
            fh.write("Canonical goal (from 00):\nGoal hash: %s\n" % h)
        assert compare(gp, rp_ok) == 0, "expected MATCH"

        # DRIFT roster (stale hash)
        rp_bad = os.path.join(base, "roster_bad.md")
        with open(rp_bad, "w") as fh:
            fh.write("Goal hash: deadbeef0000\n")
        assert compare(gp, rp_bad) == 1, "expected DRIFT"

        # Whitespace-insensitivity: extra spaces hash the same.
        assert goal_hash("a  b   c") == goal_hash("a b c"), "ws not normalized"
        # Hash is the documented short length.
        assert len(h) == 12, "hash length changed"
    finally:
        import shutil
        shutil.rmtree(base)
    print("goal_guard selftest: OK")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Goal-drift hash guard.")
    ap.add_argument("goal", nargs="?", help="path to 00-project-goal.md")
    ap.add_argument("roster", nargs="?", help="path to 21-agent-roster.md")
    ap.add_argument("--hash", dest="hash_only", metavar="GOAL_MD",
                    help="print the computed goal hash for GOAL_MD and exit")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(selftest())
    if args.hash_only:
        print(goal_hash(canonical_goal(_read(args.hash_only))))
        sys.exit(0)
    if not args.goal or not args.roster:
        ap.error("need <goal_md> <roster_md> (or --hash GOAL_MD / --selftest)")
    sys.exit(compare(args.goal, args.roster))


if __name__ == "__main__":
    main()
