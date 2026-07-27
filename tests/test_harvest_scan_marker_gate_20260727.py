"""`scan` stamped `.harvested` on runs it had not actually harvested
(audit 2026-07-27).

`.harvested` is the ONLY thing that takes a finished run off the unharvested
queue -- `unharvested()` feeds `harvest.py status` and the nightly heartbeat.
cmd_scan wrote that marker unconditionally whenever it staged nothing, on the
reasoning that "nothing new is still a completed harvest". That reasoning holds
for a run with genuinely nothing to harvest. It does NOT hold for the two ways
the scanner can come up empty while the lessons are sitting right there:

  1. DIALECT DRIFT. Only a `## ` heading whose text starts with a SECTION_TYPES
     key is read. A run written as "### Lessons" or "## Durable lessons
     (reviewed, safe to promote)" -- a shape that exists in this repo's own
     runs/ today -- yields zero candidates. `scan` printed "no harvest sources
     found", exited 0 and stamped the run, so `status` reported "none" and the
     automated discovery signal for those lessons was gone. SECTION_TYPES' own
     comment says a missed heading "silently drops lessons (bit us twice on
     2026-07-02)", so this class is a defect here, not a policy.

  2. AN UNREADABLE SOURCE. read() swallowed every OSError into "", so a
     19-memory-harvest.md at chmod 000 (or any IO fault) was indistinguishable
     from a run with no source file at all. Same outcome: "no harvest sources
     found", exit 0, run stamped, lessons intact on disk but off the radar.

Both directions are pinned, and both were mutation-verified against the unfixed
file before this test was committed:

  * RefusesADialectItCannotRead / RefusesAnUnreadableSource fail on
    HEAD-without-the-fix (exit 0 and `.harvested` present).
  * StillStampsAGenuinelyEmptyHarvest fails if the refusal is made greedy -- an
    empty-but-well-formed harvest, the SHIPPED blank template, and a run with no
    source file at all must all still be stamped and must NOT start refusing.

Every assertion is on BEHAVIOUR observed from a real `harvest.py` subprocess
over a throwaway tree -- exit status, the marker file on disk, what `status`
reports -- never on the module's source text.
"""

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HARVEST = os.path.join(ROOT, "scripts", "harvest.py")
TEMPLATE = os.path.join(ROOT, "blackboard-template", "19-memory-harvest.md")

LESSON = "Verify a fix by reverting it and watching the test fail first"
SECRET = "the client staging token lives in the ops vault at path X"


def _load():
    spec = importlib.util.spec_from_file_location("harvest_marker_gate", HARVEST)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


harvest = _load()


class _Tree(unittest.TestCase):
    """A throwaway project tree with one finished run."""

    SLUG = "sandbox"

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(self._cleanup)
        os.makedirs(os.path.join(self.tmp, "scripts"))
        shutil.copy(HARVEST, os.path.join(self.tmp, "scripts", "harvest.py"))
        self.run = os.path.join(self.tmp, "runs", self.SLUG)
        os.makedirs(self.run)
        # 13-delivery-report.md is what makes a run "done", i.e. eligible for
        # the unharvested queue at all.
        self._write("13-delivery-report.md", "# 13 Delivery Report\n")
        self.brain = os.path.join(self.tmp, "brain.jsonl")
        with open(self.brain, "w", encoding="utf-8") as f:
            f.write("")

    def _cleanup(self):
        for name in os.listdir(self.run) if os.path.isdir(self.run) else []:
            path = os.path.join(self.run, name)
            if os.path.isfile(path):
                try:
                    os.chmod(path, 0o644)
                except OSError:
                    pass
        shutil.rmtree(self.tmp, True)

    def _write(self, name, body):
        path = os.path.join(self.run, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
        return path

    def _cli(self, *args):
        env = dict(os.environ, PROJECT_OS_SHARED_BRAIN=self.brain)
        proc = subprocess.run(
            [sys.executable, os.path.join(self.tmp, "scripts", "harvest.py")]
            + list(args),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
        return proc.returncode, proc.stdout.decode()

    @property
    def marker(self):
        return os.path.join(self.run, ".harvested")

    def assertRefused(self, code, out, *needles):
        self.assertNotEqual(code, 0,
                            "scan exited 0 on a source it could not harvest: %r"
                            % out)
        self.assertIn("REFUSED", out, out)
        for needle in needles:
            self.assertIn(needle, out, out)
        self.assertFalse(
            os.path.isfile(self.marker),
            "scan stamped .harvested on a run it did not harvest")
        code, out = self._cli("status")
        self.assertIn(self.SLUG, out,
                      "the run fell off the unharvested queue: %r" % out)

    def assertStamped(self, code, out):
        self.assertEqual(code, 0, out)
        self.assertTrue(
            os.path.isfile(self.marker),
            "a genuinely empty harvest stopped being marked complete: %r" % out)
        _code, status = self._cli("status")
        self.assertNotIn(self.SLUG, status, status)


class RefusesADialectItCannotRead(_Tree):
    """Rows the scanner cannot claim must not be stamped away."""

    BODY = "- %s\n- %s\n" % (LESSON, SECRET)

    def test_an_unrecognised_h2_heading_is_refused_not_stamped(self):
        self._write("19-memory-harvest.md",
                    "## Durable lessons (reviewed, safe to promote)\n" + self.BODY)
        code, out = self._cli("scan", self.SLUG)
        self.assertRefused(code, out, "19-memory-harvest.md", "lesson")

    def test_a_deeper_heading_level_is_refused_not_stamped(self):
        self._write("19-memory-harvest.md", "### Lessons\n" + self.BODY)
        code, out = self._cli("scan", self.SLUG)
        self.assertRefused(code, out, "19-memory-harvest.md")

    def test_a_table_under_an_unrecognised_heading_is_refused(self):
        self._write("19-memory-harvest.md",
                    "## Takeaways\n| Takeaway | Evidence |\n| --- | --- |\n"
                    "| %s | logs |\n" % LESSON)
        code, out = self._cli("scan", self.SLUG)
        self.assertRefused(code, out, "19-memory-harvest.md")

    def test_rows_with_no_heading_at_all_are_refused(self):
        self._write("19-memory-harvest.md", "# Memory Harvest\n\n" + self.BODY)
        code, out = self._cli("scan", self.SLUG)
        self.assertRefused(code, out, "19-memory-harvest.md")

    def test_the_refusal_names_the_headings_the_scanner_does_recognise(self):
        """A refusal an operator cannot act on is just a different silence."""
        self._write("19-memory-harvest.md", "### Lessons\n" + self.BODY)
        _code, out = self._cli("scan", self.SLUG)
        for key in sorted(harvest.SECTION_TYPES):
            self.assertIn(key, out,
                          "the refusal did not name the recognised heading %r"
                          % key)

    def test_the_refused_run_keeps_its_source_file_intact(self):
        path = self._write("19-memory-harvest.md", "### Lessons\n" + self.BODY)
        self._cli("scan", self.SLUG)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "### Lessons\n" + self.BODY)


class RefusesAnUnreadableSource(_Tree):
    """An IO fault is not an empty harvest."""

    BODY = "## Lessons\n- %s\n- %s\n" % (LESSON, SECRET)

    def _make_unreadable(self, name):
        path = os.path.join(self.run, name)
        os.chmod(path, 0o000)
        try:
            with open(path, encoding="utf-8"):
                pass
        except OSError:
            return path
        self.skipTest("this user can read a 0o000 file (running as root?)")

    def test_read_returns_none_for_a_file_that_exists_but_cannot_be_read(self):
        """None and "" must be distinguishable; both used to be ""."""
        path = self._write("19-memory-harvest.md", self.BODY)
        self.assertEqual(harvest.read(path), self.BODY)
        self.assertEqual(
            harvest.read(os.path.join(self.run, "no-such-file.md")), "",
            "an ABSENT file must stay empty-string, not None")
        self._make_unreadable("19-memory-harvest.md")
        self.assertIsNone(
            harvest.read(path),
            "an unreadable file is indistinguishable from an absent one")

    def test_an_unreadable_harvest_file_is_refused_not_stamped(self):
        self._write("19-memory-harvest.md", self.BODY)
        self._make_unreadable("19-memory-harvest.md")
        code, out = self._cli("scan", self.SLUG)
        self.assertNotIn("no harvest sources found", out,
                         "scan claimed there were no sources: %r" % out)
        self.assertRefused(code, out, "19-memory-harvest.md")

    def test_an_unreadable_eval_log_fallback_is_refused_not_stamped(self):
        """The fallback path is gated the same way."""
        self._write("12-evaluation-log.md",
                    "| Step 3 | the retry loop failed on a stale lock |\n")
        self._make_unreadable("12-evaluation-log.md")
        code, out = self._cli("scan", self.SLUG)
        self.assertRefused(code, out, "12-evaluation-log.md")

    def test_an_unopenable_source_path_is_refused(self):
        """Root-proof mirror: a directory where the source file belongs.

        open() raises IsADirectoryError for every user, so this case pins the
        gate even where chmod cannot take a file away.
        """
        os.makedirs(os.path.join(self.run, "19-memory-harvest.md"))
        code, out = self._cli("scan", self.SLUG)
        self.assertRefused(code, out, "19-memory-harvest.md")

    def test_a_readable_harvest_file_still_stages(self):
        self._write("19-memory-harvest.md", self.BODY)
        code, out = self._cli("scan", self.SLUG)
        self.assertEqual(code, 0, out)
        self.assertIn("staged 2 new", out, out)


class StillStampsAGenuinelyEmptyHarvest(_Tree):
    """The mirror. Refusing everything must not pass this class."""

    def test_a_run_with_no_source_file_at_all_is_still_stamped(self):
        code, out = self._cli("scan", self.SLUG)
        self.assertIn("no harvest sources found", out, out)
        self.assertStamped(code, out)

    def test_the_shipped_blank_template_is_still_stamped(self):
        """The template ships unrecognised sections next to recognised ones."""
        with open(TEMPLATE, encoding="utf-8") as f:
            shutil.copyfileobj(f, open(os.path.join(
                self.run, "19-memory-harvest.md"), "w", encoding="utf-8"))
        code, out = self._cli("scan", self.SLUG)
        self.assertStamped(code, out)

    def test_a_prose_only_file_with_no_rows_is_still_stamped(self):
        self._write("19-memory-harvest.md",
                    "## Unrelated Heading\n\nnothing to see\n")
        code, out = self._cli("scan", self.SLUG)
        self.assertIn("no harvest sources found", out, out)
        self.assertStamped(code, out)

    def test_an_empty_recognised_section_is_still_stamped(self):
        self._write("19-memory-harvest.md", "## Lessons\n\n")
        code, out = self._cli("scan", self.SLUG)
        self.assertStamped(code, out)

    def test_a_fully_filtered_harvest_is_still_stamped(self):
        self._write("19-memory-harvest.md",
                    "## Lessons\n- Rejected: %s\n" % SECRET)
        code, out = self._cli("scan", self.SLUG)
        self.assertIn("filtered 1 row(s)", out, out)
        self.assertStamped(code, out)

    def test_an_all_dupes_harvest_is_still_stamped(self):
        with open(self.brain, "w", encoding="utf-8") as f:
            f.write('{"text": "%s, always"}\n' % LESSON)
        self._write("19-memory-harvest.md", "## Lessons\n- %s\n" % LESSON)
        code, out = self._cli("scan", self.SLUG)
        self.assertIn("already in the brain", out, out)
        self.assertStamped(code, out)


if __name__ == "__main__":
    unittest.main()
