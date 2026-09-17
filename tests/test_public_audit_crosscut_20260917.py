"""Observable crosscut contracts: index payload, packet rows, gauge diagnosis."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def load(name):
    spec = importlib.util.spec_from_file_location("crosscut_" + name,
                                                SCRIPTS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class IndexPayloadTests(unittest.TestCase):
    def setUp(self):
        if not shutil.which("git"):
            self.skipTest("Git is required for real index fixtures")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.scan_module = load("prepublish_check")
        self.patterns = load("secret_patterns").SECRET_PATTERNS
        self.git("init", "-q")
        # A real separate Git index, never the repair clone's index.
        self.environment = mock.patch.dict(os.environ, {
            "GIT_INDEX_FILE": str(self.repo / ".git" / "candidate-index"),
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.candidate = self.repo / "candidate.md"
        self.synthetic = "figd_" + "A" * 30

    def git(self, *args):
        result = subprocess.run(["git", "-C", str(self.repo), *args],
                                capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, "Git fixture command failed")
        return result.stdout

    def scan(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "argv", ["prepublish_check.py", *map(str, args)]), \
                mock.patch.object(self.scan_module, "load_patterns", return_value=self.patterns), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = self.scan_module.main()
        self.assertTrue(self.synthetic not in out.getvalue() + err.getvalue(),
                        "scanner exposed the synthetic matched value")
        return code, out.getvalue(), err.getvalue()

    def stage(self, text):
        self.candidate.write_text(text + "\n", encoding="utf-8")
        self.git("add", "--", self.candidate.name)

    def test_staged_shape_is_detected_after_working_copy_is_clean(self):
        self.stage(self.synthetic)
        self.candidate.write_text("ordinary public text\n")
        self.assertTrue(self.synthetic.encode() in self.git("show", ":candidate.md"))
        code, out, _ = self.scan("--tracked", self.repo)
        self.assertEqual(code, 1)
        self.assertIn("candidate.md:1", out)
        self.assertIn("1 file(s) selected, 1 scanned from Git index", out)
        self.assertEqual(self.scan("--tracked", "--working-tree", self.repo)[0], 0)

    def test_clean_index_is_independent_of_dirty_working_copy(self):
        self.stage("ordinary public text")
        self.candidate.write_text(self.synthetic + "\n")
        self.assertEqual(self.scan("--tracked", self.repo)[0], 0)
        self.assertEqual(self.scan("--tracked", "--working-tree", self.repo)[0], 1)

    def test_local_replace_refs_cannot_hide_the_staged_blob(self):
        self.stage(self.synthetic)
        original = self.git("rev-parse", ":candidate.md").decode().strip()
        self.candidate.write_text("ordinary public text\n")
        substitute = self.git("hash-object", "-w", "candidate.md").decode().strip()
        self.git("replace", original, substitute)
        self.assertEqual(self.git("cat-file", "blob", original), b"ordinary public text\n")
        self.assertEqual(self.scan("--tracked", self.repo)[0], 1)

    def test_locally_removed_staged_file_still_gets_scanned(self):
        self.stage(self.synthetic)
        self.candidate.unlink()
        self.assertEqual(self.scan("--tracked", self.repo)[0], 1)
        self.assertEqual(self.scan("--tracked", self.candidate)[0], 1)
        code, out, _ = self.scan("--tracked", "--working-tree", self.repo)
        self.assertEqual(code, 1)
        self.assertIn("NOT SCANNED", out)

    def test_single_file_scan_and_empty_selection_never_claim_zero_file_clean(self):
        self.candidate.write_text(self.synthetic + "\n")
        code, out, _ = self.scan(self.candidate)
        self.assertEqual(code, 1)
        self.assertIn("candidate.md:1", out)
        self.candidate.write_text("ordinary public text\n")
        self.assertEqual(self.scan(self.candidate)[0], 0)
        self.candidate.unlink()
        code, out, err = self.scan(self.repo)
        self.assertEqual(code, 2)
        self.assertNotIn("clean", out)
        self.assertIn("0 files scanned", err)

    def test_nested_directory_and_literal_file_selection(self):
        self.stage(self.synthetic)
        nested = self.repo / "nested"
        nested.mkdir()
        selected = nested / "[example].md"
        selected.write_text("ordinary public text\n")
        self.git("add", "--", "nested")
        self.assertEqual(self.scan("--tracked", nested)[0], 0)
        self.assertEqual(self.scan("--tracked", selected)[0], 0)
        selected.write_text(self.synthetic + "\n")
        self.git("add", "--", "nested")
        self.assertEqual(self.scan("--tracked", selected)[0], 1)

    def test_explicit_binary_suffix_refused_and_unmerged_index_refused(self):
        binary = self.repo / "candidate.png"
        binary.write_text(self.synthetic + "\n")
        self.assertEqual(self.scan(binary)[0], 2)
        self.stage("ordinary public text")
        oid = self.git("rev-parse", ":candidate.md").decode().strip()
        self.git("update-index", "--force-remove", "candidate.md")
        data = "100644 %s 1\tcandidate.md\n100644 %s 2\tcandidate.md\n" % (oid, oid)
        result = subprocess.run(["git", "-C", str(self.repo), "update-index", "--index-info"],
                                input=data, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        code, out, err = self.scan("--tracked", self.repo)
        self.assertEqual(code, 2)
        self.assertIn("unresolved", err)
        self.assertNotIn("clean", out)

    def test_index_reads_symlink_blob_not_external_working_target(self):
        self.stage("ordinary public text")
        self.candidate.unlink()
        outside = self.repo / "outside.txt"
        outside.write_text(self.synthetic + "\n")
        self.candidate.symlink_to(outside)
        # The staged regular file is still safe; local symlink changes do not
        # redirect index reads to another artifact.
        self.assertEqual(self.scan("--tracked", self.repo)[0], 0)
        self.git("add", "--", "candidate.md")
        self.assertEqual(self.scan("--tracked", self.repo)[0], 0)
        self.candidate.unlink()
        directory = self.repo / "linked-directory"
        directory.mkdir()
        self.candidate.symlink_to(directory, target_is_directory=True)
        self.git("add", "--", "candidate.md")
        self.assertEqual(self.scan("--tracked", self.candidate)[0], 0)

    def test_unreadable_bytes_fail_and_matching_filename_is_redacted(self):
        self.candidate.write_bytes(b"\xff\xfe")
        self.git("add", "--", "candidate.md")
        code, out, _ = self.scan("--tracked", self.repo)
        self.assertEqual(code, 1)
        self.assertIn("NOT SCANNED", out)
        self.assertIn("1 file(s) selected, 0 scanned from Git index", out)
        self.candidate.unlink()
        named = self.repo / (self.synthetic + ".md")
        named.write_text(self.synthetic + "\n")
        code, out, _ = self.scan(named)
        self.assertEqual(code, 1)
        self.assertIn("[REDACTED].md:1", out)


class PacketIndexTests(unittest.TestCase):
    def test_unsafe_ids_are_refused_before_any_write_and_safe_row_is_draft(self):
        module = load("promptsmith")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bb = root / "blackboard"
            bb.mkdir()
            index = bb / "05-agent-packets.md"
            before = "| ID | Agent | Goal | Status | File |\n|---|---|---|---|---|\n"
            index.write_text(before)
            brief = root / "brief.md"
            brief.write_text("## DON'T\n- avoid neon\n")
            packets = bb / "packets"
            task = "Task | forged\n\r\tApproved"

            def compile_packet(pid):
                argv = ["promptsmith.py", "--task", task, "--packet-id", pid,
                        "--brief-file", str(brief), "--out-dir", str(packets)]
                with mock.patch.object(module, "ROOT", str(root)), \
                        mock.patch.object(module, "fetch_brief", side_effect=AssertionError("unexpected brain call")), \
                        mock.patch.object(sys, "argv", argv), \
                        contextlib.redirect_stdout(io.StringIO()), \
                        contextlib.redirect_stderr(io.StringIO()), \
                        self.assertRaises(SystemExit) as exited:
                    module.main()
                return exited.exception.code

            for pid in ("p | actor | goal | Approved | forged |\n| row", "x\ry", "x\ty",
                        "", "-leading", "dot.name", "é", "x" * 121):
                with self.subTest(case=repr(pid)):
                    self.assertEqual(compile_packet(pid), 2)
                    self.assertEqual(index.read_text(), before)
                    self.assertFalse(packets.exists())
            self.assertEqual(compile_packet("Safe_packet-17"), 0)
            self.assertEqual(index.read_text()[len(before):],
                             "| Safe_packet-17 | promptsmith | Task / forged Approved | Draft | "
                             "packets/Safe_packet-17-worker-prompt.md |\n")
            self.assertEqual(len(list(packets.glob("*.md"))), 2)


class NightlyGaugeTests(unittest.TestCase):
    def test_gauge_failures_and_capacity_severity_produce_distinct_advice(self):
        module = load("os_nightly")
        cases = [
            (("OVERALL: OK", 0), None, None),
            (("OVERALL: WATCH", 1), None, "WATCH"),
            (("OVERALL: CUTOVER", 2), None, "CUTOVER"),
            (("missing", 3), None, "Shared brain missing"),
            (("failed", 7), None, "crashed"),
            (("brain_scale CRASHED (exit 2): fixture", None), None, "failed"),
            (None, subprocess.TimeoutExpired(["fixture-gauge"], 1), "failed"),
            (None, OSError("fixture failure"), "failed"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            for value, error, expected in cases:
                with self.subTest(expected=expected, error=type(error).__name__):
                    notices, entries = [], []
                    with mock.patch.object(module, "LOG", str(Path(tmp) / "log.md")), \
                            mock.patch.object(module, "run_gauge", return_value=value, side_effect=error), \
                            mock.patch.object(module, "reap_locks", return_value=0), \
                            mock.patch.object(module, "stale_packets", return_value=[]), \
                            mock.patch.object(module, "stuck_plans", return_value=[]), \
                            mock.patch.object(module, "write_entry", side_effect=lambda rows: entries.extend(rows) or True), \
                            mock.patch.object(module, "notify", side_effect=notices.append), \
                            mock.patch.dict(sys.modules, {"harvest": types.SimpleNamespace(unharvested=lambda: [])}), \
                            contextlib.redirect_stdout(io.StringIO()), \
                            self.assertRaises(SystemExit) as exited:
                        module.main()
                    self.assertEqual(exited.exception.code, 0)
                    if expected is None:
                        self.assertEqual(notices, [])
                    else:
                        self.assertEqual(len(notices), 1)
                        self.assertIn(expected, notices[0])
                    if error is not None or (value and value[1] is None):
                        if error is not None:
                            self.assertTrue(any("brain_scale FAILED" in row for row in entries))
                        self.assertNotIn("CUTOVER", notices[0])
                        self.assertNotIn("brain_archive", notices[0])
                    if expected == "CUTOVER":
                        self.assertIn("brain_archive.py", notices[0])
