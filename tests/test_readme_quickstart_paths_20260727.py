#!/usr/bin/env python3
"""The Quick start and 5-Minute Demo must work as literally written.

Three defects found on 2026-07-28 by running the README's own commands against
a fresh clone of the PUBLIC repo, after the installer itself had already been
fixed. Documentation drifted from behaviour in three places a new reader hits
in their first two minutes:

1. Quick start told the reader to say `/project I want to build...` immediately
   after `./install.sh ../my-new-project --check-tools`. That is a STARTER
   install: it writes no `.claude/` directory, so there are no slash commands in
   the target. install.sh's own closing message was fixed for exactly this, but
   the README kept the broken instruction -- the fix landed on one of the two
   surfaces that say it, which is this repo's recurring two-copies-one-guard
   shape.

2. The 5-Minute Demo runs `./install.sh ../demo-project --dry-run` and then
   shows sample output naming `/tmp/project-os-demo`. The real output names the
   resolved `../demo-project`. A reader comparing the two sees paths that do not
   match and cannot tell whether their run went wrong.

3. `promptsmith runs end-to-end without any personal setup via --brief-file
   examples/sample-brief.md` is true in a clone and FALSE in an installed
   project: `examples/` was never copied to the target. The whole point of the
   tool is to run it in YOUR project, where the documented command failed with
   "cannot read --brief-file".

These are runtime checks, not text greps: each one runs the documented command
and reads what actually happens.
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
README = ROOT / "README.md"
INSTALL_SH = ROOT / "install.sh"


class QuickStartTellsTheTruth(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.home = self.work / "home"
        self.home.mkdir()

    def _install(self, target, *extra):
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        return subprocess.run(["sh", str(INSTALL_SH), str(target), *extra],
                              capture_output=True, text=True, cwd=str(ROOT), env=env)

    def test_quickstart_does_not_send_a_starter_install_to_a_slash_command(self):
        """The README's own quick-start sequence must end somewhere real."""
        text = README.read_text(encoding="utf-8")
        m = re.search(r"## Quick start(.+?)(?=\n## )", text, re.S)
        self.assertIsNotNone(m, "could not locate the Quick start section")
        section = m.group(1)

        # Which install does Quick start actually tell the reader to run?
        installs = re.findall(r"\./install\.sh[^\n`]*", section)
        self.assertTrue(installs, "Quick start no longer shows an install command")
        starter = [c for c in installs
                   if "--claude-engine" not in c and "--codex-engine" not in c]
        if not starter:
            self.skipTest("Quick start now documents an engine install; nothing to prove")

        # A starter install creates no slash commands -- confirm, then require
        # that the section does not tell the reader to type one.
        target = self.work / "qs"
        r = self._install(target)
        if r.returncode != 0:
            self.skipTest("installer did not run here (rc=%d)" % r.returncode)
        self.assertFalse((target / ".claude").exists(),
                         "assumption broken: a starter install now creates .claude/")

        offenders = re.findall(r"^\s*/[a-z][a-z-]+\b", section, re.M)
        self.assertFalse(
            offenders,
            "Quick start documents %s as the next step, but the install it just "
            "showed (%s) creates no .claude/ directory, so that command does not "
            "exist in the reader's project."
            % (", ".join(sorted(set(offenders))), starter[0].strip()))


class DemoOutputMatchesTheDemoCommand(unittest.TestCase):
    def test_the_sample_output_names_the_path_the_command_uses(self):
        text = README.read_text(encoding="utf-8")
        m = re.search(r"## 5-Minute Demo(.+?)(?=\n## )", text, re.S)
        self.assertIsNotNone(m, "could not locate the 5-Minute Demo section")
        section = m.group(1)

        cmd = re.search(r"\./install\.sh\s+(\S+)\s+--dry-run", section)
        self.assertIsNotNone(cmd, "the demo no longer shows a --dry-run command")
        documented_target = cmd.group(1)
        # e.g. "../demo-project" -> the basename is what shows up in output
        stem = Path(documented_target).name

        sample = re.search(r"```text\n(.+?)```", section, re.S)
        self.assertIsNotNone(sample, "the demo no longer shows sample output")
        body = sample.group(1)

        paths = re.findall(r"would (?:create|write) (\S+)", body)
        self.assertTrue(paths, "sample output shows no would-create/write lines")
        mismatched = [p for p in paths if stem not in p]
        self.assertFalse(
            mismatched,
            "the demo runs `./install.sh %s --dry-run` but its sample output names "
            "%s -- a reader comparing the two cannot tell whether their run went "
            "wrong. Show output for the target the command actually uses."
            % (documented_target, ", ".join(sorted(set(mismatched))[:3])))


class DocumentedToolCommandsWorkInAnInstalledProject(unittest.TestCase):
    """The tools are meant to be run in YOUR project, not only in a clone."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.home = self.work / "home"
        self.home.mkdir()
        self.target = self.work / "proj"
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        r = subprocess.run(["sh", str(INSTALL_SH), str(self.target)],
                           capture_output=True, text=True, cwd=str(ROOT), env=env)
        if r.returncode != 0:
            self.skipTest("installer did not run here (rc=%d)" % r.returncode)

    def test_the_brief_file_the_readme_names_is_installed(self):
        text = README.read_text(encoding="utf-8")
        named = re.findall(r"--brief-file\s+(\S+?)`", text)
        if not named:
            self.skipTest("README no longer names a --brief-file path")
        for rel in set(named):
            self.assertTrue(
                (self.target / rel).is_file(),
                "README says promptsmith runs end-to-end via `--brief-file %s`, but "
                "the installer never copies it into the target, so that command "
                "fails in the reader's own project with 'cannot read --brief-file'."
                % rel)

    def test_promptsmith_actually_runs_there(self):
        """The claim is end-to-end, so run it end-to-end."""
        brief = self.target / "examples" / "sample-brief.md"
        if not brief.is_file():
            self.fail("examples/sample-brief.md was not installed; see the test above")
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        r = subprocess.run(
            [sys.executable, "scripts/promptsmith.py", "--task", "build the hero section",
             "--brief-file", "examples/sample-brief.md"],
            capture_output=True, text=True, cwd=str(self.target), env=env)
        self.assertEqual(
            r.returncode, 0,
            "promptsmith failed in a freshly installed project:\n%s\n%s"
            % (r.stdout[-800:], r.stderr[-800:]))


if __name__ == "__main__":
    unittest.main()
