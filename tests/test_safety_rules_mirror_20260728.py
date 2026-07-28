#!/usr/bin/env python3
"""AGENTS.md and CLAUDE.md must carry the same Non-Negotiable Safety Rules.

Both files SAY they are mirrored. AGENTS.md:5 calls it "the one deliberate
exception" to single-source doctrine and instructs "if you edit those rules,
update both blocks in the same commit"; CLAUDE.md repeats the promise above its
own copy. Nothing enforced it.

They were byte-identical when this test was written (4 bullets, verified), so
this pins a property that currently holds rather than fixing a live defect. It
is worth having anyway: the block contains "never git push without explicit
approval" and "never include personal/local tooling in template commits" -- the
two rules whose violation is least recoverable -- and CLAUDE.md is the file that
gets auto-loaded, so a drifted copy there is the one an assistant would actually
obey. This is exactly the two-copies-one-guard shape that has already produced
real defects in this repo: a write-path guard applied to one of two installers,
and a slash-command instruction fixed in install.sh but not in the README.

Deliberately compares BULLETS, not the whole section: the two files legitimately
differ in their surrounding prose (AGENTS.md explains the mirror, CLAUDE.md
explains why it is duplicated). Comparing whole sections would fail for a
formatting change and teach people to weaken the test.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEADING = "## Non-Negotiable Safety Rules"


def bullets(path):
    """Top-level '- ' bullets under the safety heading, in order."""
    out, inside = [], False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(HEADING):
            inside = True
            continue
        if inside and line.startswith("## "):
            break
        if inside and line.startswith("- "):
            out.append(line.strip())
    return out


class SafetyRulesStayMirrored(unittest.TestCase):
    def setUp(self):
        self.agents = ROOT / "AGENTS.md"
        self.claude = ROOT / "CLAUDE.md"

    def test_both_files_have_the_block(self):
        for p in (self.agents, self.claude):
            self.assertIn(
                HEADING, p.read_text(encoding="utf-8"),
                "%s no longer has a %r section; the mirror promise in AGENTS.md:5 "
                "is now false" % (p.name, HEADING))

    def test_the_block_is_not_empty(self):
        """Guards the guard: two empty lists compare equal."""
        a = bullets(self.agents)
        self.assertGreaterEqual(
            len(a), 3,
            "AGENTS.md's safety block parsed as %d bullet(s). Either the rules were "
            "deleted or the heading/format changed and this test is now blind -- "
            "fix the parser, do not lower this bound." % len(a))

    def test_agents_and_claude_carry_identical_rules(self):
        a, c = bullets(self.agents), bullets(self.claude)
        if a == c:
            return
        only_a = [x for x in a if x not in c]
        only_c = [x for x in c if x not in a]
        msg = ["AGENTS.md and CLAUDE.md's safety rules have drifted. Both files "
               "state they are mirrored verbatim and must be edited in the same "
               "commit; CLAUDE.md is the auto-loaded one, so its copy is what an "
               "assistant actually obeys.",
               "  AGENTS.md has %d bullet(s), CLAUDE.md has %d." % (len(a), len(c))]
        for x in only_a:
            msg.append("  ONLY IN AGENTS.md: %s" % x[:160])
        for x in only_c:
            msg.append("  ONLY IN CLAUDE.md: %s" % x[:160])
        if not only_a and not only_c:
            msg.append("  Same rules, different ORDER -- mirror them exactly.")
        self.fail("\n".join(msg))


if __name__ == "__main__":
    unittest.main()
