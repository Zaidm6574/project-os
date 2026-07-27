#!/usr/bin/env python3
"""The compiled packet must hand the checker its verification criteria.

Found by a cold-clone reviewer, 2026-07-27. `plan_artifact` makes declaring
verification a hard gate: validate() refuses a multi-step plan unless some
work-dependent checker carries `verification` with a nonempty, non-placeholder
`method` AND `expected` (plan_artifact.py:732-736, and `-`/`n/a`/`todo`/`tbd`
are all rejected by _real_verification_value). That gate is the centerpiece the
README leads with.

Then `compile` interpolated role, task, depends_on and outputs into the packet
body and never `s.get("verification")` -- so a plan declaring

    method:   "curl -X POST /api/book and assert 200"
    expected: "HTTP 200 and a row in the bookings table"

compiled to a packet whose entire instruction to the checker was
"Task: check the form submits", with zero criteria. The reviewer's words: the
mechanism collects the answer and throws it away at the exact moment it would
do work.

That is a fail-open gate, which is the shape this repo audits for: it looks
enforced (the plan is refused without it) while the artifact a worker actually
executes never carries it. The strings are collected, validated, stored in the
plan JSON, and dropped on the floor at the only point they could act.

Both directions are pinned here:
  - a checker's declared method/expected reach the packet body verbatim
  - a non-checker step does NOT grow an empty "Verification method: (none
    declared)" line, which would be noise on every builder packet
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

METHOD = "curl -X POST /api/book and assert 200"
EXPECTED = "HTTP 200 and a row in the bookings table"

STEPS = [
    {
        "id": "s1",
        "role": "builder",
        "task": "build the booking form",
        "depends_on": [],
        "outputs": ["src/booking.py"],
    },
    {
        "id": "s2",
        "role": "checker",
        "task": "check the form submits",
        "depends_on": ["s1"],
        "verification": {"method": METHOD, "expected": EXPECTED},
    },
]


class PacketCarriesVerification(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        # plan_artifact derives its ROOT from its OWN __file__, not from cwd, so
        # running the checked-out copy writes into the real blackboard/ no matter
        # what cwd is set to -- the first draft of this test did exactly that.
        # Copy scripts/ into the sandbox so ROOT resolves inside it.
        shutil.copytree(ROOT / "scripts", self.work / "scripts")
        self.plan_artifact = self.work / "scripts" / "plan_artifact.py"
        # A fake HOME so nothing reaches the operator's real ~/.project-os.
        self.home = self.work / "home"
        self.home.mkdir()
        self.blackboard = self.work / "blackboard"
        (self.blackboard / "plans").mkdir(parents=True)
        (self.blackboard / "packets").mkdir(parents=True)

        self.steps_file = self.work / "steps.json"
        self.steps_file.write_text(json.dumps(STEPS), encoding="utf-8")

    def _run(self, *args):
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        return subprocess.run(
            [sys.executable, str(self.plan_artifact), *args],
            capture_output=True, text=True, cwd=str(self.work), env=env)

    def _compile_plan(self):
        """Drive the documented lifecycle: create -> approve -> compile."""
        created = self._run("create", "--goal", "ship a booking form",
                            "--steps-file", str(self.steps_file))
        self.assertEqual(created.returncode, 0,
                         "create failed:\n%s\n%s" % (created.stdout, created.stderr))
        plans = sorted((self.blackboard / "plans").glob("*.json"))
        self.assertEqual(len(plans), 1, "expected exactly one plan artifact, got %s" % plans)
        plan_path = plans[0]
        plan_id = json.loads(plan_path.read_text(encoding="utf-8"))["id"]

        approved = self._run("approve", str(plan_path))
        self.assertEqual(approved.returncode, 0,
                         "approve failed:\n%s\n%s" % (approved.stdout, approved.stderr))
        compiled = self._run("compile", str(plan_path))
        self.assertEqual(compiled.returncode, 0,
                         "compile failed:\n%s\n%s" % (compiled.stdout, compiled.stderr))

        packets = {p.name: p.read_text(encoding="utf-8")
                   for p in (self.blackboard / "packets").glob("*.md")}
        self.assertTrue(packets, "compile wrote no packets")
        return plan_id, packets

    def _checker_packet(self, packets):
        matches = [body for name, body in packets.items() if name.endswith("-s2.md")]
        self.assertEqual(len(matches), 1,
                         "expected one checker packet, got %s" % sorted(packets))
        return matches[0]

    def test_the_checker_packet_states_how_to_verify(self):
        """The declared method reaches the document the checker executes."""
        _, packets = self._compile_plan()
        body = self._checker_packet(packets)
        self.assertIn(
            METHOD, body,
            "the checker packet never tells the agent HOW to verify -- the plan "
            "declared method=%r and the gate REFUSED the plan without it, but "
            "compile dropped it. Packet body was:\n%s" % (METHOD, body))

    def test_the_checker_packet_states_what_a_pass_looks_like(self):
        """The declared pass criterion reaches the packet too."""
        _, packets = self._compile_plan()
        body = self._checker_packet(packets)
        self.assertIn(
            EXPECTED, body,
            "the checker packet never tells the agent what a PASS looks like -- "
            "the plan declared expected=%r. Packet body was:\n%s" % (EXPECTED, body))

    def test_a_step_without_verification_stays_uncluttered(self):
        """The mirror direction: builder packets do not grow empty criteria lines.

        Without this, the obvious fix (interpolate unconditionally) puts
        "Verification method: (none declared)" on every builder packet in every
        plan -- noise that trains readers to skip the line that matters.
        """
        _, packets = self._compile_plan()
        builder = [body for name, body in packets.items() if name.endswith("-s1.md")]
        self.assertEqual(len(builder), 1)
        self.assertNotIn(
            "Verification method:", builder[0],
            "a builder step with no declared verification grew a criteria line:\n%s"
            % builder[0])


if __name__ == "__main__":
    unittest.main()
