#!/usr/bin/env python3
"""Install the optional Project OS full engine add-on into a target project."""

from __future__ import annotations

import argparse
import importlib.util
import shutil
from pathlib import Path


TEMPLATE_ROOT = Path(__file__).resolve().parents[1]
ADDON_ROOT = TEMPLATE_ROOT / "addons" / "full-engine"


def copy_file(src: Path, dst: Path, force: bool, dry_run: bool = False) -> str:
    if dst.exists() and not force:
        return f"kept existing {dst}"
    if dry_run:
        action = "overwrite" if dst.exists() else "write"
        return f"would {action} {dst}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return f"wrote {dst}"


def copy_tree(src_dir: Path, dst_dir: Path, force: bool, dry_run: bool = False) -> list[str]:
    results: list[str] = []
    if not src_dir.exists():
        return results
    for src in sorted(p for p in src_dir.rglob("*") if p.is_file()):
        if "__pycache__" in src.parts or src.suffix == ".pyc":
            continue
        rel = src.relative_to(src_dir)
        results.append(copy_file(src, dst_dir / rel, force, dry_run=dry_run))
    return results


def load_central_brain_module():
    central_path = ADDON_ROOT / "brain" / "central_brain.py"
    spec = importlib.util.spec_from_file_location("project_os_central_brain", central_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {central_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def install_full_engine(
    target: Path,
    force: bool = False,
    claude: bool = False,
    central_brain: Path | None = None,
    project_id: str | None = None,
    dry_run: bool = False,
) -> list[str]:
    target = target.expanduser().resolve()

    if not ADDON_ROOT.exists():
        raise FileNotFoundError(f"missing add-on folder: {ADDON_ROOT}")

    results: list[str] = []
    if dry_run and not target.exists():
        results.append(f"would create {target}")
    if not dry_run:
        target.mkdir(parents=True, exist_ok=True)

    results.extend(copy_tree(ADDON_ROOT / "memory", target / "memory", force, dry_run=dry_run))
    results.extend(copy_tree(ADDON_ROOT / "brain", target / "brain", force, dry_run=dry_run))
    results.extend(copy_tree(ADDON_ROOT / "blackboard-addons", target / "blackboard", force, dry_run=dry_run))

    memory_store = target / "memory" / "store"
    brain_dir = target / "brain"
    if dry_run:
        results.append(f"would ensure {memory_store}")
        results.append(f"would ensure {brain_dir}")
    else:
        memory_store.mkdir(parents=True, exist_ok=True)
        brain_dir.mkdir(parents=True, exist_ok=True)
    shared_brain = target / "brain" / "shared-brain.jsonl"
    if not shared_brain.exists():
        if dry_run:
            results.append(f"would write {shared_brain}")
        else:
            shared_brain.write_text("", encoding="utf-8")
            results.append(f"wrote {shared_brain}")
    else:
        results.append(f"kept existing {shared_brain}")

    if claude:
        results.extend(
            copy_tree(ADDON_ROOT / "staged" / "agents", target / ".claude" / "agents", force, dry_run=dry_run)
        )
        results.extend(
            copy_tree(ADDON_ROOT / "staged" / "commands", target / ".claude" / "commands", force, dry_run=dry_run)
        )
    else:
        results.append("skipped .claude agents/commands; pass --claude to install them")

    if central_brain is not None:
        central_path = central_brain.expanduser().resolve()
        if dry_run:
            if project_id:
                results.append(f"would write {target / 'brain' / 'CENTRAL_BRAIN.md'}")
            results.append(f"would initialize central brain at {central_path / 'shared-brain.jsonl'}")
            return results
        central = load_central_brain_module()
        brain_file = central.init_central(central_path)
        if project_id:
            marker = target / "brain" / "CENTRAL_BRAIN.md"
            marker.write_text(
                "# Central Brain Connection\n\n"
                f"Central brain path: `{central_path}`\n"
                f"Project ID: `{project_id}`\n\n"
                "Push approved lessons:\n\n"
                "```bash\n"
                f"python3 brain/central_brain.py push --path {central_path} --project . --project-id {project_id}\n"
                "```\n\n"
                "Pull approved lessons:\n\n"
                "```bash\n"
                f"python3 brain/central_brain.py pull --path {central_path} --project . --project-id {project_id}\n"
                "```\n",
                encoding="utf-8",
            )
            results.append(f"wrote {marker}")
        results.append(f"initialized central brain at {brain_file}")

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Install the optional Project OS full engine add-on.")
    parser.add_argument("--target", default=".", help="Project folder to update. Default: current folder.")
    parser.add_argument("--force", action="store_true", help="Overwrite add-on files that already exist.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be copied without writing files.")
    parser.add_argument("--claude", action="store_true", help="Also install Claude Code agents and slash commands.")
    parser.add_argument(
        "--central-brain",
        default=None,
        help="Optional central brain folder to initialize/connect after installing the full engine.",
    )
    parser.add_argument("--project-id", default=None, help="Stable id to use when pushing this project to central brain.")
    args = parser.parse_args()

    results = install_full_engine(
        Path(args.target),
        force=args.force,
        claude=args.claude,
        central_brain=Path(args.central_brain) if args.central_brain else None,
        project_id=args.project_id,
        dry_run=args.dry_run,
    )
    if args.dry_run:
        print("Project OS full engine add-on dry run complete.")
    else:
        print("Project OS full engine add-on install complete.")
    for result in results:
        print(f"- {result}")
    print()
    print("Next:")
    print("1. Run: python3 memory/new_run.py demo --tier solo")
    print("2. Run: python3 memory/score_rubric.py --selftest")
    print("3. Optional central brain: python3 brain/central_brain.py sync --path <central-brain> --project . --project-id <project-id>")
    print("4. If you passed --claude, restart Claude Code or reload the project.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
