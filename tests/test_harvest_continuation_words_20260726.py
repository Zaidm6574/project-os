"""A rejection verdict does not have to reach for punctuation.

Commit b4cc70f dropped bullets whose marker was followed by a bare continuation
word ("Rejected pending review"). The rewrite that replaced it required
annotation punctuation immediately after the marker, so that whole family began
harvesting through into a PUBLIC brain with no entry in the DROPPED report --
content is often rejected precisely because it is private.

These tests assert on what bullets_by_section actually YIELDS, never on the
source text of the module or on the regexes in isolation, because a helper can
be correct while the call site that should use it is not.
"""

import importlib.util
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HARVEST = os.path.join(ROOT, "scripts", "harvest.py")


def _load():
    spec = importlib.util.spec_from_file_location("harvest_continuation", HARVEST)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


harvest = _load()


def _harvested(markdown):
    """The lesson texts that survive the reject filter, via the real entry point."""
    out = []
    for section, text in harvest.bullets_by_section(markdown):
        out.append(text)
    return out


class ContinuationWordVerdictsAreDropped(unittest.TestCase):
    """Each of these flipped from DROP to KEEP in the rewrite. They must drop."""

    REGRESSED = [
        "Rejected pending review",
        "Rejected dupe of lesson 12",
        "Rejected superseded by the 07-25 fix",
        "Rejected see note",
        "Rejected wontfix",
        "Rejection awaiting board sign-off",
        "Rejected duplicate of the harvest lesson",
        "Rejected stale after the rewrite",
        "Rejected obsolete once the gate landed",
    ]

    def test_each_regressed_verdict_is_dropped(self):
        for verdict in self.REGRESSED:
            with self.subTest(verdict=verdict):
                kept = _harvested("## Lessons\n- %s\n" % verdict)
                self.assertEqual(
                    kept, [],
                    "verdict harvested through into the public brain: %r" % verdict)

    def test_verdict_in_a_tables_lesson_cell_is_dropped(self):
        table = (
            "## Lessons\n"
            "| Lesson | Notes |\n"
            "| --- | --- |\n"
            "| Rejected per the board | notes |\n"
        )
        self.assertEqual(
            _harvested(table), [],
            "a first-cell verdict harvested through")


class OrdinaryProseAboutRejectionSurvives(unittest.TestCase):
    """The rewrite existed to stop over-eager drops. Do not undo that."""

    KEPT = [
        "Rejection criteria belong in the rubric",
        "Reject-first workflow keeps the queue short",
        "Evaluator must reject a plan that has no Definition of Done",
        "The plan was rejected by the board, so we rewrote the brief",
        "Rejects are logged with a reason",
        "Rejected pendings are not a thing, this is a real lesson about naming",
    ]

    def test_real_lessons_are_still_harvested(self):
        for lesson in self.KEPT:
            with self.subTest(lesson=lesson):
                kept = _harvested("## Lessons\n- %s\n" % lesson)
                self.assertEqual(
                    kept, [lesson],
                    "a real lesson was dropped: %r" % lesson)


class StrongerRulesAreNotWeakened(unittest.TestCase):
    """Guards against a future 'fix' loosening what the rewrite got right."""

    def test_directive_anywhere_still_drops(self):
        self.assertEqual(
            _harvested("## Lessons\n- Keep this one private-only, it has a token\n"),
            [])

    def test_punctuation_annotation_still_drops(self):
        for verdict in ("Rejected: dupe", "Rejected, holds a private token",
                        "Rejected -- superseded"):
            with self.subTest(verdict=verdict):
                self.assertEqual(_harvested("## Lessons\n- %s\n" % verdict), [])

    def test_bare_marker_still_drops(self):
        self.assertEqual(_harvested("## Lessons\n- Rejected\n"), [])


if __name__ == "__main__":
    unittest.main()
