"""Regression coverage for the final adversarial tool-review findings.

All filesystem and Git mutations stay inside temporary directories.  The tests
exercise the shipped scripts as CLIs where possible so clean diagnostics and
preflight ordering are part of the contract.
"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
GOAL_GUARD = ROOT / "addons" / "full-engine" / "memory" / "goal_guard.py"
sys.path.insert(0, str(SCRIPTS))

import promptsmith  # noqa: E402
import wt  # noqa: E402


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(cwd, *args):
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} failed: {result.stderr or result.stdout}")
    return result.stdout.strip()


class TestGoalGuardSectionParsing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.goal_guard = load_module(GOAL_GUARD, "goal_guard_remaining_tools")

    def test_multiline_html_comment_is_skipped_before_goal(self):
        text = (
            "# Goal\n\n"
            "## Canonical Goal (one sentence)\n\n"
            "<!--\n"
            "This explanatory comment spans multiple lines.\n"
            "It is not the canonical goal.\n"
            "-->\n"
            "Ship the verified local workflow.\n"
        )
        self.assertEqual(
            self.goal_guard.canonical_goal(text),
            "Ship the verified local workflow.")

    def test_empty_goal_section_does_not_fall_through_to_next_section(self):
        text = (
            "## Canonical Goal (one sentence)\n\n"
            "<!-- still intentionally blank -->\n\n"
            "## Definition of Done\n\n"
            "This sentence belongs to a different section.\n"
        )
        with self.assertRaises(self.goal_guard.GoalAnchorMissing):
            self.goal_guard.canonical_goal(text)

    def test_prefix_lookalike_heading_is_not_the_canonical_section(self):
        text = (
            "## Canonical Goal Notes\n\n"
            "Do not hash this note.\n\n"
            "## Canonical Goal (one sentence)\n\n"
            "Hash this exact goal.\n"
        )
        self.assertEqual(
            self.goal_guard.canonical_goal(text), "Hash this exact goal.")


class ScriptCopyMixin:
    script_names = ()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "project"
        self.scripts = self.root / "scripts"
        self.scripts.mkdir(parents=True)
        for name in self.script_names:
            shutil.copy2(SCRIPTS / name, self.scripts / name)
        self.env = dict(os.environ)
        self.env["BB_LOCK_DIR"] = str(Path(self.tmp.name) / "locks")
        self.addCleanup(self.tmp.cleanup)

    def run_script(self, name, *args):
        return subprocess.run(
            [sys.executable, str(self.scripts / name), *args],
            capture_output=True, text=True, env=self.env, cwd=self.root)


class TestEvolutionPathAndDataGuards(ScriptCopyMixin, unittest.TestCase):
    script_names = ("evolution.py", "bb_lock.py")

    def record(self, run):
        return self.run_script(
            "evolution.py", "record", "--run", run,
            "--variant", "v1", "--change", "initial", "--score", "0.5",
            "--verdict", "revise")

    def assert_clean_refusal(self, result):
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertNotIn("traceback", result.stderr.lower())
        self.assertEqual(len(result.stderr.strip().splitlines()), 1, result.stderr)

    def test_absolute_and_parent_run_paths_are_rejected_before_disk_writes(self):
        outside = Path(self.tmp.name) / "absolute-escape"
        result = self.record(str(outside))
        self.assert_clean_refusal(result)
        self.assertFalse(outside.exists())

        traversal = self.record("../parent-escape")
        self.assert_clean_refusal(traversal)
        self.assertFalse((self.root / "parent-escape").exists())

    def test_symlinked_run_directory_cannot_escape_runs_root(self):
        runs = self.root / "runs"
        outside = Path(self.tmp.name) / "outside-run"
        runs.mkdir()
        outside.mkdir()
        (runs / "linked-run").symlink_to(outside, target_is_directory=True)

        result = self.record("linked-run")
        self.assert_clean_refusal(result)
        self.assertEqual(list(outside.iterdir()), [])

    def test_safe_existing_run_name_still_round_trips(self):
        result = self.record("client-alpha_01.release")
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(
            (self.root / "runs" / "client-alpha_01.release" /
             "evolution.json").read_text(encoding="utf-8"))
        self.assertEqual(record["records"][0]["variant"], "v1")

    def test_a_crash_mid_record_leaves_the_prior_history_intact(self):
        """A killed writer must not destroy evolution.json.

        `record` used to truncate the real file in place (open(jf, "w")), so a
        crash after truncation but before the dump completed left a 13-byte
        stub and the tool could no longer read its own history — the run's
        entire evolution record, unrecoverable. Every sibling writer in this
        repo publishes durable state through a temp file + os.replace; this
        pins that behavior here too.
        """
        run = "crash-run"
        self.assertEqual(self.record(run).returncode, 0)
        for variant in ("v2", "v3"):
            result = self.run_script(
                "evolution.py", "record", "--run", run, "--variant", variant,
                "--change", "second pass", "--score", "0.7",
                "--verdict", "approve")
            self.assertEqual(result.returncode, 0, result.stderr)

        evolution_json = self.root / "runs" / run / "evolution.json"
        before = evolution_json.read_bytes()
        self.assertEqual(len(json.loads(before)["records"]), 3)

        # Kill the writer mid-publish. Both the in-place write (json.dump into
        # the truncated real file) and the atomic write (os.replace of the temp
        # over the target) are hooked, so the crash lands at the same logical
        # moment whichever way the publish is implemented.
        script = str(self.scripts / "evolution.py")
        crasher = (
            "import json, os, sys\n"
            "sys.argv = ['evolution.py', 'record', '--run', %r, '--variant',\n"
            "            'v4', '--change', 'boom', '--score', '0.9',\n"
            "            '--verdict', 'approve']\n"
            "def die(*a, **k):\n"
            "    os._exit(137)\n"
            "def exploding_dump(obj, fp, **kw):\n"
            "    fp.write('{\"records\": [')\n"   # in-place path: truncated stub
            "    os._exit(137)\n"
            "json.dump = exploding_dump\n"
            "os.replace = die\n"                  # atomic path: never published
            "src = open(%r, encoding='utf-8').read()\n"
            "exec(compile(src, %r, 'exec'),\n"
            "     {'__name__': '__main__', '__file__': %r})\n"
        ) % (run, script, script, script)
        killed = subprocess.run([sys.executable, "-c", crasher],
                                capture_output=True, text=True,
                                env=self.env, cwd=self.root)
        self.assertNotEqual(killed.returncode, 0,
                            "the injected crash did not fire")

        after = evolution_json.read_bytes()
        self.assertEqual(
            after, before,
            "a crash mid-record rewrote or truncated the durable history")
        self.assertEqual(len(json.loads(after)["records"]), 3)
        # The tool can still read what it wrote before the crash.
        best = self.run_script("evolution.py", "best", "--run", run)
        self.assertEqual(best.returncode, 0, best.stderr)
        # A killed process cannot run its own cleanup, so a stray temp may
        # survive; what matters is that it never shadows the real file and that
        # the next healthy write still succeeds and tidies up after itself.
        run_dir = self.root / "runs" / run
        self.assertTrue(all(p.name.startswith(".")
                            for p in run_dir.iterdir()
                            if p.name not in ("evolution.json", "evolution.md")),
                        "crash left a non-hidden stray file beside the history")
        recovered = subprocess.run(
            [sys.executable, str(self.scripts / "evolution.py"), "record",
             "--run", run, "--variant", "v5", "--change", "after the crash",
             "--score", "0.95", "--verdict", "approve"],
            capture_output=True, text=True, cwd=self.root,
            # The killed writer could not release its lease. bb_lock clears a
            # dead owner's lock by TIME only (BB_LOCK_STALE, 60s by default) —
            # it records the pid but deliberately never reaps on liveness, so
            # that a wedged lock exits through stale reaping rather than a
            # force override. Collapse that window instead of waiting it out.
            env=dict(self.env, BB_LOCK_STALE="0"))
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertEqual(
            len(json.loads(evolution_json.read_text(encoding="utf-8"))["records"]),
            4, "the post-crash record did not append to the recovered history")

    def test_malformed_stored_json_is_a_clean_error_for_read_and_record(self):
        run_dir = self.root / "runs" / "broken"
        run_dir.mkdir(parents=True)
        evolution_json = run_dir / "evolution.json"
        original = b'{"schema":"evolution/v1","records":['
        evolution_json.write_bytes(original)

        for command in (
                ("best", "--run", "broken"),
                ("report", "--run", "broken"),
                ("next", "--run", "broken"),
                ("record", "--run", "broken", "--variant", "v2",
                 "--change", "retry", "--score", "0.6",
                 "--verdict", "revise")):
            with self.subTest(command=command[0]):
                result = self.run_script("evolution.py", *command)
                self.assert_clean_refusal(result)
                self.assertIn("evolution.json", result.stderr)
                self.assertEqual(evolution_json.read_bytes(), original)
                self.assertFalse((run_dir / "evolution.md").exists())


class TestPromptsmithPreflight(ScriptCopyMixin, unittest.TestCase):
    script_names = ("promptsmith.py", "bb_lock.py")

    def setUp(self):
        super().setUp()
        self.brief = Path(self.tmp.name) / "brief.md"
        self.brief.write_text("## DON'T\n- no neon\n", encoding="utf-8")

    def run_promptsmith(self, *extra, no_index=True):
        args = [
            "--task", "build a restrained panel",
            "--brief-file", str(self.brief),
            "--packet-id", "safe-id",
        ]
        if no_index:
            args.append("--no-index")
        args.extend(extra)
        return self.run_script("promptsmith.py", *args)

    def assert_clean_refusal(self, result):
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertNotIn("traceback", result.stderr.lower())
        self.assertEqual(len(result.stderr.strip().splitlines()), 1, result.stderr)

    def test_missing_brief_is_preflighted_before_output_directory_creation(self):
        missing = Path(self.tmp.name) / "missing-brief.md"
        out_dir = Path(self.tmp.name) / "packets"
        result = self.run_script(
            "promptsmith.py", "--task", "x", "--brief-file", str(missing),
            "--packet-id", "safe-id", "--out-dir", str(out_dir), "--no-index")
        self.assert_clean_refusal(result)
        self.assertIn("brief", result.stderr.lower())
        self.assertFalse(out_dir.exists())

    def test_symlinked_output_directory_is_refused(self):
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        out_link = Path(self.tmp.name) / "packets-link"
        out_link.symlink_to(outside, target_is_directory=True)

        result = self.run_promptsmith("--out-dir", str(out_link))
        self.assert_clean_refusal(result)
        self.assertEqual(list(outside.iterdir()), [])

    def test_output_file_symlink_is_refused_without_touching_target(self):
        out_dir = Path(self.tmp.name) / "packets"
        out_dir.mkdir()
        victim = Path(self.tmp.name) / "victim.md"
        victim.write_text("keep me\n", encoding="utf-8")
        (out_dir / "safe-id-worker-prompt.md").symlink_to(victim)

        result = self.run_promptsmith("--out-dir", str(out_dir))
        self.assert_clean_refusal(result)
        self.assertEqual(victim.read_text(encoding="utf-8"), "keep me\n")
        self.assertFalse((out_dir / "safe-id-rubric.md").exists())

    def test_existing_packet_is_not_silently_overwritten(self):
        out_dir = Path(self.tmp.name) / "packets"
        out_dir.mkdir()
        worker = out_dir / "safe-id-worker-prompt.md"
        worker.write_text("existing candidate\n", encoding="utf-8")

        result = self.run_promptsmith("--out-dir", str(out_dir))
        self.assert_clean_refusal(result)
        self.assertEqual(worker.read_text(encoding="utf-8"), "existing candidate\n")
        self.assertFalse((out_dir / "safe-id-rubric.md").exists())

    def test_symlinked_packet_index_is_refused_before_packet_writes(self):
        blackboard = self.root / "blackboard"
        blackboard.mkdir()
        victim = Path(self.tmp.name) / "outside-index.md"
        victim.write_text("outside index\n", encoding="utf-8")
        (blackboard / "05-agent-packets.md").symlink_to(victim)
        out_dir = blackboard / "packets"

        result = self.run_promptsmith(
            "--out-dir", str(out_dir), no_index=False)
        self.assert_clean_refusal(result)
        self.assertEqual(victim.read_text(encoding="utf-8"), "outside index\n")
        self.assertFalse(out_dir.exists())

    def test_pair_publication_rolls_back_first_file_when_second_publish_fails(self):
        self.assertTrue(
            hasattr(promptsmith, "_publish_pair"),
            "promptsmith needs one transactional pair-publication primitive")
        with tempfile.TemporaryDirectory() as td:
            worker = Path(td) / "worker.md"
            rubric = Path(td) / "rubric.md"
            real_link = os.link
            calls = []

            def fail_second(source, target, *args, **kwargs):
                calls.append((source, target))
                if len(calls) == 2:
                    raise OSError("injected second-publish failure")
                return real_link(source, target, *args, **kwargs)

            with mock.patch.object(promptsmith.os, "link", side_effect=fail_second):
                with self.assertRaisesRegex(OSError, "second-publish"):
                    promptsmith._publish_pair(
                        str(worker), "worker body\n", str(rubric), "rubric body\n")
            self.assertFalse(worker.exists())
            self.assertFalse(rubric.exists())
            self.assertEqual(list(Path(td).iterdir()), [])


class TestWorktreeNamespaceSymlink(unittest.TestCase):
    def test_escape_like_name_is_rejected_before_any_directory_creation(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            git(repo, "init", "-q", "-b", "main")
            git(repo, "config", "user.email", "test@example.invalid")
            git(repo, "config", "user.name", "Test")
            (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
            git(repo, "add", "tracked.txt")
            git(repo, "commit", "-qm", "base")

            old_home = wt.HOME
            wt.HOME = str(Path(td) / "home")
            self.addCleanup(setattr, wt, "HOME", old_home)
            outside = Path(td) / "home" / ".project-os" / "escape"

            with self.assertRaisesRegex(SystemExit, "direct component"):
                wt.cmd_create(str(repo), "../../escape/child")
            self.assertFalse(outside.exists())
            self.assertNotIn("refs/heads/wt/", git(repo, "show-ref"))

    def test_namespace_symlink_is_rejected_before_git_worktree_add(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            git(repo, "init", "-q", "-b", "main")
            git(repo, "config", "user.email", "test@example.invalid")
            git(repo, "config", "user.name", "Test")
            (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
            git(repo, "add", "tracked.txt")
            git(repo, "commit", "-qm", "base")

            old_home = wt.HOME
            wt.HOME = str(Path(td) / "home")
            self.addCleanup(setattr, wt, "HOME", old_home)
            outside = Path(td) / "outside"
            outside.mkdir()
            namespace = Path(wt.wt_dir(str(repo), "probe")).parent
            namespace.parent.mkdir(parents=True)
            namespace.symlink_to(outside, target_is_directory=True)

            with self.assertRaises(SystemExit) as caught:
                wt.cmd_create(str(repo), "probe")
            self.assertIn("symlink", str(caught.exception).lower())
            self.assertEqual(list(outside.iterdir()), [])
            self.assertNotIn("refs/heads/wt/probe", git(repo, "show-ref"))


class TestReadmeLockClaim(unittest.TestCase):
    def test_lock_claim_describes_cooperative_fencing_not_suspension_immunity(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("locks are token-fenced and survive process suspension", readme)
        self.assertIn("cooperative", readme.lower())
        self.assertIn("stale holder", readme.lower())


if __name__ == "__main__":
    unittest.main()
