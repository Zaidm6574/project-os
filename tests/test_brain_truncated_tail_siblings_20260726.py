"""No brain writer may weld a new record onto a crash-truncated last line.

`scripts/brain_append.py` got this heal in d71aeca. The three OTHER writers to
the same file did not, and an adversary round on 2026-07-26 reproduced the
defect verbatim in each: they merge the damaged fragment with the record being
saved into ONE unparseable line, print success, and exit 0. Every reader skips
unparseable lines silently, so the loss is permanent and invisible.

`save-chat` is the worst case: unlike central-brain push/pull there is no
upstream copy to re-sync from, so the lesson is gone for good.

Each test drives the REAL writer against a file whose last line was truncated,
then asserts the new record is recoverable by parsing the file back -- never by
reading the source. Deleting a heal makes the parse fail.
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRAIN_DIR = os.path.join(ROOT, "addons", "full-engine", "brain")

# An intact record, then a tail cut mid-write: no closing brace, no newline.
TRUNCATED = ('{"id":"L1","type":"lesson","text":"an intact earlier lesson"}\n'
             '{"id":"L2","type":"lesson","text":"crash-tru')


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _parsable(path):
    """Records that survive a real read-back, as every consumer does it."""
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


class SaveChatDoesNotWeld(unittest.TestCase):
    """brain.py cmd_save_chat -- the unrecoverable one."""

    def test_a_saved_summary_survives_a_truncated_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            brain_file = Path(tmp) / "shared-brain.jsonl"
            brain_file.write_text(TRUNCATED, encoding="utf-8")
            brain = _load("brain_trunc_savechat", os.path.join(BRAIN_DIR, "brain.py"))
            # _safe_path refuses any path outside ROOT, so the sandbox has to
            # BE the root -- pointing only BRAIN_FILE at a tempdir trips the
            # guard rather than exercising the writer.
            brain.ROOT = tmp
            brain.BRAIN_FILE = str(brain_file)
            rc = brain.cmd_save_chat(_Args(
                mode="summary",
                summary="a brand new approved summary I just saved",
                kind="lesson"))
            self.assertEqual(rc, 0)

            texts = [r.get("text", "") or r.get("summary", "")
                     for r in _parsable(brain_file)]
            self.assertTrue(
                any("brand new approved summary" in t for t in texts),
                "the saved record was welded onto the truncated tail and is "
                "unrecoverable; save-chat has no upstream copy to re-sync from",
            )
            # The intact earlier record must survive untouched.
            self.assertTrue(any("an intact earlier lesson" in t for t in texts))


class CentralBrainAppendDoesNotWeld(unittest.TestCase):
    """central_brain.append_new serves BOTH push and pull."""

    def test_a_pushed_record_survives_a_truncated_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "shared-brain.jsonl"
            target.write_text(TRUNCATED, encoding="utf-8")
            cb = _load("central_trunc", os.path.join(BRAIN_DIR, "central_brain.py"))
            added = cb.append_new(target, [
                {"id": "NEW1", "type": "lesson", "text": "a cross-project lesson"},
            ])
            self.assertEqual(added, 1)

            ids = [r.get("id") for r in _parsable(target)]
            self.assertIn(
                "NEW1", ids,
                "append_new welded the record onto the truncated tail; it "
                "reported success and the record is gone",
            )
            self.assertIn("L1", ids)


class BBLockAppendDoesNotWeld(unittest.TestCase):
    """The generic `append` the bb_lock docstring advertises."""

    def test_an_appended_line_survives_a_truncated_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "records.jsonl"
            target.write_text(TRUNCATED, encoding="utf-8")
            env = dict(os.environ)
            env["BB_LOCK_DIR"] = str(Path(tmp) / "locks")
            done = subprocess.run(
                [sys.executable, os.path.join(ROOT, "scripts", "bb_lock.py"),
                 "append", str(target), "--line", '{"id":"NEW2"}',
                 "--agent", "test"],
                capture_output=True, text=True, env=env, timeout=60,
            )
            self.assertEqual(done.returncode, 0, done.stderr)

            ids = [r.get("id") for r in _parsable(target)]
            self.assertIn(
                "NEW2", ids,
                "bb_lock append welded onto the truncated tail -- the command "
                "brain_append's own heal names as a CAUSE of this damage",
            )
            self.assertIn("L1", ids)


class _Args(object):
    """argparse stand-in: any option not set explicitly reads as None.

    Enumerating the flags by hand meant every new option broke this test with
    an AttributeError that had nothing to do with the behaviour under test.
    """

    def __init__(self, **kw):
        self.__dict__.update(kw)

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return None


if __name__ == "__main__":
    unittest.main()
