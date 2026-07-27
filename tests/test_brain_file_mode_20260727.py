"""Every creator of a shared brain must create it 0600 -- and leave an existing mode alone.

`scripts/brain_archive.py` states the intent outright: the brain "holds lesson
text harvested from every project", so when there is no existing file to copy
permissions from it picks 0600 rather than the umask default
(_mode_or_private), and it creates the archive with
os.open(..., O_CREAT|O_EXCL, 0o600).

Every OTHER creator of that same file used `Path.touch()`, `write_text()` or
`open(path, "a")`, all of which create at 0666 & ~umask -- 0644 on a stock
umask 022 account. The file is sensitive by its own writers' definition: every
record is credential-scanned before it is allowed in (brain.py::gate_record,
brain_append.py's privacy gate), which is a strange thing to do to a file that
is then published to every local account.

Both directions matter and both are asserted here:
  * a brain created just now is 0600, whatever the umask was;
  * a brain that ALREADY exists keeps the mode its owner chose. An operator who
    deliberately set 0640 for a shared group must keep 0640 -- brain_archive.py
    documents that same choice for the archive ("An archive that already exists
    keeps whatever mode its owner chose") and silently tightening someone's
    file is its own defect.

Every test drives the REAL writer and stats the REAL file; none reads source.
The umask is forced to 022 so the defect is reproducible on any developer
account (under umask 077 the bug is invisible, which is why it survived).
"""

import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRAIN_DIR = ROOT / "addons" / "full-engine" / "brain"
FULL_ENGINE = ROOT / "scripts" / "install_full_engine.py"
BRAIN_APPEND = ROOT / "scripts" / "brain_append.py"

RECORD = {"id": "L-mode-1", "type": "lesson", "text": "a durable lesson"}


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _mode(path):
    return stat.S_IMODE(os.stat(str(path)).st_mode)


class _Args(object):
    """argparse stand-in: any option not set explicitly reads as None."""

    def __init__(self, **kw):
        self.__dict__.update(kw)

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return None


class BrainModeCase(unittest.TestCase):
    """Forces the stock umask so a world-readable creation is reproducible."""

    def setUp(self):
        self._umask = os.umask(0o022)

    def tearDown(self):
        os.umask(self._umask)

    def assertPrivate(self, path, writer):
        self.assertTrue(os.path.exists(str(path)), "%s created nothing" % writer)
        self.assertEqual(
            _mode(path), 0o600,
            "%s created the shared brain %s (0%o); every record in it is "
            "credential-scanned before it is written, and brain_archive.py "
            "creates the same data 0600" % (writer, path, _mode(path)),
        )

    def assertKept(self, path, mode, writer):
        self.assertEqual(
            _mode(path), mode,
            "%s changed an EXISTING brain's mode from 0%o to 0%o; an operator "
            "who deliberately chose that mode must keep it"
            % (writer, mode, _mode(path)),
        )


class InstallFullEngineCreatesPrivateBrain(BrainModeCase):

    def _target(self, base):
        target = Path(base) / "project"
        (target / "blackboard").mkdir(parents=True)
        (target / "AGENTS.md").write_text("# Project OS\n", encoding="utf-8")
        (target / "blackboard" / "00-project-goal.md").write_text("# Goal\n", encoding="utf-8")
        return target

    def test_a_new_brain_is_private(self):
        full_engine = _load("ifm_mode_new", FULL_ENGINE)
        with tempfile.TemporaryDirectory() as tmp:
            target = self._target(tmp)
            full_engine.install_full_engine(target)
            self.assertPrivate(target / "brain" / "shared-brain.jsonl",
                               "install_full_engine.py")

    def test_an_existing_brain_keeps_its_mode(self):
        full_engine = _load("ifm_mode_keep", FULL_ENGINE)
        with tempfile.TemporaryDirectory() as tmp:
            target = self._target(tmp)
            brain = target / "brain" / "shared-brain.jsonl"
            brain.parent.mkdir(parents=True, exist_ok=True)
            brain.write_text(json.dumps(RECORD) + "\n", encoding="utf-8")
            os.chmod(str(brain), 0o640)
            full_engine.install_full_engine(target)
            self.assertKept(brain, 0o640, "install_full_engine.py")
            self.assertIn("durable lesson", brain.read_text(encoding="utf-8"))


class CentralBrainCreatesPrivateBrain(BrainModeCase):

    def _module(self, name):
        return _load(name, BRAIN_DIR / "central_brain.py")

    def test_init_central_creates_a_private_brain(self):
        cb = self._module("cb_mode_init")
        with tempfile.TemporaryDirectory() as tmp:
            brain_file = cb.init_central(Path(tmp) / "central")
            self.assertPrivate(brain_file, "central_brain.init_central")

    def test_init_central_keeps_an_existing_mode(self):
        cb = self._module("cb_mode_init_keep")
        with tempfile.TemporaryDirectory() as tmp:
            central = Path(tmp) / "central"
            central.mkdir()
            existing = central / "shared-brain.jsonl"
            existing.write_text(json.dumps(RECORD) + "\n", encoding="utf-8")
            os.chmod(str(existing), 0o640)
            cb.init_central(central)
            self.assertKept(existing, 0o640, "central_brain.init_central")
            self.assertIn("durable lesson", existing.read_text(encoding="utf-8"))

    def test_append_new_creates_a_private_brain(self):
        cb = self._module("cb_mode_append")
        with tempfile.TemporaryDirectory() as tmp:
            brain_file = Path(tmp) / "shared-brain.jsonl"
            self.assertEqual(cb.append_new(brain_file, [dict(RECORD)]), 1)
            self.assertPrivate(brain_file, "central_brain.append_new")

    def test_append_new_keeps_an_existing_mode(self):
        cb = self._module("cb_mode_append_keep")
        with tempfile.TemporaryDirectory() as tmp:
            brain_file = Path(tmp) / "shared-brain.jsonl"
            brain_file.write_text("", encoding="utf-8")
            os.chmod(str(brain_file), 0o640)
            self.assertEqual(cb.append_new(brain_file, [dict(RECORD)]), 1)
            self.assertKept(brain_file, 0o640, "central_brain.append_new")
            self.assertIn("durable lesson", brain_file.read_text(encoding="utf-8"))

    def test_pull_creates_a_private_project_brain(self):
        cb = self._module("cb_mode_pull")
        with tempfile.TemporaryDirectory() as tmp:
            central = Path(tmp) / "central"
            project = Path(tmp) / "project"
            (project / "brain").mkdir(parents=True)
            cb.pull(central, project, explicit_project_id="p")
            self.assertPrivate(project / "brain" / "shared-brain.jsonl",
                               "central_brain.pull")


class BrainAppendCreatesPrivateBrain(BrainModeCase):
    """scripts/brain_append.py -- the CLI every automation writes through."""

    def _run(self, brain_path, record):
        env = dict(os.environ)
        env["PROJECT_OS_SHARED_BRAIN"] = str(brain_path)
        return subprocess.run(
            [sys.executable, str(BRAIN_APPEND), "--line", json.dumps(record),
             "--no-reindex"],
            capture_output=True, text=True, env=env)

    def test_a_new_brain_is_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            brain_path = Path(tmp) / "central" / "shared-brain.jsonl"
            result = self._run(brain_path, RECORD)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertPrivate(brain_path, "brain_append.py")

    def test_an_existing_brain_keeps_its_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            brain_path = Path(tmp) / "shared-brain.jsonl"
            brain_path.write_text("", encoding="utf-8")
            os.chmod(str(brain_path), 0o640)
            result = self._run(brain_path, RECORD)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertKept(brain_path, 0o640, "brain_append.py")
            self.assertIn("durable lesson", brain_path.read_text(encoding="utf-8"))


class BrainPyCreatesPrivateBrain(BrainModeCase):
    """addons/full-engine/brain/brain.py -- save-chat and export."""

    def _brain(self, name, root, brain_file):
        module = _load(name, BRAIN_DIR / "brain.py")
        # _safe_path refuses any path outside ROOT, so the sandbox has to BE
        # the root (same reason as test_brain_truncated_tail_siblings).
        module.ROOT = str(root)
        module.BRAIN_FILE = str(brain_file)
        return module

    def test_save_chat_creates_a_private_brain(self):
        with tempfile.TemporaryDirectory() as tmp:
            brain_file = Path(tmp) / "shared-brain.jsonl"
            brain = self._brain("bpy_mode_save", tmp, brain_file)
            self.assertEqual(brain.cmd_save_chat(_Args(
                mode="summary", summary="an approved summary", kind="lesson")), 0)
            self.assertPrivate(brain_file, "brain.py save-chat")

    def test_save_chat_keeps_an_existing_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            brain_file = Path(tmp) / "shared-brain.jsonl"
            brain_file.write_text("", encoding="utf-8")
            os.chmod(str(brain_file), 0o640)
            brain = self._brain("bpy_mode_save_keep", tmp, brain_file)
            self.assertEqual(brain.cmd_save_chat(_Args(
                mode="summary", summary="an approved summary", kind="lesson")), 0)
            self.assertKept(brain_file, 0o640, "brain.py save-chat")
            self.assertIn("an approved summary",
                          brain_file.read_text(encoding="utf-8"))

    def test_export_creates_a_private_brain(self):
        with tempfile.TemporaryDirectory() as tmp:
            brain_file = Path(tmp) / "shared-brain.jsonl"
            source = Path(tmp) / "lessons.jsonl"
            source.write_text(json.dumps(
                {"id": "L-export-1", "type": "lesson",
                 "text": "an exported lesson"}) + "\n", encoding="utf-8")
            brain = self._brain("bpy_mode_export", tmp, brain_file)
            brain.cmd_export(_Args(from_file=str(source)))
            self.assertPrivate(brain_file, "brain.py export")

    def test_export_keeps_an_existing_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            brain_file = Path(tmp) / "shared-brain.jsonl"
            brain_file.write_text("", encoding="utf-8")
            os.chmod(str(brain_file), 0o640)
            source = Path(tmp) / "lessons.jsonl"
            source.write_text(json.dumps(
                {"id": "L-export-1", "type": "lesson",
                 "text": "an exported lesson"}) + "\n", encoding="utf-8")
            brain = self._brain("bpy_mode_export_keep", tmp, brain_file)
            brain.cmd_export(_Args(from_file=str(source)))
            self.assertKept(brain_file, 0o640, "brain.py export")
            self.assertIn("an exported lesson",
                          brain_file.read_text(encoding="utf-8"))


class HelperCopiesStayInSync(unittest.TestCase):
    """The four copies of _create_private must not drift apart.

    This repo's signature failure is a guard applied to one of two installers,
    and the same shape produced this defect: brain_archive.py had the 0600
    creation and no other writer did. The behaviour tests above cover every
    creation site; this pins the copies themselves so a later edit to one is a
    visible failure rather than a silent divergence, exactly as the
    SECRET_PATTERNS / _SECRET_PATTERNS sync test does.
    """

    MODULES = (
        "scripts/brain_append.py",
        "scripts/install_full_engine.py",
        "addons/full-engine/brain/brain.py",
        "addons/full-engine/brain/central_brain.py",
    )

    @staticmethod
    def _helper_source(path):
        lines = (ROOT / path).read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if line.startswith("def _create_private("):
                out = [line]
                for rest in lines[i + 1:]:
                    if rest and not rest.startswith((" ", ")", "\t")):
                        break
                    out.append(rest)
                return "\n".join(out).rstrip()
        return None

    def test_every_brain_creator_carries_the_same_helper(self):
        sources = {}
        for path in self.MODULES:
            source = self._helper_source(path)
            self.assertIsNotNone(
                source, "%s creates a shared brain but has no _create_private; "
                        "a fix at some of the sites is not a fix" % path)
            sources[path] = source
        self.assertEqual(
            len(set(sources.values())), 1,
            "the _create_private copies have drifted:\n%s"
            % "\n".join("--- %s ---\n%s" % (p, s) for p, s in sources.items()))

    def test_the_helper_is_the_brain_archive_pattern(self):
        source = self._helper_source(self.MODULES[0])
        self.assertIn("os.O_CREAT | os.O_EXCL", source)
        self.assertIn("0o600", source)
        self.assertIn("except FileExistsError", source,
                      "an existing file's mode must be left alone")


if __name__ == "__main__":
    unittest.main()
