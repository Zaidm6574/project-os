"""Heading-exclusion overreach (audit 2026-07-26).

The heading rule ran the permissive status-cell regex over the WHOLE heading,
so a heading that merely MENTIONS rejection — "## Lessons — why rejected ideas
still teach us" — was treated as a rejected-content marker and its entire
section silently vanished from the harvest.

Both directions are pinned here, and both were mutation-verified in a scratch
checkout before this file was committed:

  * MentionOnlyHeadingsHarvest fails against HEAD-without-the-refinement
    (the old whole-line rule excludes every fixture).
  * VerdictHeadingsStillExcluded fails when the ORIGINAL heading-exclusion
    hunk (`if cur and ...: cur = _EXCLUDED`) is reverted entirely, so it pins
    the rule itself, not just the refinement.

Every section label used below is read from the SHIPPED template
(blackboard-template/19-memory-harvest.md) at runtime, never invented, and
every test asserts on behaviour — what bullets_by_section yields and what
lands in DROPPED — never on source text.
"""

import importlib.util
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HARVEST = os.path.join(ROOT, "scripts", "harvest.py")
TEMPLATE = os.path.join(ROOT, "blackboard-template", "19-memory-harvest.md")


def _load():
    spec = importlib.util.spec_from_file_location("harvest_heading_overreach", HARVEST)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


harvest = _load()


def _scan(markdown):
    """(harvested texts, dropped rows) via the real entry point."""
    del harvest.DROPPED[:]
    got = [text for _typ, text in harvest.bullets_by_section(markdown)]
    return got, list(harvest.DROPPED)


def _template_section_labels():
    """The template's own harvestable section labels, read from disk.

    Filtered through the module's SECTION_TYPES so each label is one the
    scanner actually recognises — a heading it does NOT recognise is a
    non-candidate and can never demonstrate either direction.
    """
    labels = []
    with open(TEMPLATE, encoding="utf-8") as fh:
        for line in fh:
            m = re.match(r"^##\s+(.+?)\s*$", line)
            if not m:
                continue
            key = m.group(1).strip().lower()
            if any(key.startswith(k) for k in harvest.SECTION_TYPES):
                labels.append(m.group(1).strip())
    return labels


class TemplateGrounding(unittest.TestCase):
    """The fixtures below only mean something if the template still ships."""

    def test_the_shipped_template_still_carries_the_labels_and_vocabulary(self):
        labels = _template_section_labels()
        self.assertIn("Lessons", labels,
                      "the shipped template lost its Lessons section")
        self.assertGreaterEqual(len(labels), 3,
                                "the shipped template lost its harvest sections")
        with open(TEMPLATE, encoding="utf-8") as fh:
            body = fh.read()
        # "Rejected" and "Private-only" are the template's own status values
        # (its "Status values:" line); the verdict fixtures below reuse them.
        self.assertIn("Rejected", body)
        self.assertIn("Private-only", body)


class MentionOnlyHeadingsHarvest(unittest.TestCase):
    """A heading that mentions rejection in prose is not a verdict."""

    KEEP = "- Verify a fix by reverting it first\n"

    def test_mention_only_headings_harvest_their_rows(self):
        # The first shape is the audit finding's own example, applied to the
        # shipped "Lessons" label; the rest vary the position and the
        # punctuation the mention hides behind.
        heads = ["## Lessons — why rejected ideas still teach us",
                 "## Lessons from rejected prototypes",
                 "## Lessons (what rejected drafts teach)",
                 "## Lessons — rejected ideas still teach us"]
        # The same mention on every OTHER shipped label — "Next-Kickoff
        # Safeguards — screen rejected vendors early" and friends.
        for label in _template_section_labels():
            if label != "Lessons":
                heads.append("## %s — screen rejected vendors early" % label)
        for head in heads:
            with self.subTest(head=head):
                got, dropped = _scan(head + "\n" + self.KEEP)
                self.assertEqual(
                    len(got), 1,
                    "%r was treated as a rejected-content marker and its "
                    "section was excluded" % head)
                self.assertEqual(dropped, [])

    def test_a_mention_heading_over_the_shipped_lessons_table_harvests(self):
        # The table header row comes from the template on disk, so the
        # approval-column machinery under a mention-only heading is exercised
        # exactly as it ships.
        with open(TEMPLATE, encoding="utf-8") as fh:
            header = next(ln.strip() for ln in fh
                          if ln.strip().startswith("| Lesson |"))
        width = len(header.strip().strip("|").split("|"))
        sep = "|" + "---|" * width
        row = ["Archive raw generative masters by content"] \
            + ["fill"] * (width - 2) + ["Yes"]
        md = ("## Lessons — why rejected ideas still teach us\n"
              "%s\n%s\n| %s |\n" % (header, sep, " | ".join(row)))
        got, dropped = _scan(md)
        self.assertEqual(len(got), 1,
                         "a mention-only heading excluded the shipped table")
        self.assertIn("Archive raw generative masters", got[0])
        self.assertEqual(dropped, [])


class VerdictHeadingsStillExcluded(unittest.TestCase):
    """An operator's verdict over a whole section must still win, out loud.

    This class must FAIL when the heading-exclusion rule is removed
    entirely — it pins the rule, not the refinement.
    """

    SECRETIVE = "- The vendor shortlist lives in the private tracker\n"

    def test_verdict_annotations_on_every_shipped_label_exclude(self):
        labels = _template_section_labels()
        heads = []
        for label in labels:
            # "Rejected" / "Private-only" are the template's own status
            # vocabulary; the annotation shapes are the documented operator
            # idioms (parenthesis, bracket, spaced dash, colon).
            heads += ["## %s (Rejected)" % label,
                      "## %s (Private-only)" % label,
                      "## %s — Rejected" % label,
                      "## %s [Rejected]" % label,
                      "## %s: do not harvest" % label]
        heads.append("## Lessons (rejected — superseded)")
        heads.append("## Lessons — Rejected because it names a client")
        for head in heads:
            with self.subTest(head=head):
                got, dropped = _scan(head + "\n" + self.SECRETIVE)
                self.assertEqual(got, [], "%r harvested its rows" % head)
                self.assertEqual(
                    len(dropped), 1,
                    "%r dropped silently, no audit trail" % head)
                self.assertIn("vendor shortlist", dropped[0])

    def test_table_rows_under_a_verdict_heading_drop_with_audit_trail(self):
        with open(TEMPLATE, encoding="utf-8") as fh:
            header = next(ln.strip() for ln in fh
                          if ln.strip().startswith("| Lesson |"))
        width = len(header.strip().strip("|").split("|"))
        sep = "|" + "---|" * width
        row = ["The retainer amount is in the private tracker"] \
            + ["fill"] * (width - 2) + ["Yes"]
        md = ("## Lessons (Rejected)\n%s\n%s\n| %s |\n"
              % (header, sep, " | ".join(row)))
        got, dropped = _scan(md)
        self.assertEqual(got, [])
        self.assertEqual(len(dropped), 1)
        self.assertIn("retainer amount", dropped[0])


class EverySeparatorFamilyExcludes(unittest.TestCase):
    """The mirror direction, found by two independent judges.

    The first cut of the refinement recognised only a SPACED single dash, a
    colon and paren/bracket trailers, and judged only the spans AFTER the
    first separator. Every shape below was excluded before the refinement and
    harvested SILENTLY after it — silently because an excluded-by-heading
    section leaves DROPPED empty, so `cmd_scan`'s "filtered N row(s)" audit
    line never prints and nothing looks wrong.

    Verified differentially against the rule at 1ba98a4 over 137 verdict
    shapes: with the repair, zero of them harvest.
    """

    SECRETIVE = "- The vendor shortlist lives in the private tracker\n"

    SHAPES = [
        # this codebase's own ASCII idiom, used throughout harvest.py itself
        "## Lessons -- Rejected",
        "## Lessons--Rejected",
        # a typographic dash is never a word joiner, spaced or not
        "## Lessons—Rejected",
        "## Lessons–Rejected",
        # emphasis IS the annotation delimiter; _plain() flattens the very
        # wrapper that marks it, so the span has to be read before flattening
        "## Lessons **Rejected**",
        "## Lessons *Rejected*",
        "## Lessons _Rejected_",
        "## Lessons `Rejected`",
        "## Lessons **private-only**",
        # remaining annotation separators
        "## Lessons, Rejected",
        "## Lessons; Rejected",
        "## Lessons | Rejected",
        "## Lessons / Rejected",
        "## Lessons -- rejected, holds a client token",
        "## Lessons--do not harvest",
    ]

    def test_every_separator_family_still_excludes(self):
        for head in self.SHAPES:
            with self.subTest(head=head):
                got, dropped = _scan(head + "\n" + self.SECRETIVE)
                self.assertEqual(got, [], "%r harvested its rows" % head)
                self.assertEqual(len(dropped), 1,
                                 "%r dropped SILENTLY (no audit trail)" % head)

    def test_word_internal_punctuation_is_a_joiner_not_a_separator(self):
        """The over-exclusion this rule exists to close, from the other side.

        Splitting on a lone word-internal hyphen — or on an apostrophe —
        would manufacture a bare "Rejection" span out of ordinary prose and
        drop the section all over again. Both are joiners, not annotations.
        """
        label = _template_section_labels()[0]
        for tail in ("rejection-driven development notes",
                     "well-known rejection patterns",
                     "Rejection's reason must be written down"):
            head = "## %s — %s" % (label, tail)
            with self.subTest(head=head):
                got, _dropped = _scan(head + "\n- A safe public lesson\n")
                self.assertEqual(got, ["A safe public lesson"],
                                 "%r was dropped as a verdict" % head)


if __name__ == "__main__":
    unittest.main()
