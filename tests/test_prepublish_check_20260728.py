#!/usr/bin/env python3
"""The pre-publish check must use the canonical denylist, not a copy of it.

README's "Run a quick check before pushing" was a hand-written `rg` regex with
seven patterns, against the twenty-eight in brain.SECRET_PATTERNS. So the check
a reader runs immediately before making a repo public was the WEAKEST secret
scanner in the tree -- it missed Slack tokens, Figma PATs, SendGrid keys, Twilio
credentials, GitLab PATs, JWTs, database URLs carrying a password, and the
generic `api_key = ...` catch-all, among others.

The fix is deliberately NOT "expand the regex". This repo has already spent a
whole audit consolidating five drifted copies of this denylist into one; adding
a sixth in Markdown -- where no test can reach it -- would recreate the exact
defect class on the surface that matters most. `scripts/prepublish_check.py`
imports the canonical list instead, so it cannot drift by construction.

Pinned here, both directions:
  - the checker finds every shape the canonical list defines (no drift possible)
  - a clean tree passes, so the check is not merely "always fails"
  - the README points at the script rather than carrying its own patterns
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "scripts" / "prepublish_check.py"
README = ROOT / "README.md"


def _canonical_patterns():
    sys.path.insert(0, str(ROOT / "addons" / "full-engine" / "brain"))
    try:
        import brain
        return brain.SECRET_PATTERNS
    finally:
        sys.path.pop(0)


class PrepublishCheckExists(unittest.TestCase):
    def test_the_script_ships(self):
        self.assertTrue(CHECK.is_file(),
                        "scripts/prepublish_check.py is missing; the README's "
                        "pre-publish step has nothing to point at")

    def test_it_does_not_define_its_own_patterns(self):
        """A second list is the defect, not the fix."""
        body = CHECK.read_text(encoding="utf-8")
        self.assertNotIn(
            "AKIA[0-9A-Z]", body,
            "prepublish_check.py hardcodes credential patterns instead of importing "
            "the canonical list -- that is the drift this change exists to prevent")
        self.assertIn(
            "SECRET_PATTERNS", body,
            "prepublish_check.py does not reference SECRET_PATTERNS, so nothing "
            "keeps it aligned with the canonical denylist")


class ItCatchesEveryCanonicalShape(unittest.TestCase):
    """Drift is impossible only if the checker really uses the whole list."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)

    def _run(self, *args):
        env = dict(os.environ)
        env["HOME"] = str(self.work / "home")
        return subprocess.run([sys.executable, str(CHECK), *args],
                              capture_output=True, text=True, env=env)

    def test_a_clean_tree_passes(self):
        (self.work / "ok.md").write_text("nothing sensitive here\n", encoding="utf-8")
        r = self._run(str(self.work))
        self.assertEqual(r.returncode, 0,
                         "a clean tree was reported as dirty:\n%s\n%s"
                         % (r.stdout, r.stderr))

    def test_it_flags_a_planted_credential_shape(self):
        # Assembled at runtime, never a literal: GitHub push protection blocks
        # credential-shaped literals in source, and correctly so.
        planted = "figd_" + ("A" * 30)
        (self.work / "leak.md").write_text("token: %s\n" % planted, encoding="utf-8")
        r = self._run(str(self.work))
        self.assertNotEqual(r.returncode, 0,
                            "a planted Figma-PAT shape was not flagged:\n%s" % r.stdout)
        self.assertIn("leak.md", r.stdout + r.stderr,
                      "the offending file was not named in the output")

    def test_tracked_mode_ignores_untracked_local_state(self):
        subprocess.run(["git", "init", "-q", str(self.work)], check=True)
        (self.work / "tracked.md").write_text("ordinary public text\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.work), "add", "tracked.md"], check=True)
        planted = "figd_" + ("A" * 30)
        (self.work / "private-local.md").write_text(
            "token: %s\n" % planted, encoding="utf-8"
        )

        r = self._run("--tracked", str(self.work))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

        subprocess.run(
            ["git", "-C", str(self.work), "add", "private-local.md"], check=True
        )
        r = self._run("--tracked", str(self.work))
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("private-local.md", r.stdout + r.stderr)

    def test_list_matches_canonical_patterns_exactly(self):
        """Listing parity is a drift check, separate from scanner behavior."""
        r = self._run("--list")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), [p.pattern for p in _canonical_patterns()])

    def test_each_credential_family_and_clean_near_miss(self):
        # Construct shapes at runtime. No complete credential-shaped fixtures
        # belong in tracked source, even when synthetic.
        sized = [
            ("sk-", 16), ("sk_live_", 16), ("rk_test_", 16),
            ("AKIA", 16), ("ASIA", 16), ("ghp_", 20),
            ("github_pat_", 20), ("AIza", 20), ("ya29.", 20),
            ("xoxb-", 10), ("figd_", 20), ("SG.", 20),
            ("AC", 32), ("SK", 32), ("glpat-", 16),
            ("dop_v1_", 32), ("npm_", 30), ("hf_", 30),
            ("ntn_", 40), ("lin_api_", 30), ("vercel_", 20),
        ]
        cases = [(prefix + "A" * size, prefix + "A" * (size - 1))
                 for prefix, size in sized]
        cases += [
            ("https://" + "a" * 32 + "@example.invalid/1",
             "https://" + "a" * 31 + "@example.invalid/1"),
            ("AccountKey=" + "A" * 40, "AccountKey=" + "A" * 39),
            ("https://hooks.slack.com/services/" + "T" + "A" * 20,
             "https://hooks.slack.com/services/" + "T" + "A" * 19),
            ("eyJ" + "A" * 10 + ".eyJ" + "B" * 10 + ".",
             "eyJ" + "A" * 9 + ".eyJ" + "B" * 10 + "."),
            ("postgres://user:" + "example" + "@example.invalid/db",
             "postgres://user@example.invalid/db"),
            ("-----BEGIN " + "PRIVATE KEY-----", "-----BEGIN PUBLIC KEY-----"),
            ("api_key = " + "a" * 6, "api_key = " + "a" * 5),
        ]
        self.assertEqual(len(cases), len(_canonical_patterns()),
                         "new canonical family needs an end-to-end fixture")
        # Cover alternative branches within the families as well.
        cases += [("GOCSPX-" + "A" * 20, "GOCSPX-" + "A" * 19),
                  ("bearer " + "A" * 8, "bearer token")]
        candidate = self.work / "candidate.md"
        for number, (credential, near_miss) in enumerate(cases):
            with self.subTest(family=number):
                candidate.write_text(credential + "\n", encoding="utf-8")
                result = self._run(str(self.work))
                self.assertEqual(result.returncode, 1, "family was not detected")
                self.assertIn("candidate.md:1", result.stdout)
                self.assertTrue(credential not in result.stdout + result.stderr,
                                "scanner exposed a matched value")
                candidate.write_text(near_miss + "\n", encoding="utf-8")
                clean = self._run(str(self.work))
                self.assertEqual(clean.returncode, 0,
                                 "clean near miss was reported as dirty")


class ReadmePointsAtTheScript(unittest.TestCase):
    def test_the_readme_no_longer_carries_its_own_denylist(self):
        text = README.read_text(encoding="utf-8")
        self.assertIn(
            "prepublish_check.py", text,
            "README does not mention scripts/prepublish_check.py, so a reader still "
            "runs whatever ad-hoc check the page carries")
        # The old form: an rg invocation enumerating credential prefixes inline.
        self.assertNotIn(
            "AKIA[0-9A-Z]{16}", text,
            "README still hardcodes credential patterns; that is a copy of the "
            "denylist that no test can keep in sync")


if __name__ == "__main__":
    unittest.main()
