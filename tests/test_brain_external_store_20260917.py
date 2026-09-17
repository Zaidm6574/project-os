"""Installed command controls for explicit shared-brain stores and exchange paths."""
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def record(rid="seed"):
    return {"id": rid, "type": "lesson", "text": "Explain one reproducible example."}


def seed(path, rid="seed"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record(rid)) + "\n", encoding="utf-8")


@unittest.skipIf(sys.version_info < (3, 10), "full-engine installer requires Python 3.10+")
class InstalledExternalBrainTests(unittest.TestCase):
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
        installed = subprocess.run(["sh", str(ROOT / "install.sh"), str(cls.template), "--full-engine"],
                                   env=cls.env, cwd=ROOT, capture_output=True, text=True, timeout=60)
        if installed.returncode:
            raise AssertionError(installed.stdout + installed.stderr)

    def fixture(self, name):
        case = self.base / self._testMethodName / name
        project = case / "project"
        shutil.copytree(self.template, project)
        local = project / "brain/shared-brain.jsonl"
        first, second = case / "external-a/brain.jsonl", case / "external-b/brain.jsonl"
        seed(local, "local-seed")
        seed(first, "first-seed")
        seed(second, "second-seed")
        return project, local, first, second

    def binding(self, project, target, ignored=True):
        binding = project / "brain/shared-brain-binding.jsonl"
        binding.write_text(json.dumps({"schema": "project-os/shared-brain-binding/v1", "target": str(target)}) + "\n")
        if ignored:
            with (project / ".gitignore").open("a") as handle:
                handle.write("\nbrain/shared-brain-binding.jsonl\n")
        return binding

    def call(self, project, arguments, env=None, code=None):
        environment = dict(self.env, **(env or {}))
        command = [sys.executable, str(project / "brain/brain.py"), *map(str, arguments)]
        if code is not None:
            command = [sys.executable, "-c", code]
        return subprocess.run(command, cwd=project, env=environment, capture_output=True, text=True, timeout=20)

    def okay(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def refused(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_installed_roundtrip_uses_one_selected_store(self):
        for mode in ("local", "environment", "binding", "env-over-binding", "local-over-binding"):
            with self.subTest(mode=mode):
                project, local, first, second = self.fixture(mode)
                environment = {}
                selected = local
                if mode in {"binding", "env-over-binding", "local-over-binding"}:
                    self.binding(project, first)
                    selected = first
                if mode in {"environment", "env-over-binding"}:
                    environment["PROJECT_OS_SHARED_BRAIN"] = str(second)
                    selected = second
                if mode == "local-over-binding":
                    environment["PROJECT_OS_SHARED_BRAIN"] = str(local)
                    selected = local
                selected.chmod(0o640)
                untouched = {p: p.read_bytes() for p in (local, first, second) if p != selected}
                self.okay(self.call(project, ["save-chat", "--summary", "Keep a checked example.", "--id", "saved"], environment))
                self.okay(self.call(project, ["save-chat", "--summary", "Keep a checked example.", "--id", "saved"], environment))
                source = project / "source.jsonl"
                seed(source, "exported")
                self.okay(self.call(project, ["export", "--from", source], environment))
                result = self.call(project, ["import"], environment)
                self.okay(result)
                rows = [json.loads(line) for line in result.stdout.splitlines()]
                self.assertEqual(len(rows), 3)
                self.assertIn("saved", [row["id"] for row in rows])
                self.assertIn("exported", [row["id"] for row in rows])
                destination = project / "exchange.jsonl"
                self.okay(self.call(project, ["import", "--into", destination], environment))
                self.assertEqual([json.loads(line) for line in destination.read_text().splitlines()], rows)
                self.assertEqual(stat.S_IMODE(selected.stat().st_mode), 0o640)
                for path, original in untouched.items():
                    self.assertEqual(path.read_bytes(), original)

    def test_external_missing_leaf_created_private(self):
        project, local, first, second = self.fixture("missing")
        first.unlink()
        before = local.read_bytes(), second.read_bytes()
        self.okay(self.call(project, ["save-chat", "--summary", "Create one lesson.", "--id", "created"],
                            {"PROJECT_OS_SHARED_BRAIN": str(first)}))
        self.assertEqual(stat.S_IMODE(first.stat().st_mode), 0o600)
        self.assertEqual((local.read_bytes(), second.read_bytes()), before)

    def test_external_authorization_does_not_extend_to_exchange_files(self):
        project, local, first, second = self.fixture("exchange")
        self.binding(project, first)
        linked = project / "outside.jsonl"
        linked.symlink_to(second)
        original = {p: p.read_bytes() for p in (local, first, second)}
        for path in (second, linked):
            for arguments in (["export", "--from", path], ["import", "--into", path],
                              ["save-chat", "--summary-file", path]):
                with self.subTest(arguments=arguments):
                    result = self.call(project, arguments)
                    self.refused(result)
                    self.assertIn("outside the project", result.stderr)
                    for item, content in original.items():
                        self.assertEqual(item.read_bytes(), content)

    def test_exchange_hardlinks_cannot_read_or_overwrite_external_data(self):
        for mode in ("local", "external-binding"):
            with self.subTest(mode=mode):
                project, local, first, second = self.fixture(mode)
                if mode == "external-binding":
                    self.binding(project, first)
                marker = "outside exchange sentinel must stay private"
                second.write_text(json.dumps({**record("outside"), "text": marker}) + "\n")
                alias = project / "hardlinked-exchange.jsonl"
                os.link(second, alias)
                original = {p: p.read_bytes() for p in (local, first, second)}
                for arguments in (["export", "--from", alias], ["import", "--into", alias],
                                  ["save-chat", "--summary-file", alias]):
                    with self.subTest(arguments=arguments):
                        result = self.call(project, arguments)
                        self.refused(result)
                        self.assertIn("hardlink", result.stderr)
                        self.assertNotIn(marker, result.stdout + result.stderr)
                        for path, content in original.items():
                            self.assertEqual(path.read_bytes(), content)

    def test_invalid_external_configuration_fails_closed(self):
        for mode in ("relative-env", "symlink-env", "parent-symlink-env", "hardlink-env",
                     "unignored-binding", "malformed-binding", "symlink-binding", "hardlink-binding-target"):
            with self.subTest(mode=mode):
                project, local, first, second = self.fixture(mode)
                env = {}
                if mode == "relative-env":
                    env["PROJECT_OS_SHARED_BRAIN"] = "relative.jsonl"
                elif mode == "symlink-env":
                    alias = first.parent / "alias.jsonl"
                    alias.symlink_to(first)
                    env["PROJECT_OS_SHARED_BRAIN"] = str(alias)
                elif mode == "parent-symlink-env":
                    alias = first.parent.parent / "alias-dir"
                    alias.symlink_to(first.parent, target_is_directory=True)
                    env["PROJECT_OS_SHARED_BRAIN"] = str(alias / first.name)
                elif mode == "hardlink-env":
                    alias = first.parent / "hard.jsonl"
                    os.link(first, alias)
                    env["PROJECT_OS_SHARED_BRAIN"] = str(alias)
                elif mode == "unignored-binding":
                    self.binding(project, first, ignored=False)
                elif mode == "malformed-binding":
                    self.binding(project, first).write_text('{"schema": "wrong"}\n')
                elif mode == "symlink-binding":
                    binding = self.binding(project, first)
                    copy = first.parent / "binding-copy.jsonl"
                    copy.write_bytes(binding.read_bytes())
                    binding.unlink()
                    binding.symlink_to(copy)
                else:
                    self.binding(project, first)
                    os.link(first, first.parent / "hard.jsonl")
                original = {p: p.read_bytes() for p in (local, first, second)}
                for args in (["import"], ["export", "--from", second],
                             ["save-chat", "--summary", "Must not write."]):
                    self.refused(self.call(project, args, env))
                for path, content in original.items():
                    self.assertEqual(path.read_bytes(), content)

    def test_external_store_retains_secret_gate(self):
        project, local, first, second = self.fixture("secrets")
        self.binding(project, first)
        secret = "sk_" + "live_" + "F" * 32
        source = project / "source.jsonl"
        source.write_text(json.dumps({**record("secret-row"), "text": secret}) + "\n")
        original = first.read_bytes()
        for args in (["save-chat", "--summary", secret],
                     ["save-chat", "--summary", "Ordinary lesson.", "--tag", secret],
                     ["export", "--from", source]):
            result = self.call(project, args)
            self.refused(result)
            self.assertNotIn(secret, result.stdout + result.stderr)
            self.assertEqual(first.read_bytes(), original)

    def test_each_command_revalidates_changed_selection(self):
        for change in ("environment", "binding", "module-global"):
            with self.subTest(change=change):
                project, local, first, second = self.fixture(change)
                env = {}
                if change == "binding":
                    binding = self.binding(project, first)
                    mutation = "p=Path(%r); row=json.loads(p.read_text()); row['target']=%r; p.write_text(json.dumps(row)+'\\n')" % (str(binding), str(second))
                elif change == "environment":
                    mutation = "os.environ['PROJECT_OS_SHARED_BRAIN']=%r" % str(second)
                else:
                    mutation = "m.BRAIN_FILE=%r" % str(second)
                source = project / "source.jsonl"
                seed(source, "source")
                original = {p: p.read_bytes() for p in (local, first, second)}
                code = self.loaded_code() + mutation + "\n" + """
checks = [lambda: m.cmd_import(argparse.Namespace(into=None)),
          lambda: m.cmd_export(argparse.Namespace(from_file='source.jsonl')),
          lambda: m.cmd_save_chat(argparse.Namespace(summary='Never publish.', summary_file=None))]
for command in checks:
    try:
        command()
    except SystemExit as exc:
        assert 'selection changed' in str(exc), str(exc)
    else:
        raise AssertionError('changed selection was accepted')
"""
                self.okay(self.call(project, [], env, code))
                for path, content in original.items():
                    self.assertEqual(path.read_bytes(), content)

    @staticmethod
    def loaded_code():
        return "import argparse, importlib.util, json, os\nfrom pathlib import Path\ns=importlib.util.spec_from_file_location('tested_brain','brain/brain.py'); m=importlib.util.module_from_spec(s); s.loader.exec_module(m)\n"

    def test_each_command_revalidates_replaced_store(self):
        for change in ("symlink", "hardlink"):
            with self.subTest(change=change):
                project, local, first, second = self.fixture(change)
                self.binding(project, first)
                original = {p: p.read_bytes() for p in (local, first, second)}
                mutation = "p=Path(%r); p.unlink(); " % str(first)
                mutation += ("p.symlink_to(%r)" if change == "symlink" else "os.link(%r,p)") % str(second)
                code = self.loaded_code() + mutation + "\n" + """
checks = [lambda: m.cmd_import(argparse.Namespace(into=None)),
          lambda: m.cmd_export(argparse.Namespace(from_file='missing.jsonl')),
          lambda: m.cmd_save_chat(argparse.Namespace(summary='Never publish.', summary_file=None))]
for command in checks:
    try:
        command()
    except SystemExit as exc:
        assert 'symlink' in str(exc) or 'hardlink' in str(exc), str(exc)
    else:
        raise AssertionError('replaced store was accepted')
"""
                self.okay(self.call(project, [], code=code))
                self.assertEqual(local.read_bytes(), original[local])
                self.assertEqual(second.read_bytes(), original[second])

    def test_selftest_isolated_from_external_override_and_restores_selection(self):
        project, local, first, second = self.fixture("selftest")
        env = {"PROJECT_OS_SHARED_BRAIN": str(first)}
        original = {p: p.read_bytes() for p in (local, first, second)}
        result = self.call(project, ["--selftest"], env)
        self.okay(result)
        self.assertIn("selftest: OK", result.stdout)
        code = self.loaded_code() + """
before = (m.ROOT, m.BRAIN_FILE, os.environ.get('PROJECT_OS_SHARED_BRAIN'))
assert m._selftest() == 0
assert (m.ROOT, m.BRAIN_FILE, os.environ.get('PROJECT_OS_SHARED_BRAIN')) == before
"""
        self.okay(self.call(project, [], env, code))
        for path, content in original.items():
            self.assertEqual(path.read_bytes(), content)

    def test_portable_copy_stays_project_local(self):
        case = self.base / self._testMethodName
        project = case / "portable"
        (project / "brain").mkdir(parents=True)
        shutil.copy2(ROOT / "addons/full-engine/brain/brain.py", project / "brain/brain.py")
        outside = case / "external.jsonl"
        seed(outside)
        before = outside.read_bytes()
        self.okay(self.call(project, ["save-chat", "--summary", "Local portable lesson."],
                            {"PROJECT_OS_SHARED_BRAIN": str(outside)}))
        self.assertTrue((project / "brain/shared-brain.jsonl").is_file())
        self.assertEqual(outside.read_bytes(), before)
        self.refused(self.call(project, ["import", "--into", outside]))
        self.assertEqual(outside.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
