"""Regression tests for the shared secret denylist and the harvest reader.

Every one of these locks a defect that was reproduced against the working tree
on 2026-07-26 (see .claude/repro-findings.md):

  * brain_append scanned dict VALUES only, so a credential used as a dict KEY
    was appended to the durable brain.
  * import_chat_history's local redactor was missing sk_live_, rk_live_, xox*
    and glpat-, writing live credentials verbatim into the "redacted" report.
  * four divergent copies of the denylist existed; the weakest was 25 lines
    from a correct one.
  * central_brain's cross-project push leaked secret-bearing lessons and then
    miscounted them to the user as ordinary "privacy/type gate" skips.
  * harvest honored a Rejected/Private-only marker on bullets only as a leading
    prefix, and marked a run harvested even when it could not read a single
    heading (losing every lesson in it permanently).
"""
import contextlib
import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
ADDONS = ROOT / "addons" / "full-engine"
sys.path.insert(0, str(SCRIPTS))

import secret_patterns  # noqa: E402

# One live-shaped sample per format the gates must refuse. Synthetic values.
PROVIDER_SECRETS = {
    "anthropic": "sk-ant-" + "A" * 24,
    "openai-project": "sk-proj-" + "B" * 24,
    "stripe-live-secret": "sk_live_" + "C" * 24,
    "stripe-live-restricted": "rk_live_" + "D" * 24,
    "slack-bot": "xoxb-" + "1" * 20,
    "slack-webhook": "https://hooks.slack.com/services/T00/B00/" + "x" * 24,
    "gitlab-pat": "glpat-" + "E" * 20,
    "github-oauth": "gho_" + "F" * 36,
    "google-oauth-secret": "GOCSPX-" + "G" * 24,
    "npm": "npm_" + "H" * 36,
    "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0." + "I" * 24,
    "dsn-password": "postgresql://admin:" + "sup3rs3cr3t@db.example.com:5432/app",
}


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class SharedDenylistTests(unittest.TestCase):
    def test_every_provider_format_is_recognized(self):
        for label, secret in PROVIDER_SECRETS.items():
            with self.subTest(label=label):
                self.assertIsNotNone(
                    secret_patterns.looks_like_secret(secret),
                    f"{label} is not in the shared denylist")

    def test_ordinary_text_and_host_ports_are_not_flagged(self):
        harmless = (
            "Use the pooler DSN in production, it survives restarts",
            "postgresql://reader@db.example.com:5432/app",  # no password
            "https://hooks.example.com/services/plain-webhook",
            "never reject user input silently without an error message",
            "eyes on the JWT expiry, but do not log it",
        )
        for text in harmless:
            with self.subTest(text=text[:40]):
                self.assertIsNone(secret_patterns.looks_like_secret(text))

    def test_pii_shapes_are_redaction_only_never_refusals(self):
        # A lesson mentioning an email must still be harvestable; only
        # credential formats are grounds to refuse a durable write.
        for text in ("email owner@example.com about the outage",
                     "ring 415-555-0199 for the on-call"):
            with self.subTest(text=text):
                self.assertIsNone(secret_patterns.looks_like_secret(text))
        pairs = secret_patterns.redaction_pairs()
        redacted = "email owner@example.com about the outage"
        for pattern, replacement in pairs:
            redacted = pattern.sub(replacement, redacted)
        self.assertNotIn("owner@example.com", redacted)

    def test_secret_used_as_a_dict_key_is_reported(self):
        # The gate walked values only: {"id": "k1", "<secret>": "note"} passed.
        reason = secret_patterns.secret_reason(
            {"id": "k1", PROVIDER_SECRETS["stripe-live-secret"]: "note"})
        self.assertIsNotNone(reason)
        self.assertIn("key", reason.lower())

    def test_sensitive_key_name_is_reported_at_any_depth(self):
        reason = secret_patterns.secret_reason(
            {"outer": [{"api_key": "anything at all"}]})
        self.assertIsNotNone(reason)

    def test_self_referential_structure_does_not_recurse_forever(self):
        loop = {"id": "l1"}
        loop["self"] = loop
        self.assertIsNone(secret_patterns.secret_reason(loop))

    def test_every_gate_loads_the_shared_denylist(self):
        """No gate may carry its own copy again — that drift was the root cause."""
        wiring = {
            "scripts/brain_append.py": "from secret_patterns import",
            "scripts/import_chat_history.py": "from secret_patterns import redaction_pairs",
            "addons/full-engine/brain/brain.py":
                '_load_from_scripts("secret_patterns"',
            "addons/full-engine/brain/central_brain.py":
                '_load_from_scripts("secret_patterns"',
            "addons/full-engine/memory/osvec_adapter.py":
                "_secret_patterns = _load_secret_patterns()",
        }
        for relative, expected in wiring.items():
            with self.subTest(module=relative):
                source = (ROOT / relative).read_text(encoding="utf-8")
                self.assertIn(expected, source)
                self.assertNotIn(
                    "sk-ant-", source,
                    f"{relative} looks like it re-grew a local pattern copy")


class BrainAppendKeyScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.brain = Path(self.tmp.name) / "shared-brain.jsonl"
        self.env = dict(os.environ)
        self.env.update({
            "PROJECT_OS_SHARED_BRAIN": str(self.brain),
            "MNEME_INDEX": str(Path(self.tmp.name) / "mneme_index.json"),
            "MNEME_EMBEDDER": "lexical",
            "BB_LOCK_DIR": str(Path(self.tmp.name) / "locks"),
        })

    def _append(self, record, extra=()):
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "brain_append.py"),
             "--line", json.dumps(record), "--no-reindex", *extra],
            capture_output=True, text=True, env=self.env)

    def test_credential_as_a_dict_key_is_refused_and_nothing_is_written(self):
        result = self._append(
            {"id": "k1", PROVIDER_SECRETS["stripe-live-secret"]: "note"})
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("secret", result.stderr.lower())
        self.assertFalse(self.brain.exists(),
                         "a refused record must not create the brain")

    def test_slack_token_at_ten_characters_is_refused(self):
        # This gate required 16 trailing chars while its siblings required 10.
        result = self._append({"id": "k2", "text": "leak xoxp-" + "9" * 12})
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertFalse(self.brain.exists())

    def test_a_freshly_created_brain_is_not_world_readable(self):
        """Durable lessons must not land 0644 on a fresh install.

        The gate scans every record for credentials before it is written, which
        is the project's own statement that this file is sensitive. Every
        sibling path already protects it — central_brain creates the central
        brain 0600, the installer migrates at 0600, harvest stages packets at
        0600 — but a project-local brain created by a plain append inherited
        the umask and came out world-readable.
        """
        result = self._append({"id": "p1", "type": "lesson",
                               "text": "a durable lesson worth protecting"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.brain.exists())
        mode = stat.S_IMODE(self.brain.stat().st_mode)
        self.assertEqual(
            mode & 0o077, 0,
            "shared brain is group/world accessible (mode %o)" % mode)

    def test_non_utf8_brain_is_a_clean_error_not_a_traceback(self):
        self.brain.parent.mkdir(parents=True, exist_ok=True)
        self.brain.write_bytes(b'{"id":"good","text":"keep"}\n\xff\xfe bad bytes\n')
        result = self._append({"id": "fresh", "text": "a new durable lesson"})
        self.assertNotIn("Traceback", result.stderr)
        if result.returncode == 0:
            rows = self.brain.read_bytes().splitlines()
            self.assertIn(b'"id": "fresh"', b"\n".join(rows))


class ImportChatRedactionTests(unittest.TestCase):
    def test_all_provider_formats_are_redacted_from_the_report(self):
        module = load_module(SCRIPTS / "import_chat_history.py",
                            "import_chat_history_redaction")
        for label, secret in PROVIDER_SECRETS.items():
            with self.subTest(label=label):
                self.assertNotIn(secret, module.redact(f"my key is {secret} ok"))

    def test_written_report_never_contains_a_live_credential(self):
        module = load_module(SCRIPTS / "import_chat_history.py",
                            "import_chat_history_report")
        with tempfile.TemporaryDirectory() as tmp:
            export = Path(tmp) / "chat.txt"
            secret = PROVIDER_SECRETS["stripe-live-secret"]
            export.write_text(
                f"i want the deploy to use {secret} for the payment step please\n",
                encoding="utf-8")
            out = Path(tmp) / "report.md"
            lines = module.clean_lines(module.read_export(export))
            module.write_summary(lines, out, 30, include_excerpts=True)
            body = out.read_text(encoding="utf-8")
        self.assertNotIn(secret, body)
        self.assertIn("STRIPE", body.upper())


class CentralBrainLeakTests(unittest.TestCase):
    """A cross-project push must not carry credentials, and must say so."""

    def setUp(self):
        environment = mock.patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("PROJECT_OS_SHARED_BRAIN", None)

    def _layout(self, root, lessons):
        central_script = root / "brain" / "central_brain.py"
        central_script.parent.mkdir(parents=True)
        import shutil
        shutil.copy2(ADDONS / "brain" / "central_brain.py", central_script)
        scripts = root / "scripts"
        scripts.mkdir()
        for name in ("bb_lock.py", "secret_patterns.py", "brain_paths.py"):
            shutil.copy2(SCRIPTS / name, scripts / name)
        project_brain = root / "project" / "brain" / "shared-brain.jsonl"
        project_brain.parent.mkdir(parents=True)
        # Plain lesson records (no chat-approval vocabulary) so syncable_summary
        # lets them through — the only thing under test here is the secret gate.
        project_brain.write_text(
            "".join(json.dumps({
                "id": f"leak-{i}", "source": "project-os", "type": "lesson",
                "text": text, "tags": [],
            }) + "\n" for i, text in enumerate(lessons)),
            encoding="utf-8")
        return load_module(central_script, "central_brain_leak"), root / "project"

    def test_secret_bearing_lessons_are_neither_pushed_nor_miscounted(self):
        leaky = [
            f"post failures to {PROVIDER_SECRETS['slack-webhook']}",
            f"connect with {PROVIDER_SECRETS['dsn-password']}",
            f"the service token is {PROVIDER_SECRETS['jwt']}",
            f"publish with {PROVIDER_SECRETS['npm']}",
        ]
        clean = "always write the rollback step before the deploy step"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            central_brain, project = self._layout(root, leaky + [clean])
            central = root / "central"
            # Central pushes hold the bb_lock; keep its lock dir inside the
            # test tmp instead of the runner's HOME.
            with mock.patch.object(central_brain.bb_lock, "LOCK_DIR",
                                   str(root / "locks")), \
                    contextlib.redirect_stdout(io.StringIO()):
                pushed, skipped = central_brain.push(central, project, "leak-test")
            rows = central_brain.read_jsonl(central / "shared-brain.jsonl")

        self.assertEqual(pushed, 1, "only the clean lesson may cross projects")
        self.assertEqual([r["text"] for r in rows], [clean])
        self.assertEqual(int(skipped), 4)
        # The old message called every skip a "privacy/type gate" skip, hiding
        # that four of them were credentials.
        self.assertEqual(getattr(skipped, "secret", None), 4, skipped)
        self.assertIn("secret-looking", skipped.describe())

    def test_skip_tally_still_compares_equal_to_a_plain_int(self):
        central_brain = load_module(ADDONS / "brain" / "central_brain.py",
                                    "central_brain_tally")
        tally = central_brain.SkipTally(2, 3)
        self.assertEqual(tally, 5)
        self.assertEqual((1, tally), (1, 5))
        self.assertEqual(central_brain.SkipTally(0, 0).describe(), "none")


class HarvestReaderTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(SCRIPTS))
        import harvest
        self.harvest = harvest

    def test_reject_marker_is_honored_in_every_position(self):
        md = (
            "## Lessons\n"
            "- Rejected: leading marker never harvests\n"
            "- trailing marker never harvests - Rejected\n"
            "- em dash marker never harvests — Private-only\n"
            "- bracketed marker never harvests (Rejected)\n"
            "- Status: rejected\n"
        )
        self.assertEqual(list(self.harvest.bullets_by_section(md)), [])

    def test_prose_that_mentions_rejection_still_harvests_in_full(self):
        md = (
            "## Lessons\n"
            "- never reject user input silently without an error message\n"
            "- use the pooler DSN — it survives a restart\n"
            "- Approved for reuse: write the rollback step first\n"
        )
        got = [text for _, text in self.harvest.bullets_by_section(md)]
        self.assertEqual(got, [
            "never reject user input silently without an error message",
            "use the pooler DSN — it survives a restart",
            "write the rollback step first",
        ])

    def test_bullet_status_leaves_colon_prose_intact(self):
        status, text = self.harvest.bullet_status(
            "Deploy checklist: run migrations before the swap")
        self.assertEqual(status, "")
        self.assertEqual(text, "Deploy checklist: run migrations before the swap")

    def test_recognized_sections_sees_every_heading_dialect(self):
        md = ("## Lessons\n## user-preference\n## Next-Kickoff Safeguards\n"
              "## Random Notes\n")
        found = self.harvest.recognized_sections(md)
        self.assertEqual(len(found), 3, found)
        self.assertEqual(self.harvest.recognized_sections("## Random Notes\n"), [])

    def test_content_free_text_is_not_a_duplicate(self):
        # is_dupe returned True for empty-after-normalize text, so the CLI told
        # the user it was "already in the brain" against an empty brain.
        norms = [self.harvest.norm("keep this too")]  # brain_norms() returns normalized
        self.assertFalse(self.harvest.is_dupe("---", []))
        self.assertFalse(self.harvest.is_dupe("!!!", norms))
        self.assertTrue(self.harvest.is_dupe("keep this", norms))


class HarvestScanMarkerTests(unittest.TestCase):
    """cmd_scan must not retire a run whose harvest file it could not read."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        import shutil
        for relative in ("scripts/harvest.py", "scripts/brain_append.py",
                         "scripts/bb_lock.py", "scripts/secret_patterns.py"):
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)
        mneme = root / "memory" / "mneme_adapter.py"
        mneme.parent.mkdir(parents=True)
        mneme.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
        self.run_dir = root / "runs" / "demo-run"
        self.run_dir.mkdir(parents=True)
        self.packets = root / "blackboard" / "packets"
        self.packets.mkdir(parents=True)
        self.brain = root / "brain" / "shared-brain.jsonl"
        self.harvest = load_module(root / "scripts" / "harvest.py",
                                  "harvest_marker_gate")
        self.harvest.ROOT = str(root)
        self.harvest.RUNS = str(root / "runs")
        self.harvest.PACKETS = str(self.packets)
        self.harvest.SHARED_BRAIN = str(self.brain)

    def _scan(self):
        with contextlib.redirect_stdout(io.StringIO()) as out, \
                contextlib.redirect_stderr(io.StringIO()) as err:
            rc = self.harvest.cmd_scan("demo-run")
        return rc, out.getvalue() + err.getvalue()

    def _write(self, body):
        (self.run_dir / "19-memory-harvest.md").write_text(body, encoding="utf-8")

    def test_unreadable_headings_refuse_without_retiring_the_run(self):
        self._write("### lessons\n- a real lesson worth keeping forever\n")
        rc, msg = self._scan()
        self.assertEqual(rc, 2)
        self.assertFalse((self.run_dir / ".harvested").exists())
        self.assertIn("recognized section heading", msg)
        self.assertIn("lesson", msg, "must list the accepted headings")

    def test_recognized_but_empty_section_still_completes_the_harvest(self):
        self._write("## Lessons\n\n")
        rc, msg = self._scan()
        self.assertEqual(rc, 0, msg)
        self.assertTrue((self.run_dir / ".harvested").exists())

    def test_content_free_candidates_are_not_reported_as_brain_members(self):
        self._write("## Lessons\n- ---\n- ...\n")
        rc, msg = self._scan()
        self.assertEqual(rc, 0, msg)
        self.assertIn("no harvestable text", msg)
        self.assertNotIn("already in the brain", msg)


class MnemeSourceStrictnessTests(unittest.TestCase):
    def _adapter(self, brain, root):
        sys.path.insert(0, str(ROOT / "memory"))
        try:
            import mneme_adapter
        finally:
            sys.path.pop(0)
        mneme_adapter.SHARED_BRAIN = str(brain)
        mneme_adapter.ROOT = str(root)
        return mneme_adapter

    def test_non_utf8_source_fails_the_build_instead_of_emptying_the_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            brain = root / "brain" / "shared-brain.jsonl"
            brain.parent.mkdir(parents=True)
            brain.write_bytes(b'{"id":"l1","text":"a durable lesson"}\n\xff\n')
            adapter = self._adapter(brain, root)
            with self.assertRaises(UnicodeDecodeError):
                adapter._gather()

    def test_malformed_lines_are_skipped_loudly_and_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            brain = root / "brain" / "shared-brain.jsonl"
            brain.parent.mkdir(parents=True)
            brain.write_text(
                json.dumps({"id": "l1", "text": "a durable lesson"}) + "\n"
                + "{oops\n" + "42\n",
                encoding="utf-8")
            adapter = self._adapter(brain, root)
            with contextlib.redirect_stderr(io.StringIO()) as err:
                items = adapter._gather()
            warning = err.getvalue()
        self.assertEqual([i for i, _, _ in items], ["l1"])
        self.assertIn("skipped 2 unreadable brain line(s)", warning)
        self.assertIn("NOT in the index", warning)


if __name__ == "__main__":
    unittest.main()
