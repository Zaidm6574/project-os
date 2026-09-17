#!/usr/bin/env python3
"""prepublish_check — scan a tree for credential shapes before you publish it.

Uses this installation's canonical scripts/secret_patterns.py, not a copy of it
or the optional brain module's portable compatibility list.

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
  python3 scripts/prepublish_check.py [PATH]     # working directory/file; default: repo root
  python3 scripts/prepublish_check.py --tracked  # stage-0 index blobs, not working files
  python3 scripts/prepublish_check.py --tracked --working-tree  # index paths, local bytes
  python3 scripts/prepublish_check.py --list     # print the patterns in use

Exit codes:
  0  at least one selected file was scanned and nothing matched
  1  at least one credential shape found (the file and line are printed)
  2  invalid target/options, empty selection, unresolved index or unavailable denylist:
     a scanner that silently checks nothing is worse than no scanner at all
"""
import argparse
import importlib.util
import os
import re
import subprocess
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
    """Load and validate the installation-local canonical list, or refuse."""
    canonical = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                             "secret_patterns.py")
    try:
        if os.path.islink(canonical) or not os.path.isfile(canonical):
            raise ValueError("canonical module must be a local regular file")
        spec = importlib.util.spec_from_file_location("_prepublish_canonical", canonical)
        module = importlib.util.module_from_spec(spec)
        # Read the exact sibling source, not a cached/ancestor/sys.path module
        # or a stale bytecode file. A scan must not create cache files either.
        with open(canonical, "rb") as handle:
            source = handle.read()
        exec(compile(source, canonical, "exec"), module.__dict__)
        patterns = module.SECRET_PATTERNS
        specs = module.SECRET_PATTERN_SPECS
        if (not isinstance(patterns, (tuple, list)) or not patterns
                or not isinstance(specs, (tuple, list)) or not specs):
            raise ValueError("canonical exports must be nonempty sequences")
        expected = []
        for item in specs:
            if (not isinstance(item, (tuple, list)) or len(item) != 2
                    or not all(isinstance(value, str) and value for value in item)):
                raise ValueError("invalid canonical pattern specification")
            expected.append(re.compile(item[0]))
        if (any(not isinstance(pattern, re.Pattern)
                or not isinstance(pattern.pattern, str) for pattern in patterns)
                or [(pattern.pattern, pattern.flags) for pattern in patterns]
                != [(pattern.pattern, pattern.flags) for pattern in expected]):
            raise ValueError("compiled patterns do not match canonical specifications")
        return tuple(patterns)
    except (Exception, SystemExit):  # invalid scanner source must also fail closed
        # Import/compile exception text can contain source or credential values.
        print("prepublish_check: cannot load the installation-local canonical denylist.\n"
              "Restore a complete, valid scripts/secret_patterns.py from this installation.\n"
              "Refusing to run a partial scan.", file=sys.stderr)
        raise SystemExit(2) from None


def iter_files(root):
    """Select text candidates, including an explicitly named regular file."""
    if os.path.isfile(root):
        if root.lower().endswith(SKIP_SUFFIXES):
            raise RuntimeError("explicit target has an excluded binary suffix")
        yield root
        return
    if not os.path.isdir(root):
        raise RuntimeError("target must be a regular file or directory")
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if not name.lower().endswith(SKIP_SUFFIXES):
                yield os.path.join(dirpath, name)


def _git(root, *args):
    try:
        # Local replace refs must not substitute different bytes for a staged
        # blob: the published tree still records the original object ID.
        result = subprocess.run(["git", "--no-replace-objects", "-C", root, *args],
                                capture_output=True, check=False)
    except OSError:
        raise RuntimeError("cannot run Git for --tracked") from None
    if result.returncode:
        # Git's diagnostics can contain data from the selected artifact.
        raise RuntimeError("cannot read Git index/blob; --tracked requires a Git worktree")
    return result.stdout


def iter_index_files(target):
    """Yield (path, blob ID) from stage 0, taking a snapshot of index object IDs.

    Index-selected files do not have to exist in the working tree. Never follow
    working-tree symlinks when reading the index; scan the stored link text.
    Unmerged entries and gitlinks have no single text blob to certify.
    """
    directory = os.path.isdir(target) and not os.path.islink(target)
    root = target if directory else os.path.dirname(target)
    pathspec = [] if directory else ["--", ":(literal)" + os.path.basename(target)]
    entries = _git(root, "ls-files", "--stage", "-z", *pathspec)
    for entry in entries.split(b"\0"):
        if not entry:
            continue
        metadata, raw_path = entry.split(b"\t", 1)
        mode, oid, stage = metadata.split()
        if stage != b"0":
            raise RuntimeError("unresolved index entries; resolve conflicts before scanning")
        if mode not in (b"100644", b"100755", b"120000"):
            raise RuntimeError("index contains a non-blob entry (such as a submodule); scan it separately")
        path = os.path.join(root, os.fsdecode(raw_path))
        if path.lower().endswith(SKIP_SUFFIXES):
            if not directory:
                raise RuntimeError("explicit target has an excluded binary suffix")
            continue
        yield path, oid.decode("ascii")


def iter_tracked_files(root):
    """Compatibility helper: index-selected paths, including locally missing files."""
    for path, _ in iter_index_files(root):
        yield path


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", nargs="?", default=ROOT)
    parser.add_argument("--tracked", action="store_true",
                        help="scan stage-0 Git index blobs below PATH")
    parser.add_argument("--working-tree", action="store_true",
                        help="with --tracked, scan local bytes for index-selected paths")
    parser.add_argument("--list", action="store_true", help="list canonical patterns")
    args = parser.parse_args()
    if args.working_tree and not args.tracked:
        parser.error("--working-tree requires --tracked (plain PATH already scans local bytes)")
    patterns = load_patterns()
    if args.list:
        for p in patterns:
            print(getattr(p, "pattern", p))
        return 0

    target = os.path.abspath(args.path)
    try:
        if args.tracked:
            files = list(iter_index_files(target))
        else:
            files = [(path, None) for path in iter_files(target)]
    except RuntimeError as exc:
        print("prepublish_check: %s" % exc, file=sys.stderr)
        return 2
    if not files:
        print("prepublish_check: no eligible files selected; 0 files scanned", file=sys.stderr)
        return 2
    directory = os.path.isdir(target) and (not args.tracked or not os.path.islink(target))
    base = target if directory else os.path.dirname(target)
    source = "Git index" if args.tracked and not args.working_tree else "working tree"

    def location(path):
        # Even a path can contain a credential; never emit matching values.
        label = os.path.relpath(path, base)
        for pattern in patterns:
            label = pattern.sub("[REDACTED]", label)
        return repr(label)[1:-1]

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

    scanned = 0
    for path, oid in files:
        try:
            if args.tracked and not args.working_tree:
                lines = _git(base, "cat-file", "blob", oid).decode("utf-8").splitlines()
            else:
                with open(path, encoding="utf-8") as fh:
                    lines = fh.readlines()
            scanned += 1
        except (OSError, UnicodeDecodeError, RuntimeError):
            # Unreadable or non-UTF-8: report rather than skip in silence.
            # An unscanned file is not a clean file.
            unreadable.append(location(path))
            continue
        for lineno, line in enumerate(lines, start=1):
            for pat in patterns:
                if pat.search(line):
                    rel = location(path)
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

    print("prepublish_check: %d file(s) selected, %d scanned from %s"
          % (len(files), scanned, source))
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
