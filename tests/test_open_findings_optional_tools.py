"""Regression tests for scripts/check_optional_tools.py report rewriting.

The automated optional-tool check owns exactly one block of
`blackboard/17-capability-preflight.md`. Everything else in that file is
hand-written by a human and must survive every re-run, including prose that
merely *mentions* the report heading.
"""

import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOL_CHECK = ROOT / "scripts" / "check_optional_tools.py"


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


HAND_WRITTEN_AFTER_MENTION = """# Capability Preflight

Status labels: Verified, Not configured, Unavailable.

## How This File Is Maintained

The section titled "## Automated Optional Tool Check" is written by
`scripts/check_optional_tools.py`. Do not edit it by hand.

## Human Notes

- Model routing is configured in the AI tool, not on PATH.
- KEEPME: this line was written by a human and must never be deleted.

## Reality Check

| Capability | Status |
|---|---|
| Model routing | Not configured |
"""


LEGACY_REPORT_FILE = """# Capability Preflight

Status labels: Verified, Not configured, Unavailable.

## Automated Optional Tool Check

- Date: 2026-06-25 14:08
- Scope: local machine PATH, selected environment variables, and installed Python packages

| Capability | Status | Evidence | Recommendation |
|---|---|---|---|
| File search | Verified | Found on PATH: rg | Install ripgrep if missing. |

### Plain-English Summary

- Stale summary line from the legacy report.

## Human Notes After The Report

- KEEPME: hand-written follow-up that sits after the generated block.
"""


class OptionalToolReportRewriteTests(unittest.TestCase):
    def setUp(self):
        self.tool_check = load_module(TOOL_CHECK, "open_findings_check_optional_tools")

    def _write_report(self, target, existing=None):
        preflight = target / "blackboard" / "17-capability-preflight.md"
        if existing is not None:
            preflight.parent.mkdir(parents=True, exist_ok=True)
            preflight.write_text(existing, encoding="utf-8")
        path = self.tool_check.write_report(target)
        return path.read_text(encoding="utf-8")

    def test_prose_that_mentions_the_heading_is_not_truncated(self):
        """A human sentence quoting the heading must not eat the rest of the file."""
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            text = self._write_report(target, HAND_WRITTEN_AFTER_MENTION)

        self.assertIn("KEEPME: this line was written by a human", text)
        self.assertIn("## Human Notes", text)
        self.assertIn("## Reality Check", text)
        self.assertIn("| Model routing | Not configured |", text)
        self.assertIn("## How This File Is Maintained", text)
        self.assertIn('The section titled "## Automated Optional Tool Check" is written by', text)
        # The fresh report was still written.
        self.assertIn(self.tool_check.BEGIN_MARKER, text)
        self.assertIn(self.tool_check.END_MARKER, text)
        self.assertIn("- Scope: local machine PATH", text)

    def test_legacy_bare_heading_is_migrated_once_and_keeps_later_prose(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            first = self._write_report(target, LEGACY_REPORT_FILE)

            self.assertIn("KEEPME: hand-written follow-up", first)
            self.assertIn("## Human Notes After The Report", first)
            self.assertNotIn("Stale summary line from the legacy report", first)
            self.assertEqual(first.count(self.tool_check.BEGIN_MARKER), 1)
            self.assertEqual(first.count(self.tool_check.END_MARKER), 1)
            self.assertEqual(first.count(self.tool_check.REPORT_MARKER + "\n"), 1)

            second = self._write_report(target)

        # Migration happens exactly once: no second block, no drift beyond the
        # timestamp line.
        self.assertEqual(second.count(self.tool_check.BEGIN_MARKER), 1)
        self.assertEqual(second.count(self.tool_check.END_MARKER), 1)
        self.assertEqual(second.count(self.tool_check.REPORT_MARKER + "\n"), 1)
        self.assertIn("KEEPME: hand-written follow-up", second)

        def strip_dates(text):
            return [line for line in text.splitlines() if not line.startswith("- Date:")]

        self.assertEqual(strip_dates(first), strip_dates(second))

    def test_repeated_runs_are_idempotent_on_a_fresh_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            first = self._write_report(target)
            second = self._write_report(target)
            third = self._write_report(target)

        for text in (first, second, third):
            self.assertEqual(text.count(self.tool_check.BEGIN_MARKER), 1)
            self.assertEqual(text.count(self.tool_check.END_MARKER), 1)
            self.assertEqual(text.count(self.tool_check.REPORT_MARKER + "\n"), 1)

        def strip_dates(text):
            return [line for line in text.splitlines() if not line.startswith("- Date:")]

        self.assertEqual(strip_dates(first), strip_dates(second))
        self.assertEqual(strip_dates(second), strip_dates(third))

    def test_heading_inside_a_fenced_code_block_is_not_treated_as_the_block(self):
        existing = (
            "# Capability Preflight\n"
            "\n"
            "Example of what the generated block looks like:\n"
            "\n"
            "```markdown\n"
            "## Automated Optional Tool Check\n"
            "\n"
            "- Date: 2026-01-01 00:00\n"
            "- Scope: local machine PATH, selected environment variables\n"
            "```\n"
            "\n"
            "## Human Notes\n"
            "\n"
            "- KEEPME: documentation prose below the fence.\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            text = self._write_report(target, existing)

        self.assertIn("```markdown", text)
        self.assertIn("KEEPME: documentation prose below the fence.", text)
        self.assertIn("## Human Notes", text)
        self.assertEqual(text.count(self.tool_check.BEGIN_MARKER), 1)

    def test_content_before_and_after_the_delimited_block_survives(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            self._write_report(target)
            preflight = target / "blackboard" / "17-capability-preflight.md"
            text = preflight.read_text(encoding="utf-8")
            preflight.write_text(
                text.rstrip("\n") + "\n\n## Appendix\n\n- KEEPME: added after the machine block.\n",
                encoding="utf-8",
            )

            updated = self._write_report(target)

        self.assertIn("## Appendix", updated)
        self.assertIn("KEEPME: added after the machine block.", updated)
        self.assertEqual(updated.count(self.tool_check.BEGIN_MARKER), 1)
        self.assertTrue(
            updated.index(self.tool_check.END_MARKER) < updated.index("## Appendix"),
            "appendix must stay after the machine-owned block",
        )

    def test_empty_existing_file_gets_a_titled_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            text = self._write_report(target, "   \n\n")

        self.assertTrue(text.startswith("# Capability Preflight\n"))
        self.assertIn(self.tool_check.BEGIN_MARKER, text)
        self.assertIn(self.tool_check.REPORT_MARKER, text)

    def test_human_section_reusing_the_heading_is_not_claimed_as_legacy(self):
        """A human `## Automated Optional Tool Check` section that also quotes a
        report bullet must not be mistaken for a legacy generated block."""
        existing = (
            "# Capability Preflight\n"
            "\n"
            "## Automated Optional Tool Check\n"
            "\n"
            "House rules for reading the generated block below:\n"
            "\n"
            "- Scope: local machine PATH is all the script can see.\n"
            "- KEEPME-ALPHA: hand-written bullet in a human section.\n"
            "\n"
            "KEEPME-BRAVO: hand-written paragraph in a human section.\n"
            "\n"
            "## Next Section\n"
            "\n"
            "- KEEPME-CHARLIE\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            first = self._write_report(target, existing)
            second = self._write_report(target)

        for text in (first, second):
            self.assertIn("KEEPME-ALPHA", text)
            self.assertIn("KEEPME-BRAVO", text)
            self.assertIn("KEEPME-CHARLIE", text)
            self.assertIn("House rules for reading the generated block below:", text)
            self.assertEqual(text.count(self.tool_check.BEGIN_MARKER), 1)
            self.assertEqual(text.count(self.tool_check.END_MARKER), 1)

    def test_legacy_migration_keeps_hand_written_subsections(self):
        """Migration must stop at the end of generated *shape*, not run all the
        way to the next `## ` heading and swallow human `###` subsections."""
        existing = (
            "# Capability Preflight\n"
            "\n"
            "## Automated Optional Tool Check\n"
            "\n"
            "- Date: 2026-06-25 14:08\n"
            "- Scope: local machine PATH, selected environment variables\n"
            "\n"
            "| Capability | Status |\n"
            "|---|---|\n"
            "| File search | Verified |\n"
            "\n"
            "### Plain-English Summary\n"
            "\n"
            "- Stale summary line from the legacy report.\n"
            "\n"
            "### My Own Sub-Notes\n"
            "\n"
            "- KEEPME-DELTA: hand-written subsection under the old report.\n"
            "\n"
            "## Next Real Section\n"
            "\n"
            "- KEEPME-ECHO\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            first = self._write_report(target, existing)
            second = self._write_report(target)

        self.assertNotIn("Stale summary line from the legacy report", first)
        for text in (first, second):
            self.assertIn("KEEPME-DELTA", text)
            self.assertIn("### My Own Sub-Notes", text)
            self.assertIn("KEEPME-ECHO", text)
            self.assertEqual(text.count(self.tool_check.BEGIN_MARKER), 1)
            self.assertEqual(text.count(self.tool_check.END_MARKER), 1)

    def test_legacy_migration_stops_at_the_end_of_generated_shape(self):
        """Content a human added after the generated summary bullets — separated
        by a blank line — is outside the generated shape and must survive."""
        existing = (
            "# Capability Preflight\n"
            "\n"
            "## Automated Optional Tool Check\n"
            "\n"
            "- Date: 2026-06-25 14:08\n"
            "- Scope: local machine PATH, selected environment variables\n"
            "\n"
            "| Capability | Status |\n"
            "|---|---|\n"
            "| File search | Verified |\n"
            "\n"
            "### Plain-English Summary\n"
            "\n"
            "- Stale summary line from the legacy report.\n"
            "\n"
            "- KEEPME-HOTEL: hand-written bullet after the generated summary.\n"
            "\n"
            "KEEPME-INDIA: hand-written paragraph after the generated summary.\n"
            "\n"
            "## Next Real Section\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            first = self._write_report(target, existing)
            second = self._write_report(target)

        self.assertNotIn("Stale summary line from the legacy report", first)
        for text in (first, second):
            self.assertIn("KEEPME-HOTEL", text)
            self.assertIn("KEEPME-INDIA", text)
            self.assertEqual(text.count(self.tool_check.BEGIN_MARKER), 1)
            self.assertEqual(text.count(self.tool_check.END_MARKER), 1)

    def test_unclosed_code_fence_does_not_grow_a_new_block_every_run(self):
        """An unclosed fence hides the block from fence tracking; the script must
        still find and replace it instead of appending forever."""
        existing = "# Capability Preflight\n\n```\nsomeone forgot to close this fence\n"
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            texts = [self._write_report(target, existing)]
            for _ in range(3):
                texts.append(self._write_report(target))

        for text in texts:
            self.assertEqual(text.count(self.tool_check.BEGIN_MARKER), 1)
            self.assertEqual(text.count(self.tool_check.END_MARKER), 1)
            self.assertIn("someone forgot to close this fence", text)

    def test_begin_marker_without_end_marker_keeps_trailing_prose(self):
        """A hand-mangled file (END marker deleted) must not lose everything
        written after the orphaned BEGIN marker."""
        existing = (
            "# Capability Preflight\n"
            "\n"
            + self.tool_check.BEGIN_MARKER
            + "\n"
            "| File search | Verified |\n"
            "\n"
            "KEEPME-FOXTROT: prose after the orphan marker, no heading to stop at.\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            first = self._write_report(target, existing)
            second = self._write_report(target)

        for text in (first, second):
            self.assertIn("KEEPME-FOXTROT", text)
            self.assertEqual(text.count(self.tool_check.BEGIN_MARKER), 1)
            self.assertEqual(text.count(self.tool_check.END_MARKER), 1)

    def test_duplicate_delimited_blocks_are_coalesced(self):
        """A stale second machine block must be dropped, not left presenting old
        tool statuses as current."""
        report = self.tool_check.build_report(None)
        stale = self.tool_check.render_block(report).replace("Plain-English Summary", "STALEMARK")
        existing = (
            "# Capability Preflight\n"
            "\n"
            + self.tool_check.render_block(report)
            + "\n"
            "\n"
            "## Human Notes\n"
            "\n"
            + stale
            + "\n"
            "\n"
            "- KEEPME-GOLF\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            first = self._write_report(target, existing)
            second = self._write_report(target)

        for text in (first, second):
            self.assertEqual(text.count(self.tool_check.BEGIN_MARKER), 1)
            self.assertEqual(text.count(self.tool_check.END_MARKER), 1)
            self.assertNotIn("STALEMARK", text)
            self.assertIn("KEEPME-GOLF", text)
            self.assertIn("## Human Notes", text)


if __name__ == "__main__":
    unittest.main()
