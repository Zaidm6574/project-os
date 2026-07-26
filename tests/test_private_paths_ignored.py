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
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


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


if __name__ == "__main__":
    unittest.main()
