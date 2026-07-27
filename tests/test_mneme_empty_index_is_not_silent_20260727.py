#!/usr/bin/env python3
"""An empty mneme index must SAY it is empty, and --help must print help.

Three cold-clone reviewers hit this independently on 2026-07-27. README's
"Optional: smarter memory search" section is a silent exit-0 no-op on a fresh
clone:

    python3 memory/mneme_adapter.py build
      -> [mneme] built .../memory/mneme_index.json - 0 vectors (...)
    python3 memory/mneme_adapter.py query "have we solved this before?"
      -> []
    python3 memory/mneme_adapter.py --help
      -> (nothing)   exit 0

Every one of those is a success-shaped report of "nothing happened". A reader
cannot tell "it worked, there is genuinely nothing to index yet" from "it is
broken" or "I ran it from the wrong directory" -- and the repo's own CLAUDE.md
forbids presenting unverified capability as active.

Deliberately NOT fixed by making build exit non-zero: a fresh install has no
lessons yet, and an empty corpus producing an empty index is CORRECT. Failing
there would break the documented first run for every new user. The defect is
silence, not the exit code, so the fix is to name what was scanned and what to
do about it.

Both directions are pinned:
  - empty corpus  -> build and query both say so, naming the scanned paths
  - non-empty     -> no spurious "nothing to index" warning, and query returns
                     the real hit
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class MnemeEmptyIndexIsNotSilent(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        # mneme_adapter derives ROOT from its own __file__, so it must be copied
        # into the sandbox or it indexes (and rewrites the index of) the real tree.
        shutil.copytree(ROOT / "memory", self.work / "memory")
        self.adapter = self.work / "memory" / "mneme_adapter.py"
        self.home = self.work / "home"
        self.home.mkdir()
        self.brain = self.work / "brain" / "shared-brain.jsonl"
        self.brain.parent.mkdir(parents=True)

    def _run(self, *args):
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env["MNEME_INDEX"] = str(self.work / "memory" / "mneme_index.json")
        env["PROJECT_OS_SHARED_BRAIN"] = str(self.brain)
        env["OSVEC_EMBEDDER"] = "lexical"  # never reach for Ollama in a test
        return subprocess.run(
            [sys.executable, str(self.adapter), *args],
            capture_output=True, text=True, cwd=str(self.work), env=env)

    def _seed_a_lesson(self):
        self.brain.write_text(json.dumps({
            "id": "lesson-1", "type": "lesson",
            "text": "Always verify the probe against the unfixed code first.",
        }) + "\n", encoding="utf-8")

    # ---------------------------------------------------------- empty corpus

    def test_building_an_empty_index_explains_itself(self):
        r = self._run("build")
        out = r.stdout + r.stderr
        self.assertIn("0 vectors", out, "expected the existing count line; got:\n%s" % out)
        self.assertRegex(
            out, r"(?i)nothing to index|no .*sources|empty",
            "build reported an empty index with no explanation -- a reader cannot "
            "tell success-with-nothing-to-do from a broken invocation. Output was:\n%s" % out)

    def test_building_an_empty_index_names_where_it_looked(self):
        r = self._run("build")
        out = r.stdout + r.stderr
        self.assertIn(
            str(self.brain), out,
            "build did not name the brain path it scanned, so a reader has no way "
            "to discover it indexed a location they did not expect. Output was:\n%s" % out)

    def test_querying_an_empty_index_says_so_instead_of_printing_nothing(self):
        self._run("build")
        r = self._run("query", "have we solved something like this before?")
        out = r.stdout + r.stderr
        self.assertRegex(
            out, r"(?i)empty|nothing to index|no .*indexed",
            "query on an empty index printed results with no note that the index "
            "is empty -- indistinguishable from 'no match found'. Output was:\n%s" % out)

    def test_query_output_stays_machine_readable(self):
        """The mirror direction: explanation goes to stderr, JSON stays on stdout.

        Without this, the obvious fix (print prose on stdout) breaks anything
        piping this into jq -- and README shows it as a JSON-producing command.
        """
        self._run("build")
        r = self._run("query", "anything")
        self.assertEqual(json.loads(r.stdout), [],
                         "stdout was not parseable JSON:\n%r" % r.stdout)

    # ------------------------------------------------------------- --help

    def test_help_prints_usage_instead_of_nothing(self):
        r = self._run("--help")
        out = r.stdout + r.stderr
        self.assertTrue(out.strip(),
                        "--help printed nothing at all and exited %d" % r.returncode)
        for expected in ("build", "query", "stats"):
            self.assertIn(expected, out,
                          "--help does not mention the %r subcommand:\n%s" % (expected, out))

    def test_an_unknown_subcommand_is_refused_not_silently_ignored(self):
        r = self._run("frobnicate")
        self.assertNotEqual(
            r.returncode, 0,
            "an unknown subcommand exited 0 and did nothing, so a typo'd command "
            "is indistinguishable from a successful run. stdout=%r stderr=%r"
            % (r.stdout, r.stderr))

    # -------------------------------------------------------- non-empty corpus

    def test_a_real_corpus_does_not_get_the_empty_warning(self):
        self._seed_a_lesson()
        r = self._run("build")
        out = r.stdout + r.stderr
        self.assertIn("1 vectors", out, "expected 1 indexed vector; got:\n%s" % out)
        self.assertNotRegex(
            out, r"(?i)nothing to index",
            "a non-empty corpus still got the empty-index warning:\n%s" % out)

    def test_a_real_corpus_still_returns_hits(self):
        self._seed_a_lesson()
        self._run("build")
        r = self._run("query", "verify the probe against unfixed code")
        hits = json.loads(r.stdout)
        self.assertTrue(hits, "query returned no hits for a seeded lesson:\n%s" % r.stdout)
        self.assertEqual(hits[0]["id"], "lesson-1")


if __name__ == "__main__":
    unittest.main()
