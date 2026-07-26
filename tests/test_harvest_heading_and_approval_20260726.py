"""Four fail-opens found by the 2026-07-26 Opus adversary round, all verified
against the shipped code before this file existed:

  1. "## Lessons (private-only)" — the heading regex STRIPPED the annotation
     before matching the section key, so the one marker that covers a whole
     section in a single stroke was discarded, every row under it harvested,
     and DROPPED stayed empty. The dash/bracket forms leaked the same way.
  2. A bare "No" / "Not approved" in the template's own "Approved For Reuse?"
     column matched no marker, so the operator's refusal was ignored and the
     row staged with zero signal.
  3. "Rejected--", "Rejected->" and "*Rejected*->" (emphasis stripped, then
     glued) never matched, because `(?![-\\w])` made `-`/`_` characters that
     could NEVER follow a marker — while "Rejected — ..." was dropped.
  4. "_do not harvest_" in a status column bypassed every rule: the
     underscore wrapper defeated the word boundary, while `*do not harvest*`
     was caught.

Every test asserts on BEHAVIOUR — what bullets_by_section yields and what
lands in DROPPED — never on source text or a regex in isolation.
"""

import importlib.util
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HARVEST = os.path.join(ROOT, "scripts", "harvest.py")


def _load():
    spec = importlib.util.spec_from_file_location("harvest_heading_approval", HARVEST)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


harvest = _load()


def _scan(markdown):
    """(harvested texts, dropped rows) via the real entry point."""
    del harvest.DROPPED[:]
    got = [text for _typ, text in harvest.bullets_by_section(markdown)]
    return got, list(harvest.DROPPED)


APPROVAL_TABLE = (
    "## Lessons\n"
    "| Lesson | Evidence | Approved For Reuse? |\n"
    "|---|---|---|\n"
    "| %s | intake call | %s |\n"
)


class ExcludedSectionHeading(unittest.TestCase):
    """An operator marking a whole section must always win, out loud."""

    SECRETIVE = "- The client roster lives only in the private CRM\n"

    def test_parenthesised_private_only_excludes_the_section(self):
        for head in ("## Lessons (private-only)",
                     "## Lessons (DO NOT HARVEST)",
                     "## Lessons (rejected — superseded)",
                     "## Lessons — private-only",
                     "## Lessons: do not harvest",
                     "## Lessons [REJECTED]"):
            with self.subTest(head=head):
                got, dropped = _scan(head + "\n" + self.SECRETIVE)
                self.assertEqual(got, [], f"{head!r} harvested its rows")
                self.assertEqual(len(dropped), 1,
                                 f"{head!r} dropped silently, no audit trail")
                self.assertIn("client roster", dropped[0])

    def test_table_rows_under_an_excluded_heading_are_dropped_and_recorded(self):
        md = ("## Lessons (do not harvest)\n"
              "| Lesson | Evidence |\n"
              "|---|---|\n"
              "| The vendor contract number is 8841 | email thread |\n")
        got, dropped = _scan(md)
        self.assertEqual(got, [])
        self.assertEqual(len(dropped), 1)
        self.assertIn("vendor contract", dropped[0])

    def test_benign_heading_annotations_still_harvest(self):
        for head in ("## Lessons (wave 2)", "## Lessons (2026-07-25)",
                     "## Lessons"):
            with self.subTest(head=head):
                got, dropped = _scan(
                    head + "\n- Verify a fix by reverting it first\n")
                self.assertEqual(len(got), 1, f"{head!r} wrongly excluded")
                self.assertEqual(dropped, [])

    def test_an_unrecognised_heading_stays_a_non_candidate_not_a_drop(self):
        # Rows under "## Random notes" were never candidates; recording them
        # in DROPPED would inflate the audit trail with non-events.
        got, dropped = _scan("## Random notes (private-only)\n- whatever\n")
        self.assertEqual(got, [])
        self.assertEqual(dropped, [])


class ApprovalColumnRefusal(unittest.TestCase):
    """The template's own reuse gate must be honoured, by header not shape."""

    def test_explicit_refusals_drop_the_row_and_are_recorded(self):
        for verdict in ("No", "no", "Not approved", "Not for reuse", "Never",
                        "No — pending legal"):
            with self.subTest(verdict=verdict):
                got, dropped = _scan(APPROVAL_TABLE % (
                    "The client home addresses live in the CRM", verdict))
                self.assertEqual(got, [], f"{verdict!r} was ignored")
                self.assertEqual(len(dropped), 1)

    def test_affirmative_and_blank_cells_still_harvest(self):
        for verdict in ("Yes", "yes", ""):
            with self.subTest(verdict=verdict):
                got, dropped = _scan(APPROVAL_TABLE % (
                    "Prefer the Solo tier before escalating", verdict))
                self.assertEqual(len(got), 1, f"{verdict!r} wrongly dropped")
                self.assertEqual(dropped, [])

    def test_no_under_an_unrelated_column_is_ordinary_prose(self):
        md = ("## Lessons\n"
              "| Lesson | Uses numpy? |\n"
              "|---|---|\n"
              "| Prefer the stdlib on the CI floor | No |\n")
        got, dropped = _scan(md)
        self.assertEqual(len(got), 1, "a non-approval 'No' was treated as a verdict")
        self.assertEqual(dropped, [])

    def test_a_headerless_table_keeps_the_old_behaviour(self):
        md = ("## Lessons\n"
              "| Prefer the stdlib on the CI floor | No |\n")
        got, dropped = _scan(md)
        self.assertEqual(len(got), 1)
        self.assertEqual(dropped, [])


class GluedAnnotations(unittest.TestCase):
    """`-`/`_` right after the marker is an annotation, not a shield."""

    SECRET_TAIL = "the transcript holds the client auth token"

    def test_glued_punctuation_after_the_marker_drops(self):
        for bullet in ("Rejected-- %s", "Rejected-> %s", "Rejected_ %s",
                       "*Rejected*-> %s", "**Rejected**-> %s",
                       "`Rejected`-- %s", "Rejected-dupe of lesson 12: %s",
                       "Rejected-because it names a client: %s"):
            with self.subTest(bullet=bullet.split(" ")[0]):
                got, dropped = _scan(
                    "## Lessons\n- " + (bullet % self.SECRET_TAIL) + "\n")
                self.assertEqual(got, [], f"{bullet!r} harvested through")
                self.assertEqual(len(dropped), 1)

    def test_glued_forms_in_a_table_lesson_cell_drop_too(self):
        for cell in ("*Rejected*-> %s", "`Rejected`-- %s", "**Rejected**-> %s"):
            with self.subTest(cell=cell.split(" ")[0]):
                got, dropped = _scan(
                    "## Lessons\n| " + (cell % self.SECRET_TAIL) + " | ok |\n")
                self.assertEqual(got, [], f"{cell!r} harvested through")
                self.assertEqual(len(dropped), 1)

    def test_compound_words_on_the_bare_verb_stay_prose(self):
        for bullet in ("Reject-first workflow beats cleanup later",
                       "Rejection-driven development is a smell to log",
                       "Reject stale locks before retrying"):
            with self.subTest(bullet=bullet.split(" ")[0]):
                got, dropped = _scan("## Lessons\n- " + bullet + "\n")
                self.assertEqual(len(got), 1, f"{bullet!r} wrongly dropped")
                self.assertEqual(dropped, [])


class UnderscoreEmphasis(unittest.TestCase):
    """Markdown italics must not hide a directive from the status rule."""

    def test_underscore_wrapped_directives_in_a_status_cell_drop(self):
        for status in ("_do not harvest_", "__do not harvest__",
                       "_private-only_", "_Rejected_"):
            with self.subTest(status=status):
                got, dropped = _scan(
                    "## Lessons\n| The login failed with the live key | %s |\n"
                    % status)
                self.assertEqual(got, [], f"{status!r} bypassed the verdict rule")
                self.assertEqual(len(dropped), 1)

    def test_snake_case_words_are_not_torn_apart(self):
        # do_not_harvest_this_row must still read as the directive it is...
        got, dropped = _scan("## Lessons\n- do_not_harvest_this_row\n")
        self.assertEqual(got, [])
        self.assertEqual(len(dropped), 1)
        # ...and ordinary snake_case prose must not be reshaped into a match.
        got, dropped = _scan(
            "## Lessons\n- Name scratch runs _selftest_tmp_run style, "
            "with a leading underscore\n")
        self.assertEqual(len(got), 1)
        self.assertEqual(dropped, [])


if __name__ == "__main__":
    unittest.main()
