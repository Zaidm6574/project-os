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


class GoalAnchorMissing(Exception):
    """The goal file has no usable '## Canonical Goal' heading.

    This must never degrade to hashing the empty string. With no anchor EVERY
    goal hashes identically, so once a roster records that hash the guard reports
    MATCH forever regardless of how the goal is rewritten -- drift becomes
    undetectable. A guard that cannot find what it guards must refuse, not answer.
    """


_PLACEHOLDER_GOALS = (
    "replace this line",
    "todo: write the one-sentence canonical goal",
    "tbd",
    "one sentence",
)


def _is_placeholder(line):
    """True when the 'goal' is still template text nobody replaced.

    A placeholder hashes to a stable value across EVERY scaffolded run, which
    is functionally identical to the empty-string hash this guard already
    refuses: record it once in a roster and the guard reports MATCH forever.
    """
    low = line.strip().strip("*`_").casefold()
    return any(low.startswith(p) or low == p for p in _PLACEHOLDER_GOALS)


def canonical_goal(goal_md_text):
    """First real line after the '## Canonical Goal' heading (no comments/blanks).

    Raises GoalAnchorMissing when the heading is absent, or present with no goal
    line beneath it. Fail closed -- see the exception's docstring.
    """
    grab = False
    in_comment = False
    for line in goal_md_text.splitlines():
        s = line.strip()
        if s.startswith("## Canonical Goal"):
            grab = True
            continue
        if not grab:
            continue

        # Track HTML comment BLOCKS, not just lines that start with '<!--'.
        # The old check skipped only the opening line, so with the multi-line
        # explanatory comment this template ships, line 2 of the comment was
        # returned AS THE GOAL -- every project hashed that same sentence, and
        # editing the real goal did not change the hash at all. Exactly the
        # drift-blindness this guard exists to prevent, verified end-to-end
        # through the documented install flow (2026-07-25).
        if in_comment:
            if "-->" in s:
                in_comment = False
                rest = s.split("-->", 1)[1].strip()
                if rest and not rest.startswith("#"):
                    return rest
            continue
        if s.startswith("<!--"):
            if "-->" not in s:
                in_comment = True
            continue

        if not s or s.startswith("#"):
            continue
        if _is_placeholder(s):
            raise GoalAnchorMissing(
                "the goal under '## Canonical Goal' is still the template "
                "placeholder (%r). Every run that leaves it hashes identically, "
                "so drift cannot be detected. Write the real one-sentence goal."
                % s[:60]
            )
        return s
    if grab:
        raise GoalAnchorMissing(
            "'## Canonical Goal' heading present but no goal line beneath it. "
            "Write the one-sentence canonical goal under that heading."
        )
    raise GoalAnchorMissing(
        "no '## Canonical Goal' heading in the goal file. Without it every goal "
        "hashes identically and drift cannot be detected. Add that heading with "
        "the one-sentence canonical goal beneath it."
    )


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


def migrate(goal_path, apply=False):
    """Add a '## Canonical Goal' heading to a goal file that predates it.

    The fail-closed change landed with no migration path, so every goal file
    written before it became uncheckable (exit 2) until hand-edited -- which in
    practice means the guard gets skipped rather than fixed. This makes the
    upgrade mechanical. Seeds the goal line from the first real line under
    '## Summary' when there is one, else leaves a clearly-marked placeholder.
    """
    text = _read(goal_path)
    if "## Canonical Goal" in text:
        print("already anchored: %s" % goal_path)
        return 0

    seed = ""
    grab = False
    for line in text.splitlines():
        s = line.strip()
        if s.lower().startswith("## summary"):
            grab = True
            continue
        if grab:
            if not s or s.startswith("<!--") or s.startswith("#"):
                continue
            seed = s
            break
    placeholder = seed or "TODO: write the one-sentence canonical goal."

    block = (
        "## Canonical Goal\n\n"
        "<!-- ONE sentence, hashed by goal_guard.py and recorded in\n"
        "     21-agent-roster.md. Do not delete this heading. -->\n\n"
        "%s\n\n" % placeholder
    )
    lines = text.splitlines(keepends=True)
    insert_at = 1 if lines and lines[0].startswith("# ") else 0
    if insert_at == 1 and len(lines) > 1 and not lines[1].strip():
        insert_at = 2
    updated = "".join(lines[:insert_at]) + ("\n" if insert_at else "") + block + "".join(lines[insert_at:])

    if not apply:
        print("WOULD MIGRATE: %s" % goal_path)
        print("  seeded goal: %r%s" % (placeholder, "" if seed else "  (PLACEHOLDER -- edit it)"))
        return 0
    with open(goal_path, "w", encoding="utf-8") as fh:
        fh.write(updated)
    print("migrated: %s" % goal_path)
    if not seed:
        print("  WARNING: placeholder goal written -- edit it before trusting the hash")
    return 0


def compare(goal_path, roster_path):
    try:
        line = canonical_goal(_read(goal_path))
    except GoalAnchorMissing as exc:
        # Exit 2 (not 1): this is "cannot check", which is distinct from DRIFT.
        print("UNANCHORED: %s (%s)" % (exc, goal_path))
        return 2
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
    ap.add_argument("--migrate", metavar="GOAL_MD",
                    help="add a '## Canonical Goal' heading to a pre-fix goal file")
    ap.add_argument("--apply", action="store_true",
                    help="with --migrate: write the change (default is a dry run)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.migrate:
        sys.exit(migrate(args.migrate, apply=args.apply))
    if args.selftest:
        sys.exit(selftest())
    if args.hash_only:
        try:
            print(goal_hash(canonical_goal(_read(args.hash_only))))
        except GoalAnchorMissing as exc:
            # Never emit a hash we cannot stand behind -- recording the
            # empty-goal hash in a roster is what permanently blinds the guard.
            sys.stderr.write("UNANCHORED: %s\n" % exc)
            sys.exit(2)
        sys.exit(0)
    if not args.goal or not args.roster:
        ap.error("need <goal_md> <roster_md> (or --hash GOAL_MD / --selftest)")
    sys.exit(compare(args.goal, args.roster))


if __name__ == "__main__":
    main()
