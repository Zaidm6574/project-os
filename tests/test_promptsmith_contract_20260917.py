"""Promptsmith CLI, packet context and index destination regressions.

Runs only relocated shipped scripts in temporary projects. The synthetic brain
records any call so malformed commands must fail before invoking a helper.
"""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from urllib.parse import unquote


REPO = Path(__file__).resolve().parents[1]
TASK = "Build a calm hero with one primary action"
BRIEF = """# Brief

## Palette
- Background: #111113
- Text: #F5F1E8

## Mood
- Calm editorial spacing

## DON'T
- Don't use neon gradients
- Never present placeholder metrics as real results
"""


class PromptsmithContract(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="promptsmith-contract-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "project"
        self.scripts = self.root / "scripts"
        self.scripts.mkdir(parents=True)
        for name in ("promptsmith.py", "bb_lock.py"):
            shutil.copyfile(REPO / "scripts" / name, self.scripts / name)
        self.script = self.scripts / "promptsmith.py"
        self.blackboard = self.root / "blackboard"
        self.blackboard.mkdir()
        self.index = self.blackboard / "05-agent-packets.md"
        self.index.write_text("| ID | Agent | Task | Status | File |\n", encoding="utf-8")
        self.index_before = self.index.read_bytes()
        self.brief = self.root / "brief.md"
        self.brief.write_text(BRIEF, encoding="utf-8")
        self.brain = self.base / "synthetic-brain"
        self.brain.mkdir()
        self.calls = self.brain / "calls.json"
        (self.brain / "brain_query.py").write_text(
            "import json, pathlib, sys\n"
            "pathlib.Path(__file__).with_name('calls.json').write_text(json.dumps(sys.argv[1:]))\n"
            "print('## Palette\\n- Synthetic helper brief')\n", encoding="utf-8")
        self.env = dict(os.environ, BB_LOCK_DIR=str(self.base / "locks"),
                        PROJECT_OS_BRAIN_DIR=str(self.brain),
                        PYTHONDONTWRITEBYTECODE="1")

    def run_cli(self, args, cwd=None):
        return subprocess.run([sys.executable, "-B", str(self.script)] + args,
                              cwd=cwd or self.root, env=self.env,
                              text=True, capture_output=True, timeout=30)

    def compile(self, *extra, pid="contract"):
        result = self.run_cli(["--task", TASK, "--brief-file", str(self.brief),
                               "--packet-id", pid] + list(extra))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.calls.exists(), "--brief-file must bypass the brain helper")
        return json.loads(result.stdout)

    def indexed_worker(self):
        added = self.index.read_bytes()[len(self.index_before):].decode("utf-8")
        self.assertEqual(len(added.splitlines()), 1, added)
        cells = added.strip().split("|")
        self.assertEqual(len(cells), 7, added)
        self.assertEqual(cells[4].strip(), "Draft")
        return (self.index.parent / unquote(cells[5].strip())).resolve()

    def assert_refused_untouched(self, args):
        before = {str(p.relative_to(self.base)): p.read_bytes()
                  for p in self.base.rglob("*") if p.is_file()}
        entries = {str(p.relative_to(self.base)) for p in self.base.rglob("*")}
        result = self.run_cli(args)
        # Inspect side effects even when an old implementation returns success.
        self.assertFalse(self.calls.exists(), "invalid CLI invoked the brain helper")
        self.assertEqual(self.index.read_bytes(), self.index_before)
        self.assertEqual(entries, {str(p.relative_to(self.base)) for p in self.base.rglob("*")})
        self.assertEqual(before, {str(p.relative_to(self.base)): p.read_bytes()
                                 for p in self.base.rglob("*") if p.is_file()})
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("usage error", result.stderr.lower())
        self.assertNotIn("Traceback", result.stderr)
        return result

    def assert_blank_brief_refused(self, content, no_index=False):
        self.brief.write_text(content, encoding="utf-8")
        args = ["--task", TASK, "--brief-file", str(self.brief),
                "--out-dir", "new-run/nested/packets", "--packet-id", "blank"]
        if no_index:
            args.append("--no-index")
        result = self.assert_refused_untouched(args)
        self.assertIn("--brief-file", result.stderr)
        self.assertIn("empty", result.stderr.lower())
        self.assertEqual(result.stdout, "")

    def test_empty_brief_file_rejected_before_helpers_or_writes(self):
        self.assert_blank_brief_refused("")

    def test_empty_brief_file_no_index_rejected_without_creating_output(self):
        self.assert_blank_brief_refused("", no_index=True)

    def test_whitespace_brief_file_rejected_before_helpers_or_writes(self):
        self.assert_blank_brief_refused(" \t\n\r\n\u00a0\u2003 ")

    def test_whitespace_brief_file_no_index_rejected_without_creating_output(self):
        self.assert_blank_brief_refused(" \t\n\r\n\u00a0\u2003 ", no_index=True)

    def test_missing_brief_file_still_refused_without_helpers_or_writes(self):
        missing = self.root / "missing-brief.md"
        for flags in ([], ["--no-index"]):
            with self.subTest(flags=flags):
                result = self.assert_refused_untouched([
                    "--task", TASK, "--brief-file", str(missing),
                    "--out-dir", "new-run/nested/packets"] + flags)
                self.assertIn("could not read --brief-file", result.stderr)
                self.assertEqual(result.stdout, "")

    def test_substantive_brief_with_surrounding_whitespace_stays_available(self):
        self.brief.write_text(" \t\n" + BRIEF + "\n\u00a0\u2003 ", encoding="utf-8")
        for no_index in (False, True):
            with self.subTest(no_index=no_index):
                index_before = self.index.read_bytes()
                flags = ["--no-index"] if no_index else []
                data = self.compile(*flags, pid="healthy-" + str(no_index).lower())
                self.assertTrue(data["brain_available"])
                self.assertEqual(data["brief_source"], "pre-fetched: " + str(self.brief))
                self.assertEqual(data["donts_extracted"], 2)
                for kind in ("worker_prompt", "rubric"):
                    text = Path(data[kind]).read_text(encoding="utf-8")
                    self.assertIn(BRIEF.strip(), text)
                    self.assertIn(TASK, text)
                    self.assertNotIn("BRAIN-UNAVAILABLE", text)
                if no_index:
                    self.assertEqual(self.index.read_bytes(), index_before)
                else:
                    self.assertEqual(self.indexed_worker(), Path(data["worker_prompt"]))

    def test_empty_optional_helper_output_remains_marked_unavailable(self):
        (self.brain / "brain_query.py").write_text(
            "import pathlib\n"
            "pathlib.Path(__file__).with_name('calls.json').write_text('called')\n"
            "print(' \\t\\n')\n", encoding="utf-8")
        result = self.run_cli(["--task", TASK, "--packet-id", "empty-helper", "--no-index"])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertTrue(self.calls.exists())
        data = json.loads(result.stdout)
        self.assertFalse(data["brain_available"])
        self.assertEqual(data["brief_source"], "unavailable")
        self.assertEqual(data["donts_extracted"], 0)
        for kind in ("worker_prompt", "rubric"):
            text = Path(data[kind]).read_text(encoding="utf-8")
            self.assertIn(TASK, text)
            self.assertIn("BRAIN-UNAVAILABLE", text)
            self.assertIn("empty output", text)
        self.assertEqual(self.index.read_bytes(), self.index_before)

    def test_default_index_path_still_resolves(self):
        data = self.compile()
        self.assertEqual(self.indexed_worker(), Path(data["worker_prompt"]).resolve())
        self.assertIn("packets/contract-worker-prompt.md", self.index.read_text())

    def test_relative_custom_output_is_relative_to_index(self):
        self.compile("--out-dir", "runs/demo/packets")
        self.assertEqual(self.indexed_worker(), self.root / "runs/demo/packets/contract-worker-prompt.md")
        self.assertTrue(self.indexed_worker().is_file())

    def test_external_output_is_relative_to_index(self):
        out = self.base / "external-packets"
        data = self.compile("--out-dir", str(out))
        self.assertEqual(self.indexed_worker(), Path(data["worker_prompt"]))
        self.assertTrue(self.indexed_worker().is_file())
        self.assertFalse((self.blackboard / "packets").exists())

    def test_custom_output_from_another_working_directory(self):
        cwd = self.base / "caller"
        cwd.mkdir()
        result = self.run_cli(["--task", TASK, "--brief-file", str(self.brief),
                               "--packet-id", "caller", "--out-dir", "packets"], cwd=cwd)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.indexed_worker(), cwd / "packets/caller-worker-prompt.md")
        self.assertTrue(self.indexed_worker().is_file())

    def test_index_path_escapes_table_delimiters_without_losing_destination(self):
        out = self.base / "two  spaces|percent%23\nline"
        data = self.compile("--out-dir", str(out))
        self.assertEqual(self.indexed_worker(), Path(data["worker_prompt"]))
        self.assertTrue(self.indexed_worker().is_file())

    def test_no_index_does_not_import_index_helper(self):
        (self.scripts / "bb_lock.py").write_text("raise AssertionError('index helper called')\n")
        data = self.compile("--out-dir", str(self.base / "standalone"), "--no-index")
        self.assertEqual(self.index.read_bytes(), self.index_before)
        self.assertTrue(Path(data["worker_prompt"]).is_file())
        self.assertTrue(Path(data["rubric"]).is_file())

    def test_sample_command_runs_without_index_mutation(self):
        examples = self.root / "examples"
        examples.mkdir()
        sample = examples / "sample-brief.md"
        shutil.copyfile(REPO / "examples/sample-brief.md", sample)
        match = re.search(r"```bash\n(.*?)\n```", sample.read_text(encoding="utf-8"), re.S)
        self.assertIsNotNone(match)
        command = shlex.split(match.group(1).replace("\\\n", ""))
        self.assertEqual(command[:2], ["python3", "scripts/promptsmith.py"])
        result = self.run_cli(command[2:])
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertTrue((self.root / data["worker_prompt"]).is_file())
        self.assertTrue((self.root / data["rubric"]).is_file())
        self.assertEqual(self.index.read_bytes(), self.index_before)
        self.assertFalse(self.calls.exists())

    def test_rubric_contains_exact_task_and_complete_brief(self):
        data = self.compile("--no-index")
        for kind in ("worker_prompt", "rubric"):
            text = Path(data[kind]).read_text(encoding="utf-8")
            self.assertIn(TASK, text)
            self.assertIn(BRIEF.strip(), text)
        self.assertEqual(data["donts_extracted"], 2)
        rubric = Path(data["rubric"]).read_text(encoding="utf-8")
        self.assertIn("- [ ] Don't use neon gradients", rubric)
        self.assertIn("- [ ] Never present placeholder metrics as real results", rubric)

    def test_unavailable_brain_marks_rubric_and_keeps_task(self):
        (self.brain / "brain_query.py").unlink()
        result = self.run_cli(["--task", TASK, "--packet-id", "unavailable", "--no-index"])
        self.assertEqual(result.returncode, 1, result.stderr)
        data = json.loads(result.stdout)
        self.assertFalse(data["brain_available"])
        rubric = Path(data["rubric"]).read_text(encoding="utf-8")
        self.assertIn(TASK, rubric)
        self.assertIn("BRAIN-UNAVAILABLE", rubric)
        self.assertIn("Do NOT invent taste", rubric)
        self.assertEqual(self.index.read_bytes(), self.index_before)

    def test_unknown_flag_rejected_before_helper_or_writes(self):
        self.assert_refused_untouched(["--task", TASK, "--no-inde"])

    def test_abbreviated_flag_is_not_accepted(self):
        self.assert_refused_untouched(["--task", TASK, "--no-ind"])

    def test_unexpected_positional_argument_is_rejected(self):
        self.assert_refused_untouched(["--task", TASK, "extra"])

    def test_missing_value_cannot_consume_another_option(self):
        self.assert_refused_untouched(["--task", TASK, "--query", "--no-index"])

    def test_duplicate_options_are_rejected(self):
        values = {"--task": TASK, "--query": "hero", "--packet-id": "sample",
                  "--out-dir": "packets", "--brief-file": str(self.brief)}
        for flag, value in values.items():
            with self.subTest(flag=flag):
                args = ["--task", TASK] if flag != "--task" else []
                self.assert_refused_untouched(args + [flag, value, flag, value])
        self.assert_refused_untouched(["--task", TASK, "--no-index", "--no-index"])

    def test_empty_values_are_rejected(self):
        for flag in ("--task", "--query", "--packet-id", "--out-dir", "--brief-file"):
            for value in ("", " \t"):
                with self.subTest(flag=flag, value=value):
                    args = ["--task", TASK] if flag != "--task" else []
                    self.assert_refused_untouched(args + [flag, value])

    def test_invalid_options_precede_brief_reads(self):
        spec = importlib.util.spec_from_file_location("promptsmith_contract", self.script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        argv = [str(self.script), "--task", TASK, "--brief-file", str(self.brief), "--no-inde"]
        with mock.patch.object(sys, "argv", argv), \
                mock.patch("builtins.open", side_effect=AssertionError("unexpected brief read")), \
                mock.patch.object(module, "fetch_brief", side_effect=AssertionError("unexpected helper")), \
                contextlib.redirect_stderr(io.StringIO()), \
                self.assertRaises(SystemExit) as exited:
            module.main()
        self.assertEqual(exited.exception.code, 2)
        self.assertEqual(self.index.read_bytes(), self.index_before)

    def test_all_documented_options_accept_order_independent_values(self):
        result = self.run_cli(["--no-index", "--query", "landing hero", "--out-dir", "custom",
                               "--packet-id", "ordered", "--task", TASK])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.calls.read_text()), ["brief", "landing hero"])
        self.assertTrue((self.root / "custom/ordered-worker-prompt.md").is_file())
        self.assertEqual(self.index.read_bytes(), self.index_before)

    def test_duplicate_packet_refuses_without_changes(self):
        self.compile()
        before = {str(p.relative_to(self.root)): p.read_bytes()
                  for p in self.root.rglob("*") if p.is_file()}
        result = self.run_cli(["--task", TASK, "--brief-file", str(self.brief),
                               "--packet-id", "contract"])
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("refusing to overwrite", result.stderr)
        self.assertEqual(before, {str(p.relative_to(self.root)): p.read_bytes()
                                 for p in self.root.rglob("*") if p.is_file()})


if __name__ == "__main__":
    unittest.main()
