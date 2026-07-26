#!/usr/bin/env python3
"""Adopt an existing project folder into Project OS runs/.

Given a path to an existing codebase or docs folder, scaffolds a run under
runs/<slug>/ and writes an inferred 00-project-goal.md stub from README,
package.json, pyproject.toml, or the folder name.

The blackboard/ template stays read-only; the run dir is the adoption target.

Usage:
  python3 memory/adopt_project.py <existing_project_path> [--slug name]
  python3 memory/adopt_project.py --selftest

Standard library only. No network access.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import new_run  # noqa: E402

# Share new_run's root discovery instead of recomputing it. The local copy said
# dirname(dirname(__file__)), which in the full-engine addon pointed at
# addons/full-engine/ -- a directory with no blackboard/ and no runs/, so every
# adopt crashed (audit 2026-07-25).
ROOT = new_run.ROOT

def _safe_path(path: str) -> str:
    return os.path.abspath(path)


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "adopted-project"


def _infer_goal(project_path: str) -> tuple[str, str]:
    """Return (one-line goal, source hint)."""
    for fname in ("README.md", "readme.md", "README"):
        p = os.path.join(project_path, fname)
        if os.path.isfile(p):
            with open(p, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    s = line.strip().lstrip("#").strip()
                    if s and not s.startswith("!["):
                        return s[:200], fname
    for fname in ("package.json", "pyproject.toml"):
        p = os.path.join(project_path, fname)
        if os.path.isfile(p):
            if fname.endswith(".json"):
                try:
                    with open(p, encoding="utf-8") as fh:
                        blob = json.load(fh)
                    desc = (blob.get("description") or blob.get("name") or "").strip()
                    if desc:
                        return desc[:200], fname
                except (json.JSONDecodeError, OSError):
                    pass
            else:
                with open(p, encoding="utf-8", errors="replace") as fh:
                    m = re.search(r'name\s*=\s*["\']([^"\']+)["\']', fh.read())
                    if m:
                        return m.group(1)[:200], fname
    base = os.path.basename(project_path.rstrip(os.sep))
    return f"Adopt and improve {base}", "folder name"


def _replace_canonical_goal(text: str, goal: str, source: str) -> str:
    """Put `goal` under the '## Canonical Goal' heading, adding it if absent.

    This used to be a literal regex for '## Canonical Goal (one sentence)'
    followed by 'TBD'. When the template's heading and placeholder were reworded
    the regex stopped matching, and because re.sub reports no error it silently
    wrote nothing -- every adopted run kept the template placeholder, so they
    ALL hashed to the same goal and goal_guard could never see drift between
    them. Match the heading the way goal_guard.canonical_goal does (prefix, not
    exact string) and replace whatever placeholder line follows it.
    """
    lines = text.splitlines()
    replacement = ["<!-- inferred from %s -->" % source, goal]

    for i, line in enumerate(lines):
        if not line.strip().startswith("## Canonical Goal"):
            continue
        # Find the first real line under the heading -- that is the placeholder
        # goal_guard would read, so that is exactly what we must overwrite.
        j = i + 1
        while j < len(lines):
            s = lines[j].strip()
            if not s or s.startswith("<!--") or s.startswith("-->"):
                j += 1
                continue
            if s.startswith("#"):
                break  # next section; heading exists but has no goal line
            return "\n".join(lines[:j] + replacement + lines[j + 1:]) + "\n"
        return "\n".join(lines[:j] + replacement + [""] + lines[j:]) + "\n"

    # No anchor at all: insert one after the title so goal_guard can hash it.
    block = ["## Canonical Goal", ""] + replacement + [""]
    at = 1 if lines and lines[0].startswith("# ") else 0
    return "\n".join(lines[:at] + [""] * bool(at) + block + lines[at:]) + "\n"


def _add_adoption_criteria(text: str, project_path: str) -> str:
    """Ensure the two adoption criteria exist under '## Success Criteria'.

    The old code did a literal replace of '- [ ] TBD\\n- [ ] TBD'. Any template
    whose Success Criteria were reworded (or already populated, as when
    scaffolding from a worked example) silently kept none of the adoption
    criteria, so an adopted run had no closeout condition at all. Append rather
    than pattern-match, and drop an empty placeholder bullet if one is there.
    """
    added = [
        "- [ ] Adopted project at `%s` meets its stated purpose" % project_path,
        "- [ ] Run closed with /deliver and VALIDATE: PASS",
    ]
    if added[1] in text:
        return text

    lines = text.splitlines()
    for i, line in enumerate(lines):
        if not line.strip().lower().startswith("## success criteria"):
            continue
        j = i + 1
        while j < len(lines) and not lines[j].strip().startswith("#"):
            j += 1
        block = lines[i + 1:j]
        # Drop a bare placeholder bullet ("- " / "- [ ] TBD") and trailing blanks.
        block = [b for b in block if b.strip() not in ("-", "- [ ] TBD")]
        while block and not block[-1].strip():
            block.pop()
        return "\n".join(lines[:i + 1] + block + added + [""] + lines[j:]) + "\n"

    return text.rstrip("\n") + "\n\n## Success Criteria\n\n" + "\n".join(added) + "\n"


def _write_goal_stub(run_dir: str, goal: str, source: str, project_path: str) -> None:
    goal_path = os.path.join(run_dir, "00-project-goal.md")
    with open(goal_path, encoding="utf-8") as fh:
        text = fh.read()

    text = _replace_canonical_goal(text, goal, source)
    text = _add_adoption_criteria(text, project_path)

    with open(goal_path, "w", encoding="utf-8") as fh:
        fh.write(text)

    # Verify the write with the SAME parser the guard uses, reading the file
    # back FROM DISK. Checking the in-memory string only proved the function was
    # self-consistent -- a short or corrupted write still reported success
    # (adversarial verify 2026-07-25).
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import goal_guard  # noqa: E402

        with open(goal_path, encoding="utf-8") as fh:
            on_disk = fh.read()
        written = goal_guard.canonical_goal(on_disk)
    except Exception as exc:  # noqa: BLE001 - report, never silently continue
        raise SystemExit(
            "adopt: wrote %s but goal_guard cannot read a canonical goal from "
            "it (%s). Refusing to report a successful adoption." % (goal_path, exc)
        )
    if written != goal:
        raise SystemExit(
            "adopt: wrote %s but goal_guard reads %r, not the inferred goal %r."
            % (goal_path, written, goal)
        )


def adopt(project_path: str, slug: str | None = None, tier: str = "solo") -> int:
    full = _safe_path(project_path)
    if not os.path.isdir(full):
        sys.stderr.write("adopt: not a directory: %s\n" % full)
        return 1
    slug = slug or _slugify(os.path.basename(full))
    dest = os.path.join(ROOT, "runs", slug)
    if os.path.exists(dest):
        sys.stderr.write("runs/%s/ already exists — refusing to overwrite.\n" % slug)
        return 1
    code = new_run.scaffold(slug, tier=tier)
    if code != 0:
        return code
    goal, source = _infer_goal(full)
    _write_goal_stub(dest, goal, source, full)
    new_run.regenerate_index()
    print("ADOPT: scaffolded runs/%s/ from %s (goal from %s)" % (slug, full, source))
    return 0


def selftest() -> int:
    # Run entirely inside a throwaway tree. This selftest used to scaffold into
    # the REAL runs/ and rewrite the real runs/INDEX.md, so two concurrent
    # suites collided on the same slug and the run turned red at random
    # (audit 2026-07-25).
    global ROOT
    with new_run.isolated_root() as tmp_root:
        saved, ROOT = ROOT, tmp_root
        try:
            return _selftest_body()
        finally:
            ROOT = saved


def _selftest_body() -> int:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import goal_guard  # noqa: E402

    def _adopt_one(title, body_line, slug):
        """Adopt a throwaway project and return its written canonical goal."""
        tmp = tempfile.mkdtemp(prefix="adopt-selftest-")
        dest = os.path.join(ROOT, "runs", slug)
        if os.path.exists(dest):
            shutil.rmtree(dest)
        with open(os.path.join(tmp, "README.md"), "w", encoding="utf-8") as fh:
            fh.write("# %s\n\n%s\n" % (title, body_line))
        assert adopt(tmp, slug=slug, tier="solo") == 0, "adopt failed for %s" % slug
        assert os.path.isdir(dest)
        with open(os.path.join(dest, "00-project-goal.md"), encoding="utf-8") as fh:
            body = fh.read()
        shutil.rmtree(tmp)
        return dest, body

    slugs = ["adoptselftest", "adoptselftest2"]
    try:
        dest_a, body_a = _adopt_one("Widget Tracker", "Track widgets locally.", slugs[0])
        assert "Widget Tracker" in body_a, "inferred goal was not written"
        assert "VALIDATE: PASS" in body_a, "adoption criteria were not written"
        with open(os.path.join(ROOT, "runs", "INDEX.md"), encoding="utf-8") as fh:
            assert slugs[0] in fh.read()

        # The goal must be readable by the guard that hashes it -- the old stub
        # writer left the template placeholder in place and nothing noticed.
        goal_a = goal_guard.canonical_goal(body_a)
        assert goal_a == "Widget Tracker", "goal_guard reads %r" % goal_a

        # THE regression this selftest missed: with a dead stub writer every
        # adopted run kept the same placeholder, so every run hashed identically
        # and goal_guard reported MATCH no matter which project it guarded.
        _, body_b = _adopt_one("Ledger Sync", "Reconcile ledgers nightly.", slugs[1])
        goal_b = goal_guard.canonical_goal(body_b)
        assert goal_b == "Ledger Sync", "goal_guard reads %r" % goal_b
        assert goal_guard.goal_hash(goal_a) != goal_guard.goal_hash(goal_b), (
            "two different projects adopted to the SAME goal hash -- the stub "
            "writer is not writing the inferred goal"
        )
    finally:
        for slug in slugs:
            d = os.path.join(ROOT, "runs", slug)
            if os.path.exists(d):
                shutil.rmtree(d)
        new_run.regenerate_index()
    print("adopt_project selftest: OK")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Adopt an existing project into runs/.")
    ap.add_argument("path", nargs="?", help="existing project directory")
    ap.add_argument("--slug", default=None, help="run slug (default: from folder name)")
    ap.add_argument("--tier", choices=["solo", "mini", "full"], default="solo")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        sys.exit(selftest())
    if not args.path:
        ap.error("path is required (or use --selftest)")
    sys.exit(adopt(args.path, slug=args.slug, tier=args.tier))


if __name__ == "__main__":
    main()
