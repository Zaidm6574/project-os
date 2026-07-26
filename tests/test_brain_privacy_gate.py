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

Third finding (2026-07-26): the save-chat and brain_append guards here were
SOURCE-TEXT assertions -- "does the string 'gate_record' appear in the function"
-- not behaviour. Narrowing the call to `gate_record({"text": text}, ...)`, a
plausible refactor, satisfied every substring while re-opening the original leak
(a secret in --tag/--source/--id written verbatim to shared-brain.jsonl), and
the whole suite stayed green. They now RUN the writers. The one remaining
structural test is the AST sweep, whose job is different: it catches a NEW,
unknown writer, which no behavioural test can enumerate in advance.

Fourth finding (2026-07-26, judge pass): the behavioural save-chat cases all ran
`--mode summary`, so a gate made conditional on the mode
(`if args.mode == "summary": gate_record(...)`) still passed the whole suite
while `save-chat --mode raw --tag <secret>` wrote the credential verbatim. The
field matrix is now crossed with BOTH modes, refusals and control alike. Same
pass: the brain_append probes redirected the brain file into a tempdir but not
`bb_lock`, which leaves a permanent `<hash>.lock.guard` under
`~/.project-os/locks`; BB_LOCK_DIR is now redirected too, and the probes assert
the real HOME brain never sees their id.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
BRAIN_DIR = ROOT / "addons" / "full-engine" / "brain"

sys.path.insert(0, str(BRAIN_DIR))
import brain  # noqa: E402


# Where a brain_append subprocess writes when nothing redirects it. Read, never
# written -- its job here is to prove the redirect is still in force.
HOME_BRAIN = Path(os.path.expanduser("~/.project-os/central-brain/shared-brain.jsonl"))


def _sandbox_env(tmp: Path, brain_path: Path) -> dict:
    """Subprocess env with every HOME-rooted write of brain_append redirected.

    Two of them, and only the first was covered before:
      * PROJECT_OS_SHARED_BRAIN -- default ~/.project-os/central-brain/
        shared-brain.jsonl. An unredirected probe once appended its own fake
        credential to the developer's real brain, which central_brain.py pull
        then fans out to other projects.
      * BB_LOCK_DIR -- default ~/.project-os/locks. brain_append appends under
        bb_lock, and the lock leaves a PERSISTENT <hash>.lock.guard behind, so
        every run of this file littered HOME.
    """
    env = dict(os.environ)
    env["PROJECT_OS_SHARED_BRAIN"] = str(brain_path)
    env["BB_LOCK_DIR"] = str(tmp / "locks")
    return env


@contextlib.contextmanager
def home_brain_guard(test: unittest.TestCase, marker: str):
    """Fail if the block adds an occurrence of `marker` to the REAL brain.

    A delta, not an absolute count: on a machine where the OLD unredirected
    version of this test already leaked, the historical lines must not make the
    guard cry wolf -- but one NEW line must.
    """
    def occurrences() -> int:
        if not HOME_BRAIN.exists():
            return 0
        return HOME_BRAIN.read_text(encoding="utf-8", errors="replace").count(marker)

    before = occurrences()
    yield
    test.assertEqual(
        occurrences(), before,
        f"a test record ({marker}) was written to the REAL brain at "
        f"{HOME_BRAIN}; PROJECT_OS_SHARED_BRAIN is no longer redirecting it",
    )


def _load_brain(name: str):
    """Load a PRIVATE copy of brain.py so patching its globals can't leak.

    cmd_save_chat reads module-level ROOT/BRAIN_FILE, so exercising it means
    redirecting those at a tempdir. Doing that on the shared `brain` module
    would race every other test in the suite that imported the same object.
    """
    spec = importlib.util.spec_from_file_location(name, str(BRAIN_DIR / "brain.py"))
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


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

    @staticmethod
    @contextlib.contextmanager
    def _isolated_brain():
        """Run brain.py from a throwaway COPY, then assert nothing was written.

        BRAIN_FILE is derived from the script's own directory, so running the
        in-repo brain.py meant that the instant the export gate regressed, this
        probe's own credential was appended to the live shared-brain.jsonl --
        the test caused the leak it was hunting. Copy the script into a tempdir
        instead; the copy's BRAIN_FILE lands beside it, and its emptiness
        becomes an assertable outcome rather than a hope.
        """
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            (project / "brain").mkdir(parents=True)
            script = project / "brain" / "brain.py"
            shutil.copy2(str(BRAIN_DIR / "brain.py"), str(script))
            yield project, script, project / "brain" / "shared-brain.jsonl"

    def assertNothingWritten(self, path: Path, label: str) -> None:
        written = path.read_text(encoding="utf-8") if path.exists() else ""
        self.assertEqual(written, "", f"{label} wrote a record holding a secret")

    def test_export_from_file_refuses_a_secret_record(self) -> None:
        with self._isolated_brain() as (project, script, brain_file):
            src = project / "lessons.jsonl"
            src.write_text(json.dumps(self.SECRET_RECORD) + "\n", encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(script), "export", "--from", str(src)],
                capture_output=True, text=True, cwd=str(project), check=False,
            )
            self.assertNotEqual(proc.returncode, 0,
                                f"export ACCEPTED a secret record: {proc.stdout}")
            self.assertIn("refuse", (proc.stdout + proc.stderr).lower())
            self.assertNothingWritten(brain_file, "export")

    def test_brain_append_refuses_a_secret_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # Without the redirects the default targets are HOME: a regression
            # made this test append its own fake credential to the developer's
            # REAL brain (and central_brain pull would then fan it out).
            target = Path(tmp) / "shared-brain.jsonl"
            with home_brain_guard(self, self.SECRET_RECORD["id"]):
                proc = subprocess.run(
                    [sys.executable, str(ROOT / "scripts" / "brain_append.py"),
                     "--line", json.dumps(self.SECRET_RECORD), "--no-reindex"],
                    capture_output=True, text=True, cwd=str(ROOT),
                    env=_sandbox_env(Path(tmp), target), check=False,
                )
            self.assertNotEqual(proc.returncode, 0,
                                f"brain_append ACCEPTED a secret record: {proc.stdout}")
            self.assertIn("refused", (proc.stdout + proc.stderr).lower())
            self.assertNothingWritten(target, "brain_append")

    def test_brain_append_still_accepts_a_clean_record(self) -> None:
        """The gate must not break the normal path."""
        clean = dict(self.SECRET_RECORD, id="clean-append-probe",
                     text="A clean lesson about fail-closed gates.")
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "shared-brain.jsonl"
            with home_brain_guard(self, clean["id"]):
                proc = subprocess.run(
                    [sys.executable, str(ROOT / "scripts" / "brain_append.py"),
                     "--line", json.dumps(clean), "--no-reindex"],
                    capture_output=True, text=True, cwd=str(ROOT),
                    env=_sandbox_env(Path(tmp), target), check=False,
                )
            # Either it appended, or it failed for an unrelated reason -- but it
            # must NOT have been refused by the privacy gate.
            self.assertNotIn("looks like it contains a secret",
                             proc.stdout + proc.stderr,
                             "gate false-positived on a clean record")
            # This is the path that takes the lock. If BB_LOCK_DIR stopped being
            # honoured the lock (and its permanent .lock.guard) would land in
            # ~/.project-os/locks instead.
            if proc.returncode == 0:
                self.assertTrue(
                    (Path(tmp) / "locks").exists(),
                    "brain_append appended without using the sandboxed BB_LOCK_DIR; "
                    "check that the lock is not being taken in HOME",
                )


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


class SaveChatGatesTheWholeRecord(unittest.TestCase):
    """The exact bypass the hand-written checklist missed -- run, don't read.

    This used to assert that the substring "gate_record" appeared somewhere in
    cmd_save_chat's source. That is a proxy, not behaviour: narrowing the call
    to `gate_record({"text": text}, ...)` -- a plausible refactor -- keeps the
    substring, keeps the whole suite green, and re-opens the 2026-07-25 finding
    verbatim (a secret in --tag/--source/--id is written to shared-brain.jsonl,
    because only --summary is screened by _chat_text). So call the command.

    Every case runs in BOTH modes. Running only `--mode summary` left a second
    survivor: `if args.mode == "summary": gate_record(record, ...)` kept the
    entire suite green while `save-chat --mode raw --tag <secret>` returned 0
    and wrote the credential verbatim.
    """

    SECRET = MODERN_SECRETS["anthropic"]
    MODES = ("summary", "raw")

    # A secret in a field the chat-text screen never looks at.
    SECRET_FIELDS = {
        "tag": {"tag": [MODERN_SECRETS["anthropic"]]},
        "tag_among_clean_tags": {"tag": ["lesson", MODERN_SECRETS["anthropic"], "privacy"]},
        "source": {"source": MODERN_SECRETS["anthropic"]},
        "id": {"id": MODERN_SECRETS["anthropic"]},
    }

    @staticmethod
    @contextlib.contextmanager
    def _sandboxed_brain(name: str):
        """Yield (module, brain_file) with ROOT/BRAIN_FILE inside a tempdir."""
        module = _load_brain(name)
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            brain_file = project / "brain" / "shared-brain.jsonl"
            brain_file.parent.mkdir(parents=True)
            with mock.patch.object(module, "ROOT", str(project)), \
                    mock.patch.object(module, "BRAIN_FILE", str(brain_file)):
                yield module, brain_file

    @staticmethod
    def _args(**over):
        base = dict(summary="A clean lesson about fail-closed gates.",
                    summary_file=None, id="chat-gate-test", kind="lesson",
                    tag=[], source=None, mode="summary")
        base.update(over)
        return argparse.Namespace(**base)

    def test_save_chat_refuses_a_secret_outside_the_text_field(self) -> None:
        for mode in self.MODES:
            for label, over in self.SECRET_FIELDS.items():
                with self.subTest(field=label, mode=mode):
                    name = "brain_savechat_%s_%s" % (mode, label)
                    with self._sandboxed_brain(name) as (mod, bf):
                        buf = io.StringIO()
                        with self.assertRaises(SystemExit) as raised, \
                                contextlib.redirect_stdout(buf):
                            mod.cmd_save_chat(self._args(mode=mode, **over))
                        self.assertIn(
                            "refuse", str(raised.exception).lower(),
                            f"save-chat --mode {mode} did not refuse a secret in {label}")
                        written = bf.read_text(encoding="utf-8") if bf.exists() else ""
                        self.assertNotIn(
                            self.SECRET, written,
                            f"secret in {label} was WRITTEN to the brain in {mode} mode")
                        self.assertEqual(
                            written, "",
                            f"save-chat --mode {mode} wrote a record despite a "
                            f"secret in {label}")

    def test_save_chat_still_appends_a_clean_record(self) -> None:
        """Control: the gate must refuse secrets, not refuse everything.

        Also per mode -- without the raw half, a gate that refused every raw
        record would satisfy the refusal test above for free.
        """
        for mode in self.MODES:
            with self.subTest(mode=mode):
                with self._sandboxed_brain("brain_savechat_clean_" + mode) as (mod, bf):
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(mod.cmd_save_chat(self._args(
                            mode=mode, tag=["lesson", "privacy"],
                            source="project-os")), 0)
                    record = json.loads(bf.read_text(encoding="utf-8"))
                    self.assertEqual(record["id"], "chat-gate-test")
                    self.assertIn("privacy", record["tags"])
                    self.assertEqual(record["raw_chat"], mode == "raw")


class BrainAppendGatesTheWholeRecord(unittest.TestCase):
    """brain_append's gate, exercised on a field _chat_text never sees.

    The predecessor test only asserted the string "record_secret_hit" appeared
    in brain_append.py -- which survives in a comment and in the getattr()
    fallback even if `field = _gate(record)` is neutered to `field = None`.
    """

    SECRET = MODERN_SECRETS["stripe_live"]

    def _append(self, record, tmp: Path, brain_path: Path):
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "brain_append.py"),
             "--line", json.dumps(record), "--no-reindex"],
            capture_output=True, text=True, cwd=str(ROOT),
            env=_sandbox_env(tmp, brain_path), check=False,
        )

    def test_secret_in_a_tag_is_refused_and_nothing_is_written(self) -> None:
        record = {"id": "append-tag-leak", "ts": "2026-07-26T00:00:00Z",
                  "source": "project-os", "type": "lesson",
                  "text": "a harmless lesson", "tags": ["lesson", self.SECRET]}
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "shared-brain.jsonl"
            with home_brain_guard(self, record["id"]):
                proc = self._append(record, Path(tmp), target)
            self.assertNotEqual(proc.returncode, 0,
                                f"brain_append ACCEPTED a secret tag: {proc.stdout}")
            self.assertIn("refused", (proc.stdout + proc.stderr).lower())
            written = target.read_text(encoding="utf-8") if target.exists() else ""
            self.assertEqual(written, "", "brain_append wrote a record holding a secret")

    def test_clean_record_with_tags_is_appended(self) -> None:
        """Control, so 'refuse everything' cannot pass the test above."""
        record = {"id": "append-clean", "ts": "2026-07-26T00:00:00Z",
                  "source": "project-os", "type": "lesson",
                  "text": "Prefer fail-closed gates.", "tags": ["lesson", "privacy"]}
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "shared-brain.jsonl"
            with home_brain_guard(self, record["id"]):
                proc = self._append(record, Path(tmp), target)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["id"],
                             "append-clean")
            # The append path takes the lock; it must have landed in the sandbox,
            # not in ~/.project-os/locks (see _sandbox_env).
            self.assertTrue((Path(tmp) / "locks").exists(),
                            "the bb_lock did not use the sandboxed BB_LOCK_DIR")


if __name__ == "__main__":
    unittest.main()
