#!/usr/bin/env python3
"""Bootstrap Project OS files into a target project."""

from __future__ import annotations

import argparse
import collections
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


TEMPLATE_ROOT = Path(__file__).resolve().parents[1]
FULL_ENGINE_MEMORY = TEMPLATE_ROOT / "addons" / "full-engine" / "memory"

# memory/ helpers this installer owns and delivers from the repo's CANONICAL
# memory/, not from the add-on tree.
#
# context_budget.py and code_graph.py were added to memory/ without being added
# here, so AGENTS.md's "Kickoff preflight: run `python3
# memory/context_budget.py`" -- the FIRST documented step of a run -- pointed
# at a nonexistent file in every installed project (pre-push audit
# 2026-07-26). Doc drift in an installer is a broken promise, not a cosmetic
# issue: ship what the docs tell people to run.
#
# Module-level because scripts/install_full_engine.py -- which install.sh runs
# against the SAME target right after this one -- reads it to know which files
# it must not shadow with the older add-on forks it carries.
CANONICAL_MEMORY_ADAPTERS = ("mneme_adapter.py", "build_graph.py",
                             "context_budget.py", "code_graph.py")
GITIGNORE_MARKER = "# Project OS private files"
GENERATED_DIRS = {"__pycache__", "store", "out", "graphify-out", ".turbovec"}
GENERATED_SUFFIXES = (".pyc", ".tvim", ".sidecar.json", ".manifest.json",
                      ".db", ".db.tmp", ".db-wal", ".db-shm", ".db-journal",
                      ".db.tmp-wal", ".db.tmp-shm", ".db.tmp-journal")

# Directories that are never a "project" — installing ~120 files and appending
# ignore rules into any of them is a mistake, not a choice. Compared by
# realpath EXACT match only: /tmp is fine as a parent (README's own demo uses
# /tmp/demo, and every test sandbox lives under /private/var/folders/...), it
# is only wrong as the target itself.
SYSTEM_TARGET_ROOTS = ("/usr", "/etc", "/var", "/bin", "/sbin", "/lib", "/opt",
                       "/dev", "/proc", "/boot", "/srv", "/root", "/tmp",
                       "/private", "/Users", "/home", "/Library", "/System",
                       "/Applications", "/Volumes")

# A destination the installer must not write: `link` is the symlink in the way
# (None when the path escaped for another reason) and `escapes` says whether
# the real path it would have hit lies OUTSIDE the target root -- the
# difference between "your repo's own AGENTS.md -> CLAUDE.md link was skipped"
# and "this install would have written into someone else's directory".
Refusal = collections.namedtuple("Refusal", "dest link reason escapes")


def nonempty_path(value: str) -> Path:
    if not value.strip():
        raise argparse.ArgumentTypeError("target must be a non-empty path")
    return Path(value)


def _within(real_root: str, real_path: str) -> bool:
    if real_path == real_root:
        return True
    try:
        return os.path.commonpath([real_root, real_path]) == real_root
    except ValueError:
        return False


def _first_symlink(root: Path, dst: Path):
    """First symlink between `root` and `dst`, or None when the path is clean.

    Checked component by component so the link is caught BEFORE anything
    follows it. `dst.exists()` cannot do this job: it returns False for a
    DANGLING link, so the "kept existing" branch never fires and the write
    creates a brand-new file wherever the link points.
    """
    try:
        rel = dst.relative_to(root)
    except ValueError:
        return None
    current = root
    for part in rel.parts:
        current = current / part
        if current.is_symlink():
            return current
    return None


def unsafe_destination(dst: Path, root=None):
    """Refusal when writing `dst` would leave the target, else None.

    shutil.copy2, Path.mkdir(parents=True) and Path.write_text all FOLLOW
    symlinks, so before this guard a link anywhere between the target root and
    a destination redirected the installer's writes out of the project it was
    pointed at: a `scripts -> /somewhere/else` directory link carried the whole
    scripts/ tree with it, and no --force was needed (audit 2026-07-26).

    Two independent checks, because neither alone is enough: the component
    walk refuses to follow a link (realpath cannot distinguish a file the
    installer created outside from one that was always there), and a
    realpath + commonpath containment check -- the same shape as the worktree
    guard in scripts/wt.py -- catches whatever the walk cannot see.
    """
    dst = Path(dst)
    if root is None:
        # No root to contain against (helper used standalone): still never
        # write through a link.
        if dst.is_symlink():
            return Refusal(dst, dst, "symlink %s -> %s" % (dst, os.path.realpath(dst)), True)
        return None
    root = Path(root)
    real_root = os.path.realpath(root)
    link = _first_symlink(root, dst)
    if link is not None:
        real_link = os.path.realpath(link)
        escapes = not _within(real_root, real_link)
        where = "outside the target" if escapes else "inside the target"
        return Refusal(dst, link, "symlink %s -> %s (%s)" % (link, real_link, where), escapes)
    real_dst = os.path.realpath(dst)
    if not _within(real_root, real_dst):
        return Refusal(dst, None, "%s resolves to %s (outside the target %s)"
                                  % (dst, real_dst, real_root), True)
    return None


def _refuse(dst: Path, root, refusals):
    """Shared prologue for every write: returns a result line, or None to go on."""
    refusal = unsafe_destination(dst, root)
    if refusal is None:
        return None
    if refusals is not None:
        refusals.append(refusal)
    if refusal.link is None:
        return "REFUSED %s" % refusal.reason
    return "REFUSED %s: not written through %s" % (dst, refusal.reason)


def unsafe_target_root(target: Path) -> str:
    """Why this target is not a project folder, or "" when it is fine.

    $HOME was accepted with no guard at all, so `./install.sh ~` (or
    `./install.sh .` from a home-directory shell) sprayed ~120 files across the
    user's home and appended Project OS rules to ~/.gitignore (audit
    2026-07-26).
    """
    real = os.path.realpath(target)
    if os.path.dirname(real) == real:
        return "the filesystem root"
    if real == os.path.realpath(os.path.expanduser("~")):
        return "your home directory ($HOME)"
    for candidate in SYSTEM_TARGET_ROOTS:
        if real == os.path.realpath(candidate):
            return "the system directory %s" % candidate
    return ""


def is_distributable(src: Path, src_dir: Path) -> bool:
    rel = src.relative_to(src_dir)
    return (
        src.name != "shared-brain.jsonl"
        and not any(part in GENERATED_DIRS for part in rel.parts)
        and not src.name.endswith(GENERATED_SUFFIXES)
    )


def copy_file(src: Path, dst: Path, force: bool, dry_run: bool = False,
              root=None, refusals=None) -> str:
    refused = _refuse(dst, root, refusals)
    if refused is not None:
        return refused
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
        # The backup is a write too: through a link named <file>.pre-force it
        # would copy the user's file OUT of the target. No safe backup means no
        # overwrite -- the edit is worth more than the template.
        refused_backup = _refuse(backup, root, refusals)
        if refused_backup is not None:
            return f"{refused_backup}; kept existing {dst}"
        shutil.copy2(dst, backup)
        shutil.copy2(src, dst)
        return f"wrote {dst} (previous version saved to {backup.name})"
    shutil.copy2(src, dst)
    return f"wrote {dst}"


def copy_tree_files(src_dir: Path, dst_dir: Path, force: bool, dry_run: bool = False,
                    root=None, refusals=None) -> list[str]:
    results: list[str] = []
    # Without an explicit root the tree's own destination is the containment
    # boundary, so a standalone caller is guarded too.
    root = dst_dir if root is None else root
    for src in sorted(p for p in src_dir.rglob("*") if p.is_file() and is_distributable(p, src_dir)):
        rel = src.relative_to(src_dir)
        results.append(copy_file(src, dst_dir / rel, force, dry_run=dry_run,
                                 root=root, refusals=refusals))
    return results


def _atomic_write_text(dst: Path, text: str) -> None:
    """Put `text` into `dst` with one rename; never truncate `dst` in place.

    Path.write_text() opens the destination with mode "w", which truncates the
    user's own file to zero BEFORE a single new byte is flushed. A full disk, a
    kill, or any OSError at that moment left an EMPTY .gitignore and no
    backup -- while copy_file() forty lines above deliberately keeps a one-deep
    .pre-force copy of anything it is about to overwrite. The file this touches
    is not ours: it holds the user's node_modules/, .env and secret rules, and
    emptying it silently starts committing exactly those (audit 2026-07-27).
    --force is not consulted on the merge path, so a plain ./install.sh reached
    the truncating write.

    Same shape as scripts/brain_archive.py::_atomic_write_preserving_mode and
    addons/full-engine/memory/cost_actuals.py::_atomic_write_preserving_mode:
    tempfile.mkstemp() in the destination's OWN directory (O_CREAT|O_EXCL, so
    the temp name can never be pre-planted), write, flush + fsync so the bytes
    are durable before the swap, carry the destination's existing mode across,
    then a single os.replace(). Readers see the old file or the new one, never
    a half-written one, and a failure anywhere leaves the original untouched.

    `dst` is NOT resolved. _refuse()/unsafe_destination() already decided this
    literal path is safe to write; realpath-ing here would re-follow a link at
    write time and hand that decision back -- the exact regression already
    fixed in brain_archive.py. os.replace() does not follow symlinks either.
    """
    dst = Path(dst)
    try:
        dest_mode = stat.S_IMODE(os.stat(str(dst)).st_mode)
    except OSError:
        dest_mode = None
    fd, tmp_name = tempfile.mkstemp(dir=str(dst.parent),
                                    prefix=dst.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if dest_mode is None:
            # Nothing to copy from: use the ordinary create-a-file mode (0666
            # honoring the umask) rather than leaving mkstemp's deliberate
            # 0600, which would hand the user a .gitignore their own group and
            # CI cannot read.
            umask = os.umask(0)
            os.umask(umask)
            dest_mode = 0o666 & ~umask
        # Not best-effort: swallowing this would silently narrow (or widen) the
        # user's file, which is its own defect.
        os.chmod(tmp_name, dest_mode)
        os.replace(tmp_name, str(dst))
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def merge_gitignore(src: Path, dst: Path, force: bool, dry_run: bool = False,
                    root=None, refusals=None) -> str:
    refused = _refuse(dst, root, refusals)
    if refused is not None:
        return refused
    project_os_lines = src.read_text(encoding="utf-8").splitlines()
    project_os_block = "\n".join(project_os_lines).strip()
    if not dst.exists():
        if dry_run:
            return f"would write {dst}"
        dst.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(dst, project_os_block + "\n")
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
    _atomic_write_text(
        dst,
        f"{existing}{separator}\n{GITIGNORE_MARKER}\n" + "\n".join(missing_lines) + "\n",
    )
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


def bootstrap(target: Path, force: bool, dry_run: bool = False,
              allow_unsafe_target: bool = False, results=None, refusals=None) -> list[str]:
    target = target.expanduser().resolve()

    wrong_root = unsafe_target_root(target)
    if wrong_root and not allow_unsafe_target:
        raise SystemExit(
            "REFUSED: --target %s is %s.\n"
            "A Project OS install writes ~120 files into the target and appends its\n"
            "ignore rules to the target's .gitignore, so this would scatter them across\n"
            "that whole directory.\n"
            "Point --target at a project folder instead, e.g. --target %s.\n"
            "Pass --allow-unsafe-target if you really mean this directory."
            % (target, wrong_root, os.path.join(str(target), "my-project")))

    # Callers that want to see a partial install (and the refusals) after a
    # mid-run failure pass their own lists in; everyone else gets fresh ones.
    results = [] if results is None else results
    refusals = [] if refusals is None else refusals
    if dry_run and not target.exists():
        results.append(f"would create {target}")
    if not dry_run:
        target.mkdir(parents=True, exist_ok=True)

    def copy(src, dst):
        return copy_file(src, dst, force, dry_run=dry_run, root=target, refusals=refusals)

    def copy_tree(src_dir, dst_dir):
        return copy_tree_files(src_dir, dst_dir, force, dry_run=dry_run,
                               root=target, refusals=refusals)

    results.append(copy(TEMPLATE_ROOT / "AGENTS.md", target / "AGENTS.md"))
    results.append(copy(TEMPLATE_ROOT / "CLAUDE.md", target / "CLAUDE.md"))
    # Merge the CURATED ignore list, not the repo's own .gitignore. The repo
    # file also ignores lines specific to this repository (a bare `app`,
    # `code-graph.json`, `arachne-out/`, ...), and copying it wholesale silently
    # un-tracked a user's own `app/` directory (audit 2026-07-25).
    curated_ignore = TEMPLATE_ROOT / "scripts" / "project-os.gitignore"
    ignore_src = curated_ignore if curated_ignore.is_file() else TEMPLATE_ROOT / ".gitignore"
    results.append(merge_gitignore(ignore_src, target / ".gitignore", force, dry_run=dry_run,
                                   root=target, refusals=refusals))
    results.extend(copy_tree(TEMPLATE_ROOT / "prompts", target / "prompts"))
    results.extend(copy_tree(TEMPLATE_ROOT / "scripts", target / "scripts"))
    results.extend(copy_tree(TEMPLATE_ROOT / "addons" / "full-engine", target / "addons" / "full-engine"))
    results.extend(copy_tree(TEMPLATE_ROOT / "blackboard-template", target / "blackboard"))
    results.extend(copy_tree(TEMPLATE_ROOT / "runs-template", target / "runs"))
    results.extend(copy_tree(TEMPLATE_ROOT / "outputs-template", target / "outputs"))
    results.extend(copy_tree(TEMPLATE_ROOT / "memory-template", target / "memory"))
    # Vector-memory + graph adapters are code, not personal data — the installer
    # must deliver what the README promises. The core adapters ship from the
    # repo's own memory/; the full-engine helpers fill in anything missing.
    delivered = set()
    for adapter in CANONICAL_MEMORY_ADAPTERS:
        src = TEMPLATE_ROOT / "memory" / adapter
        if src.is_file():
            results.append(copy(src, target / "memory" / adapter))
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
            results.append(copy(src, target / "memory" / helper))

    # mkdir(exist_ok=True) follows a symlink too: through a live link it
    # "ensures" someone else's directory, and through a dangling one it
    # CREATES it. Same guard as the file writes.
    for private in (target / "private-memory", target / "private-imports"):
        refused = _refuse(private, target, refusals)
        if refused is not None:
            results.append(refused)
        elif dry_run:
            results.append(f"would ensure {private}")
        else:
            private.mkdir(exist_ok=True)
            results.append(f"ensured {private}")

    return results


def report_partial_install(exc: OSError, results: list) -> int:
    """A mid-install write failure used to abort with a raw traceback, leaving a
    half-installed target and no idea which half (audit 2026-07-26). Say what
    was already written and how to undo it."""
    written = [line for line in results
               if line.startswith(("wrote ", "merged ", "ensured "))]
    print("REFUSED: the install stopped part-way, so the target is half installed.",
          file=sys.stderr)
    print(f"Cause: {exc}", file=sys.stderr)
    print("", file=sys.stderr)
    if written:
        print(f"Already written before the failure ({len(written)} path(s)):", file=sys.stderr)
        for line in written:
            print(f"- {line}", file=sys.stderr)
    else:
        print("Nothing had been written yet.", file=sys.stderr)
    print("", file=sys.stderr)
    print("To reverse it:", file=sys.stderr)
    print("- delete the paths listed above that the project did not have before", file=sys.stderr)
    print("- restore any <file>.pre-force backup left next to a file you had edited", file=sys.stderr)
    print("- in a git repo: git status --short --ignored, then git checkout -- <path>", file=sys.stderr)
    print("Then fix the cause above (a permission, or a file where a folder is "
          "needed) and re-run.", file=sys.stderr)
    return 1


def report_refusals(refusals: list) -> int:
    """Print skipped destinations, one line per link in the way.

    Exit status is nonzero only when a refusal pointed OUT of the target: that
    is the install-into-someone-else's-directory case, and install.sh (set -e)
    should stop before its later stages. A link that stays inside the target --
    this project's own `AGENTS.md -> CLAUDE.md` convention, say -- is reported
    and skipped, but it is not a reason to fail an otherwise good install.
    """
    if not refusals:
        return 0
    groups = collections.OrderedDict()
    for refusal in refusals:
        groups.setdefault(str(refusal.link or refusal.dest), []).append(refusal)
    print("", file=sys.stderr)
    print(f"REFUSED to write {len(refusals)} path(s): the installer never writes "
          "through a symlink or outside the target.", file=sys.stderr)
    for group in groups.values():
        first = group[0]
        if first.link is None:
            print(f"- {first.reason}", file=sys.stderr)
            continue
        more = "" if len(group) == 1 else f" ({len(group)} destinations under it)"
        print(f"- {first.reason}{more}: skipped {first.dest}", file=sys.stderr)
    print("Nothing was written through those links. Remove or rename the link (or "
          "copy the file in yourself) and re-run.", file=sys.stderr)
    return 1 if any(refusal.escapes for refusal in refusals) else 0


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
    parser.add_argument(
        "--allow-unsafe-target",
        action="store_true",
        help="Install into $HOME, /, or a system directory anyway. Almost always a "
             "mistake: the install writes ~120 files into the target and appends to "
             "its .gitignore.",
    )
    args = parser.parse_args()

    results: list[str] = []
    refusals: list = []
    try:
        bootstrap(args.target, args.force, dry_run=args.dry_run,
                  allow_unsafe_target=args.allow_unsafe_target,
                  results=results, refusals=refusals)
    except OSError as exc:
        return report_partial_install(exc, results)
    if args.dry_run:
        print("Project OS setup dry run complete.")
    else:
        print("Project OS setup complete.")
    for result in results:
        print(f"- {result}")
    exit_code = report_refusals(refusals)
    warning = committed_memory_index_warning(args.target.expanduser().resolve())
    if warning:
        print()
        for line in warning:
            print(line)
    print()
    print("Next:")
    print("1. Open the target project in Codex, Claude, or your AI coding tool.")
    # 2026-07-27: this said `Say: /project <your idea>`. This function runs for
    # the STARTER install, which writes no .claude/ directory -- so it named a
    # slash command that does not exist in the target it just created, as the
    # very first thing a new user is told to do.
    print("2. Describe your idea in plain words — CLAUDE.md / AGENTS.md is read")
    print("   automatically. For slash commands, reinstall with --full-engine")
    print("   --claude-engine (Claude) or --codex-engine (Codex).")
    print("3. Build graph context when useful: python3 memory/build_graph.py --root blackboard")
    print("4. Optional: run scripts/import_chat_history.py on a local chat export.")
    print("5. Before committing, run: git status --short --ignored")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
