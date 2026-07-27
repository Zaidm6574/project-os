#!/usr/bin/env python3
"""Install the optional Project OS full engine add-on into a target project."""

from __future__ import annotations

import argparse
import importlib.util
import shlex
import sys
from pathlib import Path


TEMPLATE_ROOT = Path(__file__).resolve().parents[1]
ADDON_ROOT = TEMPLATE_ROOT / "addons" / "full-engine"
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


def _guard():
    """setup_project_os's destination guard, imported rather than copied.

    install.sh drives BOTH installers with the same --target, so a guard on
    only one of them is no guard at all: --full-engine reached this file's
    unprotected sinks and wrote through a symlinked target directory exactly
    as before, exit 0, no refusal (adversary 2026-07-26). Importing the one
    implementation is deliberate -- the same week, a forked copy of the secret
    patterns drifted and silently stopped matching two token families.
    """
    path = Path(__file__).resolve().parent / "setup_project_os.py"
    spec = importlib.util.spec_from_file_location("project_os_setup", path)
    if spec is None or spec.loader is None:  # pragma: no cover - unreachable
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_SETUP = _guard()
REFUSALS: list = []


def _refuse_unsafe(dst: Path, root) -> str | None:
    """Refusal line when writing `dst` would leave `root`, else None."""
    if root is None:
        return None
    return _SETUP._refuse(Path(dst), Path(root), REFUSALS)


def copy_file(src: Path, dst: Path, force: bool, dry_run: bool = False,
              root=None) -> str:
    """setup_project_os's copy_file, delegated rather than forked.

    This was a hand-copy of it, and the copy drifted: 2026-07-25 added the
    one-deep `.pre-force` backup here but not the refusal that guards the
    BACKUP's own destination. That backup writes the USER's bytes, so through a
    link named <file>.pre-force this installer copied their file OUT of the
    target -- exit 0, no refusal -- while the sibling installer refused the
    identical attack. Delegating is the same choice `_guard` documents above:
    one implementation is the only version of this guard that stays fixed.
    """
    return _SETUP.copy_file(src, dst, force, dry_run=dry_run, root=root,
                            refusals=REFUSALS)


def canonical_owner(rel: Path):
    """The canonical memory/ file this add-on path would shadow, or None.

    setup_project_os.py delivers CANONICAL_MEMORY_ADAPTERS from the repo's own
    memory/ and deliberately refuses to let the older add-on forks land on top
    of them (audit 2026-07-25). install.sh then runs THIS installer against the
    same target and forwards --force to it, and copying addons/full-engine/
    memory/ wholesale put the older build_graph.py fork straight back -- a
    different graph schema, with the good file left behind as
    build_graph.py.pre-force as though the user had edited it. The roster is
    read from the sibling module so adding an adapter there cannot silently
    re-open this here.
    """
    if len(rel.parts) != 1 or rel.name not in _SETUP.CANONICAL_MEMORY_ADAPTERS:
        return None
    canonical = TEMPLATE_ROOT / "memory" / rel.name
    return canonical if canonical.is_file() else None


def copy_tree(src_dir: Path, dst_dir: Path, force: bool, dry_run: bool = False,
              root=None, skip=None) -> list[str]:
    results: list[str] = []
    if not src_dir.exists():
        return results
    for src in sorted(p for p in src_dir.rglob("*") if p.is_file() and is_distributable(p, src_dir)):
        rel = src.relative_to(src_dir)
        owned = None if skip is None else skip(rel)
        if owned is not None:
            results.append("kept canonical %s (owned by %s)"
                           % (dst_dir / rel, owned.relative_to(TEMPLATE_ROOT)))
            continue
        results.append(copy_file(src, dst_dir / rel, force, dry_run=dry_run,
                                 root=root))
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
    codex: bool = False,
    central_brain: Path | None = None,
    project_id: str | None = None,
    dry_run: bool = False,
    starter_planned: bool = False,
) -> list[str]:
    target = target.expanduser().resolve()

    if not ADDON_ROOT.exists():
        raise FileNotFoundError(f"missing add-on folder: {ADDON_ROOT}")
    if not (dry_run and starter_planned):
        starter_markers = (target / "AGENTS.md", target / "blackboard" / "00-project-goal.md")
        if not all(marker.is_file() for marker in starter_markers):
            raise FileNotFoundError(
                "full-engine install needs a starter Project OS workspace; "
                "run install.sh <target> --full-engine or bootstrap the target first"
            )

    results: list[str] = []
    if dry_run and not target.exists():
        results.append(f"would create {target}")
    if not dry_run:
        target.mkdir(parents=True, exist_ok=True)

    results.extend(copy_tree(ADDON_ROOT / "memory", target / "memory", force, dry_run=dry_run, root=target,
                             skip=canonical_owner))
    results.extend(copy_tree(ADDON_ROOT / "brain", target / "brain", force, dry_run=dry_run, root=target))
    results.extend(copy_tree(ADDON_ROOT / "blackboard-addons", target / "blackboard", force, dry_run=dry_run, root=target))

    # mkdir and write_text follow symlinks exactly as copy2 does, so they need
    # the same guard: with only copy_file protected, a symlinked memory/ still
    # got a store/ directory created inside the outside target.
    memory_store = target / "memory" / "store"
    brain_dir = target / "brain"
    for d in (memory_store, brain_dir):
        refused = _refuse_unsafe(d, target)
        if refused:
            results.append(refused)
        elif dry_run:
            results.append(f"would ensure {d}")
        else:
            d.mkdir(parents=True, exist_ok=True)
    shared_brain = target / "brain" / "shared-brain.jsonl"
    refused = _refuse_unsafe(shared_brain, target)
    if refused:
        results.append(refused)
    elif not shared_brain.exists():
        if dry_run:
            results.append(f"would write {shared_brain}")
        else:
            shared_brain.write_text("", encoding="utf-8")
            results.append(f"wrote {shared_brain}")
    else:
        results.append(f"kept existing {shared_brain}")

    if claude:
        results.extend(
            copy_tree(ADDON_ROOT / "staged" / "agents", target / ".claude" / "agents", force, dry_run=dry_run, root=target)
        )
        results.extend(
            copy_tree(ADDON_ROOT / "staged" / "commands", target / ".claude" / "commands", force, dry_run=dry_run, root=target)
        )
    else:
        results.append("skipped .claude agents/commands; pass --claude to install them")

    # Symmetric Codex slot. The workflows are already runtime-neutral in
    # prompts/workflows/ (installed unconditionally), so this adapter only buys
    # DISCOVERABILITY: without it a Codex session never learns the workflows
    # exist. Path verified against codex-cli 0.144.1 — .agents/skills/<name>/
    # SKILL.md is auto-loaded, and .agents/ is the vendor-neutral spelling.
    if codex:
        results.extend(
            copy_tree(ADDON_ROOT / "staged" / "codex-skills",
                      target / ".agents" / "skills", force, dry_run=dry_run,
                      root=target)
        )
    else:
        results.append("skipped .agents/skills; pass --codex to install them")

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
            quoted_central_path = shlex.quote(str(central_path))
            quoted_project_id = shlex.quote(project_id)
            marker.write_text(
                "# Central Brain Connection\n\n"
                f"Central brain path: `{central_path}`\n"
                f"Project ID: `{project_id}`\n\n"
                "Push lessons (chat-derived records sync only as approved summaries;\n"
                "raw or private-tagged records never leave the project):\n\n"
                "```bash\n"
                f"python3 brain/central_brain.py push --path {quoted_central_path} --project . --project-id {quoted_project_id}\n"
                "```\n\n"
                "Pull lessons (same privacy gate applies on the way in):\n\n"
                "```bash\n"
                f"python3 brain/central_brain.py pull --path {quoted_central_path} --project . --project-id {quoted_project_id}\n"
                "```\n",
                encoding="utf-8",
            )
            results.append(f"wrote {marker}")
        results.append(f"initialized central brain at {brain_file}")

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Install the optional Project OS full engine add-on.")
    parser.add_argument(
        "--target",
        type=nonempty_path,
        default=Path("."),
        help="Existing starter Project OS folder to update. Default: current folder.",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite add-on files that already exist.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be copied without writing files.")
    parser.add_argument("--starter-planned", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--claude", action="store_true", help="Also install Claude Code agents and slash commands.")
    parser.add_argument("--codex", action="store_true", help="Also install Codex project-local skills into .agents/skills/.")
    parser.add_argument(
        "--central-brain",
        default=None,
        help="Optional central brain folder to initialize/connect after installing the full engine.",
    )
    parser.add_argument("--project-id", default=None, help="Stable id to use when pushing this project to central brain.")
    args = parser.parse_args()

    try:
        results = install_full_engine(
            args.target,
            force=args.force,
            claude=args.claude,
            codex=args.codex,
            central_brain=Path(args.central_brain) if args.central_brain else None,
            project_id=args.project_id,
            dry_run=args.dry_run,
            starter_planned=args.starter_planned,
        )
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
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
