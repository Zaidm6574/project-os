#!/usr/bin/env python3
"""Install the optional Project OS full engine add-on into a target project."""

from __future__ import annotations

import argparse
import importlib.util
import shutil
from pathlib import Path


TEMPLATE_ROOT = Path(__file__).resolve().parents[1]
ADDON_ROOT = TEMPLATE_ROOT / "addons" / "full-engine"


def copy_file(src: Path, dst: Path, force: bool) -> str:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and not force:
        return f"kept existing {dst}"
    shutil.copy2(src, dst)
    return f"wrote {dst}"


def copy_tree(src_dir: Path, dst_dir: Path, force: bool) -> list[str]:
    results: list[str] = []
    if not src_dir.exists():
        return results
    for src in sorted(p for p in src_dir.rglob("*") if p.is_file()):
        if "__pycache__" in src.parts or src.suffix == ".pyc":
            continue
        rel = src.relative_to(src_dir)
        results.append(copy_file(src, dst_dir / rel, force))
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
) -> list[str]:
    target = target.expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)

    if not ADDON_ROOT.exists():
        raise FileNotFoundError(f"missing add-on folder: {ADDON_ROOT}")

    results: list[str] = []
    results.extend(copy_tree(ADDON_ROOT / "memory", target / "memory", force))
    results.extend(copy_tree(ADDON_ROOT / "brain", target / "brain", force))
    results.extend(copy_tree(ADDON_ROOT / "blackboard-addons", target / "blackboard", force))

    (target / "memory" / "store").mkdir(parents=True, exist_ok=True)
    (target / "brain").mkdir(parents=True, exist_ok=True)
    shared_brain = target / "brain" / "shared-brain.jsonl"
    if not shared_brain.exists():
        shared_brain.write_text("", encoding="utf-8")
        results.append(f"wrote {shared_brain}")
    else:
        results.append(f"kept existing {shared_brain}")

    if claude:
        results.extend(copy_tree(ADDON_ROOT / "staged" / "agents", target / ".claude" / "agents", force))
        results.extend(copy_tree(ADDON_ROOT / "staged" / "commands", target / ".claude" / "commands", force))
    else:
        results.append("skipped .claude agents/commands; pass --claude to install them")

    if central_brain is not None:
        central = load_central_brain_module()
        central_path = central_brain.expanduser().resolve()
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
    )
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
