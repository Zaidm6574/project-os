"""A rejection's REASON is usually introduced by a causal word, not punctuation.

Three defects, all verified against the shipped code before this file existed:

  1. "Rejected because the transcript holds the client auth token" (and the
     as/for/due/since variants) matched no rule and was staged into the PUBLIC
     brain with no DROPPED entry. Content is often rejected precisely BECAUSE
     it is private, so this direction has to fail closed.
  2. Imperative safeguard bullets -- "Reject stale locks before retrying" --
     were dropped as verdicts, because the continued-verdict rule accepted the
     bare verb "reject" as a marker.
  3. The DROPPED audit report was printed only when something fresh was staged,
     so a scan whose rows were ALL filtered printed a factually false
     "no harvest sources found" and reported none of the filtering.

Every test asserts on BEHAVIOUR -- what bullets_by_section yields, and what a
real `harvest.py scan` prints on stdout -- never on the module's source text,
a regex in isolation, or a parameter name.
"""

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HARVEST = os.path.join(ROOT, "scripts", "harvest.py")


def _load():
    spec = importlib.util.spec_from_file_location("harvest_reason_verdicts", HARVEST)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


harvest = _load()


def _harvested(markdown):
    """Lesson texts that survive the reject filter, via the real entry point."""
    return [text for _typ, text in harvest.bullets_by_section(markdown)]


class ReasonVerdictsNeverReachThePublicBrain(unittest.TestCase):
    """"Rejected <causal word> ..." is a verdict, and its reason is private."""

    LEAKED = [
        "Rejected because the transcript holds the client auth token",
        "Rejected as duplicate of the private credential lesson",
        "Rejected for reuse of the private prompt",
        "Rejected due to private client data in the evidence column",
        "Rejected since the run leaked credentials into the log",
        "Rejection because the board never signed this off",
        "Rejected as internal-only until the client approves it",
    ]

    def setUp(self):
        del harvest.DROPPED[:]

    def test_each_reason_verdict_is_dropped(self):
        for verdict in self.LEAKED:
            with self.subTest(verdict=verdict):
                del harvest.DROPPED[:]
                self.assertEqual(
                    _harvested("## Lessons\n- %s\n" % verdict), [],
                    "verdict harvested into the public brain: %r" % verdict)

    def test_a_dropped_reason_verdict_is_recorded_for_the_report(self):
        """A silent drop is as bad as a leak: the operator must see it."""
        self.assertEqual(
            _harvested("## Lessons\n- Rejected because it holds a token\n"), [])
        self.assertEqual(len(harvest.DROPPED), 1, harvest.DROPPED)
        self.assertIn("Rejected because", harvest.DROPPED[0])

    def test_a_reason_verdict_in_a_tables_lesson_cell_is_dropped(self):
        table = ("## Lessons\n"
                 "| Lesson | Notes |\n"
                 "| --- | --- |\n"
                 "| Rejected because it names the client | notes |\n")
        self.assertEqual(_harvested(table), [],
                         "a first-cell reason verdict harvested through")


class ImperativeSafeguardsAreNotVerdicts(unittest.TestCase):
    """"Reject X ..." is an instruction, and instructions are the lessons."""

    KEPT = [
        "Reject stale locks before retrying instead of trusting them",
        "Reject duplicate submissions at intake, not downstream",
        "Reject by default and allowlist explicitly, never the reverse",
        "Reject pending migrations at boot so a half-applied schema is loud",
        "Reject obsolete tokens on read, not only on write",
    ]

    def setUp(self):
        del harvest.DROPPED[:]

    def test_imperative_safeguard_bullets_are_still_harvested(self):
        for lesson in self.KEPT:
            with self.subTest(lesson=lesson):
                del harvest.DROPPED[:]
                self.assertEqual(
                    _harvested("## Next-Kickoff Safeguards\n- %s\n" % lesson),
                    [lesson],
                    "a real safeguard was dropped as a verdict: %r" % lesson)


class EarlierRulesAreNotWeakened(unittest.TestCase):
    """Nothing this file fixes may loosen what previous rounds got right."""

    def setUp(self):
        del harvest.DROPPED[:]

    def test_word_form_verdicts_from_the_previous_round_still_drop(self):
        for verdict in ("Rejected pending review", "Rejected dupe of lesson 12",
                        "Rejection awaiting board sign-off",
                        "Rejected superseded by the 07-25 fix"):
            with self.subTest(verdict=verdict):
                del harvest.DROPPED[:]
                self.assertEqual(
                    _harvested("## Lessons\n- %s\n" % verdict), [], verdict)

    def test_directives_still_drop_even_with_the_bare_verb(self):
        """Narrowing the continued rule must not un-catch a directive."""
        for bullet in ("Private-only because it names the client",
                       "Do not harvest as this holds a token"):
            with self.subTest(bullet=bullet):
                del harvest.DROPPED[:]
                self.assertEqual(
                    _harvested("## Lessons\n- %s\n" % bullet), [], bullet)

    def test_ordinary_prose_about_rejection_still_survives(self):
        for lesson in ("Rejection criteria belong in the rubric",
                       "Reject-first workflow keeps the queue short",
                       "The plan was rejected by the board, so we rewrote it",
                       "Rejects are logged with a reason"):
            with self.subTest(lesson=lesson):
                del harvest.DROPPED[:]
                self.assertEqual(
                    _harvested("## Lessons\n- %s\n" % lesson), [lesson], lesson)

    def test_a_status_column_verdict_still_drops_the_row(self):
        table = ("## Lessons\n"
                 "| Lesson | Approved For Reuse? |\n"
                 "| --- | --- |\n"
                 "| a lesson that must stay internal | No - rejected for reuse |\n")
        self.assertEqual(_harvested(table), [])


class ScanAlwaysReportsWhatItFiltered(unittest.TestCase):
    """cmd_scan's stdout, driven as a real subprocess in a temp tree."""

    def _scan(self, harvest_md, brain_lines=""):
        """Run `harvest.py scan` over a throwaway tree. Returns (stdout, dir)."""
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        os.makedirs(os.path.join(tmp, "scripts"))
        shutil.copy(HARVEST, os.path.join(tmp, "scripts", "harvest.py"))
        run = os.path.join(tmp, "runs", "sandbox")
        os.makedirs(run)
        if harvest_md is not None:
            with open(os.path.join(run, "19-memory-harvest.md"), "w",
                      encoding="utf-8") as f:
                f.write(harvest_md)
        brain = os.path.join(tmp, "brain.jsonl")
        with open(brain, "w", encoding="utf-8") as f:
            f.write(brain_lines)
        env = dict(os.environ, PROJECT_OS_SHARED_BRAIN=brain)
        proc = subprocess.run(
            [sys.executable, os.path.join(tmp, "scripts", "harvest.py"),
             "scan", "sandbox"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
        self.assertEqual(proc.returncode, 0, proc.stdout.decode())
        return proc.stdout.decode(), run

    ALL_FILTERED = ("## Lessons\n"
                    "- Rejected: dupe of lesson 3\n"
                    "- Rejected because the transcript holds the client token\n"
                    "- Private-only: the client SID is in the transcript\n")

    def test_an_all_filtered_scan_reports_the_filtering(self):
        out, run = self._scan(self.ALL_FILTERED)
        self.assertIn("filtered 3 row(s)", out,
                      "the DROPPED report never printed: %r" % out)
        self.assertIn("client SID", out, "the filtered rows were not listed")
        self.assertTrue(os.path.isfile(os.path.join(run, ".harvested")),
                        "a fully-rejected harvest is still complete")

    def test_an_all_filtered_scan_does_not_claim_there_were_no_sources(self):
        out, _ = self._scan(self.ALL_FILTERED)
        self.assertNotIn("no harvest sources found", out,
                         "scan lied about why nothing was staged: %r" % out)

    def test_a_truly_empty_run_still_says_no_sources_were_found(self):
        """The honest message must survive; don't just delete it."""
        out, _ = self._scan("## Unrelated Heading\n\nnothing to see\n")
        self.assertIn("no harvest sources found", out, out)
        self.assertNotIn("filtered", out, out)

    def test_the_all_dupes_path_also_reports_the_filtering(self):
        md = ("## Lessons\n"
              "- Rejected: this row holds a private client token\n"
              "- Verify a fix by reverting it and watching the test fail\n")
        brain = ('{"text": "Verify a fix by reverting it and watching the '
                 'test fail, always"}\n')
        out, _ = self._scan(md, brain_lines=brain)
        self.assertIn("already in the brain", out, out)
        self.assertIn("filtered 1 row(s)", out,
                      "the DROPPED report is dead on the all-dupes path: %r" % out)

    def test_a_scan_that_stages_something_still_reports_the_filtering(self):
        md = ("## Lessons\n"
              "- Rejected because this row names the client\n"
              "- Verify a fix by reverting it and watching the test fail\n")
        out, _ = self._scan(md)
        self.assertIn("staged 1 new", out, out)
        self.assertIn("filtered 1 row(s)", out, out)


if __name__ == "__main__":
    unittest.main()
