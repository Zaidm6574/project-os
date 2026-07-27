"""A TRAILING rejection annotation was ignored on free prose (audit 2026-07-27).

Every earlier rewrite of this filter judged free prose (a bullet, a table's
lesson cell, an eval-log row) from its START: a marker had to open the text and
be followed by annotation punctuation or a continuation word. So the operator
idiom that puts the verdict where a reader expects it -- at the END of the line --
walked straight past every rule:

    - the client staging token lives in the ops vault [Rejected]
    - the client staging token lives in the ops vault (Private-only)
    - the client staging token lives in the ops vault -- Rejected

All three harvested with DROPPED EMPTY, so `cmd_scan`'s "filtered N row(s)" line
never printed either: the operator's refusal was discarded with zero signal and
the text was staged into blackboard/packets/ for `apply`.

The internal inconsistency is the tell. The SAME trailing annotation is already
honoured on a HEADING ("## Lessons [Rejected]", "## Lessons -- Rejected",
"## Lessons **Rejected**" all exclude their section) and on a status COLUMN.
Only free prose read it as ordinary words.

Both directions are pinned, and both were mutation-verified against the unfixed
file before this test was committed:

  * TrailingVerdictsNeverHarvest fails on HEAD-without-the-fix (every shape
    harvests, DROPPED empty).
  * OrdinaryProseAboutRejectionStillHarvests fails if the trailer rule is made
    greedy -- it is the over-exclusion side that three earlier rounds of this
    file opened while closing the leak.

Every assertion is on BEHAVIOUR -- what the extractors yield, what DROPPED
records, what a real `harvest.py scan` stages and prints -- never on the
module's source text or on a regex in isolation. The status vocabulary and the
table header are read from the SHIPPED template at runtime, never invented.
"""

import glob
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HARVEST = os.path.join(ROOT, "scripts", "harvest.py")
TEMPLATE = os.path.join(ROOT, "blackboard-template", "19-memory-harvest.md")

SECRET = "the client staging token lives in the ops vault at path X"
PUBLIC = "Verify a fix by reverting it and watching the test fail first"


def _load():
    spec = importlib.util.spec_from_file_location("harvest_trailing", HARVEST)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


harvest = _load()


def _scan(markdown):
    """(harvested texts, dropped rows) through the real entry point."""
    del harvest.DROPPED[:]
    got = [text for _typ, text in harvest.bullets_by_section(markdown)]
    return got, list(harvest.DROPPED)


def _lessons_table_header():
    """The shipped Lessons table header row, read from disk."""
    with open(TEMPLATE, encoding="utf-8") as fh:
        return next(ln.strip() for ln in fh if ln.strip().startswith("| Lesson |"))


def _table(lesson_cell):
    """The shipped Lessons table carrying one row whose FIRST cell is prose."""
    header = _lessons_table_header()
    width = len(header.strip().strip("|").split("|"))
    sep = "|" + "---|" * width
    row = [lesson_cell] + ["fill"] * (width - 2) + ["Yes"]
    return "## Lessons\n%s\n%s\n| %s |\n" % (header, sep, " | ".join(row))


# The annotation shapes an operator actually writes at the END of a line. The
# marker words are the template's own status vocabulary ("Rejected",
# "Private-only") plus the directive the module honours anywhere.
TRAILERS = [
    "%s [Rejected]",
    "%s (Rejected)",
    "%s [REJECTED]",
    "%s (Private-only)",
    "%s [private-only]",
    "%s -- Rejected",
    u"%s — Rejected",          # em dash
    u"%s – Rejected",          # en dash
    "%s **Rejected**",
    "%s *Rejected*",
    "%s `Rejected`",
    "%s [do not harvest]",
    "%s -- Rejected: holds a client token",
    "%s (rejected, superseded)",
]


class TemplateGrounding(unittest.TestCase):
    """The fixtures below only mean something if the template still ships."""

    def test_the_shipped_template_still_carries_the_status_vocabulary(self):
        with open(TEMPLATE, encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("Rejected", body)
        self.assertIn("Private-only", body)
        self.assertTrue(_lessons_table_header().startswith("| Lesson |"))


class TrailingVerdictsNeverHarvest(unittest.TestCase):
    """A verdict at the END of a bullet is the same verdict as one in front."""

    def test_a_trailing_annotation_on_a_bullet_drops_the_row(self):
        for shape in TRAILERS:
            bullet = shape % SECRET
            with self.subTest(bullet=bullet):
                got, dropped = _scan("## Lessons\n- %s\n" % bullet)
                self.assertEqual(
                    got, [],
                    "a refused lesson was harvested: %r" % bullet)
                self.assertEqual(
                    len(dropped), 1,
                    "%r was refused SILENTLY (nothing in DROPPED, so "
                    "cmd_scan's 'filtered N row(s)' line never prints)" % bullet)
                self.assertIn("ops vault", dropped[0])

    def test_a_trailing_annotation_in_a_table_lesson_cell_drops_the_row(self):
        for shape in TRAILERS:
            cell = shape % SECRET
            with self.subTest(cell=cell):
                got, dropped = _scan(_table(cell))
                self.assertEqual(
                    got, [], "a refused table row was harvested: %r" % cell)
                self.assertEqual(len(dropped), 1,
                                 "%r was refused silently" % cell)

    def test_a_trailing_annotation_on_an_eval_log_row_drops_the_row(self):
        """The fallback path reads the same free prose out of cells[0]."""
        for shape in ("%s [Rejected]", "%s -- Rejected", "%s (Private-only)"):
            row = "| %s | notes |\n" % (shape % (SECRET + " and the retry failed"))
            with self.subTest(row=row):
                del harvest.DROPPED[:]
                got = list(harvest.eval_log_candidates(row))
                self.assertEqual(
                    got, [],
                    "a refused eval-log row was harvested: %r" % row)
                self.assertEqual(len(harvest.DROPPED), 1,
                                 "the eval-log path refused it silently")


class OrdinaryProseAboutRejectionStillHarvests(unittest.TestCase):
    """The over-exclusion side. "Drop everything" must not pass this file."""

    KEEPS = [
        "Rejection criteria belong in the rubric, not in the harvest",
        "Reject-first workflow keeps the review queue short",
        "The plan was rejected by the board, so we rewrote it",
        "Rejects are logged with a reason and a timestamp",
        # a trailing annotation slot that is NOT a verdict
        "Verify a fix by reverting it first (audit 2026-07-26)",
        "Verify both directions -- a fix in one direction opens its mirror",
        u"Archive raw generative masters — renaming them by content is free",
        "Never trust a green suite **alone**",
        "Rejection-driven development notes stay in the rubric",
        "Screen vendors early -- reject stale locks before retrying",
        u"Keep the rubric short — rejection is cheap, rework is not",
        # The annotation slot of free prose is the TRAILER and only the
        # trailer. Reading EVERY bracketed / dash-separated span the way the
        # heading rule does would manufacture a bare verdict out of the middle
        # of a sentence and drop a real lesson -- the over-exclusion this file
        # has opened three times while closing a leak.
        "Mark a draft (Rejected) only after the board has voted on it",
        "Use -- Rejected -- as the status token, never a bare no",
        u"Write the status — Rejected — in the column, not in the lesson",
    ]

    def test_ordinary_prose_bullets_still_harvest(self):
        for lesson in self.KEEPS:
            with self.subTest(lesson=lesson):
                got, dropped = _scan("## Lessons\n- %s\n" % lesson)
                self.assertEqual(len(got), 1,
                                 "a real lesson was dropped: %r" % lesson)
                self.assertEqual(dropped, [])

    def test_ordinary_prose_table_cells_still_harvest(self):
        for lesson in self.KEEPS:
            with self.subTest(lesson=lesson):
                got, dropped = _scan(_table(lesson))
                self.assertEqual(len(got), 1,
                                 "a real table lesson was dropped: %r" % lesson)
                self.assertEqual(dropped, [])

    def test_a_mention_only_heading_still_harvests_its_section(self):
        """The heading rule shares the free-prose rule; it must not tighten."""
        for head in (u"## Lessons — why rejected ideas still teach us",
                     "## Lessons (what rejected drafts teach)",
                     "## Lessons from rejected prototypes",
                     u"## Lessons — rejection-driven development notes"):
            with self.subTest(head=head):
                got, dropped = _scan(head + "\n- %s\n" % PUBLIC)
                self.assertEqual(got, [PUBLIC],
                                 "%r excluded its section" % head)
                self.assertEqual(dropped, [])


class ScanEndToEndNeverStagesATrailingVerdict(unittest.TestCase):
    """Driven as a real `harvest.py scan` subprocess over a throwaway tree."""

    def _run(self, harvest_md):
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
        for path in glob.glob(os.path.join(tmp, "blackboard", "packets",
                                           "*.jsonl")):
            with open(path, encoding="utf-8") as f:
                staged += [json.loads(line) for line in f if line.strip()]
        return proc.stdout.decode(), staged

    def test_the_refused_text_never_reaches_the_proposals_file(self):
        out, staged = self._run(
            "## Lessons\n- %s [Rejected]\n- %s\n" % (SECRET, PUBLIC))
        self.assertEqual(
            [o["text"] for o in staged], [PUBLIC],
            "a trailing-marker lesson was staged for apply: %r" % staged)
        self.assertIn("filtered 1 row(s)", out,
                      "the refusal was invisible to the operator: %r" % out)
        self.assertNotIn("ops vault", json.dumps(staged))


if __name__ == "__main__":
    unittest.main()
