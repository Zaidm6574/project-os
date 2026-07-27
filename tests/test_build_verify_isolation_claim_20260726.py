"""build_verify must not claim containment it does not provide.

Security audit, 2026-07-26. build_verify EXECUTES the target project's own
build/test command with the user's full privileges. There is no sandbox. Yet
the only thing it printed about safety was::

    BUILD-VERIFY-MODE: isolated-copy

A reader of that receipt -- human or agent -- concludes the run was contained
and points the tool at a repo they do not trust. Overclaiming a security
property is worse than claiming none: it converts "I should be careful" into
"the tool handled it".

The property --isolated ACTUALLY delivers is narrower and worth keeping: the
SOURCE TREE cannot be modified by the command. This file pins both halves.

  1. The CLI's own output and --help must state the scope out loud (source
     tree only, NOT a sandbox, the project's command runs with your
     privileges) -- asserted by driving the real CLI, never by reading source.
  2. That scope statement must be TRUE in the honest direction too: a command
     run under --isolated really can write outside the copy. The disclaimer is
     not decoration.
  3. The source-tree protection is exercised, not asserted: a command that
     edits and creates files leaves the original tree byte-identical.
  4. NEAR-MISS: ordinary projects still verify green, the receipt lines keep
     their format, and legacy in-place mode still lets a command write into
     its own tree.
  5. Stragglers: a command that backgrounds a process must not leave it
     running once the verify is over (cheap real containment, not a sandbox).
"""
import hashlib
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD_VERIFY = ROOT / "addons" / "full-engine" / "memory" / "build_verify.py"

PASSING_MAKEFILE = ".PHONY: test\ntest:\n\t@echo ok\n"

# Edits an EXISTING source file and creates a new one. Both must be invisible
# in the original tree after an --isolated run.
DAMAGING_MAKEFILE = (
    ".PHONY: test\n"
    "test:\n"
    "\t@echo clobbered > src.txt\n"
    "\t@echo polluted > written-by-verifier.txt\n"
    "\t@echo ok\n"
)

# Phrases that would put back the overclaim this file exists to kill. Note
# "sandbox" itself is allowed -- the honest banner says "NOT a sandbox".
OVERCLAIM = re.compile(
    r"(?i)\b(sandboxed|fully[ -]isolated|completely isolated|"
    r"safe to run untrusted|cannot affect|contained execution|"
    r"execution is contained)\b"
)


def tree_fingerprint(root):
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = hashlib.sha256(
                p.read_bytes()).hexdigest()
    return out


def make_project(tmp, makefile):
    proj = Path(tmp) / "proj"
    proj.mkdir()
    (proj / "Makefile").write_text(makefile, encoding="utf-8")
    (proj / "src.txt").write_text("precious source\n", encoding="utf-8")
    return proj


def run_cli(*args):
    return subprocess.run(
        [sys.executable, str(BUILD_VERIFY)] + [str(a) for a in args],
        capture_output=True, text=True)


def scope_lines(stdout):
    return [ln for ln in stdout.splitlines()
            if ln.startswith("BUILD-VERIFY-SCOPE:")]


def require_make():
    if shutil.which("make") is None:
        raise unittest.SkipTest("make not installed")


class TestIsolationClaimIsHonest(unittest.TestCase):
    def test_isolated_run_states_its_real_scope_and_claims_no_sandbox(self):
        require_make()
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_project(tmp, PASSING_MAKEFILE)
            r = run_cli(proj, "--isolated", "--timeout", "60")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # Existing contract lines are untouched -- receipts stay parseable.
        self.assertIn("BUILD-VERIFY-MODE: isolated-copy", r.stdout)
        self.assertIn("SOURCE-TREE: unchanged", r.stdout)
        self.assertIn("BUILD-VERIFY: PASS", r.stdout)
        # ...and the scope of "isolated" is now stated, not left to the reader.
        scopes = scope_lines(r.stdout)
        self.assertEqual(len(scopes), 1, r.stdout)
        scope = scopes[0].lower()
        self.assertIn("not a sandbox", scope,
                      "the banner must deny the containment it lacks")
        self.assertIn("source-tree", scope,
                      "it must name the property it DOES provide")
        self.assertIn("privileges", scope,
                      "it must say the project's command runs as the user")
        self.assertNotRegex(r.stdout, OVERCLAIM)

    def test_help_text_scopes_the_isolated_flag(self):
        r = run_cli("--help")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # argparse hard-wraps, so compare on normalised whitespace.
        flat = " ".join(r.stdout.split())
        self.assertIn("--isolated", flat)
        self.assertRegex(flat, r"(?i)not a sandbox")
        self.assertRegex(flat, r"(?i)source tree")
        self.assertRegex(flat, r"(?i)privileges")
        self.assertNotRegex(flat, OVERCLAIM)

    def test_default_in_place_run_also_declares_its_scope(self):
        require_make()
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_project(tmp, PASSING_MAKEFILE)
            r = run_cli(proj, "--timeout", "60")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        scopes = scope_lines(r.stdout)
        self.assertEqual(len(scopes), 1, r.stdout)
        self.assertIn("not a sandbox", scopes[0].lower())
        self.assertNotRegex(r.stdout, OVERCLAIM)

    def test_any_mention_of_isolation_carries_the_scope_disclaimer(self):
        """Guard against the wording drifting back to a bare claim."""
        require_make()
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_project(tmp, PASSING_MAKEFILE)
            r = run_cli(proj, "--isolated", "--timeout", "60")
        if re.search(r"(?i)isolat", r.stdout):
            self.assertTrue(scope_lines(r.stdout),
                            "output says 'isolated' with no scope line: %r"
                            % r.stdout)

    def test_isolated_execution_really_is_uncontained(self):
        """The honest direction: prove the disclaimer is not decoration.

        Under --isolated the project's command still writes anywhere the user
        can. If this ever stops being true, containment became real and the
        wording should be revisited deliberately -- not left stale.
        """
        require_make()
        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp) / "outside"
            outside.mkdir()
            escapee = outside / "escaped.txt"
            proj = make_project(
                tmp,
                ".PHONY: test\ntest:\n\t@echo escaped > '%s'\n\t@echo ok\n"
                % escapee,
            )
            r = run_cli(proj, "--isolated", "--timeout", "60")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertTrue(
                escapee.exists(),
                "the copy protects the SOURCE, not the rest of the disk; the "
                "banner must keep saying so")
            self.assertIn("not a sandbox", " ".join(scope_lines(r.stdout)).lower())


class TestSourceTreeProtectionStillHolds(unittest.TestCase):
    def test_command_that_edits_the_source_leaves_the_original_untouched(self):
        require_make()
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_project(tmp, DAMAGING_MAKEFILE)
            before = tree_fingerprint(proj)
            r = run_cli(proj, "--isolated", "--timeout", "60")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(before, tree_fingerprint(proj),
                             "--isolated must leave the source byte-identical")
            self.assertEqual((proj / "src.txt").read_text(encoding="utf-8"),
                             "precious source\n")
            self.assertFalse((proj / "written-by-verifier.txt").exists())
            self.assertIn("SOURCE-TREE: unchanged", r.stdout)


class TestOrdinaryUsageStillWorks(unittest.TestCase):
    """NEAR-MISS probes: honesty must not cost anyone a working verify."""

    def test_plain_project_passes_in_both_modes_with_a_clean_receipt(self):
        require_make()
        for extra in ([], ["--isolated"]):
            with self.subTest(mode=extra or ["default"]):
                with tempfile.TemporaryDirectory() as tmp:
                    proj = make_project(tmp, PASSING_MAKEFILE)
                    r = run_cli(proj, "--timeout", "60", *extra)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                receipts = [ln for ln in r.stdout.splitlines()
                            if ln.startswith("BUILD-VERIFY: ")]
                self.assertEqual(receipts, ["BUILD-VERIFY: PASS"], r.stdout)

    def test_legacy_in_place_mode_still_lets_a_command_write_its_own_tree(self):
        require_make()
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_project(tmp, DAMAGING_MAKEFILE)
            r = run_cli(proj, "--timeout", "60")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertTrue((proj / "written-by-verifier.txt").exists(),
                            "default mode behaviour must be unchanged")

    def test_selftest_still_green(self):
        r = run_cli("--selftest")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("build_verify selftest: OK", r.stdout)


class TestStragglersAreSwept(unittest.TestCase):
    def test_backgrounded_process_does_not_outlive_a_passing_verify(self):
        require_make()
        pid = None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                proj = make_project(
                    tmp,
                    # The background process closes the inherited pipes, the
                    # way a real daemonising dev server does. Without that it
                    # would hold our stdout open and this test would "pass"
                    # merely by waiting for `sleep` to finish on its own.
                    ".PHONY: test\ntest:\n"
                    "\t@sh -c 'sleep 45 >/dev/null 2>&1 </dev/null &"
                    " echo $$! > bg.pid'\n"
                    "\t@echo ok\n",
                )
                r = run_cli(proj, "--timeout", "60")
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                raw = (proj / "bg.pid").read_text(encoding="utf-8").strip()
            pid = int(raw)
            self.assertTrue(
                self._gone(pid),
                "a verify command that backgrounds a process must not leave "
                "it running with the user's privileges after we report a "
                "result (pid %s)" % pid)
        finally:
            if pid:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass

    @staticmethod
    def _gone(pid, timeout=5.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True
            except PermissionError:
                return False
            time.sleep(0.05)
        return False


if __name__ == "__main__":
    unittest.main()
