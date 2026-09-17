"""Importer edge regressions using synthetic values and private scratch layouts."""
import importlib.util
import os
import py_compile
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
IMPORTER = ROOT / "scripts" / "import_chat_history.py"
CANONICAL = ROOT / "scripts" / "secret_patterns.py"
BRAIN = ROOT / "addons" / "full-engine" / "brain" / "brain.py"


class ScannerEdges(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="chat-scanner-edges-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.project = self.root / "project"
        scripts = self.project / "scripts"
        scripts.mkdir(parents=True)
        self.script = scripts / IMPORTER.name
        self.canonical = scripts / CANONICAL.name
        self.brain = self.project / "addons/full-engine/brain/brain.py"
        self.brain.parent.mkdir(parents=True)
        for source, dest in ((IMPORTER, self.script), (CANONICAL, self.canonical),
                             (BRAIN, self.brain)):
            shutil.copy2(source, dest)
        self.source = self.root / "export.txt"
        self.output = self.root / "report.md"

    def run_cli(self, text, output=None):
        self.source.write_text(text, encoding="utf-8")
        return subprocess.run(
            [sys.executable, "-B", str(self.script), "--input", str(self.source),
             "--output", str(output or self.output), "--include-excerpts"],
            capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30)

    def report(self, text, name):
        output = self.root / (name + ".md")
        result = self.run_cli(text, output)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
        return output.read_text(encoding="utf-8")

    def load_importer(self):
        spec = importlib.util.spec_from_file_location("scanner_edges_importer", self.script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_escaped_quotes_remove_all_tail_words_from_written_excerpts(self):
        for quote in ('"', "'"):
            for backslashes in (1, 3):
                with self.subTest(quote=quote, backslashes=backslashes):
                    escaped = "\\" * backslashes + quote
                    value = quote + "violet " + escaped + "meadow" + escaped + " lantern" + quote
                    text = "I want to build " + "password=" + value + " healthy prose survives today."
                    report = self.report(text, "escaped-%s-%s" % (ord(quote), backslashes))
                    for word in ("violet", "meadow", "lantern"):
                        self.assertNotIn(word, report)
                    self.assertIn("[REDACTED_CREDENTIAL] healthy prose survives", report)

    def test_unescaped_and_even_backslash_closings_preserve_following_prose(self):
        for quote in ('"', "'"):
            for backslashes in (0, 2, 4):
                with self.subTest(quote=quote, backslashes=backslashes):
                    value = quote + "violet meadow" + "\\" * backslashes + quote
                    text = "I want to build " + "password=" + value + " healthy prose survives today."
                    report = self.report(text, "closing-%s-%s" % (ord(quote), backslashes))
                    self.assertNotIn("violet", report)
                    self.assertNotIn("meadow", report)
                    self.assertIn("[REDACTED_CREDENTIAL] healthy prose survives", report)

    def test_truncated_escaped_values_stop_at_line_boundary_or_eof(self):
        module = self.load_importer()
        patterns = module.redaction_patterns(module.load_credential_patterns())
        for quote in ('"', "'"):
            for ending in ("", "\nhealthy next line", "\r\nhealthy next line", "\rhealthy next line"):
                with self.subTest(quote=quote, ending=repr(ending)):
                    value = quote + "violet \\" + quote + "meadow\\" + quote + " lantern"
                    text = "before " + "password=" + value + ending
                    self.assertEqual(module.redact(text, patterns),
                                     "before [REDACTED_CREDENTIAL]" + ending)
        report = self.report(
            "I want to build " + 'password="violet \\"meadow\\" lantern\n'
            "I prefer healthy next line material for this project review.\n", "truncated")
        self.assertNotIn("lantern", report)
        self.assertIn("I prefer healthy next line", report)

    def test_multiple_quoted_credentials_preserve_prose_between_and_after(self):
        module = self.load_importer()
        patterns = module.redaction_patterns(module.load_credential_patterns())
        text = ('before ' + 'password="violet \\"meadow\\" lantern"'
                + " healthy bridge " + "token='sunrise \\'river\\' forest'" + " healthy after")
        self.assertEqual(module.redact(text, patterns),
                         "before [REDACTED_CREDENTIAL] healthy bridge [REDACTED_CREDENTIAL] healthy after")
        report = self.report("I want to build " + text, "multiple")
        for word in ("violet", "meadow", "lantern", "sunrise", "river", "forest"):
            self.assertNotIn(word, report)
        self.assertIn("Secret-like values redacted before writing: 2", report)
        self.assertIn("healthy bridge", report)

    def test_quote_handling_does_not_define_new_credential_shapes(self):
        module = self.load_importer()
        patterns = module.redaction_patterns(module.load_credential_patterns())
        text = 'I prefer a "violet \\"meadow\\" lantern" illustration for this project.'
        self.assertEqual(module.redact(text, patterns), text)

    def assert_refusal_preserves_reports(self):
        original = b"original private report\n"
        backup = b"existing private backup\n"
        self.output.write_bytes(original)
        backup_path = self.output.with_suffix(".md.bak")
        backup_path.write_bytes(backup)
        unbacked = self.root / "unbacked.md"
        unbacked.write_bytes(original)
        paths = (self.output, backup_path, unbacked)
        before = tuple(path.stat() for path in paths)
        for output in (self.output, unbacked, self.root / "missing-parent/new.md"):
            result = self.run_cli("I want to build " + "password=" + "violetmeadow in this project.", output)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("REFUSED", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertNotIn("exception-canary", result.stderr + result.stdout)
            self.assertNotIn("Wrote private memory", result.stdout)
        self.assertEqual(self.output.read_bytes(), original)
        self.assertEqual(backup_path.read_bytes(), backup)
        self.assertEqual(unbacked.read_bytes(), original)
        self.assertFalse(unbacked.with_suffix(".md.bak").exists())
        for old, path in zip(before, paths):
            current = path.stat()
            self.assertEqual((old.st_ino, old.st_mtime_ns, old.st_mode),
                             (current.st_ino, current.st_mtime_ns, current.st_mode))
        self.assertFalse((self.root / "missing-parent").exists())
        self.assertEqual(list(self.root.rglob(".chat-report-*")), [])

    def test_mismatched_canonical_exports_refuse_before_publication(self):
        original = CANONICAL.read_text(encoding="utf-8")
        mutations = (
            "SECRET_PATTERNS = SECRET_PATTERNS[:-1]",
            "SECRET_PATTERNS = SECRET_PATTERNS[::-1]",
            "SECRET_PATTERNS = (re.compile(SECRET_PATTERN_SPECS[0][0], re.I),) + SECRET_PATTERNS[1:]",
            "SECRET_PATTERNS = (re.compile(b'bytes-pattern'),)",
            "SECRET_PATTERN_SPECS = ()",
            "SECRET_PATTERN_SPECS = 'malformed'",
            "SECRET_PATTERN_SPECS = (('unterminated[', '[LABEL]'),)",
            "SECRET_PATTERN_SPECS = (('valid-pattern', ''),)",
            "SECRET_PATTERN_SPECS = (('valid-pattern', '[LABEL]', 'extra'),)",
            "del SECRET_PATTERN_SPECS",
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.canonical.write_text(original + "\n" + mutation + "\n", encoding="utf-8")
                self.assert_refusal_preserves_reports()

    def test_scanner_and_brain_import_failures_refuse_without_exception_leaks(self):
        for target in (self.canonical, self.brain):
            original = target.read_bytes()
            for source in ("raise RuntimeError('exception-canary')\n",
                           "raise SystemExit('exception-canary')\n",
                           "invalid syntax exception-canary(\n"):
                with self.subTest(target=target.name, source=source.split("(")[0]):
                    try:
                        target.write_text(source, encoding="utf-8")
                        self.assert_refusal_preserves_reports()
                    finally:
                        target.write_bytes(original)

    def test_current_canonical_source_overrides_timestamp_valid_bytecode(self):
        # Equal size + restored timestamp keeps this old cache valid to a
        # normal import. Source mismatches must still refuse immediately.
        healthy = CANONICAL.read_bytes() + b"\nSECRET_PATTERNS = SECRET_PATTERNS[::1]\n"
        broken = healthy.replace(b"[::1]", b"[:-1]")
        self.canonical.write_bytes(healthy)
        old_stat = self.canonical.stat()
        py_compile.compile(str(self.canonical), doraise=True)
        self.canonical.write_bytes(broken)
        os.utime(self.canonical, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
        self.assert_refusal_preserves_reports()

    def test_current_healthy_source_overrides_broken_cached_scanner(self):
        healthy = CANONICAL.read_bytes() + b"\nSECRET_PATTERNS = SECRET_PATTERNS[::1]\n"
        broken = healthy.replace(b"[::1]", b"[:-1]")
        self.canonical.write_bytes(broken)
        old_stat = self.canonical.stat()
        py_compile.compile(str(self.canonical), doraise=True)
        self.canonical.write_bytes(healthy)
        os.utime(self.canonical, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
        report = self.report("I want to build " + "password=" + "violetmeadow healthy project notes.", "healthy-cache")
        self.assertNotIn("violetmeadow", report)
        self.assertIn("[REDACTED_CREDENTIAL] healthy project notes", report)

    def test_current_brain_source_overrides_timestamp_valid_bytecode(self):
        healthy = BRAIN.read_bytes() + b"\n# cached module has no failure\n"
        broken = BRAIN.read_bytes() + b"\nraise SystemExit('blocked!!!')\n"
        self.assertEqual(len(healthy), len(broken))
        self.brain.write_bytes(healthy)
        old_stat = self.brain.stat()
        py_compile.compile(str(self.brain), doraise=True)
        self.brain.write_bytes(broken)
        os.utime(self.brain, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
        self.assert_refusal_preserves_reports()

    def test_healthy_current_scanner_still_writes_report_and_private_backup(self):
        self.output.write_bytes(b"previous report\n")
        result = self.run_cli("I want to build " + 'password="violet meadow lantern" healthy project notes.')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = self.output.read_text(encoding="utf-8")
        self.assertNotIn("lantern", report)
        self.assertIn("[REDACTED_CREDENTIAL] healthy project notes", report)
        backup = self.output.with_suffix(".md.bak")
        self.assertEqual(backup.read_bytes(), b"previous report\n")
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)

    def test_coherent_canonical_extension_remains_authoritative(self):
        with self.canonical.open("a", encoding="utf-8") as stream:
            stream.write("\nSECRET_PATTERN_SPECS += ((r'zzcanary-[0-9]{8}', '[REDACTED_CANARY]'),)\n"
                         "SECRET_PATTERNS = tuple(re.compile(source) for source, _ in SECRET_PATTERN_SPECS)\n")
        report = self.report("I want to build zzcanary-42424242 healthy project notes.", "extended")
        self.assertNotIn("zzcanary-42424242", report)
        self.assertIn("[REDACTED_CREDENTIAL] healthy project notes", report)

    def test_malformed_brain_patterns_refuse_without_publication(self):
        for patterns in ("None", "()", "(re.compile(b'bytes-pattern'),)"):
            with self.subTest(patterns=patterns):
                self.brain.write_text("import re\nSECRET_SCAN_EXHAUSTIVE = True\nSECRET_PATTERNS = "
                                      + patterns + "\n", encoding="utf-8")
                self.assert_refusal_preserves_reports()


if __name__ == "__main__":
    unittest.main()
