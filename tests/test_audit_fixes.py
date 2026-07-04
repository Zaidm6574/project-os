"""Tests added by the 2026-07-04 full audit: pin down the destructive and
first-run-critical paths that previously had zero coverage (evolution best-of,
wt.py data-loss guards, brain_archive apply, fresh-install brain append,
harvest dedupe, plan_artifact recompile gate, brain_scale missing-input gauge).
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import evolution  # noqa: E402
import harvest  # noqa: E402
import wt  # noqa: E402


def run_py(script, *args, env_extra=None, cwd=None, stdin=None):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True, text=True, env=env, cwd=cwd, input=stdin)


def git(cwd, *args):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, f"git {args} failed: {r.stderr}"
    return r.stdout.strip()


class TestEvolutionBestOf(unittest.TestCase):
    def test_best_of_picks_highest_score_numerically(self):
        data = {"records": [
            {"variant": "a", "score": 0.85, "verdict": "revise"},
            {"variant": "b", "score": 0.9, "verdict": "approve"},
            {"variant": "c", "score": 0.7, "verdict": "reject"},
        ]}
        self.assertEqual(evolution.best_of(data)["variant"], "b")

    def test_best_of_is_numeric_not_lexical(self):
        # lexically "0.9" < "0.10" is false but "9" > "10" lexically — pin numerics
        data = {"records": [
            {"variant": "low", "score": 0.9, "verdict": "revise"},
            {"variant": "high", "score": 1.05, "verdict": "revise"},
        ]}
        self.assertEqual(evolution.best_of(data)["variant"], "high")

    def test_best_of_all_unscored_returns_none(self):
        data = {"records": [{"variant": "a", "score": None, "verdict": "revise"}]}
        self.assertIsNone(evolution.best_of(data))


class TestHarvestDedupe(unittest.TestCase):
    def test_longer_new_lesson_not_dropped_by_short_old_entry(self):
        old = harvest.norm("verify revision flips after deploy")
        new_lesson = ("Always verify revision flips after deploy, and additionally "
                      "auto-retry when DNS hiccups make gcloud fail silently")
        self.assertFalse(harvest.is_dupe(new_lesson, [old]))

    def test_new_lesson_contained_in_existing_is_dupe(self):
        old = harvest.norm("Always verify revision flips after deploy and auto-retry on DNS hiccups")
        self.assertTrue(harvest.is_dupe("verify revision flips after deploy", [old]))

    def test_too_short_is_noise(self):
        self.assertTrue(harvest.is_dupe("short", []))


class TestWorktreeGuards(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "t@t")
        git(self.repo, "config", "user.name", "t")
        (self.repo / "f.txt").write_text("base\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "base")
        # keep worktrees inside the tempdir, not the real ~/.project-os
        self._home = wt.HOME
        wt.HOME = self.tmp.name

    def tearDown(self):
        wt.HOME = self._home
        self.tmp.cleanup()

    def _make_wt(self, name):
        path = wt.wt_dir(str(self.repo), name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        git(self.repo, "worktree", "add", "-q", "-b", f"wt/{name}", path)
        return Path(path)

    def test_remove_refuses_unmerged_commits(self):
        w = self._make_wt("feat")
        (w / "new.txt").write_text("work\n")
        git(w, "add", ".")
        git(w, "commit", "-qm", "unmerged work")
        with self.assertRaises(SystemExit) as cm:
            wt.cmd_remove(str(self.repo), "feat", force=False)
        self.assertIn("unmerged", str(cm.exception))
        self.assertTrue(w.exists(), "worktree must survive a refused remove")

    def test_remove_refuses_dirty_worktree(self):
        w = self._make_wt("dirty")
        (w / "uncommitted.txt").write_text("oops\n")
        with self.assertRaises(SystemExit) as cm:
            wt.cmd_remove(str(self.repo), "dirty", force=False)
        self.assertIn("uncommitted", str(cm.exception))
        self.assertTrue(w.exists())

    def test_merge_refuses_dirty_main(self):
        self._make_wt("clean")
        (self.repo / "f.txt").write_text("dirty main\n")
        with self.assertRaises(SystemExit) as cm:
            wt.cmd_merge(str(self.repo), "clean")
        self.assertIn("dirty", str(cm.exception))

    def test_clean_merge_and_remove_succeed(self):
        w = self._make_wt("ok")
        (w / "done.txt").write_text("done\n")
        git(w, "add", ".")
        git(w, "commit", "-qm", "done")
        self.assertEqual(wt.cmd_merge(str(self.repo), "ok"), 0)
        self.assertEqual(wt.cmd_remove(str(self.repo), "ok", force=False), 0)
        self.assertFalse(w.exists())
        self.assertTrue((self.repo / "done.txt").exists())


class BrainEnvMixin(unittest.TestCase):
    """Temp shared brain + temp mneme index via the env vars every brain
    script must honor (the audit found only brain_archive read them)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.brain = Path(self.tmp.name) / "central-brain" / "shared-brain.jsonl"
        self.env = {
            "PROJECT_OS_SHARED_BRAIN": str(self.brain),
            "MNEME_INDEX": str(Path(self.tmp.name) / "mneme_index.json"),
            "MNEME_EMBEDDER": "lexical",
            "BB_LOCK_DIR": str(Path(self.tmp.name) / "locks"),
        }

    def tearDown(self):
        self.tmp.cleanup()


class TestBrainAppendFreshInstall(BrainEnvMixin):
    def test_append_creates_missing_brain_dir(self):
        self.assertFalse(self.brain.parent.exists())
        r = run_py(SCRIPTS / "brain_append.py",
                   "--line", '{"kind":"lesson","text":"fresh install works"}',
                   "--no-reindex", env_extra=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = self.brain.read_text().strip().splitlines()
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0])["text"], "fresh install works")

    def test_append_refuses_invalid_json(self):
        r = run_py(SCRIPTS / "brain_append.py", "--line", "not json",
                   "--no-reindex", env_extra=self.env)
        self.assertEqual(r.returncode, 2)
        self.assertIn("REFUSED", r.stderr)
        self.assertFalse(self.brain.exists())

    def test_bb_lock_append_creates_parent_dir(self):
        target = Path(self.tmp.name) / "deep" / "nested" / "log.jsonl"
        r = run_py(SCRIPTS / "bb_lock.py", "append", str(target),
                   "--line", "hello", env_extra=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(target.read_text(), "hello\n")


class TestBrainArchiveApply(BrainEnvMixin):
    def _seed(self):
        self.brain.parent.mkdir(parents=True)
        rows = [
            {"id": "keep-1", "kind": "lesson", "text": "a durable lesson", "ts": "2026-07-01"},
            {"id": "old-1", "kind": "interest", "text": "stale interest", "ts": "2020-01-01"},
            {"id": "keep-2", "kind": "interest", "text": "fresh interest",
             "ts": "2099-01-01"},
        ]
        self.brain.write_text("".join(json.dumps(r) + "\n" for r in rows))
        return rows

    def test_apply_moves_stale_conserves_rows_and_backs_up(self):
        self._seed()
        r = run_py(SCRIPTS / "brain_archive.py", "apply", "--interest-days", "60",
                   env_extra=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        active = [json.loads(l) for l in self.brain.read_text().splitlines() if l.strip()]
        archive = Path(str(self.brain).replace(".jsonl", "-archive.jsonl"))
        archived = [json.loads(l) for l in archive.read_text().splitlines() if l.strip()]
        self.assertEqual({o["id"] for o in active}, {"keep-1", "keep-2"})
        self.assertEqual([o["id"] for o in archived], ["old-1"])
        self.assertIn("archived", archived[0])
        self.assertEqual(len(active) + len(archived), 3, "row count must be conserved")
        backups = list(self.brain.parent.glob("*.pre-archive-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(len(backups[0].read_text().strip().splitlines()), 3)

    def test_apply_refuses_unknown_ids_untouched(self):
        self._seed()
        before = self.brain.read_text()
        r = run_py(SCRIPTS / "brain_archive.py", "apply", "--ids", "nope",
                   env_extra=self.env)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.brain.read_text(), before)


class TestPlanArtifactRecompileGate(BrainEnvMixin):
    def _plan(self, status):
        plans = Path(self.tmp.name) / "blackboard" / "plans"
        plans.mkdir(parents=True)
        plan = {"schema": "plan/v1", "id": "p1", "status": status, "goal": "g",
                "steps": [{"id": "s1", "role": "builder", "task": "t",
                           "depends_on": [], "outputs": []}]}
        pf = plans / "p1.json"
        pf.write_text(json.dumps(plan))
        return pf  # plan_artifact accepts a full .json path as the plan id

    def test_recompile_of_done_plan_refused_without_force(self):
        pf = self._plan("done")
        r = run_py(SCRIPTS / "plan_artifact.py", "compile", str(pf),
                   env_extra=self.env)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("done", r.stderr)
        self.assertEqual(json.loads(pf.read_text())["status"], "done",
                         "status must not be clobbered by a refused compile")

    def test_unapproved_plan_refused(self):
        pf = self._plan("planned")
        r = run_py(SCRIPTS / "plan_artifact.py", "compile", str(pf),
                   env_extra=self.env)
        self.assertNotEqual(r.returncode, 0)

    def test_approved_plan_compiles_and_flips_to_running(self):
        # Success path: exercises the full-path save-back that a previous bug
        # sent to the wrong directory (status write went to PLANS/<id>.json,
        # not the loaded path). Must exit 0, write packets, and flip status.
        pf = self._plan("approved")
        r = run_py(SCRIPTS / "plan_artifact.py", "compile", str(pf),
                   env_extra=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(pf.read_text())["status"], "running")
        packets = pf.parent.parent / "packets"
        self.assertTrue(any(packets.glob("p1-*.md")))

    def test_force_recompile_backs_up_prior_state(self):
        pf = self._plan("done")
        r = run_py(SCRIPTS / "plan_artifact.py", "compile", str(pf), "--force",
                   env_extra=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(pf.with_name(pf.name + ".pre-force").exists())


class TestBrainScaleMissingInput(BrainEnvMixin):
    def test_missing_brain_is_not_healthy(self):
        r = run_py(SCRIPTS / "brain_scale.py", env_extra=self.env)
        self.assertNotEqual(r.returncode, 0,
                            "a missing shared brain must not exit 0 (healthy)")
        self.assertIn("n/a", r.stdout)

    def test_gauge_line_matches_nightly_filter(self):
        self.brain.parent.mkdir(parents=True)
        self.brain.write_text('{"kind":"lesson","text":"x"}\n')
        r = run_py(SCRIPTS / "brain_scale.py", env_extra=self.env)
        matched = [l for l in r.stdout.splitlines()
                   if "OVERALL" in l.upper() or "STATUS" in l.upper()]
        self.assertTrue(matched, "os_nightly's gauge-line filter must match brain_scale output")


if __name__ == "__main__":
    unittest.main()
