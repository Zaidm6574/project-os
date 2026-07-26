#!/usr/bin/env python3
"""The privacy gate must be exhaustive, not an allowlist of field names.

Audit finding (2026-07-26). `scripts/brain_append.py` promises records "must
never carry credentials" and delegates to `brain.record_secret_hit()`. But that
function scanned a fixed eight-name tuple (SCANNED_FIELDS) and early-returned
None for a non-dict payload, so two whole classes of write slipped through
UNSCANNED:

  a. a secret in any field outside the tuple -- a nested dict, `metadata`,
     `author`, `url`, a dict KEY, any custom key;
  b. a non-dict JSON line -- a bare string or list.

Both were verified by execution before this test was written. The gate now
walks the WHOLE payload and fails closed on anything it cannot read, so these
tests are the forcing function: reintroducing a field allowlist, or letting an
unreadable value through, turns them red.

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

SECRET = "sk-ant-api03-" + "A" * 40


def _run(argv, env_extra=None):
    env = dict(os.environ)
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable] + [str(a) for a in argv],
        capture_output=True, text=True, cwd=str(ROOT), env=env, check=False,
    )


class TheGateScansTheWholePayload(unittest.TestCase):
    """Every string, at any depth, under any key name."""

    def test_secret_in_a_deeply_nested_custom_field_is_caught(self) -> None:
        record = {
            "id": "nested-1",
            "text": "a harmless lesson",
            "tags": ["lesson"],
            "metadata": {
                "author": {
                    "credentials": {
                        "profile": [
                            {"note": "rotate this: " + SECRET},
                        ]
                    }
                }
            },
        }
        self.assertIsNotNone(
            brain.record_secret_hit(record),
            "a secret five levels down under custom keys was NOT detected",
        )
        self.assertEqual(brain.record_secret_hit(record), "metadata")
        self.assertEqual(
            brain.record_secret_path(record),
            "metadata.author.credentials.profile[0].note",
            "the refusal must name the exact value to redact",
        )

    def test_secret_in_an_unlisted_top_level_field_is_caught(self) -> None:
        for field in ("author", "url", "path", "raw", "env", "notes_2"):
            record = {"id": "x", "text": "clean", field: "KEY=" + SECRET}
            with self.subTest(field=field):
                self.assertEqual(brain.record_secret_hit(record), field)

    def test_secret_in_a_dict_key_is_caught(self) -> None:
        record = {"id": "x", "text": "clean", SECRET: "value"}
        self.assertEqual(brain.record_secret_hit(record), SECRET)

    def test_bare_string_payload_is_scanned(self) -> None:
        self.assertIsNotNone(
            brain.record_secret_hit("here is my key " + SECRET),
            "a non-dict payload skipped the gate entirely",
        )

    def test_bare_list_payload_is_scanned(self) -> None:
        self.assertIsNotNone(brain.record_secret_hit(["clean", [SECRET]]))
        self.assertIsNone(brain.record_secret_hit(["clean", ["also clean"]]))

    def test_no_field_allowlist_survives_in_the_scan(self) -> None:
        """Structural: the scan must not READ SCANNED_FIELDS.

        Checked over the AST, not the text, so a comment or docstring that
        mentions the old allowlist does not fail the test and does not let a
        real reference hide behind one either.
        """
        import ast
        src = (BRAIN_DIR / "brain.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        scanners = {"record_secret_hit", "record_secret_path",
                    "_iter_scannable_items", "_iter_scannable"}
        seen = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in scanners:
                seen.add(node.name)
                names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
                self.assertNotIn(
                    "SCANNED_FIELDS", names,
                    f"{node.name} is back to screening an allowlist of field "
                    "names instead of the whole payload",
                )
        self.assertEqual(seen, scanners, f"scan functions missing: {scanners - seen}")


class TheGateFailsClosed(unittest.TestCase):
    """"Could not scan" must never be reported as "clean"."""

    def test_unscannable_value_type_is_refused(self) -> None:
        class Opaque:
            def __repr__(self) -> str:  # pragma: no cover - not the point
                return "opaque"

        record = {"id": "x", "text": "clean", "blob": Opaque()}
        self.assertEqual(brain.record_secret_hit(record), "blob")

    def test_value_nested_deeper_than_the_cap_is_refused(self) -> None:
        deep = {"note": "clean"}
        for _ in range(brain.MAX_SCAN_DEPTH + 5):
            deep = {"child": deep}
        record = {"id": "x", "text": "clean", "tree": deep}
        self.assertIsNotNone(
            brain.record_secret_hit(record),
            "a payload nested past MAX_SCAN_DEPTH was passed through unscanned",
        )

    def test_non_string_scalars_do_not_crash_the_scan(self) -> None:
        record = {
            "id": "x", "text": "clean", "n": 7, "f": 1.5, "b": True,
            "nil": None, "empty": {}, "none_list": [None, 0, False],
            "raw": b"bytes are fine",
            1: "int key",
        }
        self.assertIsNone(brain.record_secret_hit(record))

    def test_secret_in_bytes_is_still_caught(self) -> None:
        record = {"id": "x", "text": "clean", "raw": SECRET.encode("utf-8")}
        self.assertEqual(brain.record_secret_hit(record), "raw")

    def test_self_referential_payload_terminates(self) -> None:
        record = {"id": "x", "text": "clean"}
        record["self"] = record
        record["also"] = [record]
        self.assertIsNone(brain.record_secret_hit(record))
        record["deep"] = {"leak": SECRET}
        self.assertIsNotNone(brain.record_secret_hit(record))

    def test_a_clean_record_still_passes(self) -> None:
        """Exhaustive must not mean paranoid: normal records must survive."""
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


class BrainAppendRefusesEndToEnd(unittest.TestCase):
    """The CLI, not just the library function."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.brain_file = Path(self.tmp.name) / "shared-brain.jsonl"
        self.env = {
            "PROJECT_OS_SHARED_BRAIN": str(self.brain_file),
            "BB_LOCK_DIR": str(Path(self.tmp.name) / "locks"),
        }
        self.addCleanup(self.tmp.cleanup)

    def _append(self, line):
        return _run([ROOT / "scripts" / "brain_append.py",
                     "--line", line, "--no-reindex"], self.env)

    def test_nested_custom_field_is_refused_and_nothing_is_written(self) -> None:
        line = json.dumps({"id": "n", "text": "clean",
                           "metadata": {"deploy": {"token_note": SECRET}}})
        r = self._append(line)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("REFUSED", r.stderr)
        self.assertFalse(self.brain_file.exists(),
                         "a refused record still reached the brain file")

    def test_bare_string_payload_is_refused(self) -> None:
        r = self._append(json.dumps("my key is " + SECRET))
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("REFUSED", r.stderr)
        self.assertFalse(self.brain_file.exists())

    def test_bare_object_is_required_even_when_clean(self) -> None:
        r = self._append(json.dumps(["a clean list of lessons"]))
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("JSON OBJECT", r.stderr)
        self.assertFalse(self.brain_file.exists())

    def test_duplicate_key_cannot_smuggle_a_secret_into_the_file(self) -> None:
        """json.loads keeps the LAST value; the raw line carried both."""
        r = self._append('{"id":"d","text":"%s","text":"clean lesson"}' % SECRET)
        blob = self.brain_file.read_text(encoding="utf-8") if self.brain_file.exists() else ""
        self.assertNotIn(SECRET, blob,
                         "a duplicate JSON key smuggled a secret past the gate")
        if r.returncode == 0:
            self.assertEqual(json.loads(blob)["text"], "clean lesson")

    def test_a_clean_record_is_still_appended(self) -> None:
        line = json.dumps({"id": "c", "type": "lesson",
                           "text": "Scan the whole payload, not eight fields.",
                           "tags": ["lesson"],
                           "metadata": {"depth": 3, "ok": True}})
        r = self._append(line)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rows = self.brain_file.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0])["metadata"]["depth"], 3)

    def test_append_fails_closed_when_the_gate_is_not_exhaustive(self) -> None:
        """A stale/shadowing brain.py must not be trusted to screen writes."""
        shadow = Path(self.tmp.name) / "shadow"
        (shadow / "addons" / "full-engine" / "brain").mkdir(parents=True)
        (shadow / "scripts").mkdir()
        for name in ("brain_append.py", "bb_lock.py"):
            (shadow / "scripts" / name).write_text(
                (ROOT / "scripts" / name).read_text(encoding="utf-8"),
                encoding="utf-8")
        # a gate that exists but only screens an allowlist
        (shadow / "addons" / "full-engine" / "brain" / "brain.py").write_text(
            "def record_secret_hit(record):\n    return None\n", encoding="utf-8")
        r = _run([shadow / "scripts" / "brain_append.py", "--line",
                  json.dumps({"id": "s", "text": "clean"}), "--no-reindex"],
                 self.env)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("REFUSED", r.stderr)
        self.assertFalse(self.brain_file.exists())


if __name__ == "__main__":
    unittest.main()
