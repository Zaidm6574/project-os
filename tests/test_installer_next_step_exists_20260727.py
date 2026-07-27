#!/usr/bin/env python3
"""The installer must not send a new user to a command that was never installed.

Found by cold-clone reviewers on 2026-07-27, and it is the very first thing a
stranger is told to do. Both installers close by printing:

    Open the target project in Codex, Claude, or your AI coding tool, then say:
      /project <your idea>

and a plain `./install.sh <target>` writes no `.claude/` directory at all, so
there are zero slash commands in the target. Even `--full-engine --claude-engine`
(a flag the README never mentions) shipped eleven commands and no `project.md`.
So `/project` was not a command in ANY configuration, while `CLAUDE.md:20` listed
it among the installed slash commands.

Two things are pinned here, because fixing only one leaves the break:

  1. `/project` EXISTS as a real workflow, canonical and generated, so the
     instruction is true wherever slash commands are installed. It is generated
     from prompts/workflows/ like every sibling -- a hand-written staged command
     would drift the moment sync_runtime_assets.py next runs.

  2. The closing message MATCHES WHAT WAS ACTUALLY INSTALLED. A starter install
     has no slash commands, so it must not present one as the next action; it
     must instead say what genuinely works there and how to get the commands.

The second is the one that actually protects the reader: a `project.md` that
exists in the repo still is not in a target that never received a `.claude/`
directory, and only the message can tell those two situations apart.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANON = ROOT / "prompts" / "workflows"
STAGED = ROOT / "addons" / "full-engine" / "staged" / "commands"
INSTALL_SH = ROOT / "install.sh"
SETUP = ROOT / "scripts" / "setup_project_os.py"

# A bare slash command at the start of a line, or after "say:" -- the shape that
# tells a reader "type this". Matching loosely on purpose: the point is to catch
# the instruction however it is phrased.
SLASH_INSTRUCTION = re.compile(r"^\s*/[a-z][a-z-]+\b", re.M)


class ProjectWorkflowExists(unittest.TestCase):
    def test_project_has_a_canonical_workflow(self):
        self.assertTrue(
            (CANON / "project.md").is_file(),
            "prompts/workflows/project.md does not exist, so `/project` -- the command "
            "both installers tell a new user to type -- is not a workflow at all.")

    def test_project_is_generated_into_the_claude_commands(self):
        staged = STAGED / "project.md"
        self.assertTrue(
            staged.is_file(),
            "addons/full-engine/staged/commands/project.md is missing, so "
            "`--claude-engine` installs every command EXCEPT the one the installer "
            "tells you to run.")
        body = staged.read_text(encoding="utf-8")
        self.assertIn(
            "GENERATED from prompts/workflows/project.md", body,
            "the staged command is hand-written rather than generated, so the next "
            "sync_runtime_assets.py run will overwrite or orphan it.")

    def test_the_generated_command_is_in_sync_with_its_canonical_source(self):
        """Regenerating must be a no-op -- otherwise the two silently drift."""
        sync = ROOT / "scripts" / "sync_runtime_assets.py"
        if not sync.is_file():
            self.skipTest("sync_runtime_assets.py not present")
        before = (STAGED / "project.md").read_text(encoding="utf-8")
        r = subprocess.run([sys.executable, str(sync), "--check"],
                           capture_output=True, text=True, cwd=str(ROOT))
        if r.returncode not in (0, 2):
            self.skipTest("sync --check unsupported here (rc=%d)" % r.returncode)
        self.assertEqual(
            r.returncode, 0,
            "sync_runtime_assets.py --check reports the generated commands are stale:\n"
            "%s\n%s" % (r.stdout, r.stderr))
        self.assertEqual(before, (STAGED / "project.md").read_text(encoding="utf-8"),
                         "--check modified the file; it should be read-only")


class ClosingMessageMatchesWhatWasInstalled(unittest.TestCase):
    """A starter install has no .claude/, so it must not advertise a slash command.

    These RUN the installer and read what it printed. An earlier draft of this
    file grepped install.sh's source instead, and passed against the unfixed
    code -- the offending text lives inside a `printf '%s\\n' "  /project ..."`
    so it never appears at the start of a source line. That is precisely the
    source-text assertion this repo keeps shipping by accident: satisfiable by a
    comment, blind to behaviour.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.home = self.work / "home"
        self.home.mkdir()

    def _install(self, *extra):
        target = self.work / ("t%d" % len(list(self.work.iterdir())))
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        r = subprocess.run(["sh", str(INSTALL_SH), str(target), *extra],
                           capture_output=True, text=True, cwd=str(ROOT), env=env)
        return r, target

    def test_a_starter_install_does_not_tell_you_to_type_a_slash_command(self):
        r, target = self._install()
        if r.returncode != 0:
            self.skipTest("installer did not run here (rc=%d): %s"
                          % (r.returncode, r.stderr[:400]))
        self.assertFalse(
            (target / ".claude").exists(),
            "assumption broken: a starter install now creates .claude/; update this test")
        offenders = [m.group(0).strip()
                     for m in SLASH_INSTRUCTION.finditer(r.stdout + r.stderr)]
        self.assertFalse(
            offenders,
            "the installer told the user to type %s, but it created no .claude/ "
            "directory, so that command does not exist in the target. Say what works "
            "for the install that actually ran, and name the flag that installs the "
            "commands.\n--- installer output ---\n%s"
            % (", ".join(offenders), r.stdout[-1500:]))

    def test_the_closing_message_still_tells_the_user_what_to_do(self):
        """The mirror direction: do not fix this by deleting the guidance."""
        r, _ = self._install()
        if r.returncode != 0:
            self.skipTest("installer did not run here (rc=%d)" % r.returncode)
        self.assertRegex(
            r.stdout, r"(?i)claude-engine|codex-engine|AGENTS\.md|CLAUDE\.md|describe",
            "the closing message no longer points the user anywhere at all -- "
            "removing the broken instruction without replacing it is not a fix:\n%s"
            % r.stdout[-1500:])


if __name__ == "__main__":
    unittest.main()
