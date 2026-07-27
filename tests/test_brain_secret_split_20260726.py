#!/usr/bin/env python3
"""A credential split across a JSON key and its value must still be refused.

Audit finding (2026-07-26). The exhaustive secret walk scanned every string it
could reach -- dict keys, values, list elements, bytes -- but only one string
AT A TIME. Split a credential across a key and its value ("sk-ant-api03" as the
key, the remaining 40 characters as the value) and each side alone matches no
pattern, so the pair walked straight into the brain. Same for the keyword
catch-all: {"api_key": "<secret>"} is the JSON spelling of "api_key=<secret>",
which the patterns refuse as text, yet as a key/value pair neither side matched.

The walk now also scans a joined key+value form (bare, and with "=") for every
string-keyed string/bytes value, so the existing patterns see the reassembled
pair. These tests run the REAL refusal path -- record_secret_hit / gate_record
and the brain_append writer in a sandboxed store -- never source text.

Every fixture asserts its own preconditions against the SHIPPED patterns:
each half must be clean alone and the joined pair must match, so a pattern
change that invalidates a fixture turns the test red instead of hollow.

Nothing here is a live credential; every value is a correctly-shaped fake.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRAIN_DIR = ROOT / "addons" / "full-engine" / "brain"

sys.path.insert(0, str(BRAIN_DIR))
import brain  # noqa: E402


# Split halves. Each half alone matches NO shipped pattern; joined they do.
# (key_half, value_half, join) -- join is how the two halves reassemble into a
# shape a shipped pattern catches: "" for prefix tokens that simply continue
# across the boundary, "=" for patterns that require a separator.
SPLIT_CREDENTIALS = {
    "anthropic_prefix": ("sk-ant-api03", "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcd", ""),
    "aws_prefix": ("AKIA1234", "567890ABCDEFGHIJ", ""),
    "keyword_catchall": ("api_key", "hunter2hunter2", "="),
}

# Benign near-misses to the same patterns: keys that merely CONTAIN a keyword,
# and joins whose reassembled text sits one lookbehind/length check away from a
# match. None of these may be refused.
BENIGN_PAIRS = {
    "keyword_not_at_key_end": {"api_key_docs": "how to rotate keys safely"},
    "keyword_with_suffix": {"token_budget": "large for this run"},
    "sk_dash_across_join_after_alnum": {"task": "-list-of-follow-ups-for-the-run"},
    "prefix_lookalike": {"skills": "ant-colony-optimization-notes"},
}

# Keys that END in a credential keyword — the exact shape the "key=value"
# spelling of the split scan feeds to the keyword catch-all, which needs only
# six non-space characters after its separator. Ungated, EVERY one of these was
# REFUSED, so a user could not save "auth_token: rotate quarterly per runbook"
# (judge round 2026-07-26). A value carrying whitespace or no entropy cannot BE
# the secret the pattern hunts, so all of these must be ALLOWED. Without this
# fixture family a naive "the key name contains a credential word" rule passes
# the whole file — the judge built that mutant and it survived.
LEGITIMATE_KEYWORD_KEYS = {
    "prose_value": {"auth_token": "rotate quarterly per runbook"},
    "prose_naming_a_manager": {"vault_password": "stored in 1Password"},
    "redaction_marker": {"client_secret": "REDACTED"},
    "symbolic_placeholder": {"api_key": "***"},
    "not_applicable": {"password": "n/a"},
    "todo_marker": {"secret": "TODO"},
    "angle_redaction": {"token": "<redacted>"},
    "prose_mentioning_the_key": {"passwd": "see the runbook for the value"},
}


def _sandbox_env(tmp: Path, brain_path: Path) -> dict:
    """brain_append env with every HOME-rooted write redirected into tmp."""
    env = dict(os.environ)
    env["PROJECT_OS_SHARED_BRAIN"] = str(brain_path)
    env["BB_LOCK_DIR"] = str(tmp / "locks")
    return env


class SplitFixturesMatchTheShippedPatterns(unittest.TestCase):
    """Preconditions: the fixtures target the SPLIT gap and nothing else."""

    def test_each_half_is_clean_alone_and_joined_is_a_secret(self) -> None:
        for name, (key, value, join) in SPLIT_CREDENTIALS.items():
            with self.subTest(split=name):
                self.assertFalse(
                    brain._looks_like_secret(key),
                    f"{name}: key half matches a pattern ALONE; this fixture "
                    "no longer exercises the split gap",
                )
                self.assertFalse(
                    brain._looks_like_secret(value),
                    f"{name}: value half matches a pattern ALONE; this fixture "
                    "no longer exercises the split gap",
                )
                self.assertTrue(
                    brain._looks_like_secret(key + join + value),
                    f"{name}: the joined pair no longer matches any shipped "
                    "pattern; the fixture is dead",
                )


class SplitCredentialIsRefused(unittest.TestCase):
    """Direction (a): the pair is caught by the walk and by THE gate."""

    def test_split_pair_nested_in_metadata_is_caught(self) -> None:
        for name, (key, value, _join) in SPLIT_CREDENTIALS.items():
            record = {"id": "x", "text": "a harmless lesson",
                      "metadata": {key: value}}
            with self.subTest(split=name):
                self.assertEqual(
                    brain.record_secret_hit(record), "metadata",
                    f"{name}: a credential split across the key and value of "
                    "metadata was NOT detected",
                )
                self.assertEqual(
                    brain.record_secret_path(record), "metadata." + key,
                    "the refusal must name the pair to redact",
                )

    def test_split_pair_at_top_level_is_caught(self) -> None:
        key, value, _join = SPLIT_CREDENTIALS["anthropic_prefix"]
        record = {"id": "x", "text": "clean", key: value}
        self.assertEqual(brain.record_secret_hit(record), key)

    def test_split_pair_with_bytes_value_is_caught(self) -> None:
        """The join must cover bytes values, same as the plain scan does."""
        key, value, _join = SPLIT_CREDENTIALS["anthropic_prefix"]
        record = {"id": "x", "text": "clean",
                  "metadata": {key: value.encode("utf-8")}}
        self.assertEqual(brain.record_secret_hit(record), "metadata")

    def test_gate_record_refuses_the_split_pair(self) -> None:
        """The actual refusal callable every writer goes through."""
        key, value, _join = SPLIT_CREDENTIALS["keyword_catchall"]
        record = {"id": "x", "text": "clean", "metadata": {key: value}}
        with self.assertRaises(SystemExit) as raised:
            brain.gate_record(record, where="split-test")
        self.assertIn("refuse", str(raised.exception).lower())


class BenignNearMissPairsStillPass(unittest.TestCase):
    """Direction (b): the joined scan must not start refusing normal records."""

    def test_near_miss_key_value_pairs_are_not_refused(self) -> None:
        for name, pair in BENIGN_PAIRS.items():
            record = {"id": "x", "text": "a harmless lesson", "metadata": pair}
            with self.subTest(pair=name):
                self.assertIsNone(
                    brain.record_secret_hit(record),
                    f"benign pair {pair!r} was refused: the joined key+value "
                    "scan is over-matching",
                )

    def test_credential_keyword_keys_with_non_secret_values_are_allowed(self) -> None:
        """The over-refusal direction, found by an adversarial judge.

        These are notes a user is entitled to save. Refusing them is not a
        harmless excess of caution: the gate exits the process, so the write
        is lost.
        """
        for name, pair in LEGITIMATE_KEYWORD_KEYS.items():
            record = {"id": "x", "text": "a harmless lesson", "metadata": pair}
            with self.subTest(pair=name):
                self.assertIsNone(
                    brain.record_secret_hit(record),
                    f"{pair!r} was refused: an ordinary note cannot be saved",
                )

    def test_the_same_keys_still_refuse_an_opaque_value(self) -> None:
        """...and the mirror: the gate is on the VALUE, not on the key.

        Same key names as the fixtures above; only the value changes, so a
        rule that simply stopped scanning these keys would fail here.
        """
        for pair in LEGITIMATE_KEYWORD_KEYS.values():
            key = next(iter(pair))
            record = {"id": "x", "text": "clean",
                      "metadata": {key: "hunter2hunter2"}}
            with self.subTest(key=key):
                self.assertEqual(
                    brain.record_secret_hit(record), "metadata",
                    f"an opaque value under {key!r} walked in",
                )

    def test_a_normal_record_shape_still_passes(self) -> None:
        record = {
            "id": "chat-abc123def456",
            "ts": "2026-07-26T00:00:00Z",
            "source": "project-os",
            "type": "lesson",
            "text": "Prefer fail-closed gates; scan the whole payload.",
            "tags": ["lesson", "doctrine", "project:example"],
            "summary_only": True,
            "raw_chat": False,
            "approved": True,
            "metadata": {"run": "run-2026-07-26-example", "cost_usd": 0.12,
                         "url": "https://example.com/docs/gates"},
        }
        self.assertIsNone(brain.record_secret_hit(record))


class SplitCredentialRefusedEndToEnd(unittest.TestCase):
    """The real writer that accepts arbitrary payload shapes, in a sandbox."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.brain_file = Path(self.tmp.name) / "shared-brain.jsonl"
        self.env = _sandbox_env(Path(self.tmp.name), self.brain_file)
        self.addCleanup(self.tmp.cleanup)

    def _append(self, record):
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "brain_append.py"),
             "--line", json.dumps(record), "--no-reindex"],
            capture_output=True, text=True, cwd=str(ROOT), env=self.env,
            check=False,
        )

    def test_split_credential_is_refused_and_nothing_is_written(self) -> None:
        key, value, _join = SPLIT_CREDENTIALS["anthropic_prefix"]
        r = self._append({"id": "split-e2e", "text": "a harmless lesson",
                          "metadata": {key: value}})
        self.assertEqual(r.returncode, 2,
                         "brain_append ACCEPTED a split credential: "
                         + r.stdout + r.stderr)
        self.assertIn("REFUSED", r.stderr)
        self.assertFalse(
            self.brain_file.exists(),
            "a split credential reached the brain file",
        )

    def test_benign_near_miss_record_is_still_appended(self) -> None:
        """Control: the writer must refuse the split, not refuse everything."""
        r = self._append({"id": "split-clean", "type": "lesson",
                          "text": "Rotation runbook location, no secrets.",
                          "metadata": dict(BENIGN_PAIRS["keyword_not_at_key_end"])})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rows = self.brain_file.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0])["id"], "split-clean")


if __name__ == "__main__":
    unittest.main()
