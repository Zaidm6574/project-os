"""The rejection-annotation rule was an ENUMERATION, and enumerations leak.

Adversarial verify of the 2026-07-26 fix round. The free-prose rule accepted a
hand-listed set of characters after the marker; that list grew twice and was
STILL incomplete, so each of these harvested straight into a PUBLIC brain with
nothing in DROPPED:

  * "Rejected... <reason>" written with the U+2026 ellipsis every
    smart-punctuation editor substitutes for three dots -- the single most
    plausible remaining member of the family the list was built to catch.
  * "Rejected* ...", "Rejected -> ...", "Rejected = ...", "Rejected> ..." and
    friends: any punctuation nobody had thought to type yet.
  * `do_not_harvest_this_row`, where the directive's trailing standalone-word
    boundary treated the joining `_` as a word character and refused to fire.

The fix defines an annotation by what it IS (a character that is neither word
nor whitespace) instead of by a list, and lets a directive keep a `-`/`_`
joined continuation. Two things must NOT be paid for that:

  * a possessive -- "Rejection's reason must be written down" is prose;
  * markdown emphasis -- "*Rejection* criteria belong in the rubric" is prose,
    and the star is not an annotation.

Every assertion is on BEHAVIOUR: what the extractors yield, what DROPPED
records, and what a real `harvest.py scan` subprocess writes and prints. None
of it reads the module's source, a regex object, or a parameter name. The
keep-direction is asserted as hard as the drop-direction, so a filter that
simply drops everything fails this file.
"""

import glob
import importlib.util
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
    spec = importlib.util.spec_from_file_location("harvest_annotation", HARVEST)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


harvest = _load()

TOKEN = "sk-live-abc123xyz"


def _bullet(text):
    """Lesson texts surviving the filter when the row is a BULLET."""
    return [t for _typ, t in
            harvest.bullets_by_section("## Lessons\n- %s\n" % text)]


def _lesson_cell(text):
    """Same, when the row is a TABLE and the text is the lesson cell."""
    table = ("## Lessons\n| Lesson | Status |\n| --- | --- |\n| %s | ok |\n"
             % text)
    return [t for _typ, t in harvest.bullets_by_section(table)]


class UnlistedAnnotationPunctuationIsStillAnAnnotation(unittest.TestCase):
    """The leak was "whatever character the list forgot"."""

    LEAKED = [
        u"Rejected… the transcript holds the client token " + TOKEN,
        "Rejected* the transcript holds the client token " + TOKEN,
        u"Rejected → the transcript holds the client token " + TOKEN,
        "Rejected = the transcript holds the client token " + TOKEN,
        u"Rejected» the transcript holds the client token " + TOKEN,
        "Rejected/ the transcript holds the client token " + TOKEN,
        "Rejected> the transcript holds the client token " + TOKEN,
        "Rejected~ the transcript holds the client token " + TOKEN,
        u"Rejection… the lesson names the paying client outright",
    ]

    def setUp(self):
        del harvest.DROPPED[:]

    def test_no_unlisted_annotation_harvests_as_a_bullet(self):
        for verdict in self.LEAKED:
            with self.subTest(verdict=verdict):
                del harvest.DROPPED[:]
                self.assertEqual(
                    _bullet(verdict), [],
                    "verdict harvested into the public brain: %r" % verdict)

    def test_no_unlisted_annotation_harvests_as_a_tables_lesson_cell(self):
        for verdict in self.LEAKED:
            with self.subTest(verdict=verdict):
                del harvest.DROPPED[:]
                self.assertEqual(
                    _lesson_cell(verdict), [],
                    "first-cell verdict harvested through: %r" % verdict)

    def test_the_drop_is_recorded_so_the_operator_sees_it(self):
        self.assertEqual(
            _bullet(u"Rejected… it holds the client token " + TOKEN), [])
        self.assertEqual(len(harvest.DROPPED), 1, harvest.DROPPED)
        self.assertIn("Rejected", harvest.DROPPED[0])


class ADirectiveSurvivesBeingJoinedToTheNextWord(unittest.TestCase):
    """`do_not_harvest_this_row` is the plainest directive there is."""

    JOINED = [
        "do_not_harvest_this_row: it holds the client token " + TOKEN,
        "do-not-harvest-this-row, it holds the client token " + TOKEN,
        "private_only_row about the customer's billing dispute",
        "The evidence is private-only-until-signoff and names the customer",
    ]

    def setUp(self):
        del harvest.DROPPED[:]

    def test_joined_directives_are_dropped_as_bullets(self):
        for bullet in self.JOINED:
            with self.subTest(bullet=bullet):
                del harvest.DROPPED[:]
                self.assertEqual(_bullet(bullet), [], bullet)
                self.assertEqual(len(harvest.DROPPED), 1, harvest.DROPPED)

    def test_a_joined_directive_in_a_status_column_drops_the_row(self):
        table = ("## Lessons\n| Lesson | Status |\n| --- | --- |\n"
                 "| The auth retry needs a jittered backoff | "
                 "do_not_harvest_this_row |\n")
        self.assertEqual(
            [t for _typ, t in harvest.bullets_by_section(table)], [],
            "a directive in the status column did not drop the row")

    def test_a_joined_directive_drops_an_eval_log_row(self):
        row = "| do_not_harvest_this_row | the auth failed with " + TOKEN + " |"
        self.assertEqual(list(harvest.eval_log_candidates(row)), [], row)
        self.assertEqual(len(harvest.DROPPED), 1, harvest.DROPPED)

    def test_a_letter_joined_tail_is_not_a_directive(self):
        """The standalone-word property survives: only -/_ continue it."""
        lesson = ("Do not harvesting the queue twice, it double-counts the "
                  "retries")
        self.assertEqual(_bullet(lesson), [lesson],
                         "a real lesson was dropped as a directive")


class ProseThatMerelyLooksAnnotatedStillHarvests(unittest.TestCase):
    """The generalised rule must not be bought with a wave of false drops."""

    KEPT = [
        "Rejection's reason must be written down, not implied by silence",
        "*Rejection* criteria belong in the rubric, not the reviewer's head",
        "**Rejection** criteria belong in the rubric, not the reviewer's head",
        "`Rejection` criteria belong in the rubric, not the reviewer's head",
        "Rejects are logged with a reason so the filter stays auditable",
        "Reject-first workflow keeps the review queue short",
    ]

    def setUp(self):
        del harvest.DROPPED[:]

    def test_real_lessons_survive_as_bullets(self):
        for lesson in self.KEPT:
            with self.subTest(lesson=lesson):
                del harvest.DROPPED[:]
                self.assertEqual(len(_bullet(lesson)), 1,
                                 "a real lesson was dropped: %r" % lesson)
                self.assertEqual(harvest.DROPPED, [])

    def test_emphasis_in_a_tables_lesson_cell_is_not_an_annotation(self):
        """The table cell is checked RAW, so its emphasis had to be handled."""
        for lesson in self.KEPT:
            with self.subTest(lesson=lesson):
                del harvest.DROPPED[:]
                self.assertEqual(len(_lesson_cell(lesson)), 1,
                                 "a real table lesson was dropped: %r" % lesson)


class ScanEndToEndNeverStagesAnUnlistedAnnotation(unittest.TestCase):
    """Driven as a real subprocess over a throwaway tree."""

    def _scan(self, harvest_md):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        os.makedirs(os.path.join(tmp, "scripts"))
        shutil.copy(HARVEST, os.path.join(tmp, "scripts", "harvest.py"))
        run = os.path.join(tmp, "runs", "sandbox")
        os.makedirs(run)
        with open(os.path.join(run, "19-memory-harvest.md"), "w",
                  encoding="utf-8") as f:
            f.write(harvest_md)
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
        for path in glob.glob(
                os.path.join(tmp, "blackboard", "packets", "*.jsonl")):
            with open(path, encoding="utf-8") as f:
                staged += [json.loads(line) for line in f if line.strip()]
        return proc.stdout.decode(), staged

    def test_an_ellipsis_verdict_is_filtered_and_reported(self):
        out, staged = self._scan(
            u"## Lessons\n- Rejected… the transcript holds the client "
            u"token " + TOKEN + u"\n")
        self.assertEqual([o["text"] for o in staged], [],
                         "an ellipsis verdict was staged: %r" % staged)
        self.assertIn("filtered 1 row(s)", out,
                      "the row was filtered silently: %r" % out)

    def test_the_same_scan_still_stages_a_real_lesson(self):
        out, staged = self._scan(
            "## Lessons\n"
            "- Rejection's reason must be written down, not implied\n"
            u"- Rejected… the transcript holds the client token "
            + TOKEN + u"\n")
        self.assertEqual(
            [o["text"] for o in staged],
            ["Rejection's reason must be written down, not implied"], out)


if __name__ == "__main__":
    unittest.main()
