#!/usr/bin/env python3
"""The last two truncate-in-place writers: they ate the file they were editing.

Both destinations were opened with mode "w" (via ``Path.write_text`` and via a
bare ``open(path, "w")``), which truncates the ONLY copy of the user's file
before a single new byte exists. Reproduced on 2026-07-27 against the shipped
CLIs, before the fix:

  scripts/check_optional_tools.py --target <proj>
      blackboard/17-capability-preflight.md: 289 bytes
      write interrupted mid-report
      -> 600 bytes of a half-written report: the operator prose BELOW the
         machine block is gone, and the block itself has a
         `<!-- project-os:optional-tool-check:begin -->` with no matching end
         marker, so the next run can no longer find its own block.
      OSError: [Errno 27] File too large
        File "scripts/check_optional_tools.py", line 474, in write_report
          preflight.write_text(apply_report(existing, report), encoding="utf-8")

  addons/full-engine/memory/goal_guard.py --migrate <goal> --apply
      00-project-goal.md: 193 bytes
      write interrupted mid-migration
      -> 120 bytes: the '## Summary' section, the goal sentence the migration
         was seeded from, and the operator's notes are all gone. The file is
         left holding only a heading and half of a template comment.
      OSError: [Errno 27] File too large
        File "addons/full-engine/memory/goal_guard.py", line 173, in migrate
          fh.write(updated)

check_optional_tools is the worse of the two, because apply_report()'s whole
reason to exist is to preserve the operator prose outside its markers -- a
truncation destroys the one thing the function is for.

The failure is delivered by the OS, not by a monkeypatch: RLIMIT_FSIZE is
lowered for the child, so the kernel refuses the write once the file grows past
the limit. CPython sets SIGXFSZ to SIG_IGN, so it arrives as
``OSError: [Errno 27] File too large`` out of the write -- exactly how a full
disk or an exceeded quota shows up in production. That mechanism knows nothing
about how the writer opens the file, so it cannot be satisfied by renaming
things. Every crash test also asserts the crash actually fired, because an
injected failure that silently stops firing is this repo's signature defect.

The remedy is the one already in this tree -- scripts/brain_archive.py
(_atomic_rewrite), scripts/evolution.py (_atomic_write_text) -- copied rather
than reinvented: stage in the destination's own directory with
tempfile.mkstemp (O_CREAT|O_EXCL), fsync, reproduce the destination's existing
mode (chown before chmod), os.replace onto the LITERAL path, unlink the staged
file on any exception.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

try:
    import resource
except ImportError:  # pragma: no cover - non-POSIX
    resource = None

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CAN_LIMIT_FILE_SIZE = (
    os.name == "posix"
    and resource is not None
    and hasattr(resource, "RLIMIT_FSIZE")
)


class _CrashSandbox(unittest.TestCase):
    """A copy of the shipped script under a fake HOME, so nothing here can
    reach the real blackboard, runs/ or lock directory."""

    source: str = ""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="truncate-in-place-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.script = os.path.join(self.tmp, os.path.basename(self.source))
        shutil.copy(os.path.join(REPO, self.source), self.script)
        self.env = dict(os.environ)
        self.env["HOME"] = self.tmp
        self.env["PYTHONDONTWRITEBYTECODE"] = "1"

    def run_cli(self, argv, fsize=None):
        """Drive the real CLI. fsize lowers RLIMIT_FSIZE for the child only."""
        pre = None
        if fsize is not None:
            def pre():  # noqa: E306
                resource.setrlimit(resource.RLIMIT_FSIZE, (fsize, fsize))
        return subprocess.run([sys.executable, self.script] + argv,
                              cwd=self.tmp, env=self.env, preexec_fn=pre,
                              capture_output=True, text=True, timeout=120)

    @staticmethod
    def read(path, mode="rb"):
        kwargs = {} if "b" in mode else {"encoding": "utf-8"}
        with open(path, mode, **kwargs) as fh:
            return fh.read()

    @staticmethod
    def write(path, text):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def assert_crashed(self, proc, what):
        self.assertNotEqual(
            proc.returncode, 0,
            "%s completed instead of crashing -- the injected mid-write "
            "failure never fired, so this test proves nothing:\n%s\n%s"
            % (what, proc.stdout, proc.stderr))

    def assert_intact(self, path, before, note):
        after = self.read(path)
        self.assertEqual(
            after, before,
            "%s: an interrupted write must leave the previous file "
            "byte-for-byte intact, but %d bytes became %d.\nleft on disk: %r"
            % (note, len(before), len(after), after[:200]))

    def plant_symlinks(self, directory, basename):
        """Symlinks at every guessable staging name, pointing outside the tree.

        The obvious wrong fix -- stage at `<dest>.tmp` with a plain open() --
        stops the truncation and re-opens the hole brain_archive.py was
        hardened against on 2026-07-26. mkstemp()'s O_CREAT|O_EXCL refuses ANY
        pre-existing path, symlink included.
        """
        outside = os.path.join(self.tmp, "outside")
        os.makedirs(outside, exist_ok=True)
        planted = []
        for pattern in ("%s.tmp", ".%s.tmp", "%s.new", "%s~", "%s.bak"):
            link = os.path.join(directory, pattern % basename)
            target = os.path.join(outside, os.path.basename(link) + ".victim")
            self.write(target, "PRECIOUS\n")
            os.symlink(target, link)
            planted.append(target)
        return planted

    def assert_victims_untouched(self, planted):
        for target in planted:
            self.assertEqual(
                self.read(target, "r"), "PRECIOUS\n",
                "a symlink at a guessable staging name redirected the write "
                "onto %s" % target)


# --------------------------------------------------------------------------
# scripts/check_optional_tools.py
# --------------------------------------------------------------------------

PROSE_ABOVE = "We keep ripgrep pinned for the audit; do not remove that note."
PROSE_BELOW = "Operator addendum: podman is deliberately absent on this host."

SEEDED_PREFLIGHT = (
    "# Capability Preflight\n"
    "\n"
    "%s\n"
    "\n"
    "<!-- project-os:optional-tool-check:begin -->\n"
    "## Automated Optional Tool Check\n"
    "\n"
    "- Date: 2026-01-01 00:00\n"
    "- Scope: local machine PATH, selected environment variables, and installed Python packages\n"
    "<!-- project-os:optional-tool-check:end -->\n"
    "\n"
    "### Operator Notes\n"
    "\n"
    "%s\n"
) % (PROSE_ABOVE, PROSE_BELOW)


class _PreflightSandbox(_CrashSandbox):
    source = "scripts/check_optional_tools.py"

    def setUp(self):
        super().setUp()
        self.project = os.path.join(self.tmp, "proj")
        self.blackboard = os.path.join(self.project, "blackboard")
        os.makedirs(self.blackboard)
        self.preflight = os.path.join(self.blackboard,
                                      "17-capability-preflight.md")

    def check(self, fsize=None):
        return self.run_cli(["--target", self.project], fsize=fsize)

    def seed(self, text=SEEDED_PREFLIGHT):
        self.write(self.preflight, text)
        return self.read(self.preflight)


@unittest.skipUnless(_CAN_LIMIT_FILE_SIZE, "needs RLIMIT_FSIZE")
class PreflightSurvivesAnInterruptedWrite(_PreflightSandbox):
    def test_operator_prose_survives_a_crash_during_the_report_write(self):
        before = self.seed()

        # Big enough to hold the header and part of the table, far too small
        # for the whole generated report.
        crash = self.check(fsize=600)
        self.assert_crashed(crash, "check_optional_tools under RLIMIT_FSIZE")
        self.assert_intact(self.preflight, before,
                           "blackboard/17-capability-preflight.md")

        text = self.read(self.preflight, "r")
        self.assertIn(PROSE_ABOVE, text)
        self.assertIn(PROSE_BELOW, text,
                      "the prose apply_report() exists to preserve was lost")

    def test_the_next_run_still_finds_its_own_block_after_a_crash(self):
        self.seed()
        self.assert_crashed(self.check(fsize=600), "crashing run")

        ok = self.check()
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        text = self.read(self.preflight, "r")
        self.assertEqual(text.count("<!-- project-os:optional-tool-check:begin -->"), 1,
                         "the crash left a stray block behind:\n" + text)
        self.assertEqual(text.count("<!-- project-os:optional-tool-check:end -->"), 1)
        self.assertIn(PROSE_ABOVE, text)
        self.assertIn(PROSE_BELOW, text)
        self.assertNotIn("2026-01-01", text, "the report was not refreshed")


class PreflightPermissions(_PreflightSandbox):
    """A temp-file rewrite hands the destination the TEMP file's mode, so a
    naive fix silently republishes a deliberately private blackboard. Same
    contract scripts/brain_archive.py carries."""

    def test_a_new_preflight_file_is_private(self):
        proc = self.check()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        mode = stat.S_IMODE(os.stat(self.preflight).st_mode)
        self.assertEqual(mode, 0o600,
                         "a new 17-capability-preflight.md was created "
                         "group/world readable (%s)" % oct(mode))

    def test_an_existing_files_mode_is_preserved_not_reset(self):
        self.seed()
        for wanted in (0o640, 0o644, 0o600):
            os.chmod(self.preflight, wanted)
            proc = self.check()
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(
                stat.S_IMODE(os.stat(self.preflight).st_mode), wanted,
                "the preflight file's mode %s was not preserved across the "
                "rewrite" % oct(wanted))


class PreflightStagingNameIsNotPredictable(_PreflightSandbox):
    def test_symlinks_planted_at_guessable_staging_names_are_never_written(self):
        self.seed()
        planted = self.plant_symlinks(self.blackboard,
                                      "17-capability-preflight.md")
        proc = self.check()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assert_victims_untouched(planted)


class PreflightOrdinaryRunsAreUnaffected(_PreflightSandbox):
    """Near-miss controls: a fix that breaks the happy path is its own defect,
    and a guard that can no longer write anything is not a fix."""

    def test_the_block_is_replaced_and_the_prose_is_kept(self):
        self.seed()
        proc = self.check()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        text = self.read(self.preflight, "r")
        self.assertIn(PROSE_ABOVE, text)
        self.assertIn(PROSE_BELOW, text)
        self.assertIn("### Operator Notes", text)
        self.assertNotIn("2026-01-01", text,
                         "the stale generated block was not replaced")
        self.assertIn("| Capability | Status | Evidence | Recommendation |", text)
        self.assertEqual(text.count("## Automated Optional Tool Check"), 1)

    def test_a_missing_file_is_created_with_the_documented_header(self):
        proc = self.check()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        text = self.read(self.preflight, "r")
        self.assertTrue(text.startswith("# Capability Preflight\n"), repr(text[:60]))
        self.assertTrue(text.endswith("\n"))
        self.assertIn("<!-- project-os:optional-tool-check:end -->", text)

    def test_repeated_runs_are_idempotent_in_shape(self):
        self.seed()
        self.assertEqual(self.check().returncode, 0)
        first = self.read(self.preflight, "r")
        self.assertEqual(self.check().returncode, 0)
        second = self.read(self.preflight, "r")
        self.assertEqual(first.count("\n"), second.count("\n"),
                         "the block is accumulating instead of being replaced")

    def test_a_successful_write_leaves_no_staging_litter(self):
        self.seed()
        self.assertEqual(self.check().returncode, 0)
        self.assertEqual(os.listdir(self.blackboard),
                         ["17-capability-preflight.md"],
                         "staging litter was left in blackboard/")

    def test_a_crashed_write_leaves_no_staging_litter(self):
        if not _CAN_LIMIT_FILE_SIZE:
            self.skipTest("needs RLIMIT_FSIZE")
        self.seed()
        self.assert_crashed(self.check(fsize=600), "crashing run")
        self.assertEqual(os.listdir(self.blackboard),
                         ["17-capability-preflight.md"],
                         "the failed write left its staged file behind")


# --------------------------------------------------------------------------
# addons/full-engine/memory/goal_guard.py
# --------------------------------------------------------------------------

GOAL_NOTE = "Operator note: the goal was ratified at the 07-24 board review."

SEEDED_GOAL = (
    "# 00 Project Goal\n"
    "\n"
    "## Summary\n"
    "\n"
    "Build a credit-card tracker that flags overspend before the statement closes.\n"
    "\n"
    "## Notes\n"
    "\n"
    "%s\n"
) % GOAL_NOTE


class _GoalGuardSandbox(_CrashSandbox):
    source = "addons/full-engine/memory/goal_guard.py"

    def setUp(self):
        super().setUp()
        self.rundir = os.path.join(self.tmp, "run")
        os.makedirs(self.rundir)
        self.goal = os.path.join(self.rundir, "00-project-goal.md")

    def seed(self, text=SEEDED_GOAL):
        self.write(self.goal, text)
        return self.read(self.goal)

    def migrate(self, apply=True, fsize=None):
        argv = ["--migrate", self.goal] + (["--apply"] if apply else [])
        return self.run_cli(argv, fsize=fsize)


@unittest.skipUnless(_CAN_LIMIT_FILE_SIZE, "needs RLIMIT_FSIZE")
class GoalFileSurvivesAnInterruptedMigration(_GoalGuardSandbox):
    def test_the_goal_survives_a_crash_during_the_migration_write(self):
        before = self.seed()
        crash = self.migrate(fsize=120)
        self.assert_crashed(crash, "goal_guard --migrate under RLIMIT_FSIZE")
        self.assert_intact(self.goal, before, "00-project-goal.md")

        text = self.read(self.goal, "r")
        self.assertIn("credit-card tracker", text,
                      "the sentence the migration seeds from was destroyed")
        self.assertIn(GOAL_NOTE, text)

    def test_the_migration_can_be_retried_after_a_crash(self):
        self.seed()
        self.assert_crashed(self.migrate(fsize=120), "crashing migration")

        again = self.migrate()
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        text = self.read(self.goal, "r")
        self.assertIn("## Canonical Goal", text)
        self.assertIn("credit-card tracker", text)
        self.assertIn(GOAL_NOTE, text)
        self.assertNotIn("PLACEHOLDER", again.stdout,
                         "the retry lost the seed and fell back to a "
                         "placeholder goal:\n" + again.stdout)


class GoalFilePermissions(_GoalGuardSandbox):
    def test_an_existing_goals_mode_is_preserved_not_reset(self):
        for wanted in (0o600, 0o640, 0o644):
            self.seed()
            os.chmod(self.goal, wanted)
            proc = self.migrate()
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(
                stat.S_IMODE(os.stat(self.goal).st_mode), wanted,
                "00-project-goal.md's mode %s was not preserved across the "
                "migration" % oct(wanted))


class GoalStagingNameIsNotPredictable(_GoalGuardSandbox):
    def test_symlinks_planted_at_guessable_staging_names_are_never_written(self):
        self.seed()
        planted = self.plant_symlinks(self.rundir, "00-project-goal.md")
        proc = self.migrate()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assert_victims_untouched(planted)


class GoalGuardOrdinaryRunsAreUnaffected(_GoalGuardSandbox):
    def test_a_dry_run_still_writes_nothing(self):
        before = self.seed()
        proc = self.migrate(apply=False)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("WOULD MIGRATE", proc.stdout)
        self.assert_intact(self.goal, before, "dry run")

    def test_apply_writes_the_anchor_and_keeps_every_original_line(self):
        self.seed()
        proc = self.migrate()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        text = self.read(self.goal, "r")
        for line in SEEDED_GOAL.splitlines():
            if line.strip():
                self.assertIn(line, text,
                              "the migration dropped an original line")
        self.assertIn("## Canonical Goal", text)
        self.assertTrue(text.startswith("# 00 Project Goal\n"), repr(text[:40]))

    def test_an_already_anchored_file_is_left_alone(self):
        self.seed()
        self.assertEqual(self.migrate().returncode, 0)
        after_first = self.read(self.goal)
        proc = self.migrate()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("already anchored", proc.stdout)
        self.assert_intact(self.goal, after_first, "second migration")

    def test_the_hash_and_compare_paths_still_work(self):
        self.seed()
        self.assertEqual(self.migrate().returncode, 0)
        hashed = self.run_cli(["--hash", self.goal])
        self.assertEqual(hashed.returncode, 0, hashed.stderr)
        digest = hashed.stdout.strip()
        self.assertEqual(len(digest), 12, repr(digest))

        roster = os.path.join(self.rundir, "21-agent-roster.md")
        self.write(roster, "Goal hash: %s\n" % digest)
        match = self.run_cli([self.goal, roster])
        self.assertEqual(match.returncode, 0, match.stdout + match.stderr)
        self.assertIn("MATCH", match.stdout)

        self.write(roster, "Goal hash: deadbeef0000\n")
        drift = self.run_cli([self.goal, roster])
        self.assertEqual(drift.returncode, 1, drift.stdout + drift.stderr)
        self.assertIn("DRIFT", drift.stdout)

    def test_selftest_still_passes(self):
        proc = self.run_cli(["--selftest"])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("goal_guard selftest: OK", proc.stdout)

    def test_a_successful_migration_leaves_no_staging_litter(self):
        self.seed()
        self.assertEqual(self.migrate().returncode, 0)
        self.assertEqual(os.listdir(self.rundir), ["00-project-goal.md"],
                         "staging litter was left next to the goal file")

    def test_a_crashed_migration_leaves_no_staging_litter(self):
        if not _CAN_LIMIT_FILE_SIZE:
            self.skipTest("needs RLIMIT_FSIZE")
        self.seed()
        self.assert_crashed(self.migrate(fsize=120), "crashing migration")
        self.assertEqual(os.listdir(self.rundir), ["00-project-goal.md"],
                         "the failed write left its staged file behind")


if __name__ == "__main__":
    unittest.main()
