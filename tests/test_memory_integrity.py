"""Adversarial memory-integrity and privacy regressions.

Every subprocess runs from a temporary Project OS copy so these tests never
touch the live shared brain, lock directory, blackboard, or Mneme index.
"""

import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def copy_file(root: Path, relative: str) -> Path:
    source = ROOT / relative
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target


def base_env(root: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "HOME": str(root / "home"),
            "BB_LOCK_DIR": str(root / "locks"),
            "MNEME_EMBEDDER": "lexical",
            "OSVEC_EMBEDDER": "lexical",
            "PYTHONPYCACHEPREFIX": str(root / "pycache"),
        }
    )
    return env


class MnemeIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.mneme = copy_file(self.root, "memory/mneme_adapter.py")
        self.lock = copy_file(self.root, "scripts/bb_lock.py")
        self.paths = copy_file(self.root, "scripts/brain_paths.py")
        self.brain = self.root / "brain" / "shared-brain.jsonl"
        self.brain.parent.mkdir(parents=True)
        self.index = self.root / "memory" / "mneme_index.json"
        self.env = base_env(self.root)
        self.env["PROJECT_OS_SHARED_BRAIN"] = str(self.brain)
        self.env["MNEME_INDEX"] = str(self.index)
        self.addCleanup(self.tmp.cleanup)

    def run_mneme(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(self.mneme), *args],
            capture_output=True,
            text=True,
            env=self.env,
        )

    def test_non_utf8_source_fails_closed_and_preserves_existing_index(self):
        sentinel = {
            "dim": 1,
            "embedder": "lexical-hash-v1",
            "count": 1,
            "entries": [
                {"id": "sentinel", "source": "lesson", "text": "kept", "vec": [1.0]}
            ],
        }
        self.index.write_text(json.dumps(sentinel), encoding="utf-8")
        before = self.index.read_bytes()
        self.brain.write_bytes(b'{"id":"ok","text":"valid"}\n\xff\n')

        result = self.run_mneme("build")

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("utf", result.stderr.lower())
        self.assertEqual(self.index.read_bytes(), before)

    def test_build_waits_for_index_lock_before_gathering(self):
        self.brain.write_text(
            json.dumps({"id": "one", "text": "first lesson"}) + "\n",
            encoding="utf-8",
        )
        build_lock = str(self.index) + ".build.lock"
        holder = subprocess.Popen(
            [
                sys.executable,
                "-c",
                (
                    "import fcntl,sys; "
                    "f=open(sys.argv[1],'a+'); "
                    "fcntl.flock(f.fileno(),fcntl.LOCK_EX); "
                    "print('READY',flush=True); sys.stdin.readline()"
                ),
                build_lock,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=self.env,
        )
        self.assertEqual(holder.stdout.readline().strip(), "READY")
        proc = subprocess.Popen(
            [sys.executable, str(self.mneme), "build"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=self.env,
        )
        try:
            time.sleep(0.25)
            self.assertIsNone(proc.poll(), "build bypassed the held index lock")
            with self.brain.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"id": "two", "text": "second lesson"}) + "\n")
            holder.stdin.write("release\n")
            holder.stdin.flush()
            holder.communicate(timeout=5)
            stdout, stderr = proc.communicate(timeout=15)
            self.assertEqual(proc.returncode, 0, stderr or stdout)
            built = json.loads(self.index.read_text(encoding="utf-8"))
            self.assertEqual(built["count"], 2)
            self.assertEqual({entry["id"] for entry in built["entries"]}, {"one", "two"})
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=5)
            proc.communicate()
            if holder.poll() is None:
                holder.stdin.write("release\n")
                holder.stdin.flush()
                holder.communicate(timeout=5)

    def test_invalid_index_shape_is_a_clean_error(self):
        self.index.write_text("[1]\n", encoding="utf-8")
        self.brain.write_text(
            json.dumps({"id": "one", "text": "first lesson"}) + "\n",
            encoding="utf-8",
        )

        for command in (("stats",), ("query", "first")):
            with self.subTest(command=command):
                result = self.run_mneme(*command)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("invalid", result.stderr.lower())
                self.assertNotIn("Traceback", result.stderr)

    def test_installed_project_defaults_to_its_local_brain(self):
        self.env.pop("PROJECT_OS_SHARED_BRAIN")
        self.brain.write_text("", encoding="utf-8")
        append = copy_file(self.root, "scripts/brain_append.py")
        copy_file(self.root, "scripts/secret_patterns.py")
        record = json.dumps({"id": "local-one", "text": "local project lesson"})

        result = subprocess.run(
            [sys.executable, str(append), "--line", record, "--no-reindex"],
            capture_output=True,
            text=True,
            env=self.env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        local_rows = [json.loads(line) for line in self.brain.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([row["id"] for row in local_rows], ["local-one"])
        self.assertFalse(
            (self.root / "home" / ".project-os" / "central-brain" / "shared-brain.jsonl").exists()
        )

        built = self.run_mneme("build")
        self.assertEqual(built.returncode, 0, built.stderr)
        self.assertEqual(json.loads(self.index.read_text(encoding="utf-8"))["count"], 1)


    def test_blackboard_only_build_indexes_meaningful_h2_with_stable_provenance(self):
        self.brain.write_text("", encoding="utf-8")
        blackboard = self.root / "blackboard"
        blackboard.mkdir()
        decisions = blackboard / "03-decisions.md"
        decisions.write_text(
            "# Decisions\n\n"
            "## Use Local Brain\n\n"
            "Every project keeps durable lessons in its own local brain.\n\n"
            "## Placeholder\n\n"
            "- TBD\n",
            encoding="utf-8",
        )

        first = self.run_mneme("build")
        self.assertEqual(first.returncode, 0, first.stderr)
        first_index = json.loads(self.index.read_text(encoding="utf-8"))
        entries = [
            entry for entry in first_index["entries"] if entry["source"] == "blackboard"
        ]
        self.assertEqual(len(entries), 1)
        self.assertEqual(
            entries[0]["id"],
            "blackboard/03-decisions.md#use-local-brain",
        )
        self.assertIn("Every project keeps", entries[0]["text"])
        self.assertNotIn("TBD", entries[0]["text"])

        decisions.write_text(
            "# Decisions\n\n"
            "## Use Local Brain\n\n"
            "Updated detail: every project keeps its own durable lessons.\n",
            encoding="utf-8",
        )
        second = self.run_mneme("build")
        self.assertEqual(second.returncode, 0, second.stderr)
        second_index = json.loads(self.index.read_text(encoding="utf-8"))
        second_ids = {
            entry["id"]
            for entry in second_index["entries"]
            if entry["source"] == "blackboard"
        }
        self.assertEqual(
            second_ids,
            {"blackboard/03-decisions.md#use-local-brain"},
        )

    def test_zero_vector_build_warns_instead_of_looking_complete(self):
        self.brain.write_text("", encoding="utf-8")
        blackboard = self.root / "blackboard"
        blackboard.mkdir()
        (blackboard / "00-project-goal.md").write_text(
            "# Goal\n\n## Canonical Goal\n\n- TBD\n",
            encoding="utf-8",
        )

        result = self.run_mneme("build")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("WARNING", result.stderr)
        self.assertIn("zero vectors", result.stderr)
        self.assertEqual(
            json.loads(self.index.read_text(encoding="utf-8"))["count"],
            0,
        )


class BrainAppendPrivacyTests(unittest.TestCase):
    def test_secret_patterns_are_refused_before_the_brain_is_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            append = copy_file(root, "scripts/brain_append.py")
            copy_file(root, "scripts/bb_lock.py")
            copy_file(root, "scripts/secret_patterns.py")
            copy_file(root, "scripts/brain_paths.py")
            brain = root / "brain.jsonl"
            env = base_env(root)
            env["PROJECT_OS_SHARED_BRAIN"] = str(brain)
            samples = (
                {"id": "a", "text": "sk-ant-" + "A" * 24},
                {"id": "b", "text": "sk-proj-" + "B" * 24},
                {"id": "c", "text": "sk_live_" + "C" * 24},
                {"id": "d", "text": "xoxb-" + "1" * 24},
                {"id": "e", "text": "glpat-" + "D" * 24},
                {"id": "f", "api_key": "ordinary-looking-sensitive-value"},
            )
            for sample in samples:
                with self.subTest(sample=sample["id"]):
                    result = subprocess.run(
                        [
                            sys.executable,
                            str(append),
                            "--line",
                            json.dumps(sample),
                            "--no-reindex",
                        ],
                        capture_output=True,
                        text=True,
                        env=env,
                    )
                    self.assertEqual(result.returncode, 2)
                    self.assertIn("secret", result.stderr.lower())
                    self.assertNotIn("Traceback", result.stderr)
                    self.assertFalse(brain.exists())


class HarvestIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.harvest = copy_file(self.root, "scripts/harvest.py")
        copy_file(self.root, "scripts/brain_append.py")
        copy_file(self.root, "scripts/bb_lock.py")
        copy_file(self.root, "scripts/secret_patterns.py")
        copy_file(self.root, "scripts/brain_paths.py")
        self.mneme = self.root / "memory" / "mneme_adapter.py"
        self.mneme.parent.mkdir(parents=True)
        self.mneme.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
        self.runs = self.root / "runs"
        self.runs.mkdir()
        (self.root / "blackboard" / "packets").mkdir(parents=True)
        self.brain = self.root / "brain.jsonl"
        self.env = base_env(self.root)
        self.env["PROJECT_OS_SHARED_BRAIN"] = str(self.brain)
        self.addCleanup(self.tmp.cleanup)

    def run_harvest(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(self.harvest), *args],
            capture_output=True,
            text=True,
            env=self.env,
        )

    def _empty_harvest_run(self, slug: str) -> Path:
        run = self.runs / slug
        run.mkdir()
        (run / "19-memory-harvest.md").write_text(
            "## Lessons\n\n", encoding="utf-8"
        )
        return run

    def _install_unsafe_marker(self, run: Path, kind: str):
        marker = run / ".harvested"
        if kind == "directory":
            marker.mkdir()
            return None, None
        victim = self.root / f"{run.name}-{kind}-victim.txt"
        victim.write_bytes(b"outside sentinel\n")
        before = victim.read_bytes()
        if kind == "symlink":
            marker.symlink_to(victim)
        elif kind == "hardlink":
            os.link(victim, marker)
        else:  # pragma: no cover - test helper contract
            raise AssertionError(kind)
        return victim, before

    def test_scan_refuses_unsafe_marker_leaves_without_overwriting_victims(self):
        for kind in ("symlink", "hardlink", "directory"):
            with self.subTest(kind=kind):
                run = self._empty_harvest_run(f"scan-marker-{kind}")
                victim, before = self._install_unsafe_marker(run, kind)

                result = self.run_harvest("scan", run.name)

                self.assertEqual(result.returncode, 2, result.stderr or result.stdout)
                self.assertIn("marker", result.stderr.lower())
                self.assertNotIn("Traceback", result.stderr)
                if victim is not None:
                    self.assertEqual(victim.read_bytes(), before)

    def test_apply_refuses_unsafe_marker_leaves_before_brain_mutation(self):
        for kind in ("symlink", "hardlink", "directory"):
            with self.subTest(kind=kind):
                run = self._empty_harvest_run(f"apply-marker-{kind}")
                victim, before = self._install_unsafe_marker(run, kind)
                proposal = self.root / f"apply-marker-{kind}.jsonl"
                proposal.write_text(
                    json.dumps(
                        {
                            "id": f"apply-marker-{kind}",
                            "project_id": run.name,
                            "text": "safe lesson text",
                            "type": "lesson",
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
                brain = self.root / f"brain-{kind}.jsonl"
                self.env["PROJECT_OS_SHARED_BRAIN"] = str(brain)

                result = self.run_harvest("apply", str(proposal))

                self.assertEqual(result.returncode, 2, result.stderr or result.stdout)
                self.assertIn("marker", result.stderr.lower())
                self.assertNotIn("Traceback", result.stderr)
                self.assertFalse(brain.exists())
                if victim is not None:
                    self.assertEqual(victim.read_bytes(), before)

    def test_unreadable_harvest_source_fails_without_marker(self):
        run = self.runs / "unreadable"
        (run / "19-memory-harvest.md").mkdir(parents=True)

        result = self.run_harvest("scan", "unreadable")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("19-memory-harvest.md", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse((run / ".harvested").exists())

    def test_short_lesson_stages_but_rejected_private_bullets_do_not(self):
        run = self.runs / "bullets"
        run.mkdir()
        (run / "19-memory-harvest.md").write_text(
            "## Lessons\n\n"
            "- Back up first\n"
            "- Rejected: do not promote this\n"
            "- Private-only: never leave this run\n",
            encoding="utf-8",
        )

        result = self.run_harvest("scan", "bullets")

        self.assertEqual(result.returncode, 0, result.stderr)
        proposals = list((self.root / "blackboard" / "packets").glob("harvest-bullets-*.jsonl"))
        self.assertEqual(len(proposals), 1)
        rows = [json.loads(line) for line in proposals[0].read_text(encoding="utf-8").splitlines()]
        self.assertEqual([row["text"] for row in rows], ["Back up first"])
        self.assertFalse((run / ".harvested").exists())

    def test_secret_lesson_is_refused_before_a_proposal_is_written(self):
        run = self.runs / "secret-run"
        run.mkdir()
        (run / "19-memory-harvest.md").write_text(
            "## Lessons\n\n- Persist password=" + "FAKEFAKEFAKE in durable memory\n",
            encoding="utf-8",
        )

        result = self.run_harvest("scan", "secret-run")

        self.assertEqual(result.returncode, 2)
        self.assertIn("secret", result.stderr.lower())
        self.assertEqual(list((self.root / "blackboard" / "packets").glob("harvest-secret-run-*")), [])
        self.assertFalse((run / ".harvested").exists())

    def test_scan_refuses_symlinked_packets_directory(self):
        run = self.runs / "linked-packets"
        run.mkdir()
        (run / "19-memory-harvest.md").write_text(
            "## Lessons\n\n- Keep proposal writes inside the project\n",
            encoding="utf-8",
        )
        packets = self.root / "blackboard" / "packets"
        packets.rmdir()
        outside = self.root / "outside-packets"
        outside.mkdir()
        packets.symlink_to(outside, target_is_directory=True)

        result = self.run_harvest("scan", "linked-packets")

        self.assertEqual(result.returncode, 2)
        self.assertIn("symlink", result.stderr.lower())
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertFalse((run / ".harvested").exists())

    def test_scan_refuses_predictable_proposal_symlink_without_changing_victim(self):
        run = self.runs / "linked-proposal"
        run.mkdir()
        (run / "19-memory-harvest.md").write_text(
            "## Lessons\n\n- Publish proposals without following output links\n",
            encoding="utf-8",
        )
        victim = self.root / "outside-victim.txt"
        victim.write_bytes(b"outside sentinel\n")
        before = victim.read_bytes()
        proposal = (
            self.root
            / "blackboard"
            / "packets"
            / f"harvest-linked-proposal-{datetime.date.today().isoformat()}.jsonl"
        )
        proposal.symlink_to(victim)

        result = self.run_harvest("scan", "linked-proposal")

        self.assertEqual(result.returncode, 2)
        self.assertIn("exists", result.stderr.lower())
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(victim.read_bytes(), before)
        self.assertTrue(proposal.is_symlink())
        self.assertFalse((run / ".harvested").exists())

    def test_apply_rejects_marker_escape_before_append(self):
        outside = self.root / "outside"
        outside.mkdir()
        proposal = self.root / "escape.jsonl"
        proposal.write_text(
            json.dumps(
                {
                    "id": "escape-one",
                    "project_id": str(outside),
                    "text": "safe lesson text",
                    "type": "lesson",
                }
            )
            + "\n",
            encoding="utf-8",
        )

        result = self.run_harvest("apply", str(proposal))

        self.assertEqual(result.returncode, 2)
        self.assertIn("project_id", result.stderr)
        self.assertFalse(self.brain.exists())
        self.assertFalse((outside / ".harvested").exists())

    def test_reindex_failure_is_clean_and_retry_is_idempotent(self):
        run = self.runs / "retry-run"
        run.mkdir()
        proposal = self.root / "retry.jsonl"
        proposal.write_text(
            json.dumps(
                {
                    "id": "retry-one",
                    "project_id": "retry-run",
                    "text": "lesson that must not duplicate on retry",
                    "type": "lesson",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        self.mneme.write_text("import sys\nsys.exit(7)\n", encoding="utf-8")

        first = self.run_harvest("apply", str(proposal))
        second = self.run_harvest("apply", str(proposal))

        for result in (first, second):
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("reindex", result.stderr.lower())
            self.assertNotIn("Traceback", result.stderr)
        rows = [json.loads(line) for line in self.brain.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([row["id"] for row in rows], ["retry-one"])
        self.assertFalse((run / ".harvested").exists())

        self.mneme.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
        third = self.run_harvest("apply", str(proposal))
        self.assertEqual(third.returncode, 0, third.stderr)
        rows = [json.loads(line) for line in self.brain.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([row["id"] for row in rows], ["retry-one"])
        self.assertTrue((run / ".harvested").is_file())


class BrainScaleShapeTests(unittest.TestCase):
    def test_valid_json_wrong_shape_does_not_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scale = copy_file(root, "scripts/brain_scale.py")
            copy_file(root, "scripts/brain_paths.py")
            (root / "memory").mkdir(exist_ok=True)
            (root / "memory" / "mneme_index.json").write_text("[1]\n", encoding="utf-8")
            brain = root / "brain.jsonl"
            brain.write_text("{}\n", encoding="utf-8")
            env = base_env(root)
            env["PROJECT_OS_SHARED_BRAIN"] = str(brain)

            result = subprocess.run(
                [sys.executable, str(scale), "--json"],
                capture_output=True,
                text=True,
                env=env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["context"]["embedder"], "none")


if __name__ == "__main__":
    unittest.main()
