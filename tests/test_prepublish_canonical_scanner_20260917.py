"""A prepublication pass requires this installation's complete canonical list."""
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


class CanonicalScannerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.install = self.work / "installation"
        self.scripts = self.install / "scripts"
        self.scripts.mkdir(parents=True)
        for name in ("prepublish_check.py", "secret_patterns.py", "brain_paths.py", "bb_lock.py"):
            shutil.copyfile(ROOT / "scripts" / name, self.scripts / name)
        # Keep the old import route present: losing the canonical module must
        # not turn its portable compatibility list into a publication pass.
        brain_dir = self.install / "addons" / "full-engine" / "brain"
        brain_dir.mkdir(parents=True)
        shutil.copyfile(ROOT / "addons" / "full-engine" / "brain" / "brain.py",
                        brain_dir / "brain.py")
        self.check = self.scripts / "prepublish_check.py"
        self.canonical = self.scripts / "secret_patterns.py"
        self.original = self.canonical.read_text(encoding="utf-8")
        self.target = self.work / "candidate.md"
        self.synthetic = "AIza" + "A" * 24
        self.target.write_text(self.synthetic + "\n", encoding="utf-8")
        self.env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")

    def scan(self, *args, env=None):
        result = subprocess.run([sys.executable, str(self.check),
                                 *map(str, args or (self.target,))],
                                cwd=self.work, env=env or self.env,
                                capture_output=True, text=True)
        self.assertTrue(self.synthetic not in result.stdout + result.stderr,
                        "scanner exposed a synthetic credential value")
        return result

    def assert_refused(self, result):
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("canonical denylist", result.stderr)
        self.assertNotIn("clean", result.stdout)
        self.assertNotIn("Traceback", result.stderr)

    def snapshot(self):
        return {str(path.relative_to(self.work)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in self.work.rglob("*") if path.is_file()}

    def test_healthy_scan_detects_shape_accepts_near_miss_and_does_not_write(self):
        before = self.snapshot()
        found = self.scan()
        self.assertEqual(found.returncode, 1, found.stdout + found.stderr)
        self.assertIn("candidate.md:1", found.stdout)
        self.assertEqual(self.snapshot(), before)
        self.target.write_text("AIza" + "A" * 19 + "\n", encoding="utf-8")
        before = self.snapshot()
        clean = self.scan()
        self.assertEqual(clean.returncode, 0, clean.stdout + clean.stderr)
        self.assertIn("1 file(s) selected, 1 scanned", clean.stdout)
        self.assertEqual(self.snapshot(), before)

    def test_missing_canonical_refuses_instead_of_using_portable_brain_patterns(self):
        self.canonical.unlink()
        before = self.snapshot()
        self.assert_refused(self.scan())
        self.assert_refused(self.scan("--list"))
        self.assertEqual(self.snapshot(), before)

    def test_ancestor_canonical_cannot_rescue_a_damaged_installation(self):
        ancestor_scripts = self.work / "scripts"
        ancestor_scripts.mkdir()
        shutil.copyfile(self.canonical, ancestor_scripts / "secret_patterns.py")
        self.canonical.unlink()
        self.assert_refused(self.scan())

    def test_symlinked_canonical_is_not_installation_local(self):
        outside = self.work / "external-scanner.py"
        self.canonical.rename(outside)
        self.canonical.symlink_to(outside)
        self.assert_refused(self.scan())

    def test_invalid_module_failure_does_not_echo_exception_or_source_values(self):
        for source in ("raise RuntimeError(%r)\n" % self.synthetic,
                       "this is not valid Python " + self.synthetic + "\n",
                       "raise SystemExit(%r)\n" % self.synthetic):
            with self.subTest(kind=source.split()[0]):
                self.canonical.write_text(source, encoding="utf-8")
                before = self.snapshot()
                self.assert_refused(self.scan())
                self.assertEqual(self.snapshot(), before)

    def test_empty_missing_or_invalid_pattern_exports_refuse_clean_claim(self):
        self.target.write_text("ordinary public note\n", encoding="utf-8")
        mutations = (
            "del SECRET_PATTERNS",
            "SECRET_PATTERNS = ()",
            "SECRET_PATTERNS = [object()]",
            "SECRET_PATTERNS = [re.compile(b'bytes-only')]",
            "SECRET_PATTERNS = SECRET_PATTERNS[:-1]",
            "SECRET_PATTERNS = tuple(re.compile(p.pattern, re.ASCII) for p in SECRET_PATTERNS)",
            "del SECRET_PATTERN_SPECS",
            "SECRET_PATTERN_SPECS = ()",
            "SECRET_PATTERN_SPECS = [('broken',)]",
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.canonical.write_text(self.original + "\n" + mutation + "\n",
                                          encoding="utf-8")
                self.assert_refused(self.scan())

    def test_import_path_and_cached_modules_cannot_replace_local_canonical_list(self):
        spec = importlib.util.spec_from_file_location("scanner_under_test", self.check)
        scanner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(scanner)
        decoy = types.ModuleType("secret_patterns")
        decoy.SECRET_PATTERNS = [re.compile("impossible-fixture-only")]
        decoy.SECRET_PATTERN_SPECS = (("impossible-fixture-only", "[REDACTED]"),)
        decoy_brain = types.ModuleType("brain")
        decoy_brain.SECRET_PATTERNS = decoy.SECRET_PATTERNS
        old_path = list(sys.path)
        with mock.patch.dict(sys.modules, {"secret_patterns": decoy, "brain": decoy_brain}):
            patterns = scanner.load_patterns()
        self.assertTrue(any(pattern.search(self.synthetic) for pattern in patterns),
                        "an already-imported foreign scanner weakened local coverage")
        self.assertEqual(sys.path, old_path)

    def test_core_install_does_not_require_optional_brain_addon(self):
        shutil.rmtree(self.install / "addons")
        self.target.write_text("ordinary public note\n", encoding="utf-8")
        before = self.snapshot()
        result = self.scan()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_local_canonical_extensions_are_scanned_and_listed(self):
        self.canonical.write_text(self.original + "\n"
                                 "SECRET_PATTERN_SPECS += ((r'fixture-extra-[0-9]{4}', '[REDACTED]'),)\n"
                                 "SECRET_PATTERNS += (re.compile(SECRET_PATTERN_SPECS[-1][0]),)\n",
                                 encoding="utf-8")
        self.target.write_text("fixture-extra-1234\n", encoding="utf-8")
        result = self.scan()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertNotIn("fixture-extra-1234", result.stdout + result.stderr)
        listed = self.scan("--list")
        self.assertEqual(listed.returncode, 0, listed.stdout + listed.stderr)
        self.assertIn("fixture-extra-[0-9]{4}", listed.stdout.splitlines())


if __name__ == "__main__":
    unittest.main()
