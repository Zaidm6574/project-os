"""merge_gitignore() must never truncate the user's .gitignore in place (2026-07-27).

scripts/setup_project_os.py::merge_gitignore wrote the merged rules with
``Path.write_text``. That opens the destination with mode ``"w"``, which
TRUNCATES the user's file to zero bytes *before* a single new byte is flushed.
A full disk, a kill, or any OSError at that moment leaves the project with an
empty .gitignore and no backup -- while ``copy_file()`` forty lines above the
same function does keep a one-deep ``.pre-force`` backup. The rules at risk are
the user's own (``node_modules/``, ``.env``, secret globs), and an emptied
.gitignore silently starts committing exactly those files.

Proven in both directions:

* Failure injection (``io.open`` write raises ENOSPC) reaches the truncating
  implementation and an atomic tempfile+os.replace implementation EQUALLY --
  ``pathlib.Path.write_text`` and ``os.fdopen`` both route through ``io.open``
  on 3.9+, so neither is handed a path around the injection. The truncating
  version loses the file; an atomic one leaves it byte-identical.
* The mode tests below are the mirror: they pass against the old truncating
  code (an in-place truncate keeps the file's mode) and FAIL against a naive
  mkstemp rewrite, which would silently narrow every .gitignore to 0600
  because mkstemp always creates 0600 regardless of umask. They exist so the
  fix cannot trade one silent data problem for another.

Everything happens in tempfile sandboxes against the real shipped script.
"""

import errno
import importlib.util
import io
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "scripts" / "setup_project_os.py"
CURATED_IGNORE = ROOT / "scripts" / "project-os.gitignore"

# Rules a real user would lose: the first is why node_modules/ stays out of the
# repo, the last two are why a credential does.
USER_RULES = "node_modules/\n.env\n*.pem\nsecrets/*.key\n"


def load_setup(name):
    spec = importlib.util.spec_from_file_location(name, SETUP)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _NoSpaceHandle:
    """A file handle whose writes fail with ENOSPC, exactly as a full disk does.

    The file has already been OPENED -- and for mode ``"w"`` already TRUNCATED
    -- by the time ``write`` raises. That ordering is the whole defect: an
    implementation that opens the user's own file for writing has destroyed it
    before it can learn that the write will not complete.
    """

    def __init__(self, handle):
        self._handle = handle

    def _no_space(self, *_args, **_kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    write = _no_space
    writelines = _no_space

    def __enter__(self):
        self._handle.__enter__()
        return self

    def __exit__(self, *exc_info):
        return self._handle.__exit__(*exc_info)

    def __getattr__(self, name):
        return getattr(self._handle, name)


def failing_writes():
    """Patch io.open so every write-mode open inside the window fails on write.

    Reads are untouched (merge_gitignore has to read the existing file to merge
    it at all). The patch is entered around a single merge_gitignore() call.
    """
    real_open = io.open

    def opener(file, mode="r", *args, **kwargs):
        handle = real_open(file, mode, *args, **kwargs)
        if any(ch in mode for ch in "wxa+"):
            return _NoSpaceHandle(handle)
        return handle

    return mock.patch("io.open", opener)


class MergeGitignoreNeverTruncatesUserFile(unittest.TestCase):
    def setUp(self):
        self.setup = load_setup("setup_project_os_atomic_gitignore")
        self.assertTrue(CURATED_IGNORE.is_file(), CURATED_IGNORE)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.target = Path(self._tmp.name) / "proj"
        self.target.mkdir()
        self.dst = self.target / ".gitignore"

    def merge(self, force=False):
        return self.setup.merge_gitignore(
            CURATED_IGNORE, self.dst, force, root=self.target
        )

    def test_failed_write_leaves_the_users_gitignore_byte_identical(self):
        self.dst.write_text(USER_RULES, encoding="utf-8")
        before = self.dst.read_bytes()

        with failing_writes():
            with self.assertRaises(OSError) as caught:
                self.merge()
        self.assertEqual(caught.exception.errno, errno.ENOSPC)

        self.assertEqual(
            self.dst.read_bytes(),
            before,
            "a failed merge truncated the user's .gitignore: their own "
            "node_modules/, .env and secret rules are gone and there is no "
            "backup",
        )

    def test_failed_write_leaves_no_temp_litter_next_to_it(self):
        self.dst.write_text(USER_RULES, encoding="utf-8")

        with failing_writes():
            with self.assertRaises(OSError):
                self.merge()

        self.assertEqual(
            sorted(p.name for p in self.target.iterdir()),
            [".gitignore"],
            "a failed merge left scratch files in the user's project",
        )

    def test_failed_create_leaves_no_empty_gitignore_behind(self):
        # No .gitignore yet: the create branch must not leave a 0-byte file
        # that looks installed. A later run would treat it as "existing".
        self.assertFalse(self.dst.exists())

        with failing_writes():
            with self.assertRaises(OSError):
                self.merge()

        self.assertFalse(
            self.dst.exists(),
            "a failed create left an empty .gitignore that ignores nothing",
        )
        self.assertEqual(
            sorted(p.name for p in self.target.iterdir()),
            [],
            "a failed create left scratch files in the user's project",
        )

    def test_successful_merge_keeps_the_users_rules_and_appends_ours(self):
        self.dst.write_text(USER_RULES, encoding="utf-8")

        result = self.merge()

        merged = self.dst.read_text(encoding="utf-8")
        self.assertIn("merged", result)
        for rule in ("node_modules/", ".env", "*.pem", "secrets/*.key"):
            self.assertIn(rule, merged, rule)
        self.assertIn(self.setup.GITIGNORE_MARKER, merged)
        curated = CURATED_IGNORE.read_text(encoding="utf-8").splitlines()
        wanted = [
            line for line in curated
            if line.strip() and not line.lstrip().startswith("#")
        ]
        for line in wanted:
            self.assertIn(line, merged.splitlines(), line)

    def test_merge_keeps_the_destinations_own_permissions(self):
        """Mirror guard: a naive mkstemp rewrite narrows 0644 to 0600."""
        self.dst.write_text(USER_RULES, encoding="utf-8")
        os.chmod(str(self.dst), 0o644)

        self.merge()

        self.assertEqual(
            stat.S_IMODE(os.stat(str(self.dst)).st_mode),
            0o644,
            "merging silently changed the .gitignore's permissions",
        )

    def test_merge_does_not_widen_a_deliberately_private_gitignore(self):
        self.dst.write_text(USER_RULES, encoding="utf-8")
        os.chmod(str(self.dst), 0o600)

        self.merge()

        self.assertEqual(
            stat.S_IMODE(os.stat(str(self.dst)).st_mode),
            0o600,
            "merging widened a deliberately owner-only .gitignore",
        )

    def test_created_gitignore_gets_the_ordinary_new_file_mode(self):
        """Mirror guard: mkstemp's 0600 must not become the new file's mode."""
        reference = self.target / "reference-new-file"
        with open(str(reference), "w", encoding="utf-8") as fh:
            fh.write("x\n")
        expected = stat.S_IMODE(os.stat(str(reference)).st_mode)

        self.merge()

        self.assertEqual(
            stat.S_IMODE(os.stat(str(self.dst)).st_mode),
            expected,
            "a freshly created .gitignore did not get the mode a plain "
            "open() would have given it (umask ignored)",
        )


if __name__ == "__main__":
    unittest.main()
