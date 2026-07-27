#!/usr/bin/env python3
"""Bootstrap Project OS files into a target project."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path


TEMPLATE_ROOT = Path(__file__).resolve().parents[1]
FULL_ENGINE_MEMORY = TEMPLATE_ROOT / "addons" / "full-engine" / "memory"
GITIGNORE_MARKER = "# Project OS private files"
GENERATED_DIRS = {"__pycache__", "store", "out", "graphify-out", ".turbovec"}
GENERATED_SUFFIXES = (".pyc", ".tvim", ".sidecar.json", ".manifest.json",
                      ".db", ".db.tmp", ".db-wal", ".db-shm", ".db-journal",
                      ".db.tmp-wal", ".db.tmp-shm", ".db.tmp-journal")


def nonempty_path(value: str) -> Path:
    if not value.strip():
        raise argparse.ArgumentTypeError("target must be a non-empty path")
    return Path(value)


def is_distributable(src: Path, src_dir: Path) -> bool:
    rel = src.relative_to(src_dir)
    return (
        src.name != "shared-brain.jsonl"
        and not any(part in GENERATED_DIRS for part in rel.parts)
        and not src.name.endswith(GENERATED_SUFFIXES)
    )


def copy_file(src: Path, dst: Path, force: bool, dry_run: bool = False) -> str:
    if dst.exists() and not force:
        return f"kept existing {dst}"
    if dry_run:
        action = "overwrite" if dst.exists() else "write"
        return f"would {action} {dst}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    # --force never destroys user edits silently: if the target differs from
    # the template, keep a one-deep .pre-force backup next to it.
    if dst.exists() and dst.read_bytes() != src.read_bytes():
        backup = dst.with_name(dst.name + ".pre-force")
        shutil.copy2(dst, backup)
        shutil.copy2(src, dst)
        return f"wrote {dst} (previous version saved to {backup.name})"
    shutil.copy2(src, dst)
    return f"wrote {dst}"


def copy_tree_files(src_dir: Path, dst_dir: Path, force: bool, dry_run: bool = False) -> list[str]:
    results: list[str] = []
    for src in sorted(p for p in src_dir.rglob("*") if p.is_file() and is_distributable(p, src_dir)):
        rel = src.relative_to(src_dir)
        results.append(copy_file(src, dst_dir / rel, force, dry_run=dry_run))
    return results


def merge_gitignore(src: Path, dst: Path, force: bool, dry_run: bool = False) -> str:
    project_os_lines = src.read_text(encoding="utf-8").splitlines()
    project_os_block = "\n".join(project_os_lines).strip()
    if not dst.exists():
        if dry_run:
            return f"would write {dst}"
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(project_os_block + "\n", encoding="utf-8")
        return f"wrote {dst}"

    existing = dst.read_text(encoding="utf-8")
    existing_lines = {line.strip() for line in existing.splitlines()}
    missing_lines = [
        line
        for line in project_os_lines
        if line.strip() and not line.lstrip().startswith("#") and line.strip() not in existing_lines
    ]
    if not missing_lines:
        return f"kept existing {dst}"

    if dry_run:
        return f"would merge Project OS ignore rules into {dst}"
    separator = "" if existing.endswith("\n") else "\n"
    dst.write_text(f"{existing}{separator}\n{GITIGNORE_MARKER}\n" + "\n".join(missing_lines) + "\n", encoding="utf-8")
    return f"merged Project OS ignore rules into {dst}"


def committed_memory_index_warning(target: Path) -> list[str]:
    """Warn when the target's git already TRACKS memory/mneme_index.json.

    Merging scripts/project-os.gitignore only PREVENTS committing the semantic
    memory index; it says nothing to a user whose earlier install already
    committed it. That index stores lesson excerpts from every project the
    brain has seen, so a tracked copy puts one client's notes inside another
    client's repo (cross-project leak, audit 2026-07-26). This is remediation
    advice, never a gate: if git is absent, the target is not a repo, or git
    fails for any reason, stay silent (fail open).
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(target), "ls-files", "--", "memory/mneme_index.json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0 or not proc.stdout.strip():
        return []
    return [
        "WARNING: this project's git already tracks memory/mneme_index.json.",
        "That file is the semantic memory index; it carries lesson excerpts from",
        "every project this brain has seen, so committing it leaks cross-project",
        "notes into this repo. The merged .gitignore only blocks FUTURE commits.",
        "To stop tracking it:",
        "  git rm --cached memory/mneme_index.json",
        "  git commit -m 'Stop tracking Project OS memory index'",
        "Note: earlier commits still contain the file. If this repo is shared,",
        "rewrite history or treat the index contents as disclosed.",
    ]


def bootstrap(target: Path, force: bool, dry_run: bool = False) -> list[str]:
    target = target.expanduser().resolve()

    results: list[str] = []
    if dry_run and not target.exists():
        results.append(f"would create {target}")
    if not dry_run:
        target.mkdir(parents=True, exist_ok=True)

    results.append(copy_file(TEMPLATE_ROOT / "AGENTS.md", target / "AGENTS.md", force, dry_run=dry_run))
    results.append(copy_file(TEMPLATE_ROOT / "CLAUDE.md", target / "CLAUDE.md", force, dry_run=dry_run))
    # Merge the CURATED ignore list, not the repo's own .gitignore. The repo
    # file also ignores lines specific to this repository (a bare `app`,
    # `code-graph.json`, `arachne-out/`, ...), and copying it wholesale silently
    # un-tracked a user's own `app/` directory (audit 2026-07-25).
    curated_ignore = TEMPLATE_ROOT / "scripts" / "project-os.gitignore"
    ignore_src = curated_ignore if curated_ignore.is_file() else TEMPLATE_ROOT / ".gitignore"
    results.append(merge_gitignore(ignore_src, target / ".gitignore", force, dry_run=dry_run))
    results.extend(copy_tree_files(TEMPLATE_ROOT / "prompts", target / "prompts", force, dry_run=dry_run))
    results.extend(copy_tree_files(TEMPLATE_ROOT / "scripts", target / "scripts", force, dry_run=dry_run))
    results.extend(
        copy_tree_files(
            TEMPLATE_ROOT / "addons" / "full-engine",
            target / "addons" / "full-engine",
            force,
            dry_run=dry_run,
        )
    )
    results.extend(copy_tree_files(TEMPLATE_ROOT / "blackboard-template", target / "blackboard", force, dry_run=dry_run))
    results.extend(copy_tree_files(TEMPLATE_ROOT / "runs-template", target / "runs", force, dry_run=dry_run))
    results.extend(copy_tree_files(TEMPLATE_ROOT / "outputs-template", target / "outputs", force, dry_run=dry_run))
    results.extend(copy_tree_files(TEMPLATE_ROOT / "memory-template", target / "memory", force, dry_run=dry_run))
    # Vector-memory + graph adapters are code, not personal data — the installer
    # must deliver what the README promises. The core adapters ship from the
    # repo's own memory/; the full-engine helpers fill in anything missing.
    delivered = set()
    # context_budget.py and code_graph.py were added to memory/ without being
    # added here, so AGENTS.md's "Kickoff preflight: run `python3
    # memory/context_budget.py`" -- the FIRST documented step of a run --
    # pointed at a nonexistent file in every installed project
    # (pre-push audit 2026-07-26). Doc drift in an installer is a broken
    # promise, not a cosmetic issue: ship what the docs tell people to run.
    for adapter in ("mneme_adapter.py", "build_graph.py",
                    "context_budget.py", "code_graph.py"):
        src = TEMPLATE_ROOT / "memory" / adapter
        if src.is_file():
            results.append(copy_file(src, target / "memory" / adapter, force, dry_run=dry_run))
            delivered.add(adapter)
    for helper in ("build_graph.py", "osvec_adapter.py"):
        # Only FILL IN what memory/ did not already deliver. build_graph.py was
        # listed in BOTH loops, so the second copy overwrote the first: with
        # --force the older full-engine helper replaced the canonical
        # memory/build_graph.py and backed the good file up as though it were a
        # user edit. The comment above always said "fill in anything missing" --
        # the code did not (audit 2026-07-25).
        if helper in delivered:
            continue
        src = FULL_ENGINE_MEMORY / helper
        if src.exists():
            results.append(copy_file(src, target / "memory" / helper, force, dry_run=dry_run))

    private_memory = target / "private-memory"
    private_imports = target / "private-imports"
    if dry_run:
        results.append(f"would ensure {private_memory}")
        results.append(f"would ensure {private_imports}")
    else:
        private_memory.mkdir(exist_ok=True)
        private_imports.mkdir(exist_ok=True)
        results.append(f"ensured {private_memory}")
        results.append(f"ensured {private_imports}")

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap Project OS into a target project.")
    parser.add_argument(
        "--target",
        type=nonempty_path,
        default=Path("."),
        help="Project folder to initialize. Default: current folder.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing Project OS files. .gitignore privacy rules are merged, not overwritten.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show what would be copied without writing files.")
    args = parser.parse_args()

    results = bootstrap(args.target, args.force, dry_run=args.dry_run)
    if args.dry_run:
        print("Project OS setup dry run complete.")
    else:
        print("Project OS setup complete.")
    for result in results:
        print(f"- {result}")
    warning = committed_memory_index_warning(args.target.expanduser().resolve())
    if warning:
        print()
        for line in warning:
            print(line)
    print()
    print("Next:")
    print("1. Open the target project in Codex, Claude, or your AI coding tool.")
    print("2. Say: /project <your idea>")
    print("3. Build graph context when useful: python3 memory/build_graph.py --root blackboard")
    print("4. Optional: run scripts/import_chat_history.py on a local chat export.")
    print("5. Before committing, run: git status --short --ignored")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
