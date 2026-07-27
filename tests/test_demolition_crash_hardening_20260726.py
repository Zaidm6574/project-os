#!/usr/bin/env python3
"""Ordinary input must produce a refusal, never a traceback (audit 2026-07-26).

Five confirmed defects, each reproduced by driving the REAL shipped CLI with the
exact bad input a first-time user types:

  1. `scripts/project-os.gitignore` -- the curated list the installer merges into
     a user's project -- did not cover `code-graph.json`, which the shipped
     `memory/code_graph.py build` writes into the project root by DEFAULT. Not a
     data leak (the graph is derived from the user's own source, unlike
     `mneme_index.json`), but generated build noise dropped into a stranger's
     `git status`.
  2. `python3 scripts/os_nightly.py` -- the README's daily heartbeat -- died with
     an unhandled FileNotFoundError on a fresh clone, because `blackboard/` is
     created by the installer and does not exist yet.
  3. `scripts/promptsmith.py` run the way the README spells it (`--brief-file
     examples/sample-brief.md`, no `--task`) exited 2 with an EMPTY stderr after
     dumping its docstring to stdout: nothing said what was wrong.
  4. promptsmith and evolution both indexed `args[i + 1]` for a flag's value, so
     a bare trailing flag raised IndexError.
  5. `brain.py export --from` called `.get()` on every record, so a JSONL line
     that is valid JSON but not an object (a bare string, number or list) raised
     AttributeError.

Every test also carries its NEAR-MISS control: the ordinary happy path of the
same tool, run in the same sandbox, must still work -- a fix that refuses real
usage is its own defect. Nothing here touches the live blackboard/, runs/,
memory/ or brain trees: each case copies the shipped modules into a tempdir,
which is where they derive ROOT from.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CURATED_IGNORE = os.path.join(REPO, "scripts", "project-os.gitignore")
CODE_GRAPH_PY = os.path.join(REPO, "memory", "code_graph.py")
BRAIN_PY = os.path.join(REPO, "addons", "full-engine", "brain", "brain.py")


def _string_constant(module_path, name):
    """Read `NAME = "literal"` out of a module without importing it."""
    with open(module_path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    value = node.value
                    # ast.Constant spans 3.9..3.14; ast.Str was removed in 3.12.
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        return value.value
    return None


def _no_traceback(testcase, proc, what):
    testcase.assertNotIn(
        "Traceback (most recent call last)", proc.stderr,
        "%s printed a raw traceback instead of a refusal:\n%s" % (what, proc.stderr))


# --------------------------------------------------------------------------
# 1. the curated installer ignore list
# --------------------------------------------------------------------------

class CuratedIgnoreListCoversTheCodeGraph(unittest.TestCase):
    """The graph path is read out of code_graph.py, not restated here."""

    def _check_ignore(self, relpath):
        """True if git, using ONLY the curated list, ignores relpath."""
        d = tempfile.mkdtemp(prefix="curated-ignore-")
        self.addCleanup(shutil.rmtree, d, True)
        subprocess.run(["git", "init", "-q", "."], cwd=d, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        shutil.copy(CURATED_IGNORE, os.path.join(d, ".gitignore"))
        full = os.path.join(d, relpath)
        parent = os.path.dirname(full)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write("{}")
        done = subprocess.run(["git", "check-ignore", "-q", relpath], cwd=d)
        return done.returncode == 0

    def test_the_shipped_default_graph_output_is_ignored(self):
        default_out = _string_constant(CODE_GRAPH_PY, "DEFAULT_OUT")
        self.assertEqual(
            default_out, "code-graph.json",
            "memory/code_graph.py's DEFAULT_OUT moved; this guard is stale")
        for path in (default_out, os.path.join("nested", "sub", default_out)):
            with self.subTest(path=path):
                self.assertTrue(
                    self._check_ignore(path),
                    "%s is what `python3 memory/code_graph.py build` writes by "
                    "default; the curated list would let it be committed into a "
                    "user's project" % path)

    def test_the_brain_archive_siblings_are_ignored_too(self):
        """Same sweep: scripts/brain_archive.py's outputs hold brain text.

        `**/shared-brain.jsonl` covered the live file only, so the archive it
        moves records INTO and the `.pre-archive-<stamp>` copy it leaves behind
        were both committable in a user's project.
        """
        for path in (os.path.join("brain", "shared-brain-archive.jsonl"),
                     os.path.join("brain",
                                  "shared-brain.jsonl.pre-archive-20260726-010101")):
            with self.subTest(path=path):
                self.assertTrue(
                    self._check_ignore(path),
                    "%s carries the same lesson text as shared-brain.jsonl "
                    "and is NOT ignored by the curated list" % path)

    def test_a_users_own_files_are_still_tracked(self):
        """Near-miss control: the new rule must not over-match real work."""
        for path in ("code-graph.md",
                     os.path.join("docs", "code-graph-notes.json"),
                     os.path.join("app", "graph.json"),
                     os.path.join("docs", "shared-brain-notes.md"),
                     os.path.join("brain", "README.md"),
                     os.path.join("memory", "my_own_notes.json")):
            with self.subTest(path=path):
                self.assertFalse(
                    self._check_ignore(path),
                    "%s is a user's own file and must stay trackable" % path)


# --------------------------------------------------------------------------
# 2. os_nightly on a fresh clone
# --------------------------------------------------------------------------

class NightlyRefusesAnUninitializedWorkspace(unittest.TestCase):
    """os_nightly derives ROOT from its own path, so a copied scripts/ dir is
    an exact stand-in for a fresh clone that has not been installed yet."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="os-nightly-fresh-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.proj = os.path.join(self.tmp, "proj")
        os.makedirs(self.proj)
        shutil.copytree(os.path.join(REPO, "scripts"),
                        os.path.join(self.proj, "scripts"))
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self.log = os.path.join(self.proj, "blackboard", "22-automation-log.md")
        self.env = dict(os.environ)
        self.env["HOME"] = self.home
        self.env["BB_LOCK_DIR"] = os.path.join(self.home, "locks")

    def _run(self):
        return subprocess.run(
            [sys.executable, os.path.join(self.proj, "scripts", "os_nightly.py")],
            cwd=self.proj, env=self.env, capture_output=True, text=True, timeout=180)

    def test_missing_blackboard_is_a_clean_actionable_refusal(self):
        proc = self._run()
        _no_traceback(self, proc, "os_nightly on a fresh clone")
        self.assertNotEqual(proc.returncode, 0,
                            "os_nightly reported success with no blackboard/")
        self.assertIn("blackboard", proc.stderr,
                      "the refusal must name the missing directory:\n" + proc.stderr)
        self.assertIn("install.sh", proc.stderr,
                      "the refusal must say how to fix it:\n" + proc.stderr)

    def test_an_installed_workspace_still_gets_its_heartbeat(self):
        """Near-miss control: with blackboard/ present the nightly still runs."""
        shutil.copytree(os.path.join(REPO, "blackboard-template"),
                        os.path.join(self.proj, "blackboard"))
        proc = self._run()
        _no_traceback(self, proc, "os_nightly on an installed workspace")
        self.assertNotIn("not initialized", proc.stderr,
                         "the guard refused a REAL workspace:\n" + proc.stderr)
        self.assertTrue(os.path.exists(self.log),
                        "the heartbeat did not write the automation log:\n"
                        + proc.stdout + proc.stderr)
        with open(self.log, encoding="utf-8") as f:
            self.assertIn("gauge:", f.read())


# --------------------------------------------------------------------------
# 3 + 4. promptsmith usage errors
# --------------------------------------------------------------------------

class PromptsmithSaysWhatWasWrong(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="psmith-usage-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        os.makedirs(os.path.join(self.tmp, "scripts"))
        os.makedirs(os.path.join(self.tmp, "blackboard"))
        for name in ("promptsmith.py", "bb_lock.py"):
            shutil.copy(os.path.join(REPO, "scripts", name),
                        os.path.join(self.tmp, "scripts", name))
        self.brief = os.path.join(self.tmp, "sample-brief.md")
        shutil.copy(os.path.join(REPO, "examples", "sample-brief.md"), self.brief)
        self.script = os.path.join(self.tmp, "scripts", "promptsmith.py")
        self.out_dir = os.path.join(self.tmp, "blackboard", "packets")
        self.env = dict(os.environ)
        self.env["BB_LOCK_DIR"] = os.path.join(self.tmp, "locks")

    def _run(self, argv):
        return subprocess.run([sys.executable, self.script] + argv,
                              cwd=self.tmp, env=self.env,
                              capture_output=True, text=True, timeout=120)

    def test_missing_task_names_the_missing_flag_on_stderr(self):
        """The README line, copied literally, with no --task."""
        proc = self._run(["--brief-file", self.brief])
        _no_traceback(self, proc, "promptsmith with no --task")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertTrue(proc.stderr.strip(),
                        "promptsmith exited 2 with an EMPTY stderr; nothing told "
                        "the user what was wrong")
        self.assertIn("--task", proc.stderr,
                      "the error must name the missing flag:\n" + proc.stderr)

    def test_bare_trailing_flag_is_a_usage_error(self):
        proc = self._run(["--brief-file", self.brief, "--task"])
        _no_traceback(self, proc, "promptsmith with a bare trailing --task")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("--task", proc.stderr,
                      "the error must name the valueless flag:\n" + proc.stderr)

    def test_unreadable_brief_file_is_a_usage_error(self):
        missing = os.path.join(self.tmp, "no-such-brief.md")
        proc = self._run(["--task", "build the hero section",
                          "--brief-file", missing,
                          "--out-dir", self.out_dir])
        _no_traceback(self, proc, "promptsmith with an unreadable --brief-file")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("--brief-file", proc.stderr, proc.stderr)

    def test_the_documented_command_still_compiles_both_packets(self):
        """Near-miss control: the README example WITH --task must still work."""
        proc = self._run(["--task", "build the hero section",
                          "--brief-file", self.brief,
                          "--packet-id", "psmith-usage-control",
                          "--out-dir", self.out_dir])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        for key in ("worker_prompt", "rubric"):
            self.assertTrue(os.path.exists(payload[key]),
                            "%s was not written" % key)


# --------------------------------------------------------------------------
# 4 (mirror). evolution's identical flag parser
# --------------------------------------------------------------------------

class EvolutionRefusesABareTrailingFlag(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="evolution-usage-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        os.makedirs(os.path.join(self.tmp, "scripts"))
        os.makedirs(os.path.join(self.tmp, "runs"))
        for name in ("evolution.py", "bb_lock.py"):
            shutil.copy(os.path.join(REPO, "scripts", name),
                        os.path.join(self.tmp, "scripts", name))
        self.script = os.path.join(self.tmp, "scripts", "evolution.py")
        self.env = dict(os.environ)
        self.env["BB_LOCK_DIR"] = os.path.join(self.tmp, "locks")

    def _run(self, argv):
        return subprocess.run([sys.executable, self.script] + argv,
                              cwd=self.tmp, env=self.env,
                              capture_output=True, text=True, timeout=120)

    def test_bare_trailing_run_flag_is_a_usage_error(self):
        proc = self._run(["best", "--run"])
        _no_traceback(self, proc, "evolution with a bare trailing --run")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("--run", proc.stderr,
                      "the error must name the valueless flag:\n" + proc.stderr)

    def test_a_real_run_name_still_works(self):
        """Near-miss control: --run WITH a value is unaffected."""
        recorded = self._run(["record", "--run", "demo-run", "--variant", "v1",
                              "--change", "first variant", "--verdict", "approve",
                              "--score", "0.81"])
        self.assertEqual(recorded.returncode, 0,
                         recorded.stdout + recorded.stderr)
        proc = self._run(["best", "--run", "demo-run"])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["variant"], "v1")


# --------------------------------------------------------------------------
# 5. brain export --from a non-object record
# --------------------------------------------------------------------------

class BrainExportRefusesANonObjectRecord(unittest.TestCase):
    """Sandbox mirrors the shipped layout (<root>/brain/brain.py) so HERE,
    ROOT and _safe_path resolve exactly as they do in the addon."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="brain-export-shape-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.proj = os.path.join(self.tmp, "proj")
        self.brain_dir = os.path.join(self.proj, "brain")
        os.makedirs(self.brain_dir)
        shutil.copy(BRAIN_PY, os.path.join(self.brain_dir, "brain.py"))
        self.brain_py = os.path.join(self.brain_dir, "brain.py")
        self.store = os.path.join(self.brain_dir, "shared-brain.jsonl")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self.env = {"HOME": self.home,
                    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                    "LC_ALL": "C"}

    def _export(self, lines, name="lessons.jsonl"):
        src = os.path.join(self.proj, name)
        with open(src, "w", encoding="utf-8") as f:
            f.write("".join(json.dumps(l) + "\n" for l in lines))
        return subprocess.run([sys.executable, self.brain_py, "export", "--from", src],
                              cwd=self.proj, env=self.env,
                              capture_output=True, text=True, timeout=120)

    def test_bare_scalar_and_list_records_are_refused(self):
        for label, record in (("string", "just a bare string"),
                              ("number", 42),
                              ("list", ["a", "b"])):
            with self.subTest(record=label):
                proc = self._export([record], name="lessons-%s.jsonl" % label)
                _no_traceback(self, proc, "brain export --from a %s record" % label)
                self.assertNotEqual(proc.returncode, 0,
                                    "export accepted a non-object record")
                self.assertIn("refuse", proc.stderr.lower(),
                              "the refusal must say what it refused:\n" + proc.stderr)
                self.assertFalse(os.path.exists(self.store),
                                 "a malformed export wrote to the brain file")

    def test_a_real_lesson_file_still_exports(self):
        """Near-miss control: valid lesson objects are unaffected."""
        proc = self._export([
            {"id": "lesson-export-control", "type": "lesson",
             "text": "Refuse malformed input; never traceback.",
             "tags": ["lesson"]},
            {"id": "not-a-lesson", "type": "note", "text": "skipped by type"},
        ])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        with open(self.store, encoding="utf-8") as f:
            ids = [json.loads(l)["id"] for l in f if l.strip()]
        self.assertEqual(ids, ["lesson-export-control"])


if __name__ == "__main__":
    unittest.main()
