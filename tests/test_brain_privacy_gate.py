#!/usr/bin/env python3
"""Every brain write path must refuse the same record.

Audit finding (2026-07-25), root cause "one gate exists; the other entry points
don't call it": `brain.py save-chat` scanned for secrets, while `export`,
`import --into`, and `scripts/brain_append.py` wrote with NO scan. The refusal
was bypassable by choosing a different verb. `central_brain.py pull` then
redistributed whatever got in to other projects.

Second finding: `SECRET_PATTERNS` used `sk-[A-Za-z0-9]{16,}`, which cannot cross
a hyphen or underscore, so every modern key format passed. And only the `text`
field was scanned, so a secret in `tags`/`source` synced untouched.

These tests are the forcing function: adding a new writer without the gate, or
narrowing the patterns again, turns them red.
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


# Fake-but-correctly-shaped credentials. None are live.
MODERN_SECRETS = {
    "anthropic":   "sk-ant-api03-" + "A" * 40,
    "openai_proj": "sk-proj-" + "B" * 40,
    "stripe_live": "sk_live_" + "C" * 24,
    "slack_bot":   "xoxb-123456789012-123456789012-" + "D" * 24,
    "figma":       "figd_" + "E" * 30,
    "sendgrid":    "SG." + "F" * 22 + "." + "G" * 30,
    "twilio_sid":  "AC" + "0" * 32,
    "gitlab":      "glpat-" + "H" * 20,
    "google_oauth": "ya29." + "I" * 40,
    "jwt":         "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abc123",
    "pg_dsn":      "postgres://admin:hunter2@db.example.com:5432/prod",
    "aws":         "AKIA" + "J" * 16,
    "github":      "ghp_" + "K" * 30,
}


class SecretPatternCoverage(unittest.TestCase):
    def test_modern_key_formats_are_detected(self) -> None:
        for name, secret in MODERN_SECRETS.items():
            with self.subTest(kind=name):
                self.assertTrue(
                    brain._looks_like_secret(f"my key is {secret}"),
                    f"{name} NOT detected: {secret[:12]}...",
                )

    def test_ordinary_prose_is_not_flagged(self) -> None:
        """Guard against a pattern so broad it refuses real lessons."""
        for text in (
            "We chose Postgres for the run ledger and it worked well.",
            "The sk- prefix identifies OpenAI keys; never paste one here.",
            "Deployed revision teas-prep-00003-czf to Cloud Run.",
            "Use a bearer token from the env, never inline.",
            "AC power draw dropped after the fix.",
        ):
            with self.subTest(text=text[:40]):
                self.assertFalse(
                    brain._looks_like_secret(text), f"false positive on: {text!r}"
                )

    def test_secret_in_tags_or_source_is_caught_not_just_text(self) -> None:
        secret = MODERN_SECRETS["anthropic"]
        for field in ("tags", "source", "summary", "id"):
            record = {"id": "x", "text": "a harmless lesson", "tags": [], "source": "claude"}
            record[field] = [secret] if field == "tags" else secret
            with self.subTest(field=field):
                self.assertEqual(
                    brain.record_secret_hit(record), field,
                    f"secret in '{field}' was not detected",
                )

    def test_clean_record_passes(self) -> None:
        record = {"id": "ok", "text": "Prefer fail-closed gates.",
                  "tags": ["lesson", "doctrine"], "source": "claude"}
        self.assertIsNone(brain.record_secret_hit(record))


class EveryWriterIsGated(unittest.TestCase):
    """The point of the fix: no verb is a way around the scan."""

    SECRET_RECORD = {
        "id": "leak-test-1",
        "ts": "2026-07-25T00:00:00Z",
        "source": "project-os",
        "type": "lesson",
        "text": "remember the key " + MODERN_SECRETS["anthropic"],
        "tags": [],
    }

    def _run_brain(self, argv, cwd):
        return subprocess.run(
            [sys.executable, str(BRAIN_DIR / "brain.py")] + argv,
            capture_output=True, text=True, cwd=cwd, check=False,
        )

    def test_export_from_file_refuses_a_secret_record(self) -> None:
        with tempfile.TemporaryDirectory(dir=str(BRAIN_DIR)) as tmp:
            src = Path(tmp) / "lessons.jsonl"
            src.write_text(json.dumps(self.SECRET_RECORD) + "\n", encoding="utf-8")
            proc = self._run_brain(["export", "--from", str(src)], cwd=str(ROOT))
            self.assertNotEqual(proc.returncode, 0,
                                f"export ACCEPTED a secret record: {proc.stdout}")
            self.assertIn("refuse", (proc.stdout + proc.stderr).lower())

    def test_brain_append_refuses_a_secret_record(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "brain_append.py"),
             "--line", json.dumps(self.SECRET_RECORD), "--no-reindex"],
            capture_output=True, text=True, cwd=str(ROOT), check=False,
        )
        self.assertNotEqual(proc.returncode, 0,
                            f"brain_append ACCEPTED a secret record: {proc.stdout}")
        self.assertIn("refused", (proc.stdout + proc.stderr).lower())

    def test_brain_append_still_accepts_a_clean_record(self) -> None:
        """The gate must not break the normal path."""
        clean = dict(self.SECRET_RECORD, text="A clean lesson about fail-closed gates.")
        env = dict(os.environ)
        with tempfile.TemporaryDirectory() as tmp:
            env["PROJECT_OS_SHARED_BRAIN"] = str(Path(tmp) / "shared-brain.jsonl")
            proc = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "brain_append.py"),
                 "--line", json.dumps(clean), "--no-reindex"],
                capture_output=True, text=True, cwd=str(ROOT), env=env, check=False,
            )
        # Either it appended, or it failed for an unrelated reason -- but it must
        # NOT have been refused by the privacy gate.
        self.assertNotIn("looks like it contains a secret",
                         proc.stdout + proc.stderr,
                         "gate false-positived on a clean record")


class GateIsReachableFromEveryWriter(unittest.TestCase):
    """Structural check: a new writer that skips the gate should be obvious."""

    def test_every_function_that_opens_the_brain_for_writing_is_gated(self) -> None:
        """Enumerate writers from the AST -- never from a hand-kept list.

        The first version of this test named cmd_export and cmd_import by hand
        and therefore missed cmd_save_chat, which was the single most-used
        write path and was completely ungated. The suite was green while the
        audit's core complaint was still true. Derive the list instead.
        """
        import ast
        src = (BRAIN_DIR / "brain.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        ungated = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            body = ast.get_source_segment(src, node) or ""
            # A writer is any function that opens BRAIN_FILE in a writing mode.
            writes = ("open(BRAIN_FILE" in body and
                      any(m in body for m in ('"a"', "'a'", '"w"', "'w'", '"a", encoding', '"w", encoding')))
            writes = writes or ("open(full" in body and '"w"' in body)
            if not writes:
                continue
            if "gate_record" not in body and "record_secret_hit" not in body:
                ungated.append(node.name)
        self.assertEqual(
            ungated, [],
            f"these functions write to the brain WITHOUT the privacy gate: {ungated}",
        )

    def test_save_chat_refuses_a_secret_in_a_tag(self) -> None:
        """The exact bypass the hand-written checklist missed."""
        import ast
        src = (BRAIN_DIR / "brain.py").read_text(encoding="utf-8")
        body = src.split("def cmd_save_chat(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("gate_record", body, "cmd_save_chat is not gated")

    def test_brain_append_references_the_gate(self) -> None:
        src = (ROOT / "scripts" / "brain_append.py").read_text(encoding="utf-8")
        self.assertIn("record_secret_hit", src,
                      "brain_append.py no longer calls the privacy gate")


if __name__ == "__main__":
    unittest.main()
