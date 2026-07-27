"""The Full Swarm 'Approved packet' gate must be MECHANICAL, not just doctrine.

Two shipped documents state the gate as a hard rule:

  addons/full-engine/staged/agents/project-os-ceo.md:42
    "A Full Swarm wave does **not** advance until **at least one packet for
     that wave is marked `Status: Approved`** in `packets/`. An empty
     `packets/` dir or only `Draft`/`Rejected` packets means the wave is
     **not** done"

  addons/full-engine/memory/new_run.py (SOLO_PACKETS_WAIVER)
    "...(and Full Swarm must have >=1 Approved packet before a wave advances)."

`validate_run.py` never parsed the `Status:` field the packet schema defines
(blackboard-template/packets/README.md:14 -> `Status: Draft / Rejected /
Approved`). `_has_packets()` returned True for ANY file under `packets/`, so a
Full Swarm run whose only packet was explicitly `Status: Rejected` closed with
`VALIDATE: PASS` -- the run-closure validator asserted a gate that no code
enforced (audit 2026-07-27).

This is the same defect class this module has already been hardened against
twice: `_tier_locked` used to scan for the words "locked" and "tier" anywhere,
and `_has_manifest` used to accept any file containing the substring
"manifest". Both were vocabulary matches standing in for a real field read.
The Approved-packet gate was the last one left unhardened.
"""
import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATE_RUN = ROOT / "addons" / "full-engine" / "memory" / "validate_run.py"
CEO_AGENT = ROOT / "addons" / "full-engine" / "staged" / "agents" / "project-os-ceo.md"
NEW_RUN = ROOT / "addons" / "full-engine" / "memory" / "new_run.py"


def load_validator(name="validate_run_approved_gate"):
    spec = importlib.util.spec_from_file_location(name, VALIDATE_RUN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FULL_SWARM_GOAL = (
    "## Definition of Done\n- [x] Ship it\n\n"
    "## Execution Level\n```text\nTier: Full Swarm\nLocked: yes\n```\n"
)
SOLO_GOAL = (
    "## Definition of Done\n- [x] Ship it\n\n"
    "## Execution Level\n```text\nTier: Solo Agent Loop\nLocked: yes\n```\n"
)
MINI_GOAL = (
    "## Definition of Done\n- [x] Ship it\n\n"
    "## Execution Level\n```text\nTier: Mini Swarm\nLocked: yes\n```\n"
)


class ApprovedPacketGateTests(unittest.TestCase):
    def setUp(self):
        self.validator = load_validator()
        self.tmp = tempfile.TemporaryDirectory()
        self.run = Path(self.tmp.name) / "project" / "runs" / "wave1"
        self.packets = self.run / "packets"
        self.packets.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _goal(self, text):
        (self.run / "00-project-goal.md").write_text(text, encoding="utf-8")

    def _packet(self, name, text):
        (self.packets / name).write_text(text, encoding="utf-8")

    # ---- the defect ---------------------------------------------------
    def test_full_swarm_run_with_only_rejected_packets_cannot_close(self):
        """The exact wording of the doctrine: only Rejected => not done."""
        self._goal(FULL_SWARM_GOAL)
        self._packet(
            "3-builder-001.md",
            "Packet ID: 3-builder-001\nAgent: builder\nTask: ship it\n"
            "Status: Rejected\n",
        )
        self.assertFalse(
            self.validator._has_packets(str(self.run)),
            "a Full Swarm run whose only packet was explicitly Status: Rejected "
            "satisfied the closure gate that doctrine says it must fail",
        )

    def test_full_swarm_run_with_only_draft_packets_cannot_close(self):
        self._goal(FULL_SWARM_GOAL)
        self._packet("3-builder-001.md", "Packet ID: 3-builder-001\nStatus: Draft\n")
        self.assertFalse(
            self.validator._has_packets(str(self.run)),
            "only Draft packets satisfied the Full Swarm Approved-packet gate",
        )

    def test_full_swarm_packet_with_no_status_field_cannot_close(self):
        """An unscored packet is not an approval. Absent field != Approved."""
        self._goal(FULL_SWARM_GOAL)
        self._packet("3-builder-001.md", "Packet ID: 3-builder-001\nAgent: builder\n")
        self.assertFalse(
            self.validator._has_packets(str(self.run)),
            "a packet with no Status: field at all cleared the Approved gate",
        )

    def test_full_swarm_empty_packets_dir_cannot_close(self):
        self._goal(FULL_SWARM_GOAL)
        self.assertFalse(
            self.validator._has_packets(str(self.run)),
            "an empty packets/ dir cleared the Full Swarm gate",
        )

    # ---- near-miss bypasses (this repo's signature failure mode) -------
    def test_the_word_approved_in_prose_is_not_an_approval(self):
        """`_tier_locked` and `_has_manifest` were both broken by exactly this:
        a vocabulary match standing in for reading the field."""
        self._goal(FULL_SWARM_GOAL)
        for prose in (
            "Packet ID: p1\nRecommended Next Step: get this approved by the user.\n"
            "Status: Draft\n",
            "Packet ID: p1\nConclusion: Approved\nStatus: Rejected\n",
            "Packet ID: p1\nEvidence: the plan was approved in 03-decisions.md\n",
        ):
            with self.subTest(prose=prose.splitlines()[1]):
                self._packet("3-builder-001.md", prose)
                self.assertFalse(
                    self.validator._has_packets(str(self.run)),
                    "the word 'approved' in prose cleared an evaluator gate",
                )

    def test_the_packet_schema_line_itself_is_not_an_approval(self):
        """`Status: Draft / Rejected / Approved` is the TEMPLATE line from
        blackboard-template/packets/README.md. Copying it is not a decision."""
        self._goal(FULL_SWARM_GOAL)
        self._packet("3-builder-001.md", "Packet ID: p1\nStatus: Draft / Rejected / Approved\n")
        self.assertFalse(
            self.validator._has_packets(str(self.run)),
            "the unfilled schema line counted as an approval",
        )

    def test_a_packet_downgraded_later_in_the_same_file_is_not_approved(self):
        """Fail closed, the way `_tier_locked` had to after the round-2 skeptic:
        do not let the first matching field win."""
        self._goal(FULL_SWARM_GOAL)
        self._packet(
            "3-builder-001.md",
            "Packet ID: p1\nStatus: Approved\n\n"
            "## Evaluator re-review\nStatus: Rejected\n",
        )
        self.assertFalse(
            self.validator._has_packets(str(self.run)),
            "a packet later marked Rejected still counted as Approved "
            "(first-match-wins)",
        )

    def test_full_swarm_cannot_close_on_the_solo_no_packets_waiver(self):
        """The waiver is the obvious way out of the gate, so it must not work
        for the tier the gate exists for."""
        self._goal(FULL_SWARM_GOAL)
        (self.run / "13-delivery-report.md").write_text(
            "no-packets: solo run\n", encoding="utf-8"
        )
        self.assertFalse(
            self.validator._has_packets(str(self.run)),
            "a Full Swarm run closed by dropping the solo-run waiver into a "
            "delivery report",
        )

    def test_readme_in_packets_dir_is_not_an_approved_packet(self):
        self._goal(FULL_SWARM_GOAL)
        self._packet("README.md", "Status: Approved\n")
        self.assertFalse(
            self.validator._has_packets(str(self.run)),
            "the packets/ README counted as an Approved packet",
        )

    # ---- the gate still opens for real approvals ----------------------
    def test_one_approved_packet_closes_the_full_swarm_gate(self):
        self._goal(FULL_SWARM_GOAL)
        self._packet("3-builder-001.md", "Packet ID: p1\nStatus: Rejected\n")
        self._packet("4-evaluator-002.md", "Packet ID: p2\nStatus: Approved\n")
        self.assertTrue(
            self.validator._has_packets(str(self.run)),
            "a real Approved packet was refused",
        )

    def test_markdown_decorated_status_field_is_read(self):
        """Packets in the wild are markdown: `- **Status:** Approved`."""
        self._goal(FULL_SWARM_GOAL)
        for line in ("**Status:** Approved", "- Status: approved", "* **Status**: Approved"):
            with self.subTest(line=line):
                self._packet("3-builder-001.md", "Packet ID: p1\n%s\n" % line)
                self.assertTrue(
                    self.validator._has_packets(str(self.run)),
                    "a decorated but valid Status field was not read",
                )

    # ---- no other tier gets stricter (this must not weaken or widen) --
    def test_solo_waiver_still_closes_a_solo_run(self):
        self._goal(SOLO_GOAL)
        (self.run / "13-delivery-report.md").write_text(
            "no-packets: solo run\n", encoding="utf-8"
        )
        self.assertTrue(
            self.validator._has_packets(str(self.run)),
            "the solo-run waiver stopped working",
        )

    def test_mini_swarm_draft_packet_still_closes(self):
        """Doctrine scopes the Approved requirement to Full Swarm only."""
        self._goal(MINI_GOAL)
        self._packet("3-builder-001.md", "Packet ID: p1\nStatus: Draft\n")
        self.assertTrue(
            self.validator._has_packets(str(self.run)),
            "the Full Swarm gate leaked onto Mini Swarm runs",
        )

    def test_run_with_no_goal_file_keeps_the_old_behaviour(self):
        self._packet("3-builder-001.md", "Packet ID: p1\n")
        self.assertTrue(
            self.validator._has_packets(str(self.run)),
            "a run with no goal doc got the Full Swarm gate applied to it",
        )


class ValidateEndToEndTests(unittest.TestCase):
    """Drive validate() itself, not just the helper -- the helper could be
    correct while the check row still reports the old answer."""

    def setUp(self):
        self.validator = load_validator("validate_run_approved_gate_e2e")
        self.tmp = tempfile.TemporaryDirectory()
        self.run = Path(self.tmp.name) / "project" / "runs" / "wave1"
        self.run.mkdir(parents=True)
        self.validator._good_run(str(self.run))
        (self.run / "00-project-goal.md").write_text(FULL_SWARM_GOAL, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_full_swarm_run_with_a_rejected_packet_fails_validation(self):
        (self.run / "packets" / "3-builder-001.md").write_text(
            "Packet ID: 3-builder-001\nStatus: Rejected\n", encoding="utf-8"
        )
        self.assertFalse(
            self.validator.validate(str(self.run)),
            "VALIDATE: PASS on a Full Swarm run with no Approved packet",
        )

    def test_full_swarm_run_with_an_approved_packet_passes_validation(self):
        (self.run / "packets" / "3-builder-001.md").write_text(
            "Packet ID: 3-builder-001\nStatus: Approved\n", encoding="utf-8"
        )
        self.assertTrue(
            self.validator.validate(str(self.run)),
            "an otherwise-good Full Swarm run with an Approved packet was "
            "refused",
        )

    def test_module_selftest_still_passes(self):
        """End-to-end control: the module's own good/bad fixtures must survive
        the new gate."""
        self.assertEqual(self.validator.selftest(), 0)


class DoctrineMatchesTheCodeTests(unittest.TestCase):
    """The gate exists because two shipped docs promise it. If the wording
    moves, this test points at the code that has to move with it."""

    def test_ceo_agent_still_states_the_full_swarm_approved_gate(self):
        text = CEO_AGENT.read_text(encoding="utf-8")
        self.assertIn("Status: Approved", text)
        self.assertIn("Full Swarm wave gate", text)

    def test_solo_waiver_text_still_claims_the_full_swarm_gate(self):
        text = NEW_RUN.read_text(encoding="utf-8")
        self.assertIn(
            "Full Swarm must have >=1 Approved packet before a ", text,
            "new_run.py's solo waiver no longer states the gate that "
            "validate_run.py now enforces",
        )


if __name__ == "__main__":
    unittest.main()
