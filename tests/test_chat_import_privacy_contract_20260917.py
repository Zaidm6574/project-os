"""Private report boundaries: synthetic data, isolated files, no external services."""
import importlib.util
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
IMPORTER = ROOT / "scripts" / "import_chat_history.py"
CANONICAL = ROOT / "scripts" / "secret_patterns.py"
BRAIN = ROOT / "addons" / "full-engine" / "brain" / "brain.py"


def load_importer(path=IMPORTER):
    spec = importlib.util.spec_from_file_location("privacy_contract_importer", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_importer(script, source, output, excerpts=True):
    args = [sys.executable, "-B", str(script), "--input", str(source),
            "--output", str(output)]
    if excerpts:
        args.append("--include-excerpts")
    return subprocess.run(args, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=30)


class PrivacyContract(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="chat-privacy-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.mod = load_importer()

    def layout(self, canonical=True, brain_at="addons/full-engine/brain"):
        project = self.root / "project"
        scripts = project / "scripts"
        scripts.mkdir(parents=True)
        script = scripts / IMPORTER.name
        shutil.copy2(IMPORTER, script)
        brain = project / brain_at / "brain.py"
        brain.parent.mkdir(parents=True)
        shutil.copy2(BRAIN, brain)
        if canonical:
            shutil.copy2(CANONICAL, scripts / CANONICAL.name)
        return script, brain

    def export(self, value="harmless planning notes"):
        source = self.root / "export.txt"
        source.write_text("I want to build " + value + " into this synthetic project workflow today\n",
                          encoding="utf-8")
        return source

    def assert_refused(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("REFUSED", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def assert_no_staging_files(self):
        self.assertEqual(list(self.root.rglob(".chat-report-*")), [])

    def test_alphabetic_assignments_are_redacted_at_canonical_length_boundaries(self):
        patterns = self.mod.redaction_patterns(self.mod.load_credential_patterns())
        for key in ("password", "passwd", "api_key", "secret", "token"):
            for separator in ("=", ": "):
                for value in ("violet", "sunshine", "abcdefghijklmno"):
                    with self.subTest(key=key, separator=separator, length=len(value)):
                        counts = Counter()
                        text = "config " + key + separator + value + " next"
                        redacted = self.mod.redact(text, patterns, counts)
                        self.assertNotIn(value, redacted)
                        self.assertEqual(sum(counts.values()), 1)
                        self.assertIn("config", redacted)
                        self.assertIn("next", redacted)

    def test_password_status_sentence_is_treated_as_sensitive(self):
        # A former prose exemption cannot distinguish this value from a password.
        text = "My password: forgotten again, need to reset it tomorrow morning."
        counts = Counter()
        result = self.mod.redact(text, counts=counts)
        self.assertNotIn("forgotten", result)
        self.assertEqual(sum(counts.values()), 1)

    def test_quoted_multiword_passphrases_do_not_leave_their_tail(self):
        for quote in ("'", '"'):
            for suffix in (quote + " now", "\nordinary followup survives"):
                with self.subTest(quote=quote, truncated=suffix.startswith("\n")):
                    counts = Counter()
                    result = self.mod.redact(
                        "config password=" + quote + "violet meadow lantern" + suffix,
                        counts=counts)
                    for fragment in ("violet", "meadow", "lantern"):
                        self.assertNotIn(fragment, result)
                    self.assertEqual(sum(counts.values()), 1)
                    self.assertIn("ordinary followup survives" if suffix.startswith("\n") else "now",
                                  result)

    def test_harmless_prose_and_near_miss_tokens_survive(self):
        lines = [
            "The secret: happiness comes from within and cannot be bought.",
            "Design token: spacing scale should follow an eight point grid.",
            "The bearer: whoever holds this note owes nothing further today.",
            "I prefer the password manager workflow for this project.",
            "I want to rotate the api_key policy before the demo.",
            "Short examples sk-abc and ghp_x are intentionally incomplete.",
        ]
        for line in lines:
            with self.subTest(line=line):
                counts = Counter()
                self.assertEqual(self.mod.redact(line, counts=counts), line)
                self.assertEqual(sum(counts.values()), 0)

    def test_written_excerpts_and_default_excerpts_off(self):
        value = "sun" + "shine"
        source = self.export("password=" + value)
        for excerpts in (False, True):
            output = self.root / ("excerpts.md" if excerpts else "default.md")
            result = run_importer(IMPORTER, source, output, excerpts=excerpts)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn(value, output.read_text())
            self.assertIn("Redacted 1 ", result.stdout)
            self.assertEqual("Excerpt:" in output.read_text(), excerpts)

    def test_missing_canonical_refuses_before_creating_or_changing_output(self):
        script, _ = self.layout(canonical=False)
        # This family is absent from the portable compatibility scanner.
        value = "AI" + "za" + "SYNTHETICPRIVACYCONTROL1234567890"
        source = self.export(value)
        absent = self.root / "new-directory" / "report.md"
        result = run_importer(script, source, absent)
        self.assert_refused(result)
        self.assertFalse(absent.parent.exists())
        existing = self.root / "existing.md"
        existing.write_bytes(b"previous private review")
        result = run_importer(script, source, existing)
        self.assert_refused(result)
        self.assertEqual(existing.read_bytes(), b"previous private review")
        self.assertFalse(existing.with_suffix(".md.bak").exists())

    def test_cached_module_cannot_substitute_for_missing_local_scanner(self):
        script, _ = self.layout(canonical=False)
        importer = load_importer(script)
        impostor = types.ModuleType("secret_patterns")
        impostor.redaction_pairs = lambda: []
        with mock.patch.dict(sys.modules, {"secret_patterns": impostor}):
            with self.assertRaises(importer.RedactionUnavailable):
                importer.redact("password=" + "sunshine")

    def test_invalid_canonical_module_refuses_without_report(self):
        script, _ = self.layout()
        scanner = script.parent / "secret_patterns.py"
        scanner.write_text("this is not Python (\n")
        output = self.root / "report.md"
        result = run_importer(script, self.export(), output)
        self.assert_refused(result)
        self.assertFalse(output.exists())

    def test_exhaustive_marker_without_canonical_coverage_is_refused(self):
        script, brain = self.layout()
        brain.write_text("import re\nSECRET_SCAN_EXHAUSTIVE = True\n"
                         "SECRET_PATTERNS = [re.compile(r'never-matches')]\n")
        output = self.root / "report.md"
        self.assert_refused(run_importer(script, self.export(), output))
        self.assertFalse(output.exists())

    def test_symlinked_canonical_module_is_refused(self):
        script, _ = self.layout(canonical=False)
        (script.parent / "secret_patterns.py").symlink_to(CANONICAL)
        output = self.root / "report.md"
        self.assert_refused(run_importer(script, self.export(), output))
        self.assertFalse(output.exists())

    def test_both_complete_install_layouts_import_and_redact(self):
        value = "AI" + "za" + "SYNTHETICPRIVACYCONTROL1234567890"
        for brain_at in ("addons/full-engine/brain", "brain"):
            with self.subTest(brain_at=brain_at):
                script, _ = self.layout(brain_at=brain_at)
                output = self.root / "report.md"
                result = run_importer(script, self.export(value), output)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn(value, output.read_text())
                self.assertIn("I want to build", output.read_text())
                self.assertIn("Redacted 1 ", result.stdout)
                shutil.rmtree(self.root / "project")
                output.unlink()

    def test_new_report_is_private_independent_of_umask(self):
        for mask in (0o000, 0o022):
            with self.subTest(umask=oct(mask)):
                output = self.root / str(mask) / "report.md"
                previous = os.umask(mask)
                try:
                    self.mod.write_summary([], output, 30, False)
                finally:
                    os.umask(previous)
                self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(output.parent.stat().st_mode), 0o700)
                self.assertFalse(output.with_suffix(".md.bak").exists())
        self.assert_no_staging_files()

    def test_replacement_and_backup_preserve_or_tighten_modes_and_bytes(self):
        for mode in (0o600, 0o644, 0o400):
            with self.subTest(mode=oct(mode)):
                output = self.root / (str(mode) + ".md")
                old = b"previous synthetic private review\n"
                output.write_bytes(old)
                output.chmod(mode)
                old_inode = output.stat().st_ino
                previous = os.umask(0o022)
                try:
                    self.mod.write_summary([], output, 30, False)
                finally:
                    os.umask(previous)
                backup = output.with_suffix(".md.bak")
                self.assertEqual(backup.read_bytes(), old)
                self.assertTrue(output.read_bytes().startswith(b"# Private Chat Memory Summary"))
                for path in (output, backup):
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode & 0o600)
                    self.assertEqual(path.stat().st_nlink, 1)
                self.assertNotEqual(output.stat().st_ino, old_inode)
        self.assert_no_staging_files()

    def test_report_symlink_hardlink_directory_and_fifo_are_refused(self):
        for kind in ("symlink", "dangling", "hardlink", "directory", "fifo"):
            with self.subTest(kind=kind):
                case = self.root / kind
                case.mkdir()
                output = case / "report.md"
                neighbor = case / "neighbor"
                if kind != "dangling":
                    neighbor.write_bytes(b"neighbor must survive")
                if kind in ("symlink", "dangling"):
                    output.symlink_to(neighbor)
                elif kind == "hardlink":
                    os.link(neighbor, output)
                elif kind == "directory":
                    output.mkdir()
                else:
                    os.mkfifo(output)
                result = run_importer(IMPORTER, self.export(), output)
                self.assert_refused(result)
                if kind == "dangling":
                    self.assertFalse(neighbor.exists())
                else:
                    self.assertEqual(neighbor.read_bytes(), b"neighbor must survive")
                self.assertFalse((case / "report.md.bak").exists())
        self.assert_no_staging_files()

    def test_existing_backup_never_clobbered_including_links(self):
        for kind in ("regular", "symlink", "dangling", "hardlink", "directory"):
            with self.subTest(kind=kind):
                case = self.root / kind
                case.mkdir()
                output = case / "report.md"
                output.write_bytes(b"prior review")
                backup = case / "report.md.bak"
                neighbor = case / "neighbor"
                if kind != "dangling":
                    neighbor.write_bytes(b"other private bytes")
                if kind == "regular":
                    backup.write_bytes(b"older review")
                elif kind in ("symlink", "dangling"):
                    backup.symlink_to(neighbor)
                elif kind == "hardlink":
                    os.link(neighbor, backup)
                else:
                    backup.mkdir()
                self.assert_refused(run_importer(IMPORTER, self.export(), output))
                self.assertEqual(output.read_bytes(), b"prior review")
                if kind == "regular":
                    self.assertEqual(backup.read_bytes(), b"older review")
                if kind == "dangling":
                    self.assertFalse(neighbor.exists())
                else:
                    self.assertEqual(neighbor.read_bytes(), b"other private bytes")
        self.assert_no_staging_files()

    def test_symlinked_parent_does_not_redirect_publication(self):
        neighbor = self.root / "other-directory"
        neighbor.mkdir()
        link = self.root / "linked-parent"
        link.symlink_to(neighbor, target_is_directory=True)
        self.assert_refused(run_importer(IMPORTER, self.export(), link / "new" / "report.md"))
        self.assertEqual(list(neighbor.iterdir()), [])

    def test_staging_failure_preserves_prior_report_without_partial_files(self):
        output = self.root / "report.md"
        output.write_bytes(b"prior private review")
        with mock.patch.object(self.mod.os, "fsync", side_effect=OSError("synthetic disk failure")):
            with self.assertRaises(OSError):
                self.mod.write_summary([], output, 30, False)
        self.assertEqual(output.read_bytes(), b"prior private review")
        self.assertFalse(output.with_suffix(".md.bak").exists())
        self.assert_no_staging_files()

    def test_replace_failure_leaves_original_and_private_backup(self):
        output = self.root / "report.md"
        output.write_bytes(b"prior private review")
        output.chmod(0o600)
        with mock.patch.object(self.mod.os, "replace", side_effect=OSError("synthetic rename failure")):
            with self.assertRaises(OSError):
                self.mod.write_summary([], output, 30, False)
        self.assertEqual(output.read_bytes(), b"prior private review")
        backup = output.with_suffix(".md.bak")
        self.assertEqual(backup.read_bytes(), b"prior private review")
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
        self.assert_no_staging_files()

    def test_new_output_race_never_clobbers_new_neighbor(self):
        output = self.root / "report.md"
        real_link = os.link

        def competing_link(source, destination, *args, **kwargs):
            output.write_bytes(b"concurrent report")
            return real_link(source, destination, *args, **kwargs)

        with mock.patch.object(self.mod.os, "link", side_effect=competing_link):
            with self.assertRaises(OSError):
                self.mod.write_summary([], output, 30, False)
        self.assertEqual(output.read_bytes(), b"concurrent report")
        self.assert_no_staging_files()


if __name__ == "__main__":
    unittest.main()
