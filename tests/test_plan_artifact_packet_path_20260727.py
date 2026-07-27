#!/usr/bin/env python3
"""plan_artifact.packet_path(): the SECOND containment layer, graded directly.

Coverage gap (audit 2026-07-27, reproduced): replacing packet_path()'s

    contained = (os.path.commonpath([real, base]) == base
                 and os.path.dirname(real) == base)

with `contained = True` left the whole suite green (656 tests, OK). Every
existing test reaches this function through the `compile` CLI, and compile
validates the plan id AND every step id with require_path_safe_plan_id() first
(plan_artifact.py:983-987) -- so layer 1 refuses the hostile name before
packet_path ever sees it, and the suite could not tell this function from
`pass`. Neutering layer 1 instead is caught immediately by
tests/test_plan_artifact_hostile_id_20260726.py; neutering only layer 2 was
caught by nothing.

packet_path is NOT dead code: with layer 1 removed the traversal is still
refused, and the refusal text is packet_path's own ("refusing packet name
...: it resolves to ... outside the packets directory"). With BOTH layers
removed the packet lands outside packets/ and compile exits 0 printing
"compiled 1 worker packets" as success. So this layer is load-bearing; it just
had no grade of its own. These are unit tests against the function, because
that is the only way a masked second layer can be graded at all.

Mutation contract -- each of these must FAIL if the named half is neutered:
  * `contained = True`                    -> every *_refused test below
  * drop `os.path.dirname(real) == base`  -> test_a_subdirectory_name_is_refused
  * base = abspath() instead of realpath  -> test_a_symlinked_packets_dir_is_not
                                             _falsely_refused (near miss)
Nothing here writes a packet: packet_path only computes and validates a path,
and every directory used is a temp tree.
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import plan_artifact  # noqa: E402


class PacketPathContainment(unittest.TestCase):
    def setUp(self):
        # base/ exists and is the escape target; packets/ is a real
        # subdirectory of it, so a `..` name resolves to a directory that
        # genuinely exists -- which is what would let an UNGUARDED write
        # actually land outside packets/.
        self.base = tempfile.mkdtemp(prefix="po-packet-path-")
        self.addCleanup(shutil.rmtree, self.base, True)
        self.packets = os.path.join(self.base, "packets")
        self.outside = os.path.join(self.base, "outside")
        os.makedirs(self.packets)
        os.makedirs(self.outside)

    def assert_refused(self, name, why):
        with self.assertRaises(plan_artifact.PlanInputError, msg=why) as ctx:
            plan_artifact.packet_path(self.packets, name)
        self.assertIn("refusing packet name", str(ctx.exception),
                      "refusal did not come from packet_path: %s" % ctx.exception)

    # ---------------- refusals: the containment guard itself ----------------

    def test_a_parent_traversal_name_is_refused(self):
        self.assert_refused(
            os.path.join("..", "outside", "pwn-s1.md"),
            "a ../ packet name escapes blackboard/packets/")

    def test_a_deep_traversal_name_is_refused(self):
        self.assert_refused(
            os.path.join("..", "..", os.path.basename(self.base), "outside",
                         "pwn-s1.md"),
            "a multi-level traversal escapes blackboard/packets/")

    def test_an_absolute_name_is_refused(self):
        # os.path.join(packets_dir, "/abs/path") DISCARDS packets_dir entirely.
        self.assert_refused(os.path.join(self.outside, "pwn-s1.md"),
                            "an absolute packet name discards packets_dir")

    def test_a_subdirectory_name_is_refused(self):
        """Stays UNDER packets/, so commonpath alone accepts it.

        This is the half that `os.path.dirname(real) == base` carries: a packet
        is one flat file in packets/, and "sub/x.md" would silently write into
        (or fail against) a directory nobody created.
        """
        os.makedirs(os.path.join(self.packets, "sub"))
        self.assert_refused(os.path.join("sub", "pwn-s1.md"),
                            "a nested packet name is not a flat packet file")

    def test_a_symlinked_subdirectory_cannot_be_traversed(self):
        os.symlink(self.outside, os.path.join(self.packets, "link"))
        self.assert_refused(os.path.join("link", "pwn-s1.md"),
                            "a symlink inside packets/ was traversed")

    def test_a_symlinked_packet_file_is_refused(self):
        """The case normpath() cannot see and realpath() can.

        A link pre-planted at the packet's own name points outside packets/;
        the string "pwn-s1.md" looks perfectly contained.
        """
        os.symlink(os.path.join(self.outside, "pwn-s1.md"),
                   os.path.join(self.packets, "pwn-s1.md"))
        self.assert_refused("pwn-s1.md",
                            "a planted symlink at the packet name was accepted")

    def test_the_packets_dir_itself_is_refused(self):
        for name in ("", ".", ".."):
            with self.subTest(name=name):
                self.assert_refused(name, "a packet name must name a file")

    # NOTE: packet_path's comment says realpath() raises ValueError on an
    # embedded NUL, so a NUL name is refused by the except-clause. That is not
    # true on this floor -- /usr/bin/python3 3.9.6 on macOS returns the path
    # with the NUL intact and packet_path ACCEPTS it. It is not a containment
    # escape (a NUL cannot forge a path separator, and CPython refuses the name
    # at the syscall), so no assertion is made here; the comment is simply
    # optimistic about one platform. Left for the owner rather than fixed as
    # scope creep.

    # ---------------- near misses: the gate must not over-refuse ------------

    def test_an_ordinary_packet_name_is_returned_unchanged(self):
        for name in ("p1-s1.md", "plan.v2_draft-3-s12.md", "A-s1.md"):
            with self.subTest(name=name):
                self.assertEqual(plan_artifact.packet_path(self.packets, name),
                                 os.path.join(self.packets, name))

    def test_a_name_for_a_file_that_does_not_exist_yet_is_accepted(self):
        """Packets are written AFTER the path is resolved: non-existence is
        the normal case, not a refusal."""
        target = os.path.join(self.packets, "p1-s9.md")
        self.assertFalse(os.path.exists(target))
        self.assertEqual(plan_artifact.packet_path(self.packets, "p1-s9.md"),
                         target)

    def test_a_symlinked_packets_dir_is_not_falsely_refused(self):
        """compile() derives packets_dir from the plan's path, which an
        operator may well reach through a link; BOTH sides are resolved, so an
        ordinary name inside it still passes."""
        linked = os.path.join(self.base, "packets-link")
        os.symlink(self.packets, linked)
        self.assertEqual(plan_artifact.packet_path(linked, "p1-s1.md"),
                         os.path.join(linked, "p1-s1.md"))

    def test_a_missing_packets_dir_still_resolves_an_ordinary_name(self):
        """realpath() of a path whose parent does not exist must not explode:
        compile() calls packet_path before/independently of the mkdir."""
        gone = os.path.join(self.base, "not-created-yet")
        self.assertEqual(plan_artifact.packet_path(gone, "p1-s1.md"),
                         os.path.join(gone, "p1-s1.md"))


if __name__ == "__main__":
    unittest.main()
