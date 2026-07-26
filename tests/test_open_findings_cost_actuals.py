#!/usr/bin/env python3
"""Regression tests for cost_actuals' atomic ACTUALS-block writer.

The defect: `_update_markers()` writes atomically via `tempfile.mkstemp()` +
`os.replace()`. mkstemp always creates its temp file 0600, and os.replace
renames that file *with its mode* over the destination, so every `--write`
silently narrowed a shared 0644 cost report to owner-only 0600 -- no error and
no message. The atomic swap must carry the destination's own mode across.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
COST_ACTUALS = ROOT / "addons" / "full-engine" / "memory" / "cost_actuals.py"

MARKER_DOC = (
    "# Cost estimate\n"
    "\n"
    "<!-- ACTUALS:START -->\n"
    "old table\n"
    "<!-- ACTUALS:END -->\n"
    "\n"
    "trailing prose\n"
)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def mode_of(path):
    return stat.S_IMODE(os.stat(str(path)).st_mode)


class UpdateMarkersPreservesFileMode(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ca = load("cost_actuals_open_findings", COST_ACTUALS)

    def _doc(self, tmpdir, mode, name="09-cost-estimate.md"):
        path = Path(tmpdir) / name
        path.write_text(MARKER_DOC, encoding="utf-8")
        os.chmod(str(path), mode)
        return path

    def test_group_readable_report_stays_group_readable(self):
        """A 0644 team-readable report must not become 0600 after --write."""
        with tempfile.TemporaryDirectory() as tmpdir:
            dest = self._doc(tmpdir, 0o644)
            self.assertEqual(mode_of(dest), 0o644, "fixture did not start at 0644")

            self.ca._update_markers(dest, "NEW TABLE")

            self.assertEqual(
                mode_of(dest),
                0o644,
                "--write narrowed the cost report's permissions "
                "(mkstemp's 0600 rode along with os.replace)",
            )
            text = dest.read_text(encoding="utf-8")
            self.assertIn("NEW TABLE", text)
            self.assertNotIn("old table", text)
            self.assertIn("trailing prose", text)

    def test_intentionally_restrictive_file_is_not_widened(self):
        """Preserving the mode must copy it, not force some default."""
        with tempfile.TemporaryDirectory() as tmpdir:
            dest = self._doc(tmpdir, 0o600)

            self.ca._update_markers(dest, "NEW TABLE")

            self.assertEqual(
                mode_of(dest), 0o600, "a deliberately private report was widened"
            )

    def test_group_writable_mode_survives_too(self):
        """A shared 0664 report keeps its group-write bit."""
        with tempfile.TemporaryDirectory() as tmpdir:
            dest = self._doc(tmpdir, 0o664)

            self.ca._update_markers(dest, "NEW TABLE")

            self.assertEqual(mode_of(dest), 0o664)

    def test_symlink_destination_survives_the_swap(self):
        """os.replace on a symlink path would clobber the link itself."""
        with tempfile.TemporaryDirectory() as tmpdir:
            real = self._doc(tmpdir, 0o644, name="real-cost.md")
            link = Path(tmpdir) / "linked-cost.md"
            os.symlink(str(real), str(link))

            self.ca._update_markers(link, "NEW TABLE")

            self.assertTrue(
                link.is_symlink(), "the symlink was replaced by a regular file"
            )
            self.assertIn("NEW TABLE", real.read_text(encoding="utf-8"))
            self.assertEqual(mode_of(real), 0o644)

    def _doc_with_special_bit(self, tmpdir, mode):
        """Fixture at `mode`, skipped if this fs/uid cannot hold the bit.

        chmod() may silently drop S_ISUID/S_ISGID (e.g. the caller is not a
        member of the file's group), so a fixture that never held the bit
        cannot prove anything about the writer.
        """
        dest = self._doc(tmpdir, mode)
        if mode_of(dest) != mode:
            self.skipTest(
                "this filesystem/uid cannot set mode %s (got %s)"
                % (oct(mode), oct(mode_of(dest)))
            )
        return dest

    def test_setgid_bit_survives_the_swap(self):
        """chown() clears setgid, so it must run BEFORE the chmod.

        POSIX makes a non-root chown() drop S_ISUID/S_ISGID -- even a
        same-owner no-op chown. Doing ownership last turned a 0o2644 report
        into 0o644 with no message: the same silent narrowing, one octal
        digit up.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            dest = self._doc_with_special_bit(tmpdir, 0o2644)

            self.ca._update_markers(dest, "NEW TABLE")

            self.assertEqual(
                mode_of(dest),
                0o2644,
                "--write stripped the setgid bit (chown ran after chmod)",
            )
            self.assertIn("NEW TABLE", dest.read_text(encoding="utf-8"))

    def test_setuid_bit_survives_the_swap(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dest = self._doc_with_special_bit(tmpdir, 0o4755)

            self.ca._update_markers(dest, "NEW TABLE")

            self.assertEqual(
                mode_of(dest), 0o4755, "--write stripped the setuid bit"
            )

    def test_sticky_bit_survives_the_swap(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dest = self._doc_with_special_bit(tmpdir, 0o1644)

            self.ca._update_markers(dest, "NEW TABLE")

            self.assertEqual(mode_of(dest), 0o1644)

    def test_unpreservable_mode_is_reported_not_swallowed(self):
        """If the OS refuses a mode bit, say so instead of narrowing quietly."""
        real_chmod = os.chmod

        def lossy_chmod(path, mode, *args, **kwargs):
            # Simulate a kernel that refuses setgid for this caller.
            return real_chmod(path, mode & ~stat.S_ISGID, *args, **kwargs)

        with tempfile.TemporaryDirectory() as tmpdir:
            dest = self._doc_with_special_bit(tmpdir, 0o2644)
            err = io.StringIO()

            with contextlib.redirect_stderr(err):
                with mock.patch.object(os, "chmod", lossy_chmod):
                    self.ca._update_markers(dest, "NEW TABLE")

            self.assertIn("WARNING", err.getvalue())
            self.assertIn("0o2644", err.getvalue())
            # The write itself still lands -- we warn, we do not abort.
            self.assertIn("NEW TABLE", dest.read_text(encoding="utf-8"))

    def test_writer_failure_leaves_dest_and_directory_clean(self):
        """Cover the writer's OWN failure path, not just marker validation.

        The marker-refusal test below never enters the atomic writer, so the
        mkstemp -> unlink -> reraise cleanup had no coverage at all.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            dest = self._doc(tmpdir, 0o644)
            before = dest.read_text(encoding="utf-8")

            def boom(src, dst, *args, **kwargs):
                raise OSError("simulated rename failure")

            with mock.patch.object(os, "replace", boom):
                with self.assertRaises(OSError):
                    self.ca._update_markers(dest, "NEW TABLE")

            self.assertEqual(dest.read_text(encoding="utf-8"), before)
            self.assertEqual(mode_of(dest), 0o644)
            leftovers = [p.name for p in Path(tmpdir).iterdir() if p != dest]
            self.assertEqual(
                leftovers, [], "failed write left a temp file behind"
            )

    def test_chmod_failure_is_loud_and_cleans_up(self):
        """A chmod that cannot run must not degrade into a silent 0600 write."""
        with tempfile.TemporaryDirectory() as tmpdir:
            dest = self._doc(tmpdir, 0o644)
            before = dest.read_text(encoding="utf-8")

            def boom(path, mode, *args, **kwargs):
                raise OSError("simulated chmod failure")

            with mock.patch.object(os, "chmod", boom):
                with self.assertRaises(OSError):
                    self.ca._update_markers(dest, "NEW TABLE")

            self.assertEqual(dest.read_text(encoding="utf-8"), before)
            self.assertEqual(mode_of(dest), 0o644)
            leftovers = [p.name for p in Path(tmpdir).iterdir() if p != dest]
            self.assertEqual(leftovers, [], "failed chmod left a temp file behind")

    def test_no_temp_files_are_left_behind(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dest = self._doc(tmpdir, 0o644)

            self.ca._update_markers(dest, "NEW TABLE")

            leftovers = [p.name for p in Path(tmpdir).iterdir() if p != dest]
            self.assertEqual(leftovers, [], "temp file left next to the target")

    def test_failed_write_leaves_the_original_file_untouched(self):
        """A corrupted-marker refusal must not touch mode or content."""
        with tempfile.TemporaryDirectory() as tmpdir:
            dest = Path(tmpdir) / "reversed.md"
            dest.write_text(
                "<!-- ACTUALS:END -->\nbody\n<!-- ACTUALS:START -->\n",
                encoding="utf-8",
            )
            os.chmod(str(dest), 0o644)
            before = dest.read_text(encoding="utf-8")

            with self.assertRaises(ValueError):
                self.ca._update_markers(dest, "NEW TABLE")

            self.assertEqual(dest.read_text(encoding="utf-8"), before)
            self.assertEqual(mode_of(dest), 0o644)


class WriteThroughTheCliPreservesMode(unittest.TestCase):
    """End-to-end: `--write` through the real CLI must not narrow the target."""

    def test_cli_write_keeps_0644(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript = Path(tmpdir) / "session.jsonl"
            transcript.write_text(
                '{"model": "claude-opus-4-8", "usage": {"input_tokens": 10, '
                '"output_tokens": 5, "cache_creation_input_tokens": 0, '
                '"cache_read_input_tokens": 0}}\n',
                encoding="utf-8",
            )
            dest = Path(tmpdir) / "09-cost-estimate.md"
            dest.write_text(MARKER_DOC, encoding="utf-8")
            os.chmod(str(dest), 0o644)

            result = subprocess.run(
                [
                    sys.executable,
                    str(COST_ACTUALS),
                    "--transcript",
                    str(transcript),
                    "--target",
                    str(dest),
                    "--write",
                ],
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Main loop", dest.read_text(encoding="utf-8"))
            self.assertEqual(
                mode_of(dest),
                0o644,
                "CLI --write narrowed the target file's permissions",
            )


class SelftestStillPasses(unittest.TestCase):
    def test_selftest(self):
        result = subprocess.run(
            [sys.executable, str(COST_ACTUALS), "--selftest"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("cost_actuals selftest: OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
