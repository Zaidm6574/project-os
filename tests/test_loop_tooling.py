"""Smoke tests for the loop-tooling scripts (bb_lock, plan_artifact, harvest, mneme).

Each test guards a failure mode that actually happened in the field — see
docs/field-notes.md. Everything runs against temp dirs; no personal data,
no network, no Ollama required (lexical embedder is forced).
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))


def run_py(script, *args, env_extra=None, cwd=None):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run([sys.executable, str(script), *args],
                          capture_output=True, text=True, env=env, cwd=cwd or ROOT)


class TestBBLock(unittest.TestCase):
    """Field note: two agents completing steps concurrently WILL lose writes."""

    def test_second_acquire_blocks_until_release(self):
        with tempfile.TemporaryDirectory() as td:
            env = {"BB_LOCK_DIR": td}
            target = str(Path(td) / "shared.md")
            r1 = run_py(SCRIPTS / "bb_lock.py", "acquire", target, "--agent", "a",
                        env_extra=env)
            self.assertEqual(r1.returncode, 0, r1.stderr)
            # Second acquire with a tiny wait must FAIL while the lock is held.
            r2 = run_py(SCRIPTS / "bb_lock.py", "acquire", target, "--agent", "b",
                        "--wait", "0.3", env_extra=env)
            self.assertNotEqual(r2.returncode, 0)
            r3 = run_py(SCRIPTS / "bb_lock.py", "release", target, "--agent", "a",
                        env_extra=env)
            self.assertEqual(r3.returncode, 0, r3.stderr)
            r4 = run_py(SCRIPTS / "bb_lock.py", "acquire", target, "--agent", "b",
                        "--wait", "0.3", env_extra=env)
            self.assertEqual(r4.returncode, 0, r4.stderr)

    def test_stale_lock_is_reaped(self):
        with tempfile.TemporaryDirectory() as td:
            env = {"BB_LOCK_DIR": td, "BB_LOCK_STALE": "0"}
            target = str(Path(td) / "shared.md")
            run_py(SCRIPTS / "bb_lock.py", "acquire", target, "--agent", "dead",
                   env_extra=env)
            # stale-after 0s: a fresh acquire must succeed by reaping.
            r = run_py(SCRIPTS / "bb_lock.py", "acquire", target, "--agent", "live",
                       "--wait", "1", env_extra=env)
            self.assertEqual(r.returncode, 0, r.stderr)


class TestPlanArtifact(unittest.TestCase):
    """Field note: maker/checker must be structural, not advisory."""

    def _create(self, steps, plan_id):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(steps, f)
            steps_file = f.name
        try:
            return run_py(SCRIPTS / "plan_artifact.py", "create",
                          "--goal", "test goal", "--steps-file", steps_file,
                          "--id", plan_id)
        finally:
            os.unlink(steps_file)

    def _cleanup(self, plan_id):
        p = ROOT / "blackboard" / "plans" / f"{plan_id}.json"
        if p.exists():
            p.unlink()

    def test_multistep_plan_without_checker_is_rejected(self):
        pid = "test-nochecker"
        try:
            r = self._create([{"id": "s1", "role": "builder", "task": "build"},
                              {"id": "s2", "role": "builder", "task": "build more",
                               "depends_on": ["s1"]}], pid)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("checker", (r.stderr + r.stdout).lower())
        finally:
            self._cleanup(pid)

    def test_plan_with_dependent_checker_is_accepted(self):
        pid = "test-checker"
        try:
            r = self._create([{"id": "s1", "role": "builder", "task": "build"},
                              {"id": "s2", "role": "reviewer", "task": "verify s1",
                               "depends_on": ["s1"]}], pid)
            self.assertEqual(r.returncode, 0, r.stderr)
        finally:
            self._cleanup(pid)

    def test_single_step_plan_is_exempt(self):
        pid = "test-solo"
        try:
            r = self._create([{"id": "s1", "role": "builder", "task": "solo"}], pid)
            self.assertEqual(r.returncode, 0, r.stderr)
        finally:
            self._cleanup(pid)


class TestHarvestParser(unittest.TestCase):
    """Field note: format drift silently destroys harvesting — twice in one day."""

    def _parse(self, md):
        import harvest
        return list(harvest.bullets_by_section(md))

    def test_all_three_heading_dialects_parse(self):
        md = (
            "## Lessons\n- plural-heading bullet lesson\n"
            "## lesson\n- kebab-heading bullet lesson\n"
            "## User Preferences Observed\n"
            "| Preference | Evidence |\n|---|---|\n| template table preference | proof |\n"
        )
        got = self._parse(md)
        self.assertEqual(len(got), 3, got)
        self.assertEqual({t for t, _ in got}, {"lesson", "preference"})

    def test_rejected_and_private_rows_never_extracted(self):
        md = (
            "## Lessons\n"
            "| Lesson | Approved For Reuse? |\n|---|---|\n"
            "| good lesson worth keeping here | Yes |\n"
            "| secret thing | Rejected |\n"
            "| other secret | Private-only |\n"
        )
        got = self._parse(md)
        self.assertEqual(len(got), 1, got)
        self.assertIn("good lesson", got[0][1])


class TestMnemeAdapter(unittest.TestCase):
    """Field note: mixed-embedder queries must be refused, not scored."""

    def test_lexical_build_and_query_standalone(self):
        with tempfile.TemporaryDirectory() as td:
            # minimal standalone layout: memory/ + empty runs/
            mem = Path(td) / "memory"
            mem.mkdir()
            (Path(td) / "runs").mkdir()
            adapter = mem / "mneme_adapter.py"
            adapter.write_text((ROOT / "memory" / "mneme_adapter.py").read_text())
            env = {"OSVEC_EMBEDDER": "lexical",
                   "HOME": td}  # point the shared-brain path away from real data
            r = run_py(adapter, "build", env_extra=env, cwd=td)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("lexical", r.stdout)

    def test_neural_index_refuses_query_when_ollama_unreachable(self):
        with tempfile.TemporaryDirectory() as td:
            mem = Path(td) / "memory"
            mem.mkdir()
            adapter = mem / "mneme_adapter.py"
            adapter.write_text((ROOT / "memory" / "mneme_adapter.py").read_text())
            # Hand-craft an index claiming a neural embedder.
            (mem / "mneme_index.json").write_text(json.dumps(
                {"dim": 768, "embedder": "neural-nomic-embed-text", "count": 0,
                 "entries": []}))
            env = {"OSVEC_OLLAMA_URL": "http://127.0.0.1:1", "HOME": td}
            r = run_py(adapter, "query", "anything", env_extra=env, cwd=td)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("rebuild", (r.stderr + r.stdout).lower())


if __name__ == "__main__":
    unittest.main()
