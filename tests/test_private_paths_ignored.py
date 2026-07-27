#!/usr/bin/env python3
"""Every path the code can write private data to must be gitignored.

This repo is a PUBLIC template. The brain holds distilled session lessons and the
OSVec side-car holds plaintext memory text; neither may ever be committed.

The 2026-07-25 audit found the ignore rules were anchored to the repo root
(`brain/shared-brain.jsonl`, `memory/store/`) while the full-engine modules write
two directories deeper (`addons/full-engine/brain/`, `addons/full-engine/memory/`).
`git check-ignore` said NOT-IGNORED for both real paths. Nothing had leaked --
the files did not exist -- but running the documented selftest followed by
`git add -A` would have staged them.

These tests read the WRITE PATHS OUT OF THE MODULES THEMSELVES rather than
restating them, so moving a module or adding a new store cannot silently
reopen the hole.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Every check here asks GIT what it would ignore, so it is only meaningful
# inside a real repository. GitHub offers "Download ZIP" right next to the
# clone URL, and in a .git-less copy `git check-ignore` returns non-zero for
# EVERY path -- read as NOT-IGNORED -- so the whole module failed loudly with
# alarming "this repo is public / holds PLAINTEXT memory text" messages, on
# the very command the README leads with (pre-push audit 2026-07-26). Skip
# with a reason instead; CI uses actions/checkout, which provides a real .git,
# so the guard still fails closed everywhere it can actually run.
HAVE_GIT_REPO = (ROOT / ".git").exists()
requires_repo = unittest.skipUnless(
    HAVE_GIT_REPO,
    "no .git here (ZIP download or vendored copy) — git check-ignore cannot "
    "answer, and a non-answer must not read as a leak",
)


def is_ignored(path: str) -> bool:
    """True when git would ignore `path` (whether or not it exists)."""
    proc = subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", path],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    return proc.returncode == 0


def module_constant_dir(module_rel: str, name: str) -> Path | None:
    """Resolve a `NAME = os.path.join(HERE, "x")` constant to a real path.

    Read statically -- importing these modules has side effects (they build
    stores). Returns None when the constant is not of that shape.
    """
    src_path = ROOT / module_rel
    if not src_path.exists():
        return None
    tree = ast.parse(src_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if name not in targets:
            continue
        call = node.value
        if not (isinstance(call, ast.Call) and call.args):
            continue
        parts = []
        for arg in call.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                parts.append(arg.value)
            elif isinstance(arg, ast.Name) and arg.id == "HERE":
                parts.append("")  # HERE == the module's own directory
        if parts:
            return src_path.parent.joinpath(*[p for p in parts if p])
    return None


@requires_repo
class PrivateWritePathsAreIgnored(unittest.TestCase):
    """The regression tests for the audit finding, stated as paths."""

    def test_brain_write_path_from_module_constant_is_ignored(self) -> None:
        for module in (
            "addons/full-engine/brain/brain.py",
            "brain/brain.py",
        ):
            path = module_constant_dir(module, "BRAIN_FILE")
            if path is None:
                continue
            rel = path.relative_to(ROOT).as_posix()
            with self.subTest(module=module, path=rel):
                self.assertTrue(
                    is_ignored(rel),
                    f"{rel} is the brain's real write path (from BRAIN_FILE in "
                    f"{module}) and is NOT gitignored. This repo is public.",
                )

    def test_osvec_store_write_path_from_module_constant_is_ignored(self) -> None:
        for module in (
            "addons/full-engine/memory/osvec_adapter.py",
            "memory/osvec_adapter.py",
        ):
            store = module_constant_dir(module, "STORE_DIR")
            if store is None:
                continue
            rel = (store / "project.sidecar.json").relative_to(ROOT).as_posix()
            with self.subTest(module=module, path=rel):
                self.assertTrue(
                    is_ignored(rel),
                    f"{rel} is the OSVec side-car's real write path (from "
                    f"STORE_DIR in {module}) and holds PLAINTEXT memory text. "
                    "It is NOT gitignored. This repo is public.",
                )

    def test_known_private_paths_are_ignored_at_any_depth(self) -> None:
        """Belt-and-braces: these must be ignored wherever they appear."""
        for rel in (
            "brain/shared-brain.jsonl",
            "addons/full-engine/brain/shared-brain.jsonl",
            "memory/store/project.sidecar.json",
            "addons/full-engine/memory/store/project.sidecar.json",
            "addons/full-engine/memory/store/index.tvim",
            "some/deeply/nested/path/shared-brain.jsonl",
            "code-graph.json",
        ):
            with self.subTest(path=rel):
                self.assertTrue(is_ignored(rel), f"{rel} is NOT gitignored")

    def test_template_source_is_still_publishable(self) -> None:
        """The broad `**/` rules must not start hiding tracked template files."""
        for rel in (
            "addons/full-engine/brain/brain.py",
            "addons/full-engine/brain/README.md",
            "addons/full-engine/memory/osvec_adapter.py",
            "memory/code_graph.py",
            "AGENTS.md",
        ):
            with self.subTest(path=rel):
                self.assertFalse(
                    is_ignored(rel),
                    f"{rel} is template source and must remain publishable",
                )

    def test_no_private_artifact_is_currently_tracked(self) -> None:
        """Nothing private may already be in the index."""
        proc = subprocess.run(
            ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
        )
        tracked = proc.stdout.splitlines()
        for name in ("shared-brain.jsonl", ".sidecar.json", ".tvim", "code-graph.json"):
            hits = [t for t in tracked if t.endswith(name)]
            with self.subTest(artifact=name):
                self.assertEqual(hits, [], f"private artifact is TRACKED: {hits}")


BRAIN_ARCHIVE = ROOT / "scripts" / "brain_archive.py"
# A string that exists nowhere else in this repo, so "the file holds a lesson"
# is decided by looking for it rather than by trusting a file name.
LESSON_MARKER = "PRIVATE-LESSON-TEXT-8f3c1a"


def archive_residue_names() -> list[str]:
    """File names a REAL `brain_archive.py apply` leaves beside the brain.

    Taken from behaviour, never from a literal: `ARCHIVE` is derived at import
    time from the configured brain path and the backup name ends in a
    `time.strftime()` stamp, so a hard-coded name here would go stale the
    moment either changed and the guard would quietly stop guarding. Only
    files that actually contain the lesson text are returned -- a lock file or
    other empty residue is not what this test is about.
    """
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        home = tmp / "home"
        home.mkdir()
        brain_dir = tmp / "central-brain"
        brain_dir.mkdir()
        brain = brain_dir / "shared-brain.jsonl"
        brain.write_text(
            "".join(
                json.dumps({"id": rid, "type": "lesson",
                            "text": LESSON_MARKER + " " + rid}) + "\n"
                for rid in ("L001", "L002")
            ),
            encoding="utf-8",
        )
        env = dict(os.environ)
        env["HOME"] = str(home)
        env["PROJECT_OS_SHARED_BRAIN"] = str(brain)
        # Keep the index rebuild out of the repo's own memory/ directory.
        env["MNEME_INDEX"] = str(tmp / "mneme_index.json")
        proc = subprocess.run(
            [sys.executable, str(BRAIN_ARCHIVE), "apply", "--ids", "L001"],
            env=env, capture_output=True, text=True, check=False,
        )
        if proc.returncode != 0:
            raise AssertionError(
                "brain_archive.py apply failed, so this test cannot say what "
                "it leaves on disk:\n" + proc.stdout + proc.stderr
            )
        names = []
        for path in sorted(brain_dir.iterdir()):
            if path.name == brain.name or not path.is_file():
                continue
            if LESSON_MARKER in path.read_text(encoding="utf-8", errors="replace"):
                names.append(path.name)
        return names


@requires_repo
class BrainArchiveResidueIsIgnored(unittest.TestCase):
    """`scripts/brain_archive.py` writes two more plaintext-lesson files.

    Archiving copies the active brain to `<brain>.pre-archive-<stamp>` and
    appends the moved records to a sibling `-archive.jsonl`. Both hold the very
    lesson text `**/shared-brain.jsonl` exists to keep out of this public repo,
    and neither name matches that rule, so with a brain configured inside the
    tree they landed as `??` and `git add -A` staged them verbatim.
    `scripts/project-os.gitignore` (the curated list shipped to other projects)
    has carried both rules since the 2026-07-26 audit; the repo's own
    .gitignore was left behind -- two files, one guard.
    """

    def test_the_run_leaves_lesson_text_on_disk(self) -> None:
        """The control: without residue the rest of this class proves nothing."""
        self.assertTrue(
            archive_residue_names(),
            "brain_archive.py apply left no lesson text beside the brain; "
            "the probe below would then pass for the wrong reason",
        )

    def test_archive_residue_is_ignored_at_every_brain_location(self) -> None:
        for name in archive_residue_names():
            for parent in (
                "brain",
                "addons/full-engine/brain",
                "some/deeply/nested/brain",
            ):
                rel = parent + "/" + name
                with self.subTest(path=rel):
                    self.assertTrue(
                        is_ignored(rel),
                        f"{rel} is written by scripts/brain_archive.py, holds "
                        "brain lesson text in plaintext and is NOT gitignored. "
                        "This repo is public.",
                    )

    def test_the_new_rules_hide_no_tracked_file(self) -> None:
        """The other control: an ignore rule that swallows tracked source is
        a worse bug than the one it fixes (the 2026-07-25 audit refused to
        copy this repo's own list into user projects for exactly that reason).
        """
        tracked = subprocess.run(
            ["git", "ls-files", "-z"], cwd=ROOT,
            capture_output=True, text=True, check=True,
        ).stdout
        hidden = subprocess.run(
            ["git", "check-ignore", "--no-index", "--stdin", "-z"],
            cwd=ROOT, input=tracked, capture_output=True, text=True, check=False,
        )
        self.assertEqual(
            hidden.stdout.replace("\0", "\n").strip(), "",
            "these tracked files are now ignored and would silently drop out "
            "of the published template",
        )


if __name__ == "__main__":
    unittest.main()
