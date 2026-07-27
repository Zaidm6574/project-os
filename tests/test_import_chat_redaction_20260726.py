"""Regression tests: scripts/import_chat_history.py must not leak credentials.

The README advertises the importer as "redacts secrets", but it carried its own
fork of the secret list -- a handful of shapes from an older era. A 2026-07-26
audit fed it live-shaped Slack, SendGrid, Figma, Stripe, Twilio and GitLab keys
plus a bare `api_key=...` and every one of them was written verbatim into the
report. The shared brain's gate (addons/full-engine/brain/brain.py's
SECRET_PATTERNS, asserted on by scripts/brain_append.py) already covered all of
them; the importer just never asked it.

These tests pin the three properties of the fix:

  1. every vendor family the finding names, plus the generic keyword
     catch-all, is redacted out of the WRITTEN BYTES of the report;
  2. the patterns are LOADED from brain.py, not copied -- a pattern that
     exists only in a scratch brain.py is honored, so the two lists can never
     drift apart again;
  3. the importer fails closed (non-zero, nothing written) when that list
     cannot be loaded, instead of falling back to a weaker one.

Each gate has a near-miss probe next to it: ordinary prose that merely mentions
"api_key" (or holds a too-short token) must survive UNREDACTED, and the same
standalone copy that refuses without brain.py must succeed once it is present.

All fixtures live in tempfile sandboxes; the live blackboard/, memory/, brain/
and runs/ trees are never written. Stdlib only -- passes on the 3.9.6 CI floor.
"""
import importlib.util
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMPORTER = ROOT / "scripts" / "import_chat_history.py"
BRAIN = ROOT / "addons" / "full-engine" / "brain" / "brain.py"

def _mint(prefix, body):
    """Assemble a credential-SHAPED (never live) fixture at RUNTIME.

    These must never appear as literals in the source. GitHub push protection
    scans every pushed blob and correctly refuses a commit containing a
    live-shaped Slack, Stripe or Twilio credential -- it cannot tell a
    synthetic fixture from a real key, and neither can a human skimming the
    diff. It blocked this very file (2026-07-26). Splitting each vendor prefix
    across the concatenation leaves no scannable literal while the assembled
    value stays byte-identical, so the pattern under test is exercised exactly
    as before.
    """
    return prefix + body


# One live-SHAPED (never live) credential per vendor family. Values are
# structurally valid for each vendor's documented format and nothing else.
VENDOR_SECRETS = {
    "slack": _mint("xo" + "xb-", "1111111111-2222222222-abcdefghijklmnopqrst"),
    "slack_webhook": _mint("https://hooks.sl" + "ack.com/services/",
                           "T00000000/B00000000/abcdefghijklmnopqrstuvwx"),
    "sendgrid": _mint("S" + "G.", "abcdefghijklmnopqrstuv.wxyz0123456789"
                                  "abcdefghijklmnopqrstuvwxyz012"),
    "figma": _mint("fig" + "d_", "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"),
    "stripe": _mint("sk_" + "live_", "51AbCdEfGhIjKlMnOpQrStUv"),
    "stripe_restricted": _mint("rk_" + "live_", "51AbCdEfGhIjKlMnOpQrStUv"),
    "twilio_sid": _mint("A" + "C", "0123456789abcdef0123456789abcdef"),
    "twilio_key": _mint("S" + "K", "0123456789abcdef0123456789abcdef"),
    "gitlab": _mint("glp" + "at-", "AbCdEfGhIjKlMnOp"),
    # Families the old fork already covered -- kept so the swap cannot regress.
    "openai": _mint("sk-" + "ant-api03-", "ABCDEFGHIJKLMNOPQRSTUVWXYZ"),
    "github": _mint("gh" + "p_", "ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"),
    "google": _mint("AI" + "zaSy", "A1B2C3D4E5F6G7H8I9J0KLMNOPQRSTUVW"),
    "aws": _mint("AK" + "IA", "IOSFODNN7EXAMPLE"),
    # The generic keyword catch-all: an unlabelled vendor, pasted as key=value.
    "generic_keyword": _mint("api_key=", "sUpErSeCrEtV4lu3"),
}


def transcript_line(secret):
    """A line that survives the importer's length + selection filters.

    The secret sits inside the first ten words so it lands in a --include-
    excerpts hint: that is exactly the shape that leaked.
    """
    return "I want to build an app with %s wired into this project workflow today" % secret


def run_importer(script, input_path, output_path, *extra):
    return subprocess.run(
        [sys.executable, str(script),
         "--input", str(input_path), "--output", str(output_path)] + list(extra),
        capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=180)


def standalone_copy(tmp, brain_at="addons/full-engine/brain"):
    """A project tree holding ONLY the importer, plus brain.py at `brain_at`.

    Mirrors how the importer resolves the pattern source relative to its own
    location, without touching the real repo. `brain_at=None` omits it;
    "brain" is the layout scripts/install_full_engine.py produces.
    """
    root = Path(tmp) / "project"
    (root / "scripts").mkdir(parents=True)
    script = root / "scripts" / "import_chat_history.py"
    shutil.copy2(IMPORTER, script)
    brain_copy = None
    if brain_at:
        brain_dir = root.joinpath(*brain_at.split("/"))
        brain_dir.mkdir(parents=True)
        brain_copy = brain_dir / "brain.py"
        shutil.copy2(BRAIN, brain_copy)
    return script, brain_copy


class TestImporterRedactsEveryVendorFamily(unittest.TestCase):
    def test_no_vendor_credential_reaches_the_written_report_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            export = Path(tmp) / "export.txt"
            output = Path(tmp) / "report.md"
            export.write_text(
                "\n".join(transcript_line(s) for s in VENDOR_SECRETS.values()) + "\n",
                encoding="utf-8")

            # --include-excerpts is the documented mode that copies source
            # text, i.e. the mode that leaked.
            result = run_importer(IMPORTER, export, output, "--include-excerpts")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            written = output.read_bytes()
            self.assertIn(b"[REDACTED_CREDENTIAL]", written,
                          "the report must show that credentials were removed")
            for family, secret in VENDOR_SECRETS.items():
                self.assertNotIn(
                    secret.encode("utf-8"), written,
                    "%s credential was written into the report verbatim" % family)
            # No fragment of a split-shaped key may survive either: SendGrid's
            # signature segment is the half a prefix-only pattern leaves behind.
            self.assertNotIn(b"wxyz0123456789abcdefghijklmnopqrstuvwxyz012", written)
            self.assertNotIn(b"sUpErSeCrEtV4lu3", written)

    def test_redaction_count_is_visible_and_not_silent(self):
        with tempfile.TemporaryDirectory() as tmp:
            export = Path(tmp) / "export.txt"
            output = Path(tmp) / "report.md"
            export.write_text(
                "\n".join(transcript_line(s) for s in VENDOR_SECRETS.values()) + "\n",
                encoding="utf-8")

            result = run_importer(IMPORTER, export, output, "--include-excerpts")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            printed = re.search(r"Redacted (\d+) secret-like value", result.stdout)
            self.assertIsNotNone(
                printed, "the importer must print how much it redacted:\n" + result.stdout)
            text = output.read_text(encoding="utf-8")
            reported = re.search(
                r"Secret-like values redacted before writing: (\d+)", text)
            self.assertIsNotNone(
                reported, "the report must state its own redaction count:\n" + text)
            self.assertEqual(int(printed.group(1)), int(reported.group(1)))
            self.assertEqual(int(reported.group(1)), len(VENDOR_SECRETS),
                             "every planted credential must be counted exactly once")

    def test_ordinary_prose_is_not_redacted(self):
        """Near-miss: mentioning a credential is not pasting one."""
        prose = [
            "I want to rotate the api_key policy before the demo for this project build",
            "I like short tokens like sk-abc and ghp_x in my notes for this build app",
            "I prefer the password manager workflow for this project, no secrets in chat",
            "my goal: build a website where the token lives in the vault for the business",
            "I need help, the problem: my bearer auth is confusing and the bug is hard",
        ]
        with tempfile.TemporaryDirectory() as tmp:
            export = Path(tmp) / "export.txt"
            output = Path(tmp) / "report.md"
            export.write_text("\n".join(prose) + "\n", encoding="utf-8")

            result = run_importer(IMPORTER, export, output, "--include-excerpts")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            text = output.read_text(encoding="utf-8")
            self.assertNotIn("[REDACTED", text,
                             "ordinary prose must pass through untouched:\n" + text)
            self.assertIn("Secret-like values redacted before writing: 0", text)
            for fragment in ("rotate the api_key policy",
                             "short tokens like sk-abc",
                             "the password manager workflow"):
                self.assertIn(fragment, text,
                              "usable excerpts must survive redaction:\n" + text)


class TestRedactionSpanCoversWholeCredentials(unittest.TestCase):
    """Unit-level probes for the widened replacement span (read-only)."""

    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "import_chat_history_under_test", str(IMPORTER))
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)
        self.patterns = self.mod.redaction_patterns(
            self.mod.load_credential_patterns())

    def redact(self, text):
        counts = Counter()
        return self.mod.redact(text, self.patterns, counts), counts

    def test_two_credentials_on_one_line_are_both_removed(self):
        line = "deploy used %s and %s together" % (
            VENDOR_SECRETS["slack"], VENDOR_SECRETS["figma"])
        out, counts = self.redact(line)
        self.assertNotIn(VENDOR_SECRETS["slack"], out)
        self.assertNotIn(VENDOR_SECRETS["figma"], out)
        self.assertEqual(sum(counts.values()), 2)
        self.assertIn("deploy used", out)
        self.assertIn("together", out)

    def test_a_second_key_glued_to_the_first_is_not_skipped(self):
        # Widening one span past the start of the next match must merge the
        # two, not drop the second: the second body would otherwise survive.
        glued = ("-----BEGIN RSA PRIVATE KEY-----\nAAAAsecretbodyAAAA\n"
                 "-----END RSA PRIVATE KEY----------BEGIN RSA PRIVATE KEY-----\n"
                 "BBBBsecretbodyBBBB\n-----END RSA PRIVATE KEY-----")
        out, _ = self.redact(glued)
        self.assertNotIn("AAAAsecretbodyAAAA", out)
        self.assertNotIn("BBBBsecretbodyBBBB", out,
                         "the second key body must not survive the merge")

    def test_a_whitespace_free_blob_stays_linear(self):
        # A minified JSON export line is one enormous token. Rescanning it once
        # per match is O(n^2): this exact 96 KB input took 80 seconds before the
        # scans were bounded by the previous span's end. The bound keeps it at
        # ~0.02s, so a 10s ceiling is a 500x margin, not a stopwatch race.
        blob = "sk-AAAAAAAAAAAAAAAAAAAA." * 4000
        started = time.time()
        out, counts = self.redact(blob)
        self.assertLess(time.time() - started, 10.0,
                        "redaction must not rescan the same token per match")
        self.assertEqual(out, "[REDACTED_CREDENTIAL]")
        self.assertEqual(sum(counts.values()), 4000)

    def test_widening_stops_at_whitespace(self):
        # Near-miss for the widener itself: it must not eat the sentence.
        out, _ = self.redact("rotate %s now please" % VENDOR_SECRETS["gitlab"])
        self.assertEqual(out, "rotate [REDACTED_CREDENTIAL] now please")


class TestImporterUsesTheSharedPatternList(unittest.TestCase):
    """The list must be LOADED from brain.py, never copied into the importer."""

    SYNTHETIC = "zzsyntheticpattern-42424242"

    def test_a_pattern_only_brain_knows_is_honored(self):
        with tempfile.TemporaryDirectory() as tmp:
            script, brain_copy = standalone_copy(tmp)
            with brain_copy.open("a", encoding="utf-8") as f:
                f.write("\nSECRET_PATTERNS.append(re.compile("
                        "r'zzsyntheticpattern-[0-9]{8}'))\n")

            export = Path(tmp) / "export.txt"
            output = Path(tmp) / "report.md"
            export.write_text(transcript_line(self.SYNTHETIC) + "\n", encoding="utf-8")

            result = run_importer(script, export, output, "--include-excerpts")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotIn(self.SYNTHETIC.encode("utf-8"), output.read_bytes(),
                             "the importer must read brain.py's live SECRET_PATTERNS, "
                             "not a copy frozen into its own source")

    def test_control_the_synthetic_pattern_is_not_already_matched(self):
        # Without the scratch brain.py edit the same token survives, so the
        # assertion above cannot pass for the wrong reason.
        with tempfile.TemporaryDirectory() as tmp:
            export = Path(tmp) / "export.txt"
            output = Path(tmp) / "report.md"
            export.write_text(transcript_line(self.SYNTHETIC) + "\n", encoding="utf-8")

            result = run_importer(IMPORTER, export, output, "--include-excerpts")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn(self.SYNTHETIC, output.read_text(encoding="utf-8"))


class TestImporterFailsClosedWithoutThePatternSource(unittest.TestCase):
    def test_refuses_and_writes_nothing_when_brain_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            script, _ = standalone_copy(tmp, brain_at=None)
            export = Path(tmp) / "export.txt"
            output = Path(tmp) / "report.md"
            export.write_text(
                transcript_line(VENDOR_SECRETS["slack"]) + "\n", encoding="utf-8")

            result = run_importer(script, export, output)
            self.assertNotEqual(result.returncode, 0,
                                "an unscreenable import must not exit 0")
            self.assertIn("REFUSED", result.stderr)
            self.assertFalse(output.exists(),
                             "nothing may be written when the secret list is unavailable")

    def test_refuses_when_brain_cannot_be_imported(self):
        with tempfile.TemporaryDirectory() as tmp:
            script, brain_copy = standalone_copy(tmp)
            brain_copy.write_text("this is not python(\n", encoding="utf-8")
            export = Path(tmp) / "export.txt"
            output = Path(tmp) / "report.md"
            export.write_text(
                transcript_line(VENDOR_SECRETS["slack"]) + "\n", encoding="utf-8")

            result = run_importer(script, export, output)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("REFUSED", result.stderr)
            self.assertFalse(output.exists())

    def test_refuses_a_brain_that_does_not_advertise_exhaustive_scanning(self):
        # Same assertion scripts/brain_append.py makes: an older or shadowing
        # brain.py must not silently downgrade the gate.
        with tempfile.TemporaryDirectory() as tmp:
            script, brain_copy = standalone_copy(tmp)
            brain_copy.write_text(
                "import re\n"
                "SECRET_SCAN_EXHAUSTIVE = False\n"
                "SECRET_PATTERNS = [re.compile(r'never-matches-anything')]\n",
                encoding="utf-8")
            export = Path(tmp) / "export.txt"
            output = Path(tmp) / "report.md"
            export.write_text(
                transcript_line(VENDOR_SECRETS["slack"]) + "\n", encoding="utf-8")

            result = run_importer(script, export, output)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("REFUSED", result.stderr)
            self.assertFalse(output.exists())

    def _assert_install_layout_works(self, brain_at):
        with tempfile.TemporaryDirectory() as tmp:
            script, _ = standalone_copy(tmp, brain_at=brain_at)
            export = Path(tmp) / "export.txt"
            output = Path(tmp) / "report.md"
            export.write_text(
                transcript_line(VENDOR_SECRETS["slack"]) + "\n"
                "I want to build a small tracker app for the shop this week\n",
                encoding="utf-8")

            result = run_importer(script, export, output, "--include-excerpts")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(output.exists(), "a complete install must still produce a report")
            written = output.read_bytes()
            self.assertNotIn(VENDOR_SECRETS["slack"].encode("utf-8"), written)
            self.assertIn(b"build a small tracker app", written,
                          "the report must still carry usable review material")

    def test_the_same_copy_still_works_once_brain_is_present(self):
        """Near-miss for the fail-closed gate: legitimate installs must run."""
        # setup_project_os.bootstrap ships addons/full-engine/ into the target.
        self._assert_install_layout_works("addons/full-engine/brain")

    def test_the_install_full_engine_layout_also_works(self):
        # install_full_engine.py copies addons/full-engine/brain -> <project>/brain.
        self._assert_install_layout_works("brain")


if __name__ == "__main__":
    unittest.main()
