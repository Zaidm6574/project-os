"""Two ways a rejected row still reached the PUBLIC brain (audit 2026-07-26).

Both were verified against the shipped code before this file existed:

  1. A verdict followed by SENTENCE-TERMINATOR punctuation. The free-prose rule
     accepted only `[:;,[()-]` after the marker, and the continuation-word rule
     required whitespace there, so "Rejected. The transcript holds the client
     token", "Rejected!", "REJECTED." and "'Rejected' pending legal review" all
     harvested through with nothing in DROPPED -- the same annotation family
     three earlier rewrites chased, failing OPEN.
  2. The eval-log FALLBACK path. A run with no 19-memory-harvest.md went through
     eval_log_candidates(), which applied no reject/private filtering at all: a
     row whose own status column said "private-only, do not harvest" was staged
     into the proposals JSONL with the directive text embedded in the lesson,
     and DROPPED stayed empty.

Also covered: snake_case directives (`do_not_harvest:`, `private_only`), which
bypassed the directive rule entirely, and the provenance of fallback proposals
(origin_id used to claim 19-memory-harvest.md for a lesson read out of
12-evaluation-log.md).

Every assertion is on BEHAVIOUR -- what the extractors yield, what DROPPED
records, and what a real `harvest.py scan` writes and prints -- never on the
module's source text, a regex in isolation, or a parameter name. The
keep-direction is asserted just as hard as the drop-direction, so "drop
everything" cannot pass this file.
"""

import importlib.util
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HARVEST = os.path.join(ROOT, "scripts", "harvest.py")


def _load():
    spec = importlib.util.spec_from_file_location("harvest_terminator", HARVEST)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


harvest = _load()


def _harvested(markdown):
    """Lesson texts that survive the reject filter, via the real entry point."""
    return [text for _typ, text in harvest.bullets_by_section(markdown)]


class TerminatorVerdictsNeverReachThePublicBrain(unittest.TestCase):
    """"Rejected." is the same annotation as "Rejected:" -- and as common."""

    LEAKED = [
        "Rejected. The transcript holds the client auth token sk-live-abc123",
        "Rejected! The evidence column names the paying client outright",
        "REJECTED. The run pasted a credential into the delivery report",
        "Rejected? The board never signed this lesson off for reuse",
        "'Rejected' pending legal review of the client transcript",
        '"Rejected" because the log holds the customer account number',
        "Rejection. The lesson duplicates an internal-only safeguard",
    ]

    def setUp(self):
        del harvest.DROPPED[:]

    def test_each_terminator_verdict_is_dropped(self):
        for verdict in self.LEAKED:
            with self.subTest(verdict=verdict):
                del harvest.DROPPED[:]
                self.assertEqual(
                    _harvested("## Lessons\n- %s\n" % verdict), [],
                    "verdict harvested into the public brain: %r" % verdict)

    def test_a_dropped_terminator_verdict_is_recorded_for_the_report(self):
        """A silent drop is as bad as a leak: the operator must see it."""
        self.assertEqual(
            _harvested("## Lessons\n- Rejected. It holds a client token\n"), [])
        self.assertEqual(len(harvest.DROPPED), 1, harvest.DROPPED)
        self.assertIn("Rejected.", harvest.DROPPED[0])

    def test_a_terminator_verdict_in_a_tables_lesson_cell_is_dropped(self):
        table = ("## Lessons\n"
                 "| Lesson | Status |\n"
                 "| --- | --- |\n"
                 "| Rejected. Holds the client auth token sk-abc | done |\n")
        self.assertEqual(_harvested(table), [],
                         "a first-cell terminator verdict harvested through")


class SnakeCaseDirectivesAreStillDirectives(unittest.TestCase):
    """Run notes get written in snake_case; the directive still binds."""

    def setUp(self):
        del harvest.DROPPED[:]

    def test_snake_case_directives_are_dropped(self):
        for bullet in ("do_not_harvest: this row holds the client auth token",
                       "private_only until the client approves the write-up",
                       "The evidence is private_only and names the customer"):
            with self.subTest(bullet=bullet):
                del harvest.DROPPED[:]
                self.assertEqual(
                    _harvested("## Lessons\n- %s\n" % bullet), [], bullet)
                self.assertEqual(len(harvest.DROPPED), 1, harvest.DROPPED)


class OrdinaryProseAboutRejectionStillHarvests(unittest.TestCase):
    """The drop-direction must not be bought with a wave of false drops."""

    KEPT = [
        "Rejection criteria belong in the rubric, not in the reviewer's head",
        "Reject-first workflow keeps the review queue short",
        "The plan was rejected by the board, so we rewrote the scope",
        "Rejects are logged with a reason so the filter stays auditable",
        "Rejections due to rate limits should be retried with backoff",
        "Reject stale locks before retrying instead of trusting them",
        "Reject by default and allowlist explicitly, never the reverse",
        "Private data belongs in the run folder, never in a public issue",
    ]

    def setUp(self):
        del harvest.DROPPED[:]

    def test_real_lessons_are_not_dropped(self):
        for lesson in self.KEPT:
            with self.subTest(lesson=lesson):
                del harvest.DROPPED[:]
                self.assertEqual(
                    _harvested("## Lessons\n- %s\n" % lesson), [lesson],
                    "a real lesson was dropped as a verdict: %r" % lesson)

    def test_an_ordinary_table_row_still_harvests(self):
        table = ("## Lessons\n"
                 "| Lesson | Status |\n"
                 "| --- | --- |\n"
                 "| Verify a fix by reverting it and watching it fail | ok |\n")
        self.assertEqual(
            _harvested(table),
            ["Verify a fix by reverting it and watching it fail — ok"])


class EvalLogFallbackHonoursTheSameFilter(unittest.TestCase):
    """The fallback extractor is not a filter-free side door."""

    def setUp(self):
        del harvest.DROPPED[:]

    def _candidates(self, row):
        return list(harvest.eval_log_candidates(row))

    FILTERED = [
        "| Auth flow failed: holds client token sk-live-abc123"
        " | private-only, do not harvest |",
        "| The retry loop failed and the log holds a client token"
        " | do_not_harvest |",
        "| Rejected. The failed run pasted the client token into the log"
        " | notes |",
        "| Step 3 | the login failed on a stale token | Rejected for reuse |",
    ]

    def test_rejected_and_private_eval_rows_are_not_yielded(self):
        for row in self.FILTERED:
            with self.subTest(row=row):
                del harvest.DROPPED[:]
                self.assertEqual(
                    self._candidates(row), [],
                    "eval-log row bypassed the reject filter: %r" % row)

    def test_a_filtered_eval_row_is_recorded_for_the_report(self):
        self._candidates(self.FILTERED[0])
        self.assertEqual(len(harvest.DROPPED), 1, harvest.DROPPED)
        self.assertIn("private-only", harvest.DROPPED[0])

    def test_an_ordinary_eval_row_is_still_a_candidate(self):
        row = "| Step 3 | the retry loop failed on a stale lock | fix landed |"
        self.assertEqual(
            self._candidates(row),
            [("lesson",
              "Step 3 — the retry loop failed on a stale lock — fix landed")],
            "the fallback path stopped harvesting real eval-log rows")
        self.assertEqual(harvest.DROPPED, [])


class ScanEndToEndOnTheFallbackPath(unittest.TestCase):
    """cmd_scan driven as a real subprocess over a throwaway tree."""

    def _scan(self, eval_log):
        """Run `harvest.py scan` with ONLY a 12-evaluation-log.md present."""
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        os.makedirs(os.path.join(tmp, "scripts"))
        shutil.copy(HARVEST, os.path.join(tmp, "scripts", "harvest.py"))
        run = os.path.join(tmp, "runs", "sandbox")
        os.makedirs(run)
        with open(os.path.join(run, "12-evaluation-log.md"), "w",
                  encoding="utf-8") as f:
            f.write(eval_log)
        brain = os.path.join(tmp, "brain.jsonl")
        with open(brain, "w", encoding="utf-8") as f:
            f.write("")
        env = dict(os.environ, PROJECT_OS_SHARED_BRAIN=brain)
        proc = subprocess.run(
            [sys.executable, os.path.join(tmp, "scripts", "harvest.py"),
             "scan", "sandbox"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
        self.assertEqual(proc.returncode, 0, proc.stdout.decode())
        staged = []
        for path in glob.glob(os.path.join(tmp, "blackboard", "packets", "*.jsonl")):
            with open(path, encoding="utf-8") as f:
                staged += [json.loads(line) for line in f if line.strip()]
        return proc.stdout.decode(), staged

    PRIVATE_ROW = ("| Finding | Notes |\n"
                   "| --- | --- |\n"
                   "| Auth flow failed: transcript holds the client token"
                   " sk-live-abc123xyz | private-only, do not harvest |\n")

    def test_a_private_eval_row_is_never_staged(self):
        """Nothing brain-bound is written. (The local DROPPED report echoes the
        row on purpose -- that is the operator's own console, not the brain.)"""
        out, staged = self._scan(self.PRIVATE_ROW)
        self.assertEqual(
            [o["text"] for o in staged], [],
            "a do-not-harvest eval-log row was staged: %r" % staged)
        self.assertNotIn("staged", out, out)

    def test_the_filtering_is_reported_not_silent(self):
        out, _ = self._scan(self.PRIVATE_ROW)
        self.assertIn("filtered 1 row(s)", out,
                      "the eval-log path filtered silently: %r" % out)
        self.assertNotIn("no harvest sources found", out, out)

    def test_a_real_eval_row_is_staged_with_honest_provenance(self):
        out, staged = self._scan(
            "| Step 3 | the retry loop failed on a stale lock and hung"
            " | fix landed |\n")
        self.assertEqual(len(staged), 1, out)
        self.assertIn("stale lock", staged[0]["text"])
        self.assertEqual(
            staged[0]["origin_id"], "sandbox/12-evaluation-log.md",
            "a fallback proposal claims it came from 19-memory-harvest.md")


if __name__ == "__main__":
    unittest.main()
