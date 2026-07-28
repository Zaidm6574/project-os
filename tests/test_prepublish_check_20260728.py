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

    def test_it_uses_every_pattern_not_a_subset(self):
        """The old rg had 7 of 28. Prove the script sees all of them."""
        pats = _canonical_patterns()
        self.assertGreaterEqual(len(pats), 20, "canonical list unexpectedly small")
        r = self._run("--list")
        self.assertEqual(r.returncode, 0, "--list failed:\n%s" % r.stderr)
        reported = [l for l in r.stdout.splitlines() if l.strip()]
        self.assertGreaterEqual(
            len(reported), len(pats),
            "the checker reports %d patterns but the canonical list has %d -- it is "
            "using a subset" % (len(reported), len(pats)))


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
