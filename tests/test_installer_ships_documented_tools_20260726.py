"""Every `python3 memory/<tool>.py` the docs tell a user to run must ship.

AGENTS.md is copied verbatim into every installed project, so a command it
names is a promise to a stranger. `memory/context_budget.py` was added to this
repo along with an AGENTS.md rule making it the FIRST step of a run -- "Kickoff
preflight: run `python3 memory/context_budget.py`" -- but the installer's copy
list was never updated, so that first documented step pointed at a nonexistent
file in every install (pre-push audit 2026-07-26).

The requirement is DERIVED from AGENTS.md rather than restated here: adding a
new documented tool without shipping it fails this test, and so does deleting a
tool the docs still reference.
"""

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "AGENTS.md"
SETUP = ROOT / "scripts" / "setup_project_os.py"
FULL_ENGINE = ROOT / "scripts" / "install_full_engine.py"


def documented_memory_tools():
    """Tool names in any `python3 memory/<name>.py` command in AGENTS.md."""
    text = AGENTS.read_text(encoding="utf-8")
    return sorted(set(re.findall(r"python3\s+memory/([A-Za-z0-9_]+)\.py", text)))


class InstallerShipsWhatTheDocsPromise(unittest.TestCase):
    def test_agents_md_names_at_least_one_memory_tool(self):
        """Guards the guard: a regex that matches nothing proves nothing."""
        self.assertTrue(
            documented_memory_tools(),
            "no `python3 memory/*.py` commands found in AGENTS.md -- the "
            "extraction broke, so this whole module is vacuous",
        )

    def test_every_documented_memory_tool_exists_somewhere_in_this_repo(self):
        """A documented tool may live in core memory/ or in the addon.

        Both land at `memory/<tool>.py` in an installed project; which source
        tree they come from is an internal detail.
        """
        for tool in documented_memory_tools():
            with self.subTest(tool=tool):
                self.assertTrue(
                    (ROOT / "memory" / f"{tool}.py").is_file()
                    or (ROOT / "addons" / "full-engine" / "memory"
                        / f"{tool}.py").is_file(),
                    "AGENTS.md tells the user to run memory/%s.py, which "
                    "exists in neither memory/ nor addons/full-engine/memory/"
                    % tool,
                )

    def test_a_full_install_delivers_every_documented_memory_tool(self):
        """The end-to-end check: install, then look for the files.

        Asserting on the installer's source tuple would pass while the copy
        silently failed, so this runs the real installers and inspects the
        target. The full engine is what AGENTS.md's commands assume, so both
        stages run -- the core installer alone is not the documented setup.
        """
        tools = documented_memory_tools()
        with tempfile.TemporaryDirectory() as tmp:
            for script in (SETUP, FULL_ENGINE):
                done = subprocess.run(
                    [sys.executable, str(script), "--target", tmp],
                    capture_output=True, text=True, timeout=300,
                )
                self.assertEqual(
                    done.returncode, 0,
                    "%s failed:\n%s" % (script.name, done.stderr))
            for tool in tools:
                with self.subTest(tool=tool):
                    self.assertTrue(
                        os.path.isfile(os.path.join(tmp, "memory", f"{tool}.py")),
                        "AGENTS.md ships into every project and tells the user "
                        "to run `python3 memory/%s.py`, but a full install does "
                        "not deliver it" % tool,
                    )


if __name__ == "__main__":
    unittest.main()
