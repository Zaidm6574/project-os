#!/usr/bin/env python3
"""new_run.py must never exit 0 with an empty/partial run directory.

Regression for the 2026-07-26 audit: `_blackboard_dir()` accepted the live
`blackboard/` as the whole template as soon as it held ANY numbered file. A
blackboard containing a single numbered file therefore made the template copy
collapse -- `--tier solo` produced a run directory with no numbered file at all
(only `PACKETS.md`) and still printed "Scaffolded runs/<slug>/" and exited 0.

Every assertion below is on the filesystem, in a tempdir sandbox. Nothing here
touches the live blackboard/, memory/, runs/ or home trees.

Standard library only; must pass on the 3.9 CI floor.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEW_RUN = os.path.join(REPO, "addons", "full-engine", "memory", "new_run.py")
REAL_TEMPLATE = os.path.join(REPO, "blackboard-template")
REAL_ADDONS = os.path.join(REPO, "addons", "full-engine", "blackboard-addons")

# The repo's own "slim but closable run schema" (TIER_FILES["solo"]). Any tier
# that scaffolds successfully must cover at least these schema slots.
SOLO_PREFIXES = {"00", "07", "09", "12", "13", "14", "19", "21"}
MINI_PREFIXES = SOLO_PREFIXES | {"01", "02", "03", "04", "06"}


def prefixes(names):
    return set(n[:2] for n in names if n.endswith(".md") and n[:2].isdigit())


class SandboxCase(unittest.TestCase):
    """Builds a throwaway project root and points new_run.py at it.

    new_run.py locates its root by walking up from ``__file__`` with
    ``os.path.abspath``, which does not resolve symlinks -- so a symlink to the
    real script inside the sandbox makes the sandbox its root.
    """

    def setUp(self):
        self.assertTrue(os.path.isfile(NEW_RUN), NEW_RUN)
        self.root = tempfile.mkdtemp(prefix="new-run-empty-scaffold-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        os.makedirs(os.path.join(self.root, "memory"))
        os.makedirs(os.path.join(self.root, "runs"))
        os.makedirs(os.path.join(self.root, "blackboard"))
        self.script = os.path.join(self.root, "memory", "new_run.py")
        os.symlink(NEW_RUN, self.script)
        # Mirror the full-engine layout the script ships in: the addon supplies
        # 21-agent-roster.md, which the module's own selftest expects.
        addons = os.path.join(self.root, "blackboard-addons")
        os.makedirs(addons)
        for name in os.listdir(REAL_ADDONS):
            src = os.path.join(REAL_ADDONS, name)
            if os.path.isfile(src) and name.endswith(".md"):
                shutil.copy2(src, os.path.join(addons, name))

    # -- sandbox helpers -------------------------------------------------
    def install_template(self):
        """Ship `blackboard-template/`, as a real clone of the repo has."""
        dest = os.path.join(self.root, "blackboard-template")
        os.makedirs(dest, exist_ok=True)
        for name in os.listdir(REAL_TEMPLATE):
            src = os.path.join(REAL_TEMPLATE, name)
            if os.path.isfile(src) and name.endswith(".md"):
                shutil.copy2(src, os.path.join(dest, name))
        return dest

    def fill_blackboard(self):
        """A healthy working blackboard: the full numbered schema."""
        dest = os.path.join(self.root, "blackboard")
        for name in os.listdir(REAL_TEMPLATE):
            src = os.path.join(REAL_TEMPLATE, name)
            if os.path.isfile(src) and name.endswith(".md"):
                shutil.copy2(src, os.path.join(dest, name))
        return dest

    def write_blackboard_file(self, name):
        path = os.path.join(self.root, "blackboard", name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("# scratch\n")
        return path

    def run_new_run(self, *args):
        return subprocess.run(
            [sys.executable, self.script] + list(args),
            cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True,
        )

    def run_dir(self, slug):
        return os.path.join(self.root, "runs", slug)

    def assert_complete(self, slug, required):
        d = self.run_dir(slug)
        self.assertTrue(os.path.isdir(d), "run dir missing: %s" % slug)
        listing = os.listdir(d)
        self.assertTrue(
            os.path.isfile(os.path.join(d, "00-project-goal.md")),
            "no 00-project-goal.md in %s (got %r)" % (slug, sorted(listing)),
        )
        missing = required - prefixes(listing)
        self.assertEqual(
            set(), missing,
            "scaffolded run %s is missing schema slots %s (got %r)"
            % (slug, sorted(missing), sorted(listing)),
        )


class RefusesEmptyScaffold(SandboxCase):
    def test_partial_blackboard_no_template_refuses_solo(self):
        """The reported defect: one stray numbered file -> empty solo run."""
        self.write_blackboard_file("03-decisions.md")
        res = self.run_new_run("myrun", "--tier", "solo")
        self.assertNotEqual(
            0, res.returncode,
            "exit 0 with an empty scaffold; stdout=%r listing=%r"
            % (res.stdout, sorted(os.listdir(self.run_dir("myrun")))
               if os.path.isdir(self.run_dir("myrun")) else None),
        )
        self.assertFalse(
            os.path.exists(self.run_dir("myrun")),
            "refused but left a run directory behind: %r"
            % (sorted(os.listdir(self.run_dir("myrun")))
               if os.path.isdir(self.run_dir("myrun")) else None),
        )
        self.assertIn("refus", res.stderr.lower(), res.stderr)
        # The message must name what was missing, not just fail.
        self.assertIn("00-", res.stderr, res.stderr)
        self.assertNotIn("Scaffolded", res.stdout, res.stdout)

    def test_partial_blackboard_no_template_refuses_full(self):
        """Full tier silently produced a run holding one unrelated file."""
        self.write_blackboard_file("03-decisions.md")
        res = self.run_new_run("myrun", "--tier", "full")
        self.assertNotEqual(0, res.returncode, res.stdout)
        d = self.run_dir("myrun")
        if os.path.exists(d):
            self.fail("left a partial run dir: %r" % sorted(os.listdir(d)))
        self.assertIn("00-", res.stderr, res.stderr)

    def test_no_numbered_source_at_all_refuses(self):
        """Empty blackboard and no template: still must not exit 0."""
        res = self.run_new_run("myrun", "--tier", "solo")
        self.assertNotEqual(0, res.returncode, res.stdout)
        self.assertFalse(os.path.exists(self.run_dir("myrun")), res.stdout)
        self.assertTrue(res.stderr.strip(), "refused with no explanation")

    def test_index_not_claimed_after_refusal(self):
        """A refused scaffold must not advertise the run anywhere."""
        self.write_blackboard_file("03-decisions.md")
        res = self.run_new_run("myrun", "--tier", "solo")
        self.assertNotEqual(0, res.returncode, res.stdout)
        index = os.path.join(self.root, "runs", "INDEX.md")
        if os.path.exists(index):
            with open(index, "r", encoding="utf-8") as fh:
                self.assertNotIn("myrun", fh.read())


class NearMissStillWorks(SandboxCase):
    """The legitimate cases adjacent to the blocked one must keep working."""

    def test_partial_blackboard_with_template_scaffolds_complete_run(self):
        """A partial blackboard + shipped template = complete run, exit 0."""
        self.install_template()
        self.write_blackboard_file("03-decisions.md")
        res = self.run_new_run("myrun", "--tier", "solo")
        self.assertEqual(0, res.returncode, res.stderr)
        self.assert_complete("myrun", SOLO_PREFIXES)

    def test_live_blackboard_file_wins_over_template(self):
        """Gap-fill must not override a file the live blackboard provides."""
        self.install_template()
        self.fill_blackboard()
        marker = "UNIQUE-LIVE-BLACKBOARD-MARKER"
        with open(os.path.join(self.root, "blackboard", "00-project-goal.md"),
                  "w", encoding="utf-8") as fh:
            fh.write("## Canonical Goal\n\n%s\n" % marker)
        res = self.run_new_run("myrun", "--tier", "full")
        self.assertEqual(0, res.returncode, res.stderr)
        with open(os.path.join(self.run_dir("myrun"), "00-project-goal.md"),
                  "r", encoding="utf-8") as fh:
            self.assertIn(marker, fh.read())

    def test_fresh_blackboard_scaffolds_every_tier(self):
        self.install_template()
        self.fill_blackboard()
        for tier, required in (("solo", SOLO_PREFIXES),
                               ("mini", MINI_PREFIXES),
                               ("full", MINI_PREFIXES)):
            slug = "run-%s" % tier
            res = self.run_new_run(slug, "--tier", tier)
            self.assertEqual(0, res.returncode, res.stderr)
            self.assertIn("Scaffolded", res.stdout)
            self.assert_complete(slug, required)
            if tier == "solo":
                self.assertTrue(os.path.isfile(
                    os.path.join(self.run_dir(slug), "PACKETS.md")))
                self.assertFalse(os.path.isdir(
                    os.path.join(self.run_dir(slug), "packets")))
            else:
                self.assertTrue(os.path.isdir(
                    os.path.join(self.run_dir(slug), "packets")))

    def test_template_only_clone_scaffolds(self):
        """Fresh clone: only blackboard-template/ exists, blackboard/ is bare."""
        self.install_template()
        res = self.run_new_run("myrun", "--tier", "full")
        self.assertEqual(0, res.returncode, res.stderr)
        self.assert_complete("myrun", MINI_PREFIXES)

    def test_run_appears_in_index(self):
        self.install_template()
        self.fill_blackboard()
        self.assertEqual(0, self.run_new_run("myrun", "--tier", "solo").returncode)
        with open(os.path.join(self.root, "runs", "INDEX.md"),
                  "r", encoding="utf-8") as fh:
            self.assertIn("myrun", fh.read())

    def test_rerun_refuses_and_preserves_existing_run(self):
        """Documented re-run behaviour: refuse to overwrite, touch nothing."""
        self.install_template()
        self.fill_blackboard()
        self.assertEqual(0, self.run_new_run("myrun", "--tier", "solo").returncode)
        work = os.path.join(self.run_dir("myrun"), "WORK-IN-PROGRESS.md")
        with open(work, "w", encoding="utf-8") as fh:
            fh.write("real work\n")
        res = self.run_new_run("myrun", "--tier", "solo")
        self.assertNotEqual(0, res.returncode, res.stdout)
        self.assertIn("already exists", res.stderr)
        self.assertTrue(os.path.isfile(work), "clobbered existing run content")
        self.assert_complete("myrun", SOLO_PREFIXES)

    def test_rerun_over_incomplete_run_still_refuses(self):
        """An existing (even hollow) run dir is never silently overwritten."""
        self.install_template()
        self.fill_blackboard()
        os.makedirs(self.run_dir("myrun"))
        res = self.run_new_run("myrun", "--tier", "solo")
        self.assertNotEqual(0, res.returncode, res.stdout)
        self.assertIn("already exists", res.stderr)

    def test_reindex_still_works(self):
        self.install_template()
        self.fill_blackboard()
        self.assertEqual(0, self.run_new_run("myrun", "--tier", "solo").returncode)
        res = self.run_new_run("--reindex")
        self.assertEqual(0, res.returncode, res.stderr)
        self.assertIn("Regenerated", res.stdout)

    def test_selftest_still_passes(self):
        self.install_template()
        self.fill_blackboard()
        res = self.run_new_run("--selftest")
        self.assertEqual(0, res.returncode, res.stderr)
        self.assertIn("OK", res.stdout)

    def test_bad_slug_still_refused(self):
        """The pre-existing slug guard must survive the new gate."""
        self.install_template()
        self.fill_blackboard()
        res = self.run_new_run("../escape", "--tier", "solo")
        self.assertEqual(2, res.returncode, res.stdout)
        self.assertIn("refusing slug", res.stderr)


class CanonicalFileIsRequiredByName(SandboxCase):
    """A slot is not a file — the bypass an adversary drove through the gate.

    The first version of this guard compared two-character SLOT numbers while
    every consumer (regenerate_index, validate_run.py, goal_guard.py,
    adopt_project.py) opens "00-project-goal.md" by its LITERAL name. Renaming
    that one file to "00-project-goal.archived.md" left slot 00 looking covered,
    so the scaffold reported success, produced a run with no project goal, and
    adopt_project then died on it with a raw FileNotFoundError.
    """

    RENAMED = "00-project-goal.archived.md"

    def test_a_slot_occupied_by_a_non_canonical_name_is_still_missing(self):
        self.fill_blackboard()
        bb = os.path.join(self.root, "blackboard")
        os.rename(os.path.join(bb, "00-project-goal.md"),
                  os.path.join(bb, self.RENAMED))
        res = self.run_new_run("myrun", "--tier", "solo")
        self.assertEqual(
            2, res.returncode,
            "scaffolded a run with no 00-project-goal.md: %s" % res.stdout)
        self.assertIn("00-project-goal.md", res.stderr)
        self.assertFalse(os.path.isdir(self.run_dir("myrun")),
                         "an incomplete run directory was left behind")

    def test_the_message_names_the_file_not_a_slot_glob(self):
        """The refusal has to be actionable: name the file to restore."""
        self.fill_blackboard()
        bb = os.path.join(self.root, "blackboard")
        os.rename(os.path.join(bb, "00-project-goal.md"),
                  os.path.join(bb, self.RENAMED))
        res = self.run_new_run("myrun", "--tier", "solo")
        self.assertNotIn("00-project-goal.md-*.md", res.stderr)

    def test_the_template_still_fills_the_canonical_file_by_name(self):
        """The mirror: when the template HAS it, the slot must not block the fill.

        The same prefix collision suppressed the last-resort fill, so the run
        shipped without the goal file even though the template sat right there.
        """
        self.install_template()
        self.fill_blackboard()
        bb = os.path.join(self.root, "blackboard")
        os.rename(os.path.join(bb, "00-project-goal.md"),
                  os.path.join(bb, self.RENAMED))
        res = self.run_new_run("myrun", "--tier", "solo")
        self.assertEqual(0, res.returncode, res.stderr)
        self.assertTrue(
            os.path.isfile(os.path.join(self.run_dir("myrun"),
                                        "00-project-goal.md")),
            "template fill was suppressed by the occupied slot")


if __name__ == "__main__":
    unittest.main()
