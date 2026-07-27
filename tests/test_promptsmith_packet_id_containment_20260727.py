#!/usr/bin/env python3
"""Containment for scripts/promptsmith.py --packet-id (2026-07-27).

The --packet-id value is interpolated straight into the output filenames
(<id>-worker-prompt.md / <id>-rubric.md) under --out-dir. Without a containment
check a crafted id (../.., an absolute path, symlink/.. components) writes the
packet OUTSIDE --out-dir — the same bug class already fixed in plan_artifact.py.

Mutation contract: neutralize the containment guard in promptsmith.py and the
`*_rejected` cases below MUST fail (an escaping id would be written and the
process would exit 0). They pass only with the guard in place.
"""
import os
import sys
import subprocess
import tempfile
import shutil
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "scripts", "promptsmith.py")


def _run(out_dir, packet_id, brief):
    return subprocess.run(
        [sys.executable, SCRIPT,
         "--task", "build the hero section",
         "--brief-file", brief,
         "--out-dir", out_dir,
         "--packet-id", packet_id,
         "--no-index"],
        capture_output=True, text=True,
    )


class PromptsmithPacketIdContainment(unittest.TestCase):
    def setUp(self):
        # base/ exists and is the intended escape target; out_dir is a real
        # subdirectory of it, so a `..` id resolves to a path whose parent
        # (base/) genuinely exists — that is what would let an UNGUARDED write
        # actually land outside out_dir.
        self.base = tempfile.mkdtemp(prefix="po-psmith-")
        self.out_dir = os.path.join(self.base, "packets")
        os.makedirs(self.out_dir, exist_ok=True)
        self.brief = os.path.join(self.base, "brief.md")
        with open(self.brief, "w", encoding="utf-8") as f:
            f.write("# Brief\n\n## DON'T\n- never use neon\n")

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def _base_entries(self):
        return set(os.listdir(self.base))

    def test_valid_packet_id_writes_inside(self):
        pid = "psmith-valid-pkt-123"
        r = _run(self.out_dir, pid, self.brief)
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        wp = os.path.join(self.out_dir, pid + "-worker-prompt.md")
        rp = os.path.join(self.out_dir, pid + "-rubric.md")
        self.assertTrue(os.path.isfile(wp), "worker prompt not written inside out-dir")
        self.assertTrue(os.path.isfile(rp), "rubric not written inside out-dir")
        # both resolve DIRECTLY inside out_dir
        self.assertEqual(os.path.dirname(os.path.realpath(wp)),
                         os.path.realpath(self.out_dir))
        # nothing leaked into base/ other than the expected fixtures + out_dir
        self.assertEqual(self._base_entries(), {"packets", "brief.md"})

    def test_dotdot_traversal_rejected(self):
        before = self._base_entries()
        # out_dir/../escaped-pkt-*  ->  base/escaped-pkt-*  (base/ exists!)
        r = _run(self.out_dir, "../escaped-pkt", self.brief)
        self.assertNotEqual(r.returncode, 0,
                            msg="traversal id was accepted: %s" % r.stdout)
        escaped_wp = os.path.join(self.base, "escaped-pkt-worker-prompt.md")
        escaped_rp = os.path.join(self.base, "escaped-pkt-rubric.md")
        self.assertFalse(os.path.exists(escaped_wp),
                         "worker prompt escaped out-dir: %s" % escaped_wp)
        self.assertFalse(os.path.exists(escaped_rp),
                         "rubric escaped out-dir: %s" % escaped_rp)
        # no stray file (incl. any *.tmp sibling) created outside out_dir
        self.assertEqual(self._base_entries(), before)

    def test_absolute_path_id_rejected(self):
        target = os.path.join(self.base, "abs_target")
        os.makedirs(target, exist_ok=True)
        abs_pid = os.path.join(target, "pwned")
        r = _run(self.out_dir, abs_pid, self.brief)
        self.assertNotEqual(r.returncode, 0,
                            msg="absolute id was accepted: %s" % r.stdout)
        self.assertFalse(os.path.exists(abs_pid + "-worker-prompt.md"),
                         "worker prompt escaped via absolute id")
        self.assertFalse(os.path.exists(abs_pid + "-rubric.md"),
                         "rubric escaped via absolute id")
        self.assertEqual(os.listdir(target), [],
                         "files written into an absolute path outside out-dir")


if __name__ == "__main__":
    unittest.main()
