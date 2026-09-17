"""Installed save -> exact mirror -> later-run recall uses one selected store."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@unittest.skipIf(sys.version_info < (3, 10), "full-engine installer requires Python 3.10+")
class InstalledSelectedStoreMirrorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.scratch.cleanup)
        cls.base = Path(cls.scratch.name).resolve()
        cls.template = cls.base / "template"
        cls.template.mkdir()
        cls.env = {key: os.environ[key] for key in ("PATH", "TMPDIR", "LANG", "LC_ALL") if key in os.environ}
        cls.env.update(HOME=str(cls.base / "home"), PYTHONDONTWRITEBYTECODE="1",
                       PYTHONNOUSERSITE="1", BB_LOCK_DIR=str(cls.base / "locks"),
                       PROJECT_OS_LEGACY_CENTRAL_BRAIN=str(cls.base / "empty-legacy"),
                       MNEME_EMBEDDER="lexical", OSVEC_EMBEDDER="lexical",
                       GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        Path(cls.env["HOME"]).mkdir()
        proc = subprocess.run(["sh", str(ROOT / "install.sh"), str(cls.template), "--full-engine"],
                              cwd=ROOT, env=cls.env, capture_output=True, text=True, timeout=60)
        if proc.returncode:
            raise AssertionError(proc.stdout + proc.stderr)

    def fixture(self, mode):
        case = self.base / self._testMethodName / mode
        project = case / "project"
        shutil.copytree(self.template, project)
        local = project / "brain/shared-brain.jsonl"
        local.write_text("")
        selected = case / "external/shared-brain.jsonl"
        selected.parent.mkdir()
        selected.write_text("")
        selected.chmod(0o600)
        env = dict(self.env)
        if mode == "local":
            selected = local
        elif mode == "binding":
            self.bind(project, selected)
        else:
            env["PROJECT_OS_SHARED_BRAIN"] = str(selected)
        return project, local, selected, env

    def bind(self, project, selected):
        (project / "brain/shared-brain-binding.jsonl").write_text(json.dumps({
            "schema": "project-os/shared-brain-binding/v1", "target": str(selected)}) + "\n")
        with (project / ".gitignore").open("a") as handle:
            handle.write("\nbrain/shared-brain-binding.jsonl\n")

    def call(self, project, env, script, *args, cwd=None):
        return subprocess.run([sys.executable, str(project / script), *map(str, args)],
                              cwd=cwd or project, env=env, capture_output=True, text=True, timeout=30)

    def okay(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def refused(self, result):
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("ERROR:", result.stdout)
        self.assertNotIn("Traceback", result.stderr)

    def save(self, project, env, rid="chosen-lesson", text="selectedstoree2elesson: Reject boolean quantities."):
        self.okay(self.call(project, env, "brain/brain.py", "save-chat",
                            "--summary", text, "--kind", "lesson", "--id", rid))

    def test_selected_store_save_rebuild_and_recall_from_second_run(self):
        for mode in ("local", "environment", "binding"):
            with self.subTest(mode=mode):
                project, local, selected, env = self.fixture(mode)
                before = digest(local)
                self.okay(self.call(project, env, "memory/new_run.py", "origin", "--tier", "solo"))
                self.save(project, env)
                self.assertIn("chosen-lesson", selected.read_text())
                rebuilt = self.call(project, env, "memory/brain_fts_mirror.py", "rebuild")
                self.okay(rebuilt)
                self.assertIn("1 records", rebuilt.stdout)
                self.okay(self.call(project, env, "memory/brain_fts_mirror.py", "check"))
                self.okay(self.call(project, env, "memory/new_run.py", "reuse", "--tier", "solo"))
                result = self.call(project, env, "memory/brain_fts_mirror.py", "query",
                                   "selectedstoree2elesson", cwd=project / "runs/reuse")
                self.okay(result)
                self.assertIn("chosen-lesson", result.stdout)
                self.assertIn("Reject boolean quantities", result.stdout)
                if mode != "local":
                    self.assertEqual(digest(local), before)

    def test_invalid_configurations_refuse_instead_of_using_local_store(self):
        for mode in ("relative", "symlink", "hardlink", "malformed-binding", "unignored-binding"):
            with self.subTest(mode=mode):
                project, local, selected, env = self.fixture(mode)
                local.write_text(json.dumps({"id": "local-sentinel", "text": "localmustnotleak"}) + "\n")
                if mode == "relative":
                    env["PROJECT_OS_SHARED_BRAIN"] = "relative.jsonl"
                elif mode in ("symlink", "hardlink"):
                    alias = selected.parent / "alias.jsonl"
                    if mode == "symlink":
                        alias.symlink_to(selected)
                    else:
                        os.link(selected, alias)
                    env["PROJECT_OS_SHARED_BRAIN"] = str(alias)
                else:
                    env.pop("PROJECT_OS_SHARED_BRAIN")
                    binding = project / "brain/shared-brain-binding.jsonl"
                    if mode == "malformed-binding":
                        self.bind(project, selected)
                        binding.write_text('{"schema":"wrong"}\n')
                    else:
                        binding.write_text(json.dumps({"schema": "project-os/shared-brain-binding/v1", "target": str(selected)}) + "\n")
                        # The installer may already ignore this path in future versions.
                        ignore = project / ".gitignore"
                        lines = [v for v in ignore.read_text().splitlines()
                                 if v.strip() not in ("brain/shared-brain-binding.jsonl", "/brain/shared-brain-binding.jsonl")]
                        ignore.write_text("\n".join(lines) + "\n")
                original = digest(local), digest(selected)
                for arguments in (("rebuild",), ("check",), ("query", "localmustnotleak")):
                    result = self.call(project, env, "memory/brain_fts_mirror.py", *arguments)
                    self.refused(result)
                    self.assertNotIn("localmustnotleak", result.stdout + result.stderr)
                    self.assertEqual((digest(local), digest(selected)), original)
                self.assertFalse((project / "memory/brain-fts-mirror.db").exists())

    def test_save_makes_selected_mirror_stale_until_rebuild(self):
        project, local, selected, env = self.fixture("environment")
        self.okay(self.call(project, env, "memory/brain_fts_mirror.py", "rebuild"))
        self.save(project, env)
        stale = self.call(project, env, "memory/brain_fts_mirror.py", "query", "selectedstoree2elesson")
        self.refused(stale)
        self.assertIn("stale", stale.stdout)
        self.okay(self.call(project, env, "memory/brain_fts_mirror.py", "rebuild"))
        result = self.call(project, env, "memory/brain_fts_mirror.py", "query", "selectedstoree2elesson")
        self.okay(result)
        self.assertIn("chosen-lesson", result.stdout)
        absent = self.call(project, env, "memory/brain_fts_mirror.py", "query", "neversavede2elesson")
        self.okay(absent)
        self.assertIn("no matches", absent.stdout)

    def test_explicit_override_retains_caller_owned_source(self):
        project, local, selected, env = self.fixture("environment")
        self.save(project, env)
        env["PROJECT_OS_SHARED_BRAIN"] = "invalid-relative-default"
        self.okay(self.call(project, env, "memory/brain_fts_mirror.py", "--brain", selected, "rebuild"))
        result = self.call(project, env, "memory/brain_fts_mirror.py", "--brain", selected,
                           "query", "selectedstoree2elesson")
        self.okay(result)
        self.assertIn("chosen-lesson", result.stdout)

    def test_resolver_error_does_not_fall_back_to_local_memory(self):
        project, local, selected, env = self.fixture("environment")
        local.write_text(json.dumps({"id": "local-sentinel", "text": "localmustnotleak"}) + "\n")
        (project / "scripts/brain_paths.py").write_text("raise RuntimeError('synthetic resolver failure')\n")
        result = self.call(project, env, "memory/brain_fts_mirror.py", "rebuild")
        self.refused(result)
        self.assertIn("synthetic resolver failure", result.stdout)
        self.assertFalse((project / "memory/brain-fts-mirror.db").exists())

    def test_selection_is_resolved_again_on_each_operation(self):
        project, local, selected, env = self.fixture("environment")
        self.save(project, env)
        other = selected.parent / "other.jsonl"
        other.write_text(json.dumps({"id": "other", "text": "otherselectedlesson"}) + "\n")
        code = """import importlib.util, os
spec=importlib.util.spec_from_file_location('test_mirror', 'memory/brain_fts_mirror.py')
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
m.rebuild()
assert m.query('selectedstoree2elesson')[0]['id']=='chosen-lesson'
os.environ['PROJECT_OS_SHARED_BRAIN']=%r
try: m.query('selectedstoree2elesson')
except m.MirrorError as exc: assert 'stale' in str(exc),str(exc)
else: raise AssertionError('selection change reused old source')
m.rebuild()
assert m.query('otherselectedlesson')[0]['id']=='other'
os.environ['PROJECT_OS_SHARED_BRAIN']='invalid-relative'
try: m.verify()
except m.MirrorError as exc: assert 'must be absolute' in str(exc),str(exc)
else: raise AssertionError('invalid changed selection accepted')
""" % str(other)
        result = subprocess.run([sys.executable, "-c", code], cwd=project, env=env,
                                capture_output=True, text=True, timeout=30)
        self.okay(result)


if __name__ == "__main__":
    unittest.main()
