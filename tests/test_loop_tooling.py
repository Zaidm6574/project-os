"""Smoke tests for the loop-tooling scripts (bb_lock, plan_artifact, harvest, mneme).

Each test guards a failure mode that actually happened in the field — see
docs/field-notes.md. Everything runs against temp dirs; no personal data,
no network, no Ollama required (lexical embedder is forced).
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
            token = r1.stdout.strip()
            # Second acquire with a tiny wait must FAIL while the lock is held.
            r2 = run_py(SCRIPTS / "bb_lock.py", "acquire", target, "--agent", "b",
                        "--wait", "0.3", env_extra=env)
            self.assertNotEqual(r2.returncode, 0)
            # An agent label alone must NOT release a token-fenced lock —
            # only the acquisition token proves ownership (2026-07-17).
            r3 = run_py(SCRIPTS / "bb_lock.py", "release", target, "--agent", "a",
                        env_extra=env)
            self.assertNotEqual(r3.returncode, 0)
            r3b = run_py(SCRIPTS / "bb_lock.py", "release", target,
                         "--token", token, env_extra=env)
            self.assertEqual(r3b.returncode, 0, r3b.stderr)
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
                               "depends_on": ["s1"],
                               "verification": {
                                   "method": "run the focused regression test",
                                   "expected": "the test exits zero",
                                   "evidence": "captured unittest output",
                               }}], pid)
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

    def test_force_compile_rejects_dependency_cycle_without_hanging(self):
        with tempfile.TemporaryDirectory() as td:
            plans = Path(td) / "blackboard" / "plans"
            plans.mkdir(parents=True)
            plan_file = plans / "cycle.json"
            plan_file.write_text(json.dumps({
                "schema": "plan/v1",
                "id": "cycle",
                "goal": "test cycle guard",
                "status": "planned",
                "steps": [
                    {"id": "s1", "role": "builder", "task": "one",
                     "depends_on": ["s2"], "outputs": []},
                    {"id": "s2", "role": "reviewer", "task": "two",
                     "depends_on": ["s1"], "outputs": []},
                ],
            }))
            r = subprocess.run(
                [sys.executable, str(SCRIPTS / "plan_artifact.py"),
                 "compile", str(plan_file), "--force"],
                capture_output=True, text=True, cwd=ROOT, timeout=2)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("dependency cycle", (r.stderr + r.stdout).lower())

    def test_compile_revalidates_even_after_approval_or_force(self):
        # approval can predate edits; --force bypasses only the approval
        # gate — neither may skip the checker contract (2026-07-17)
        with tempfile.TemporaryDirectory() as td:
            plans = Path(td) / "blackboard" / "plans"
            plans.mkdir(parents=True)
            plan_file = plans / "edited.json"
            plan_file.write_text(json.dumps({
                "schema": "plan/v1",
                "id": "edited",
                "goal": "test compile revalidation",
                "status": "approved",
                "steps": [
                    {"id": "s1", "role": "builder", "task": "build",
                     "depends_on": [], "outputs": []},
                    {"id": "s2", "role": "checker", "task": "check",
                     "depends_on": ["s1"], "outputs": []},
                ],
            }))
            for extra in ([], ["--force"]):
                with self.subTest(extra=extra):
                    r = subprocess.run(
                        [sys.executable, str(SCRIPTS / "plan_artifact.py"),
                         "compile", str(plan_file), *extra],
                        capture_output=True, text=True, cwd=ROOT, timeout=5)
                    self.assertNotEqual(r.returncode, 0)
                    self.assertIn("cannot compile, invalid",
                                  (r.stderr + r.stdout).lower())
                    self.assertIn("verification", (r.stderr + r.stdout).lower())

    def test_locked_update_aborts_without_saving_when_lease_is_lost(self):
        # lease reaped + re-acquired mid-mutate: saving would overwrite the
        # new owner's plan, so locked_update must abort instead (2026-07-17)
        import plan_artifact
        with tempfile.TemporaryDirectory() as td:
            plan_file = Path(td) / "p.json"
            plan_file.write_text(json.dumps({
                "schema": "plan/v1", "id": "p", "goal": "g",
                "status": "planned",
                "steps": [{"id": "s1", "role": "builder", "task": "t",
                           "depends_on": [], "outputs": [], "done": False}],
            }))

            def mutate(plan):
                plan["status"] = "approved"

            with mock.patch.object(plan_artifact.bb_lock, "LOCK_DIR",
                                   str(Path(td) / "locks")), \
                    mock.patch.object(plan_artifact.bb_lock, "renew",
                                      return_value=False), \
                    contextlib.redirect_stderr(io.StringIO()) as err, \
                    self.assertRaises(SystemExit):
                plan_artifact.locked_update(str(plan_file), mutate)
            self.assertIn("lease lost", err.getvalue())
            saved = json.loads(plan_file.read_text())
            self.assertEqual(saved["status"], "planned",
                             "stale mutation must not be written")

    def test_missing_flag_value_is_a_clean_usage_error(self):
        r = run_py(SCRIPTS / "plan_artifact.py", "create", "--goal")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("missing value for --goal", r.stderr.lower())
        self.assertNotIn("traceback", r.stderr.lower())

    def test_malformed_steps_json_is_a_clean_usage_error(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("{not json")
            steps_file = f.name
        try:
            r = run_py(SCRIPTS / "plan_artifact.py", "create",
                       "--goal", "test goal", "--steps-file", steps_file)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("invalid json", r.stderr.lower())
            self.assertNotIn("traceback", r.stderr.lower())
        finally:
            os.unlink(steps_file)

    def test_numeric_steps_json_is_a_clean_usage_error(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(123, f)
            steps_file = f.name
        try:
            r = run_py(SCRIPTS / "plan_artifact.py", "create",
                       "--goal", "test goal", "--steps-file", steps_file)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("json array", r.stderr.lower())
            self.assertNotIn("traceback", r.stderr.lower())
        finally:
            os.unlink(steps_file)

    def test_malformed_plan_json_is_a_clean_usage_error(self):
        for contents in ("{not json", "123"):
            with self.subTest(contents=contents):
                with tempfile.NamedTemporaryFile("w", suffix=".json") as f:
                    f.write(contents)
                    f.flush()
                    r = run_py(SCRIPTS / "plan_artifact.py", "validate", f.name)
                self.assertNotEqual(r.returncode, 0)
                self.assertIn("usage error", r.stderr.lower())
                self.assertNotIn("traceback", r.stderr.lower())

    def test_malformed_steps_return_validation_errors(self):
        import plan_artifact
        base = {"schema": "plan/v1", "id": "bad", "goal": "test",
                "status": "planned"}
        cases = [
            (None, "steps must be a json array"),
            ([None], "step 1 must be a json object"),
            ([{"id": [], "role": "builder", "task": "build"}],
             "id must be a nonempty string"),
            ([{"id": "s1", "role": "builder", "task": "build",
               "depends_on": 3}], "depends_on must be a json array"),
        ]
        for steps, expected in cases:
            with self.subTest(steps=steps):
                problems = plan_artifact.validate({**base, "steps": steps})
                self.assertIn(expected, "\n".join(problems).lower())

    def test_checker_without_verification_contract_is_rejected(self):
        pid = "test-checker-no-verification"
        try:
            r = self._create([{"id": "s1", "role": "builder", "task": "build"},
                              {"id": "s2", "role": "reviewer", "task": "review",
                               "depends_on": ["s1"]}], pid)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("verification", (r.stderr + r.stdout).lower())
        finally:
            self._cleanup(pid)

    def test_checker_verification_requires_method_and_expected(self):
        for field in ("method", "expected"):
            pid = f"test-checker-empty-{field}"
            verification = {"method": "run tests", "expected": "tests pass"}
            verification[field] = "  "
            try:
                r = self._create([
                    {"id": "s1", "role": "builder", "task": "build"},
                    {"id": "s2", "role": "reviewer", "task": "review",
                     "depends_on": ["s1"], "verification": verification},
                ], pid)
                self.assertNotEqual(r.returncode, 0)
                self.assertIn(field, (r.stderr + r.stdout).lower())
            finally:
                self._cleanup(pid)

    def test_checker_must_depend_on_nonchecker_work_step(self):
        pid = "test-checker-depends-on-checker"
        verification = {"method": "run tests", "expected": "tests pass"}
        try:
            r = self._create([
                {"id": "s1", "role": "builder", "task": "build"},
                {"id": "s2", "role": "reviewer", "task": "review first",
                 "depends_on": ["s3"], "verification": verification},
                {"id": "s3", "role": "reviewer", "task": "review second",
                 "verification": verification},
            ], pid)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("work step", (r.stderr + r.stdout).lower())
        finally:
            self._cleanup(pid)


class TestCheckerBinding(unittest.TestCase):
    """Audit F4 (2026-07-17): the checker gate enforced shape, not substance —
    verification {"method": "-", "expected": "-"} on a checker depending on an
    unrelated work step passed validate(). These pin the semantic contract."""

    def _validate(self, steps):
        import plan_artifact
        return "\n".join(plan_artifact.validate(
            {"schema": "plan/v1", "id": "t", "goal": "g",
             "status": "planned", "steps": steps})).lower()

    def _checker(self, **kw):
        step = {"id": "c1", "role": "reviewer", "task": "verify",
                "depends_on": ["w1"],
                "verification": {"method": "run tests",
                                 "expected": "tests pass"}}
        step.update(kw)
        return step

    def test_placeholder_verification_strings_are_rejected(self):
        for junk in ("-", "n/a", "tbd", "TODO", "...", "none", "x", "pending",
                     # near-miss bypasses of a fixed-set gate (2026-07-17)
                     "---", "....", "??", ".", "_", "/", "xx", "tba", "n.a."):
            for field in ("method", "expected"):
                with self.subTest(junk=junk, field=field):
                    verification = {"method": "run tests",
                                    "expected": "tests pass", field: junk}
                    probs = self._validate([
                        {"id": "w1", "role": "builder", "task": "build"},
                        self._checker(verification=verification)])
                    self.assertIn("placeholder", probs)

    def test_unchecked_work_step_is_rejected(self):
        # c1 depends only on w2: w1 would ship unreviewed
        probs = self._validate([
            {"id": "w1", "role": "builder", "task": "build"},
            {"id": "w2", "role": "builder", "task": "build more"},
            self._checker(depends_on=["w2"])])
        self.assertIn("w1", probs)
        self.assertIn("not covered by any checker", probs)

    def test_checks_field_must_match_depends_on_and_known_steps(self):
        cases = [
            (["w2"], "checks step w2 but does not depend_on it"),
            (["ghost"], "checks unknown or non-work step ghost"),
            (["c1"], "checks unknown or non-work step c1"),
        ]
        for checks, expected in cases:
            with self.subTest(checks=checks):
                probs = self._validate([
                    {"id": "w1", "role": "builder", "task": "build"},
                    {"id": "w2", "role": "builder", "task": "build more"},
                    self._checker(depends_on=["w1", "w2"]),
                    self._checker(id="c2", depends_on=["w1"], checks=checks)])
                self.assertIn(expected, probs)

    def test_fully_bound_plan_is_accepted(self):
        probs = self._validate([
            {"id": "w1", "role": "builder", "task": "build"},
            {"id": "w2", "role": "builder", "task": "build more",
             "depends_on": ["w1"]},
            self._checker(depends_on=["w1", "w2"], checks=["w1", "w2"])])
        self.assertEqual(probs, "")


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
