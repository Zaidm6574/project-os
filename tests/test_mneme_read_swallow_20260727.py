"""memory/mneme_adapter.py must not turn a damaged brain file into an EMPTY index.

Audit 2026-07-27. `read()` was `except Exception: return ""`, which conflated
"file is absent" with "file is corrupt / not readable". Two consequences, both
reproduced against the unfixed adapter before this file was written:

  1. ONE non-UTF-8 byte anywhere in shared-brain.jsonl made `_gather()` see an
     EMPTY document. `build` then published a 0-vector index over a good one,
     printed "[mneme] built ... 0 vectors" and exited 0 with zero bytes of
     stderr. `query` then returned []. brain_append.py and brain_scale.py both
     report that state as success, so a total loss of semantic recall is
     invisible. The precondition is ordinary: brain_append/brain_archive/
     harvest all write with ensure_ascii=False, so a torn write can leave a
     partial multi-byte character.

  2. An unreadable (chmod 000) brain file did the same thing: a populated
     index on disk was REPLACED by a 0-vector one, exit 0.

Fail-loud on a damaged brain file is this repo's stated doctrine everywhere
else -- addons/full-engine/memory/brain_fts_mirror.py raises on the same bytes
(tests/test_brain_fts_mirror.py::test_malformed_jsonl_fails_loudly_not_silently)
and brain.py's export aborts non-zero on a corrupt source line
(tests/test_brain_export_and_redaction_20260727.py). mneme was the outlier.
scripts/brain_append.py:132-146 names mneme_adapter._gather as one of the
readers that makes such a loss "invisible forever".

Also pinned here:
  * `build` publishes the index atomically (sibling temp file + fsync +
    os.replace), so a build that dies partway can never leave a torn or empty
    index where a good one was.
  * a malformed / non-object JSONL line is still SKIPPED (a torn tail must not
    brick every future reindex) but is now REPORTED on stderr with its line
    number and a total count, instead of vanishing.

Every assertion is written to bite when its own guard is reverted; see the
mutation proof in the handoff. Stdlib only, no network, no Ollama (the lexical
embedder is forced). Everything runs in a tempdir with a fake HOME, so the real
shared brain is never read or written.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "memory" / "mneme_adapter.py"


class MnemeSandbox(unittest.TestCase):
    """A throwaway project tree + fake HOME holding a fake shared brain."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mneme-swallow-")
        self.addCleanup(self._cleanup)
        self.home = Path(self.tmp) / "home"
        self.proj = Path(self.tmp) / "proj"
        (self.proj / "memory").mkdir(parents=True)
        (self.proj / "runs").mkdir(parents=True)
        self.brain = self.home / ".project-os" / "central-brain" / "shared-brain.jsonl"
        self.brain.parent.mkdir(parents=True)
        # Copy the adapter so ROOT (and therefore runs/ globbing) is the sandbox.
        self.adapter = self.proj / "memory" / "mneme_adapter.py"
        shutil.copyfile(str(ADAPTER), str(self.adapter))
        self.index = self.proj / "memory" / "mneme_index.json"

    def _cleanup(self):
        # A test may have chmod 000'd the brain file; make it removable again.
        try:
            os.chmod(str(self.brain), 0o644)
        except OSError:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_brain(self, n=5, extra_lines=()):
        with open(str(self.brain), "w", encoding="utf-8") as fh:
            for i in range(n):
                fh.write(json.dumps(
                    {"id": "L%d" % i, "kind": "lesson",
                     "text": "lesson %d never push without approval" % i},
                    ensure_ascii=False) + "\n")
            for line in extra_lines:
                fh.write(line + "\n")

    def run_mneme(self, *args):
        env = dict(os.environ)
        env.update({"HOME": str(self.home),
                    "OSVEC_EMBEDDER": "lexical",
                    "MNEME_EMBEDDER": "lexical"})
        env.pop("PROJECT_OS_SHARED_BRAIN", None)
        env.pop("MNEME_INDEX", None)
        return subprocess.run([sys.executable, str(self.adapter), *args],
                              capture_output=True, text=True, env=env,
                              cwd=str(self.proj))

    def index_count(self):
        with open(str(self.index), encoding="utf-8") as fh:
            return json.load(fh)["count"]

    def corrupt_brain(self):
        """Plant one invalid UTF-8 start byte inside an existing lesson."""
        with open(str(self.brain), "rb") as fh:
            raw = fh.read()
        i = raw.index(b"lesson 3")
        with open(str(self.brain), "wb") as fh:
            fh.write(raw[:i + 7] + b"\x80" + raw[i + 8:])


class CorruptBrainMustNotProduceAnEmptyIndex(MnemeSandbox):

    def test_build_refuses_and_leaves_the_good_index_in_place(self):
        self.write_brain(5)
        good = self.run_mneme("build")
        self.assertEqual(good.returncode, 0, good.stderr)
        self.assertEqual(self.index_count(), 5, good.stdout)

        self.corrupt_brain()
        bad = self.run_mneme("build")
        # 1. It must FAIL, not print "built ... 0 vectors" and exit 0.
        self.assertNotEqual(
            bad.returncode, 0,
            "build reported success on a corrupt brain file: %r" % (bad.stdout,))
        # 2. It must SAY something. Zero bytes of stderr was the whole defect.
        self.assertTrue(bad.stderr.strip(),
                        "build failed silently; stderr was empty")
        self.assertNotIn("0 vectors", bad.stdout)
        # 2b. Legibly, in the FIRST line. scripts/brain_append.py:167 shows only
        #     stderr[:300], and a traceback puts the actual cause LAST, so the
        #     operator would get frames instead of the reason.
        self.assertNotIn("Traceback", bad.stderr, bad.stderr)
        self.assertIn("REFUSED", bad.stderr.splitlines()[0], bad.stderr)
        # 3. The previously good index must still be on disk, untouched.
        self.assertEqual(
            self.index_count(), 5,
            "a failed build overwrote a good index with an empty one")

    def test_query_still_answers_after_a_reindex_hit_the_corruption(self):
        """Recall must not silently go to zero because a byte got torn.

        The rebuild in the middle is what scripts/brain_append.py does on every
        append; without it this test would pass on the unfixed adapter too,
        because `query` would just read the still-good index it never rewrote.
        """
        self.write_brain(5)
        self.assertEqual(self.run_mneme("build").returncode, 0)
        self.corrupt_brain()
        self.run_mneme("build")  # the reindex brain_append.py fires
        got = self.run_mneme("query", "never push without approval")
        self.assertEqual(got.returncode, 0, got.stderr)
        hits = json.loads(got.stdout)
        self.assertTrue(hits, "query returned [] — semantic recall was lost")

    def test_stats_does_not_report_zero_after_a_failed_build(self):
        self.write_brain(5)
        self.assertEqual(self.run_mneme("build").returncode, 0)
        self.corrupt_brain()
        self.run_mneme("build")
        stats = self.run_mneme("stats")
        self.assertEqual(stats.returncode, 0, stats.stderr)
        self.assertEqual(json.loads(stats.stdout)["count"], 5, stats.stdout)


class UnreadableBrainMustNotProduceAnEmptyIndex(MnemeSandbox):

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0,
                     "root ignores file permissions, so chmod 000 proves nothing")
    def test_permission_denied_is_not_treated_as_an_empty_brain(self):
        self.write_brain(12)
        good = self.run_mneme("build")
        self.assertEqual(good.returncode, 0, good.stderr)
        self.assertEqual(self.index_count(), 12, good.stdout)

        os.chmod(str(self.brain), 0o000)
        bad = self.run_mneme("build")
        self.assertNotEqual(
            bad.returncode, 0,
            "an unreadable brain file was indexed as empty, exit 0: %r" % (bad.stdout,))
        self.assertTrue(bad.stderr.strip(), "build failed silently")
        self.assertEqual(
            self.index_count(), 12,
            "a failed build replaced the 12-vector index with an empty one")


class AbsentSourcesAreStillTolerated(MnemeSandbox):
    """The control: fail-loud must not turn "no brain yet" into a crash.

    A fresh install has no ~/.project-os/central-brain/shared-brain.jsonl and no
    archive tier. `build` has to keep succeeding there, otherwise this fix would
    just be a new outage.
    """

    def test_build_succeeds_with_no_brain_file_at_all(self):
        self.assertFalse(self.brain.exists())
        got = self.run_mneme("build")
        self.assertEqual(got.returncode, 0, got.stderr + got.stdout)
        self.assertEqual(self.index_count(), 0)

    def test_build_succeeds_when_only_the_archive_tier_is_missing(self):
        self.write_brain(3)
        got = self.run_mneme("build")
        self.assertEqual(got.returncode, 0, got.stderr)
        self.assertEqual(self.index_count(), 3)


class MalformedLinesAreReportedNotVanished(MnemeSandbox):
    """`except Exception: continue` dropped lines with no trace at all.

    Skipping stays (a torn tail must not brick every future reindex) but the
    drop is now named on stderr, per brain_append.py's own comment that this
    exact skip is what makes such a loss "invisible forever".
    """

    def test_unparseable_line_is_named_on_stderr(self):
        self.write_brain(3, extra_lines=['{"id": "L9", "text": "trunc'])
        got = self.run_mneme("build")
        self.assertEqual(got.returncode, 0, got.stderr)
        self.assertEqual(self.index_count(), 3, got.stdout)
        self.assertIn("4", got.stderr, "the line number was not reported: %r" % got.stderr)
        self.assertIn("skipped", got.stderr.lower(),
                      "a dropped brain line was not reported at all: %r" % got.stderr)

    def test_non_object_json_line_is_skipped_not_a_traceback(self):
        """`123` is valid JSON but has no .get(); it used to raise AttributeError."""
        self.write_brain(2, extra_lines=["123"])
        got = self.run_mneme("build")
        self.assertEqual(got.returncode, 0, got.stderr)
        self.assertNotIn("Traceback", got.stderr)
        self.assertEqual(self.index_count(), 2, got.stdout)

    def test_a_clean_brain_reports_no_skips(self):
        """The control: the warning must not fire on healthy input."""
        self.write_brain(4)
        got = self.run_mneme("build")
        self.assertEqual(got.returncode, 0, got.stderr)
        self.assertNotIn("skipped", got.stderr.lower(), got.stderr)


class IndexIsPublishedAtomically(MnemeSandbox):
    """A build that dies while writing must not leave a torn/empty index."""

    def test_a_build_killed_mid_write_leaves_the_old_index_intact(self):
        self.write_brain(6)
        self.assertEqual(self.run_mneme("build").returncode, 0)
        self.assertEqual(self.index_count(), 6)

        # Re-run build in-process with json.dump exploding partway through the
        # serialization, exactly like a full disk or a SIGKILL would.
        harness = Path(self.tmp) / "torn.py"
        harness.write_text(
            "import json, sys, importlib.util\n"
            "spec = importlib.util.spec_from_file_location('m', %r)\n"
            "m = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(m)\n"
            "real = json.dump\n"
            "def boom(obj, fh, *a, **kw):\n"
            "    fh.write('{\"dim\": 256, \"entries\": [')\n"
            "    raise RuntimeError('disk full')\n"
            "m.json.dump = boom\n"
            "try:\n"
            "    m.build()\n"
            "except RuntimeError as e:\n"
            "    print('died:', e)\n" % str(self.adapter),
            encoding="utf-8")
        env = dict(os.environ)
        env.update({"HOME": str(self.home), "OSVEC_EMBEDDER": "lexical",
                    "MNEME_EMBEDDER": "lexical"})
        env.pop("PROJECT_OS_SHARED_BRAIN", None)
        env.pop("MNEME_INDEX", None)
        died = subprocess.run([sys.executable, str(harness)], capture_output=True,
                              text=True, env=env, cwd=str(self.proj))
        self.assertIn("died:", died.stdout, died.stderr)
        # The old index must still be readable and complete.
        self.assertEqual(self.index_count(), 6,
                         "a torn write destroyed the index")
        # And no temp debris left behind.
        leftovers = [p.name for p in (self.proj / "memory").iterdir()
                     if p.name.startswith(".mneme_index.")]
        self.assertEqual(leftovers, [], "temp file left behind: %s" % leftovers)


if __name__ == "__main__":
    unittest.main()
