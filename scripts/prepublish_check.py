#!/usr/bin/env python3
"""prepublish_check — scan a tree for credential shapes before you publish it.

Uses THE canonical denylist (brain.SECRET_PATTERNS), not a copy of it.

2026-07-28: the README told readers to run a hand-written `rg` regex with seven
patterns immediately before making a repo public. The canonical list has
twenty-eight. So the last check before publication was the weakest scanner in
the tree: it missed Slack tokens, Figma PATs, SendGrid and Twilio credentials,
GitLab PATs, JWTs, database URLs carrying a password, and the generic
`api_key = ...` catch-all.

The fix is deliberately not "expand the regex". This repo already spent an audit
consolidating five drifted copies of this denylist into one, and a sixth copy
living in Markdown -- where no test can reach it -- would recreate that defect on
the surface where it matters most. Importing the list means it cannot drift.

Usage:
  python3 scripts/prepublish_check.py [PATH]     # default: repo root
  python3 scripts/prepublish_check.py --list     # print the patterns in use

Exit codes:
  0  nothing matched
  1  at least one credential shape found (the file and line are printed)
  2  the canonical denylist could not be imported -- fails CLOSED on purpose:
     a scanner that silently checks nothing is worse than no scanner at all
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Directories that never contain publishable source, and whose contents would
# otherwise bury a real hit in noise.
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv",
             "graphify-out", "store", ".turbovec", "private-memory",
             "private-imports"}

# Binary-ish suffixes: a match inside compressed bytes is meaningless.
SKIP_SUFFIXES = (".gif", ".png", ".jpg", ".jpeg", ".pdf", ".zip", ".gz",
                 ".mov", ".mp4", ".woff", ".woff2", ".ico", ".pyc")


def load_patterns():
    """Import the canonical list. Fail closed if it cannot be found."""
    sys.path.insert(0, os.path.join(ROOT, "addons", "full-engine", "brain"))
    try:
        import brain
    except Exception as exc:  # noqa: BLE001 -- report, do not pretend to scan
        print("prepublish_check: cannot import the canonical denylist from "
              "addons/full-engine/brain/brain.py (%s).\n"
              "Refusing to run a partial scan -- a check that silently verifies "
              "nothing is worse than no check." % exc, file=sys.stderr)
        sys.exit(2)
    finally:
        sys.path.pop(0)
    pats = getattr(brain, "SECRET_PATTERNS", None)
    if not pats:
        print("prepublish_check: brain.SECRET_PATTERNS is empty or missing.",
              file=sys.stderr)
        sys.exit(2)
    return pats


def iter_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if name.lower().endswith(SKIP_SUFFIXES):
                continue
            yield os.path.join(dirpath, name)


def main():
    args = [a for a in sys.argv[1:]]
    patterns = load_patterns()

    if "--list" in args:
        for p in patterns:
            print(getattr(p, "pattern", p))
        return 0

    target = args[0] if args else ROOT
    if not os.path.exists(target):
        print("prepublish_check: no such path: %s" % target, file=sys.stderr)
        return 2

    # Two tiers, because they need different amounts of your attention.
    #
    # A specific shape (AKIA..., figd_..., a PEM header) is high-confidence: the
    # string form itself is the credential. The generic
    # `(api_key|secret|token|...)\s*[:=]\s*...` rule is a deliberate backstop for
    # formats nobody has enumerated, and in SOURCE CODE it also matches every
    # variable named `token`. On this repo it fires 46 times in bb_lock.py alone.
    #
    # Both are reported and either fails the check -- this runs immediately
    # before publication, so it fails closed. But they are printed separately:
    # burying twenty high-confidence hits under fifty variable assignments is how
    # a check trains people to skim past it, and a check nobody reads is a check
    # that does not exist.
    specific, heuristic, unreadable = [], [], []
    generic = [p for p in patterns
               if "api[_-]?key" in getattr(p, "pattern", "")]

    for path in iter_files(target):
        try:
            with open(path, encoding="utf-8") as fh:
                lines = fh.readlines()
        except (OSError, UnicodeDecodeError):
            # Unreadable or non-UTF-8: report rather than skip in silence.
            # An unscanned file is not a clean file.
            unreadable.append(os.path.relpath(path, target))
            continue
        for lineno, line in enumerate(lines, start=1):
            for pat in patterns:
                if pat.search(line):
                    rel = os.path.relpath(path, target)
                    # Location only -- never echo the matched value.
                    entry = (rel, lineno, getattr(pat, "pattern", pat))
                    (heuristic if pat in generic else specific).append(entry)
                    break

    if specific:
        print("HIGH CONFIDENCE -- these match a known credential FORMAT (%d):"
              % len(specific))
        for rel, lineno, pat in specific:
            print("  %s:%d  %s" % (rel, lineno, pat))
    if heuristic:
        print("\nHEURISTIC -- a keyword near an assignment; in source code this "
              "also matches ordinary variables (%d):" % len(heuristic))
        for rel, lineno, pat in heuristic[:20]:
            print("  %s:%d" % (rel, lineno))
        if len(heuristic) > 20:
            print("  ... and %d more" % (len(heuristic) - 20))
    if unreadable:
        print("\nNOT SCANNED -- unreadable or not UTF-8 (%d). An unscanned file "
              "is not a clean file:" % len(unreadable))
        for rel in unreadable:
            print("  %s" % rel)

    total = len(specific) + len(heuristic) + len(unreadable)
    if total:
        print("\nprepublish_check: %d finding(s) across %d pattern(s). A match is "
              "not automatically a leak -- a redaction test's own fixtures will "
              "match too. Review each before publishing." % (total, len(patterns)))
        return 1
    print("prepublish_check: clean (%d patterns checked)" % len(patterns))
    return 0


if __name__ == "__main__":
    sys.exit(main())
