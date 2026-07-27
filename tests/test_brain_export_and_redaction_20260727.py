"""Lane C hardening regressions (2026-07-27).

Two independent audit findings, each pinned so the fix cannot silently rot:

Fix (3) -- DATA-LOSS-LOOKS-LIKE-SUCCESS in brain.py's export path.
  `export --from FILE` fed its source through _read_jsonl, which SKIPPED a
  corrupt/unparseable line and then printed "export: N new lesson(s) appended"
  and exited 0 -- a lost lesson reported as success. The same command also
  raised RAW TRACEBACKS on a case-variant extension (FOO.JSONL), a typo'd /
  missing path, non-UTF-8 bytes, and a newline-delimited file mislabelled
  `.json`. After the fix: a corrupt line is SURFACED and aborts non-zero; a
  valid case-variant extension is read as JSONL; and the typo/encoding/
  mislabel cases give a clean, non-zero refusal with NO traceback.

Fix (4) -- CHAT-IMPORTER REDACTION in scripts/import_chat_history.py.
  The generic keyword catch-all ((secret|password|token|...) + sep + token)
  OVER-matched ordinary prose ("the secret: happiness ...") and the redactor
  ate the sentence; and a TRUNCATED PEM (BEGIN header, NO END footer) had only
  its header line redacted while the base64 key BODY survived in the report.
  After the fix: prose survives, a real credential is still redacted, and a
  truncated PEM's body is fully removed.

Each assertion is written to BITE when its own fix is reverted (see the
mutationProof in the handoff). Stdlib only; passes on the 3.9.6 CI floor. No
fixture is a live credential, and every write lands in a tempdir sandbox -- the
real shared brain is never touched.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRAIN = ROOT / "addons" / "full-engine" / "brain" / "brain.py"
IMPORTER = ROOT / "scripts" / "import_chat_history.py"


# --------------------------------------------------------------------------- #
# Fix (3): export --from data-loss and traceback cases (CLI-level, isolated).  #
# --------------------------------------------------------------------------- #
class ExportFromFileSurfacesCorruption(unittest.TestCase):
    """`export --from` must never lose or crash on a bad source silently."""

    def _run_export(self, source_name, write, *, binary=False):
        """Copy brain.py into a throwaway project so BRAIN_FILE is sandboxed.

        brain.py derives BRAIN_FILE from its own directory, so running the
        in-repo copy would append to the developer's real shared brain. The
        copy's brain file lands beside it inside the tempdir instead, and its
        contents become an assertable outcome. Returns (returncode, combined
        output, brain-file text).
        """
        tmp = tempfile.mkdtemp(prefix="lanec-export-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        project = Path(tmp) / "project"
        (project / "brain").mkdir(parents=True)
        script = project / "brain" / "brain.py"
        shutil.copy2(BRAIN, script)
        src = project / source_name
        if binary:
            src.write_bytes(write)
        else:
            src.write_text(write, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(script), "export", "--from", str(src)],
            capture_output=True, text=True, cwd=str(project),
            stdin=subprocess.DEVNULL, timeout=60,
        )
        brain_file = project / "brain" / "shared-brain.jsonl"
        written = brain_file.read_text(encoding="utf-8") if brain_file.exists() else ""
        return proc.returncode, proc.stdout + proc.stderr, written

    def test_corrupt_middle_line_is_surfaced_and_aborts_nonzero(self):
        # Valid, CORRUPT, valid. The old lenient read dropped line 2 and
        # exported lines 1 and 3, exiting 0 -- data loss reported as success.
        source = (
            '{"id":"L1","type":"lesson","text":"first lesson"}\n'
            '{ this is not valid json at all \n'
            '{"id":"L3","type":"lesson","text":"third lesson"}\n'
        )
        rc, out, written = self._run_export("lessons.jsonl", source)
        self.assertNotEqual(rc, 0,
                            "a corrupt source line must abort, not report success:\n" + out)
        self.assertIn("line 2", out,
                      "the corrupt line must be surfaced by number:\n" + out)
        self.assertEqual(written, "",
                         "no lesson may be exported when a source line is corrupt")

    def test_case_variant_extension_is_read_as_jsonl(self):
        # FOO.JSONL is JSONL; the old case-sensitive check sent it to json.load
        # and tracebacked on the newline between records.
        source = (
            '{"id":"C1","type":"lesson","text":"upper-ext lesson one"}\n'
            '{"id":"C2","type":"lesson","text":"upper-ext lesson two"}\n'
        )
        rc, out, written = self._run_export("LESSONS.JSONL", source)
        self.assertEqual(rc, 0, "a valid .JSONL source must export cleanly:\n" + out)
        self.assertNotIn("Traceback", out)
        ids = {json.loads(line)["id"] for line in written.splitlines() if line.strip()}
        self.assertEqual(ids, {"C1", "C2"},
                         "both records from the case-variant source must export")

    def test_missing_source_path_gives_clean_error_not_traceback(self):
        # A typo'd/missing path: the old .json branch hit FileNotFoundError
        # inside open() (a raw traceback); .jsonl silently exported nothing.
        for name in ("typo.json", "typo.jsonl"):
            with self.subTest(name=name):
                rc, out, written = self._run_missing(name=name)
                self.assertNotEqual(rc, 0, "a missing source must not exit 0:\n" + out)
                self.assertNotIn("Traceback", out,
                                 "missing source raised a raw traceback:\n" + out)
                self.assertIn("does not exist", out)
                self.assertEqual(written, "")

    def _run_missing(self, name):
        tmp = tempfile.mkdtemp(prefix="lanec-missing-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        project = Path(tmp) / "project"
        (project / "brain").mkdir(parents=True)
        script = project / "brain" / "brain.py"
        shutil.copy2(BRAIN, script)
        proc = subprocess.run(
            [sys.executable, str(script), "export", "--from", str(project / name)],
            capture_output=True, text=True, cwd=str(project),
            stdin=subprocess.DEVNULL, timeout=60,
        )
        brain_file = project / "brain" / "shared-brain.jsonl"
        written = brain_file.read_text(encoding="utf-8") if brain_file.exists() else ""
        return proc.returncode, proc.stdout + proc.stderr, written

    def test_non_utf8_source_gives_clean_error_not_traceback(self):
        # A 0xFF byte is never valid UTF-8. The old read used the platform
        # default encoding and either tracebacked or mangled the record.
        blob = b'{"id":"U1","type":"lesson","text":"caf\xff corrupt bytes"}\n'
        rc, out, written = self._run_export("bad.jsonl", blob, binary=True)
        self.assertNotEqual(rc, 0, "non-UTF-8 source must abort:\n" + out)
        self.assertNotIn("Traceback", out, "non-UTF-8 raised a raw traceback:\n" + out)
        self.assertIn("UTF-8", out)
        self.assertEqual(written, "")

    def test_jsonl_content_mislabelled_dot_json_gives_clean_error(self):
        # Newline-delimited JSON in a `.json` file used to reach json.load and
        # raise a raw "Extra data" JSONDecodeError traceback.
        source = (
            '{"id":"M1","type":"lesson","text":"one"}\n'
            '{"id":"M2","type":"lesson","text":"two"}\n'
        )
        rc, out, written = self._run_export("mislabelled.json", source)
        self.assertNotEqual(rc, 0, "mislabelled JSONL must abort:\n" + out)
        self.assertNotIn("Traceback", out, "mislabelled .json raised a traceback:\n" + out)
        self.assertIn("not valid JSON", out)
        self.assertEqual(written, "")

    def test_a_clean_source_still_exports(self):
        # Near-miss: the guards must not break the happy path.
        source = '{"id":"OK1","type":"lesson","text":"a perfectly good lesson"}\n'
        rc, out, written = self._run_export("good.jsonl", source)
        self.assertEqual(rc, 0, out)
        self.assertIn("OK1", written)


# --------------------------------------------------------------------------- #
# Fix (4): chat-importer redaction application (unit-level on redact()).       #
# --------------------------------------------------------------------------- #
class ChatImporterRedaction(unittest.TestCase):
    """Prose must survive; a truncated PEM's body must not."""

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "lanec_importer_under_test", str(IMPORTER))
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)
        cls.patterns = cls.mod.redaction_patterns(cls.mod.load_credential_patterns())

    def redact(self, text):
        counts = Counter()
        return self.mod.redact(text, self.patterns, counts), counts

    def test_ordinary_prose_with_keyword_colon_word_survives(self):
        # The exact shape the generic catch-all over-matched: a keyword, a
        # separator, then a plain prose word (>=6 chars, so the pattern fires).
        prose_lines = [
            "The secret: happiness comes from within and cannot be bought.",
            "My password: forgotten again, need to reset it tomorrow morning.",
            "The bearer: whoever holds this note owes nothing further today.",
            "Design token: spacing scale should follow an eight point grid.",
        ]
        for line in prose_lines:
            with self.subTest(line=line[:32]):
                out, counts = self.redact(line)
                self.assertEqual(out, line,
                                 "ordinary prose must pass through untouched")
                self.assertEqual(sum(counts.values()), 0)

    def test_a_real_labelled_credential_is_still_redacted(self):
        # Control for the prose gate: a high-entropy value after the same
        # keyword must STILL be redacted, so the gate above did not simply
        # disable the catch-all.
        line = "config had api_key=Ab3Xy9Qz7Lm2Ns5Pk8 wired in by mistake"
        out, counts = self.redact(line)
        self.assertIn("[REDACTED_CREDENTIAL]", out)
        self.assertNotIn("Ab3Xy9Qz7Lm2Ns5Pk8", out)
        self.assertEqual(sum(counts.values()), 1)

    def test_truncated_pem_body_is_fully_redacted(self):
        # BEGIN header, base64 body, NO END footer. The old widener matched
        # only whole BEGIN...END blocks, so brain.py's header-only pattern
        # redacted just the header line and the body survived verbatim.
        body_a = "MIIEowIBAAKCAQEALEAKEDPRIVATEKEYBODY0000"
        body_b = "SECONDSECRETLINEOFKEY1111zzzz=="
        text = (
            "here is my key I pasted by accident:\n\n"
            "-----BEGIN RSA PRIVATE KEY-----\n"
            + body_a + "\n" + body_b + "\n\n"
            "and then the conversation continued about lunch plans."
        )
        out, counts = self.redact(text)
        self.assertNotIn(body_a, out, "truncated PEM key body leaked into the report")
        self.assertNotIn(body_b, out, "truncated PEM key body leaked into the report")
        self.assertIn("[REDACTED_PRIVATE_KEY]", out)
        # Fail-closed but not scorched-earth: prose after the blank-line break
        # that ends the key block must survive.
        self.assertIn("and then the conversation continued about lunch plans.", out)

    def test_full_pem_block_still_redacted(self):
        # Near-miss: the widened truncated-PEM pattern must not regress the
        # ordinary complete-block case.
        text = ("-----BEGIN RSA PRIVATE KEY-----\nAAAAfullbodyAAAA\n"
                "-----END RSA PRIVATE KEY-----\nunrelated trailing prose survives")
        out, _ = self.redact(text)
        self.assertNotIn("AAAAfullbodyAAAA", out)
        self.assertIn("[REDACTED_PRIVATE_KEY]", out)
        self.assertIn("unrelated trailing prose survives", out)


if __name__ == "__main__":
    unittest.main()
