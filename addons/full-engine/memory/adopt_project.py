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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "memory"))
import new_run  # noqa: E402

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


def _write_goal_stub(run_dir: str, goal: str, source: str, project_path: str) -> None:
    goal_path = os.path.join(run_dir, "00-project-goal.md")
    with open(goal_path, encoding="utf-8") as fh:
        text = fh.read()
    text = re.sub(
        r"(## Canonical Goal \(one sentence\)\s*\n\n)(?:<!--.*?-->\s*\n)?TBD",
        r"\1<!-- inferred from %s -->\n%s" % (source, goal),
        text,
        count=1,
        flags=re.DOTALL,
    )
    text = text.replace(
        "- [ ] TBD\n- [ ] TBD",
        "- [ ] Adopted project at `%s` meets its stated purpose\n"
        "- [ ] Run closed with /deliver and VALIDATE: PASS" % project_path,
        1,
    )
    with open(goal_path, "w", encoding="utf-8") as fh:
        fh.write(text)


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
    with tempfile.TemporaryDirectory(prefix="adopt-selftest-") as tmp:
        readme = os.path.join(tmp, "README.md")
        with open(readme, "w", encoding="utf-8") as fh:
            fh.write("# Widget Tracker\n\nTrack widgets locally.\n")
        slug = "adoptselftest"
        dest = os.path.join(ROOT, "runs", slug)
        if os.path.exists(dest):
            shutil.rmtree(dest)
        try:
            assert adopt(tmp, slug=slug, tier="solo") == 0
            assert os.path.isdir(dest)
            with open(os.path.join(dest, "00-project-goal.md"), encoding="utf-8") as fh:
                body = fh.read()
            assert "Widget Tracker" in body or "Track widgets" in body
            assert "VALIDATE: PASS" in body
            with open(os.path.join(ROOT, "runs", "INDEX.md"), encoding="utf-8") as fh:
                assert slug in fh.read()
        finally:
            if os.path.exists(dest):
                shutil.rmtree(dest)
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
