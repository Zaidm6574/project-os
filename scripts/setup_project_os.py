#!/usr/bin/env python3
"""Bootstrap Project OS files into a target project."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


TEMPLATE_ROOT = Path(__file__).resolve().parents[1]
GITIGNORE_MARKER = "# Project OS private files"


def copy_file(src: Path, dst: Path, force: bool) -> str:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and not force:
        return f"kept existing {dst}"
    shutil.copy2(src, dst)
    return f"wrote {dst}"


def copy_tree_files(src_dir: Path, dst_dir: Path, force: bool) -> list[str]:
    results: list[str] = []
    for src in sorted(p for p in src_dir.rglob("*") if p.is_file()):
        if "__pycache__" in src.parts or src.suffix == ".pyc":
            continue
        rel = src.relative_to(src_dir)
        results.append(copy_file(src, dst_dir / rel, force))
    return results


def merge_gitignore(src: Path, dst: Path, force: bool) -> str:
    dst.parent.mkdir(parents=True, exist_ok=True)
    project_os_lines = src.read_text(encoding="utf-8").splitlines()
    project_os_block = "\n".join(project_os_lines).strip()
    if not dst.exists():
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

    separator = "" if existing.endswith("\n") else "\n"
    dst.write_text(f"{existing}{separator}\n{GITIGNORE_MARKER}\n" + "\n".join(missing_lines) + "\n", encoding="utf-8")
    return f"merged Project OS ignore rules into {dst}"


def bootstrap(target: Path, force: bool) -> list[str]:
    target = target.expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)

    results: list[str] = []
    results.append(copy_file(TEMPLATE_ROOT / "AGENTS.md", target / "AGENTS.md", force))
    results.append(copy_file(TEMPLATE_ROOT / "CLAUDE.md", target / "CLAUDE.md", force))
    results.append(merge_gitignore(TEMPLATE_ROOT / ".gitignore", target / ".gitignore", force))
    results.extend(copy_tree_files(TEMPLATE_ROOT / "prompts", target / "prompts", force))
    results.extend(copy_tree_files(TEMPLATE_ROOT / "scripts", target / "scripts", force))
    results.extend(copy_tree_files(TEMPLATE_ROOT / "blackboard-template", target / "blackboard", force))
    results.extend(copy_tree_files(TEMPLATE_ROOT / "runs-template", target / "runs", force))
    results.extend(copy_tree_files(TEMPLATE_ROOT / "outputs-template", target / "outputs", force))
    results.extend(copy_tree_files(TEMPLATE_ROOT / "memory-template", target / "memory", force))

    private_memory = target / "private-memory"
    private_imports = target / "private-imports"
    private_memory.mkdir(exist_ok=True)
    private_imports.mkdir(exist_ok=True)
    results.append(f"ensured {private_memory}")
    results.append(f"ensured {private_imports}")

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap Project OS into a target project.")
    parser.add_argument("--target", default=".", help="Project folder to initialize. Default: current folder.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing Project OS files. .gitignore privacy rules are merged, not overwritten.",
    )
    args = parser.parse_args()

    results = bootstrap(Path(args.target), args.force)
    print("Project OS setup complete.")
    for result in results:
        print(f"- {result}")
    print()
    print("Next:")
    print("1. Open the target project in Codex, Claude, or your AI coding tool.")
    print("2. Say: /project <your idea>")
    print("3. Optional: run scripts/import_chat_history.py on a local chat export.")
    print("4. Before committing, run: git status --short --ignored")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
