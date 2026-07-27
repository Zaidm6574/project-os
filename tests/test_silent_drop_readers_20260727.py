#!/usr/bin/env python3
"""Five readers that dropped data and reported success (audit 2026-07-27).

This repo's signature defect is DATA LOSS THAT LOOKS LIKE SUCCESS. The sweep
that produced memory/mneme_adapter.py::read()/_gather() fixed that shape in one
place and left five survivors, each pinned here from OBSERVED BEHAVIOUR of the
shipped tool over a throwaway tree -- exit status, stderr, the artifact on disk
-- never from the module's source text:

  1. memory/build_graph.py::read      -- `except Exception: return ""`, so a
     03-decisions.md that is not valid UTF-8 (or is chmod 000) produced a graph
     with ZERO decision nodes, printed "[arachne] wrote ..." and exited 0.
  2. memory/code_graph.py::_scan_module -- a file that fails to parse is dropped
     from the graph while its sha256 STAYS in graph["files"], so `check` calls
     the graph "FRESH (all indexed file hashes match current source)" and
     `orient` answers SYMBOL ABSENT for a symbol that is right there on disk.
  3. addons/full-engine/brain/brain.py::cmd_export -- an id-less lesson is
     skipped by the same `continue` as an already-present one, and the only
     output is "export: N new lesson(s) appended".
  4. addons/full-engine/brain/central_brain.py::read_jsonl -- a non-UTF-8 byte
     escapes as a raw UnicodeDecodeError traceback where the sibling reader of
     the same format (brain.py::_read_jsonl) refuses cleanly.
  5. scripts/harvest.py::cmd_scan -- a row with too little content to be a
     lesson is counted as a dupe and reported as "already in the brain" against
     a PROVABLY EMPTY brain: a claim about a lookup that never happened.

The fix shape is mneme_adapter's: ABSENT is fine, DAMAGED or UNREADABLE is not;
a skipped record is NAMED with its line number; an empty corpus still exits 0
(the defect is the silence, not the exit code).

BOTH DIRECTIONS are pinned for every fix -- what must be kept is asserted kept
right beside what must be refused -- because scripts/harvest.py has been
rewritten five times, each pass fixing one direction and silently opening its
mirror. Stdlib only; 3.9.6 CI floor; every write lands in a tempdir.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BUILD_GRAPH = REPO / "memory" / "build_graph.py"
CODE_GRAPH = REPO / "memory" / "code_graph.py"
BRAIN = REPO / "addons" / "full-engine" / "brain" / "brain.py"
CENTRAL_BRAIN = REPO / "addons" / "full-engine" / "brain" / "central_brain.py"
HARVEST = REPO / "scripts" / "harvest.py"

# 0xFF is never a valid UTF-8 byte, in any position.
BAD_BYTE = b"\xff"


# --------------------------------------------------------------------------- #
# 1. memory/build_graph.py -- a damaged blackboard file became an empty string  #
# --------------------------------------------------------------------------- #
class BuildGraphRefusesADamagedSource(unittest.TestCase):
    """`read()` swallowed every OSError/UnicodeDecodeError into ""."""

    DECISIONS = "| ID | Decision |\n|----|----------|\n| D1 | ship the thing |\n"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="silent-drop-arachne-"))
        self.addCleanup(self._cleanup)
        self.project = self.tmp / "project"
        (self.project / "memory").mkdir(parents=True)
        self.bb = self.project / "blackboard"
        self.bb.mkdir()
        shutil.copy2(BUILD_GRAPH, self.project / "memory" / "build_graph.py")
        (self.bb / "00-project-goal.md").write_text(
            "# Sandbox Run\n\n## Canonical Goal\nship it\n", encoding="utf-8")
        self.graph = self.project / "graphify-out" / "graph.json"

    def _cleanup(self):
        for path in self.bb.glob("*"):
            try:
                os.chmod(str(path), 0o644)
            except OSError:
                pass
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def _build(self):
        proc = subprocess.run(
            [sys.executable, str(self.project / "memory" / "build_graph.py"),
             "--root", "blackboard"],
            capture_output=True, text=True, cwd=str(self.project), timeout=120)
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")

    def test_a_non_utf8_decisions_file_is_refused_not_read_as_empty(self):
        (self.bb / "03-decisions.md").write_bytes(
            b"| ID | Decision |\n|----|----|\n| D1 | caf" + BAD_BYTE + b" |\n")

        code, out = self._build()

        self.assertNotEqual(
            code, 0,
            "build_graph read a damaged source as empty and reported success:\n" + out)
        self.assertIn("03-decisions.md", out,
                      "the refusal must NAME the file it could not read:\n" + out)
        self.assertNotIn("Traceback", out,
                         "a damaged source must refuse cleanly, not traceback:\n" + out)

    def test_an_unreadable_decisions_file_is_refused_not_read_as_empty(self):
        target = self.bb / "03-decisions.md"
        target.write_text(self.DECISIONS, encoding="utf-8")
        os.chmod(str(target), 0o000)
        if os.access(str(target), os.R_OK):  # running as root: the probe is void
            self.skipTest("cannot make a file unreadable as this user")

        code, out = self._build()

        self.assertNotEqual(
            code, 0,
            "build_graph read an unreadable source as empty and reported success:\n" + out)
        self.assertIn("03-decisions.md", out, out)

    def test_a_damaged_source_leaves_the_previous_graph_untouched(self):
        self.graph.parent.mkdir(parents=True, exist_ok=True)
        self.graph.write_text('{"nodes": ["previous good graph"]}', encoding="utf-8")
        (self.bb / "03-decisions.md").write_bytes(b"| ID |\n|--|\n| " + BAD_BYTE + b" |\n")

        code, out = self._build()

        self.assertNotEqual(code, 0, out)
        self.assertIn("previous good graph", self.graph.read_text(encoding="utf-8"),
                      "a refused build overwrote the last good graph:\n" + out)

    # ---- the other direction: ABSENT is fine, and a good file still builds ---
    def test_absent_optional_files_still_build_and_exit_zero(self):
        code, out = self._build()

        self.assertEqual(code, 0,
                         "a run with no 03/04/06 file must still build:\n" + out)
        graph = json.loads(self.graph.read_text(encoding="utf-8"))
        self.assertTrue(graph["nodes"], out)

    def test_a_readable_decisions_file_still_produces_its_nodes(self):
        (self.bb / "03-decisions.md").write_text(self.DECISIONS, encoding="utf-8")

        code, out = self._build()

        self.assertEqual(code, 0, out)
        graph = json.loads(self.graph.read_text(encoding="utf-8"))
        labels = [n["label"] for n in graph["nodes"] if n["type"] == "decision"]
        self.assertEqual(labels, ["ship the thing"], out)


# --------------------------------------------------------------------------- #
# 2. memory/code_graph.py -- an unparsable module vanished from a "FRESH" graph #
# --------------------------------------------------------------------------- #
class CodeGraphNamesTheFilesItCouldNotParse(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="silent-drop-codegraph-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.root = self.tmp / "src"
        (self.root / "app").mkdir(parents=True)
        (self.root / "app" / "__init__.py").write_text("", encoding="utf-8")
        (self.root / "app" / "core.py").write_text(
            "def helper():\n    return 1\n\n\ndef work():\n    return helper()\n",
            encoding="utf-8")
        self.out = self.tmp / "code-graph.json"

    def _break(self):
        (self.root / "app" / "broken.py").write_text(
            "def oops(:\n    return 1\n", encoding="utf-8")

    def _cli(self, *args):
        proc = subprocess.run([sys.executable, str(CODE_GRAPH), *args],
                              capture_output=True, text=True, timeout=120)
        return proc.returncode, proc.stdout or "", proc.stderr or ""

    def _build(self):
        return self._cli("build", "--root", str(self.root), "--out", str(self.out))

    def test_build_names_the_unparsable_file_on_stderr(self):
        self._break()

        code, stdout, stderr = self._build()

        self.assertIn("app/broken.py", stdout + stderr,
                      "a module dropped from the graph was never named:\n"
                      + stdout + stderr)
        self.assertRegex(stderr, r"broken\.py[^\n]*line\s*1|line\s*1[^\n]*broken\.py",
                         "the skipped file must be named WITH its line number:\n" + stderr)
        self.assertEqual(code, 0,
                         "an unparsable file must not brick the whole build:\n" + stderr)

    def test_the_graph_records_which_files_are_missing_from_it(self):
        self._break()
        self._build()

        graph = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertIn("app/broken.py", graph.get("unparsed", {}),
                      "graph['files'] fingerprints a file whose symbols are absent, "
                      "with nothing in the artifact saying so")

    def test_orient_says_why_the_symbol_may_be_absent(self):
        self._break()
        self._build()

        code, stdout, stderr = self._cli(
            "orient", "app.broken:oops", "--root", str(self.root),
            "--graph", str(self.out))

        self.assertNotEqual(code, 0, stdout)
        self.assertIn("app/broken.py", stdout + stderr,
                      "'SYMBOL ABSENT' for a symbol that IS on disk, with no hint "
                      "that its file never made it into the graph:\n" + stdout + stderr)

    # ---- the other direction: a clean tree must stay quiet ------------------
    def test_a_clean_tree_produces_no_warning_and_no_unparsed_entry(self):
        code, stdout, stderr = self._build()

        self.assertEqual(code, 0, stderr)
        self.assertEqual(stderr.strip(), "",
                         "a warning that fires on a healthy tree is noise:\n" + stderr)
        graph = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual(graph.get("unparsed", {}), {}, stdout)

    def test_a_clean_tree_still_checks_fresh(self):
        self._build()

        code, stdout, _stderr = self._cli(
            "check", "--root", str(self.root), "--graph", str(self.out))

        self.assertEqual(code, 0, stdout)
        self.assertIn("FRESH", stdout, stdout)

    def test_symbols_from_the_parsable_files_are_still_indexed(self):
        self._break()
        self._build()

        graph = json.loads(self.out.read_text(encoding="utf-8"))
        ids = {n["id"] for n in graph["nodes"]}
        self.assertIn("app.core:work", ids,
                      "one bad file must not cost the good files their nodes")


# --------------------------------------------------------------------------- #
# 3. brain.py export -- an id-less lesson vanished into the dedupe `continue`   #
# --------------------------------------------------------------------------- #
class ExportRefusesAnIdLessLesson(unittest.TestCase):
    def _export(self, source_text):
        tmp = tempfile.mkdtemp(prefix="silent-drop-export-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        project = Path(tmp) / "project"
        (project / "brain").mkdir(parents=True)
        script = project / "brain" / "brain.py"
        shutil.copy2(BRAIN, script)
        src = project / "source.jsonl"
        src.write_text(source_text, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(script), "export", "--from", str(src)],
            capture_output=True, text=True, cwd=str(project),
            stdin=subprocess.DEVNULL, timeout=60)
        brain_file = project / "brain" / "shared-brain.jsonl"
        written = brain_file.read_text(encoding="utf-8") if brain_file.exists() else ""
        return proc.returncode, (proc.stdout or "") + (proc.stderr or ""), written

    def test_a_lesson_with_no_id_is_surfaced_not_silently_dropped(self):
        code, out, written = self._export(
            '{"id":"E1","type":"lesson","text":"a lesson that has an id"}\n'
            '{"type":"lesson","text":"a lesson whose id is missing entirely"}\n')

        self.assertNotEqual(
            code, 0,
            "export dropped an id-less lesson and reported success:\n" + out)
        self.assertIn("id", out.lower(), out)
        self.assertRegex(out, r"#\s*1|line\s*2|record\s*2",
                         "the dropped record must be NAMED by index/line:\n" + out)
        self.assertNotIn("Traceback", out, out)

    def test_an_empty_string_id_is_treated_the_same_as_a_missing_one(self):
        code, out, _written = self._export(
            '{"id":"","type":"lesson","text":"an id that is only an empty string"}\n')

        self.assertNotEqual(code, 0,
                            "an empty id is no id at all, and was dropped too:\n" + out)

    def test_a_refused_export_writes_nothing_at_all(self):
        code, out, written = self._export(
            '{"id":"K1","type":"lesson","text":"this one is perfectly good"}\n'
            '{"type":"lesson","text":"but this one has no id"}\n')

        self.assertNotEqual(code, 0, out)
        self.assertEqual(
            written, "",
            "a refused export half-committed its good records first:\n" + written)

    # ---- the other direction: good exports and real dedupe must survive -----
    def test_a_source_where_every_lesson_has_an_id_still_exports(self):
        code, out, written = self._export(
            '{"id":"G1","type":"lesson","text":"first good lesson"}\n'
            '{"id":"G2","type":"lesson","text":"second good lesson"}\n')

        self.assertEqual(code, 0, out)
        self.assertIn("2 new lesson(s) appended", out, out)
        ids = {json.loads(l)["id"] for l in written.splitlines() if l.strip()}
        self.assertEqual(ids, {"G1", "G2"}, written)

    def test_a_duplicate_id_is_still_skipped_and_written_once(self):
        code, out, written = self._export(
            '{"id":"D1","type":"lesson","text":"a lesson exported twice"}\n'
            '{"id":"D1","type":"lesson","text":"a lesson exported twice"}\n')

        self.assertEqual(code, 0,
                         "a repeated id is ordinary dedupe, not a refusal:\n" + out)
        lines = [l for l in written.splitlines() if l.strip()]
        self.assertEqual(len(lines), 1, written)

    def test_a_non_lesson_record_is_still_filtered_without_refusing(self):
        code, out, written = self._export(
            '{"id":"N1","type":"note","text":"not a lesson, correctly ignored"}\n'
            '{"id":"L1","type":"lesson","text":"the only real lesson here"}\n')

        self.assertEqual(code, 0,
                         "a non-lesson record has no id requirement to fail:\n" + out)
        ids = {json.loads(l)["id"] for l in written.splitlines() if l.strip()}
        self.assertEqual(ids, {"L1"}, written)


# --------------------------------------------------------------------------- #
# 4. central_brain.py -- raw UnicodeDecodeError where the sibling refuses clean #
# --------------------------------------------------------------------------- #
class CentralBrainRefusesNonUtf8LikeItsSibling(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="silent-drop-central-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.central = self.tmp / "central"
        self.project = self.tmp / "proj"
        (self.project / "brain").mkdir(parents=True)
        self.project_brain = self.project / "brain" / "shared-brain.jsonl"
        self.env = {"HOME": str(self.home),
                    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                    "LC_ALL": "C"}

    def _cli(self, *argv):
        proc = subprocess.run([sys.executable, str(CENTRAL_BRAIN), *argv],
                              capture_output=True, text=True, env=self.env,
                              cwd=str(self.tmp), timeout=120)
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")

    def _push(self):
        return self._cli("push", "--path", str(self.central),
                         "--project", str(self.project), "--project-id", "demo")

    def test_a_non_utf8_project_brain_refuses_cleanly(self):
        self.project_brain.write_bytes(
            b'{"id":"U1","type":"lesson","text":"caf' + BAD_BYTE + b' bytes"}\n')

        code, out = self._push()

        self.assertNotEqual(code, 0, out)
        self.assertNotIn("Traceback", out,
                         "the sibling reader refuses cleanly; this one tracebacks:\n" + out)
        self.assertIn("refuse", out.lower(), out)
        self.assertIn("UTF-8", out, out)
        self.assertIn(str(self.project_brain), out,
                      "the refusal must name the file it could not decode:\n" + out)

    def test_a_non_utf8_central_brain_refuses_cleanly_on_status(self):
        self.central.mkdir(parents=True)
        (self.central / "shared-brain.jsonl").write_bytes(
            b'{"id":"U2","type":"lesson","text":"' + BAD_BYTE + b'"}\n')

        code, out = self._cli("status", "--path", str(self.central))

        self.assertNotEqual(code, 0, out)
        self.assertNotIn("Traceback", out, out)
        self.assertIn("UTF-8", out, out)

    def test_the_wording_matches_the_sibling_reader_in_brain_py(self):
        """Two readers of ONE file format must not disagree about the diagnosis."""
        tmp = Path(tempfile.mkdtemp(prefix="silent-drop-sibling-"))
        self.addCleanup(shutil.rmtree, str(tmp), True)
        project = tmp / "project"
        (project / "brain").mkdir(parents=True)
        shutil.copy2(BRAIN, project / "brain" / "brain.py")
        src = project / "source.jsonl"
        src.write_bytes(b'{"id":"S1","type":"lesson","text":"' + BAD_BYTE + b'"}\n')
        proc = subprocess.run(
            [sys.executable, str(project / "brain" / "brain.py"),
             "export", "--from", str(src)],
            capture_output=True, text=True, cwd=str(project),
            stdin=subprocess.DEVNULL, timeout=60)
        sibling = (proc.stdout or "") + (proc.stderr or "")
        self.assertIn("not valid UTF-8", sibling, sibling)

        self.project_brain.write_bytes(
            b'{"id":"U3","type":"lesson","text":"' + BAD_BYTE + b'"}\n')
        _code, mine = self._push()
        self.assertIn("not valid UTF-8", mine,
                      "central_brain diagnoses the same bytes differently:\n" + mine)

    # ---- the other direction: the existing line-number report must survive ---
    def test_a_utf8_brain_with_a_damaged_line_still_reports_that_line(self):
        good = json.dumps({"id": "L1", "type": "lesson", "text": "a good lesson"})
        self.project_brain.write_text(good + "\n" + '{"id": "L2", "type": "les',
                                      encoding="utf-8")

        code, out = self._push()

        self.assertNotEqual(code, 0, out)
        self.assertRegex(out, r"line[^\n]*\b2\b", out)
        self.assertIn("1 lesson(s) added", out, out)

    def test_a_clean_brain_still_pushes_quietly(self):
        good = json.dumps({"id": "L1", "type": "lesson",
                           "text": "a perfectly good lesson to sync"})
        self.project_brain.write_text(good + "\n", encoding="utf-8")

        code, out = self._push()

        self.assertEqual(code, 0, out)
        self.assertIn("1 lesson(s) added", out, out)
        self.assertNotIn("UTF-8", out, out)


# --------------------------------------------------------------------------- #
# 5. harvest.py -- "already in the brain" against a provably EMPTY brain        #
# --------------------------------------------------------------------------- #
LONG_LESSON = "Verify a fix by reverting it and watching the test fail first"
SHORT_ROW = "Ship it"


class HarvestDoesNotClaimALookupItNeverMade(unittest.TestCase):
    SLUG = "sandbox"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="silent-drop-harvest-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        os.makedirs(os.path.join(self.tmp, "scripts"))
        shutil.copy2(str(HARVEST), os.path.join(self.tmp, "scripts", "harvest.py"))
        self.run = os.path.join(self.tmp, "runs", self.SLUG)
        os.makedirs(self.run)
        self._write("13-delivery-report.md", "# 13 Delivery Report\n")
        self.brain = os.path.join(self.tmp, "brain.jsonl")
        with open(self.brain, "w", encoding="utf-8") as f:
            f.write("")

    def _write(self, name, body):
        with open(os.path.join(self.run, name), "w", encoding="utf-8") as f:
            f.write(body)

    def _scan(self, md, brain_lines=""):
        self._write("19-memory-harvest.md", md)
        with open(self.brain, "w", encoding="utf-8") as f:
            f.write(brain_lines)
        env = dict(os.environ, PROJECT_OS_SHARED_BRAIN=self.brain)
        proc = subprocess.run(
            [sys.executable, os.path.join(self.tmp, "scripts", "harvest.py"),
             "scan", self.SLUG],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, timeout=120)
        return proc.returncode, proc.stdout.decode()

    def test_a_content_free_row_is_not_called_already_in_the_brain(self):
        code, out = self._scan("## Lessons\n- %s\n" % SHORT_ROW)

        self.assertEqual(
            0, os.path.getsize(self.brain),
            "the probe is void unless the brain is provably empty")
        self.assertNotIn(
            "already in the brain", out,
            "scan claimed a brain lookup it never performed -- the brain is "
            "EMPTY and the row was dropped for having no content:\n" + out)
        self.assertEqual(code, 0, out)

    def test_a_content_free_row_is_reported_with_its_real_reason(self):
        code, out = self._scan("## Lessons\n- %s\n" % SHORT_ROW)

        self.assertRegex(out.lower(), r"too short|too little|no content|content-free",
                         "the row was dropped with no reason given:\n" + out)

    def test_a_mixed_scan_separates_short_rows_from_real_dupes(self):
        brain = json.dumps({"text": LONG_LESSON + ", always"}) + "\n"
        md = ("## Lessons\n"
              "- %s\n"
              "- %s\n"
              "- Never weaken a gate to make a red suite go green again\n"
              % (SHORT_ROW, LONG_LESSON))

        code, out = self._scan(md, brain_lines=brain)

        self.assertEqual(code, 0, out)
        self.assertIn("staged 1 new", out, out)
        self.assertRegex(out.lower(), r"1 already in the brain|1 dupe",
                         "the one REAL dupe stopped being reported:\n" + out)
        self.assertRegex(out.lower(), r"too short|too little|no content|content-free",
                         "the short row was folded back into the dupe count:\n" + out)

    # ---- the other direction: real dupes and real lessons keep their paths ---
    def test_a_real_duplicate_is_still_reported_as_already_in_the_brain(self):
        brain = json.dumps({"text": LONG_LESSON + ", always"}) + "\n"

        code, out = self._scan("## Lessons\n- %s\n" % LONG_LESSON, brain_lines=brain)

        self.assertEqual(code, 0, out)
        self.assertIn("already in the brain", out, out)
        self.assertTrue(os.path.isfile(os.path.join(self.run, ".harvested")),
                        "an all-dupes harvest stopped being marked complete:\n" + out)

    def test_a_fresh_lesson_is_still_staged(self):
        code, out = self._scan("## Lessons\n- %s\n" % LONG_LESSON)

        self.assertEqual(code, 0, out)
        self.assertIn("staged 1 new", out, out)

    def test_a_brain_line_it_cannot_read_is_named_not_skipped_in_silence(self):
        """A brain line that took no part in the dupe check is a dupe risk."""
        brain = ('{"text": "a perfectly good brain entry to compare against"}\n'
                 '{"text": "a line cut off mid-wr\n')

        code, out = self._scan("## Lessons\n- %s\n" % LONG_LESSON,
                               brain_lines=brain)

        self.assertEqual(code, 0, out)
        self.assertRegex(out, r"line\s*2", "the skipped brain line was not named:\n" + out)
        self.assertIn("staged 1 new", out, out)

    def test_a_bare_json_string_in_the_brain_does_not_traceback(self):
        code, out = self._scan("## Lessons\n- %s\n" % LONG_LESSON,
                               brain_lines='"a bare string, valid JSON, no .get"\n')

        self.assertEqual(code, 0, out)
        self.assertNotIn("Traceback", out, out)
        self.assertRegex(out, r"line\s*1", out)

    def test_a_clean_brain_produces_no_warning(self):
        brain = json.dumps({"text": "an unrelated but well-formed entry"}) + "\n"

        code, out = self._scan("## Lessons\n- %s\n" % LONG_LESSON,
                               brain_lines=brain)

        self.assertEqual(code, 0, out)
        self.assertNotIn("WARNING", out,
                         "a warning that fires on a healthy brain is noise:\n" + out)

    def test_an_all_short_scan_is_still_stamped_complete(self):
        code, out = self._scan("## Lessons\n- %s\n" % SHORT_ROW)

        self.assertEqual(code, 0, out)
        self.assertTrue(os.path.isfile(os.path.join(self.run, ".harvested")),
                        "nothing to stage is still a completed harvest:\n" + out)


if __name__ == "__main__":
    unittest.main()
