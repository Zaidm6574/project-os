"""Regression guards for three b4cc70f cost_actuals fixes that shipped untested.

b4cc70f hardened addons/full-engine/memory/cost_actuals.py so that a tool which
cannot actually measure spend refuses instead of printing a confident number.
The subagent-transcript exclusion is already guarded by
tests/test_b4cc70f_unguarded_20260726.py; this file pins the other three hunks:

1. Unreadable/missing transcript: _parse_usage reports the file as unreadable
   and run(--write) REFUSES (exit 2, target untouched) instead of recording a
   confident $0.00 "measured" total.
2. isinstance guard: a "message" or "usage" field holding a string or null must
   not abort the whole parse with AttributeError; valid lines still count.
3. Incomplete-price detection: a tier present in the price table but missing
   "in" or "out" is flagged by unpriced_tiers() and blocks --write, instead of
   being silently priced with the zero fallback.

Each test drives the real callable with real inputs and was mutation-verified:
hand-reverting the guarded hunk (one at a time, per pre-b4cc70f code) makes the
matching test FAIL while the others stay green.
"""

import contextlib
import importlib.util
import io
import json
import os
import pathlib
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE_MEMORY = os.path.join(ROOT, "addons", "full-engine", "memory")

MARKED_TARGET = (
    "# Cost estimate\n\n"
    "<!-- ACTUALS:START -->\n"
    "SENTINEL-NOT-YET-MEASURED\n"
    "<!-- ACTUALS:END -->\n"
)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextlib.contextmanager
def _sandbox_home(home):
    """Point HOME at a sandbox so nothing can fall back to the real
    ~/.claude tree, and swallow the module's stdout/stderr chatter."""
    old = os.environ.get("HOME")
    os.environ["HOME"] = str(home)
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            yield out, err
    finally:
        if old is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old


class _CostActualsCase(unittest.TestCase):
    def setUp(self):
        self.cost = _load(
            "cost_actuals_guards", os.path.join(ENGINE_MEMORY, "cost_actuals.py")
        )


class UnreadableTranscriptMakesWriteRefuse(_CostActualsCase):
    """Fix 1: a transcript that cannot be opened previously fell through as an
    empty totals dict, so --write recorded a confident $0.00 as *measured*."""

    def test_parse_usage_reports_the_unreadable_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = pathlib.Path(tmp) / "no-such-session.jsonl"
            with _sandbox_home(tmp):
                main_t, sub_t, unreadable = self.cost._parse_usage(
                    [missing], missing
                )
            self.assertEqual(
                unreadable, [missing],
                "an unreadable transcript was not reported; the caller has no "
                "way to distinguish 'measured zero' from 'could not measure'",
            )
            self.assertEqual(main_t, {})
            self.assertEqual(sub_t, {})

    def test_write_refuses_and_leaves_the_target_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp)
            missing = home / "session-that-was-never-written.jsonl"
            target = home / "09-cost-estimate.md"
            target.write_text(MARKED_TARGET)
            with _sandbox_home(tmp):
                rc = self.cost.run(
                    missing, dict(self.cost.DEFAULT_PRICES), True, target
                )
            self.assertEqual(
                rc, 2,
                "run(--write) did not refuse on an unreadable transcript -- a "
                "confident $0.00 would be recorded as a measured actual",
            )
            self.assertIn(
                "SENTINEL-NOT-YET-MEASURED", target.read_text(),
                "the ACTUALS block was overwritten despite the transcript "
                "being unreadable",
            )


class NonDictMessageOrUsageDoesNotAbortParse(_CostActualsCase):
    """Fix 2: '"message": "hello"' or '"usage": null' used to raise
    AttributeError mid-file and abort the whole parse."""

    def test_string_and_null_fields_are_skipped_and_valid_lines_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            transcript = pathlib.Path(tmp) / "main.jsonl"
            lines = [
                {"message": "hello"},                # str message
                {"usage": None},                     # null usage
                {"message": None},                   # null message
                {"usage": "not-a-dict"},             # truthy non-dict usage
                {"message": {"usage": "also-str"}},  # nested non-dict usage
                {
                    "message": {
                        "model": "claude-opus-5",
                        "usage": {"input_tokens": 100, "output_tokens": 10},
                    }
                },
            ]
            transcript.write_text(
                "".join(json.dumps(x) + "\n" for x in lines)
            )
            with _sandbox_home(tmp):
                try:
                    main_t, sub_t, unreadable = self.cost._parse_usage(
                        [transcript], transcript
                    )
                except AttributeError as exc:  # the exact pre-fix crash
                    self.fail(
                        "_parse_usage crashed with AttributeError on a "
                        "non-dict message/usage field: %r" % (exc,)
                    )
            self.assertEqual(unreadable, [])
            self.assertEqual(main_t["opus"]["input"], 100)
            self.assertEqual(main_t["opus"]["output"], 10)


class IncompletePriceEntryIsDetected(_CostActualsCase):
    """Fix 3: a price entry missing "in" or "out" used to pass the
    `tier in prices` check, so those tokens were priced with the
    {"in": 0.0, "out": 0.0} fallback -- silently."""

    @staticmethod
    def _totals(**tokens):
        counts = {"input": 0, "output": 0, "cache_creation": 0, "cache_read": 0}
        counts.update(tokens)
        return counts

    def test_price_entry_missing_out_is_flagged(self):
        totals = {"sonnet": self._totals(input=1_000, output=500)}
        prices = {"sonnet": {"in": 3.0}}  # no "out": output silently free
        self.assertEqual(
            self.cost.unpriced_tiers(totals, prices), ["sonnet"],
            "a tier with an incomplete price entry was not reported; its "
            "output tokens would be priced at $0.00 with no warning",
        )

    def test_complete_entry_and_zero_token_tier_are_not_flagged(self):
        totals = {
            "sonnet": self._totals(input=1_000, output=500),
            "haiku": self._totals(),  # present but consumed nothing
        }
        prices = {"sonnet": {"in": 3.0, "out": 15.0}}
        self.assertEqual(self.cost.unpriced_tiers(totals, prices), [])

    def test_write_refuses_when_the_price_table_is_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp)
            transcript = home / "main.jsonl"
            transcript.write_text(
                json.dumps(
                    {
                        "message": {
                            "model": "claude-sonnet-4",
                            "usage": {
                                "input_tokens": 1_000,
                                "output_tokens": 500,
                            },
                        }
                    }
                )
                + "\n"
            )
            target = home / "09-cost-estimate.md"
            target.write_text(MARKED_TARGET)
            with _sandbox_home(tmp):
                rc = self.cost.run(
                    transcript, {"sonnet": {"in": 3.0}}, True, target
                )
            self.assertEqual(
                rc, 2,
                "run(--write) accepted an incomplete price entry -- output "
                "tokens would be recorded at $0.00 as a measured actual",
            )
            self.assertIn("SENTINEL-NOT-YET-MEASURED", target.read_text())


if __name__ == "__main__":
    unittest.main()
