"""build_verify must not lie about WHY it refused.

Open-findings pass, 2026-07-26. Two defects in one place:

1. A project with package.json but no npm on PATH was reported as
   "no detectable stack: package.json/pyproject/Makefile". The stack was
   detected; the toolchain was missing. The message sent readers hunting for a
   project-layout problem that does not exist.
2. That same early return meant a polyglot repo (package.json PLUS a working
   Makefile/pyproject) was refused outright even though an installed toolchain
   could have verified it.

Fail-closed behavior (exit 1 when nothing is runnable) must survive both fixes.
"""
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD_VERIFY = ROOT / "addons" / "full-engine" / "memory" / "build_verify.py"
sys.path.insert(0, str(BUILD_VERIFY.parent))

import build_verify  # noqa: E402

PASSING_MAKEFILE = ".PHONY: test\ntest:\n\t@exit 0\n"


@contextlib.contextmanager
def path_containing(real=(), fake=()):
    """Run with PATH reduced to a temp bin dir holding only the named tools.

    `real` tools are symlinked from the ambient PATH; `fake` tools become stub
    executables that succeed. Anything not listed (npm, pytest, ...) is
    genuinely absent from PATH, so shutil.which is exercised for real instead
    of being mocked.
    """
    original = os.environ.get("PATH", "")
    with tempfile.TemporaryDirectory(prefix="bv-bin-") as bindir:
        for tool in real:
            found = shutil.which(tool, path=original)
            if found is None:
                raise unittest.SkipTest("required tool not installed: %s" % tool)
            os.symlink(found, os.path.join(bindir, tool))
        for tool in fake:
            stub = os.path.join(bindir, tool)
            with open(stub, "w", encoding="utf-8") as fh:
                fh.write("#!/bin/sh\nexit 0\n")
            os.chmod(stub, 0o755)
        os.environ["PATH"] = bindir
        try:
            yield bindir
        finally:
            os.environ["PATH"] = original


def run_verify(project):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = build_verify.verify(str(project), timeout=60)
    return rc, buf.getvalue()


class TestBuildVerifyRefusalMessage(unittest.TestCase):
    def test_missing_npm_names_the_stack_and_the_missing_tool(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj"
            proj.mkdir()
            (proj / "package.json").write_text('{"name":"x"}\n', encoding="utf-8")
            with path_containing():
                rc, out = run_verify(proj)
        # Fail-closed behavior is unchanged.
        self.assertEqual(rc, 1, out)
        self.assertIn("BUILD-VERIFY: FAIL", out)
        # ...but the reason must be true.
        self.assertNotIn("no detectable stack", out,
                         "package.json IS a detected stack; the toolchain is "
                         "what is missing")
        # The message must SAY the stack was detected, not merely mention the
        # marker: "FAIL (package.json; npm)" names both strings and still
        # tells the reader nothing about which half of the problem is real.
        self.assertIn("stack detected", out)
        self.assertIn("package.json", out)
        self.assertIn("npm not on PATH", out)

    def test_truly_empty_project_still_says_no_detectable_stack(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj"
            proj.mkdir()
            (proj / "README.md").write_text("nothing here\n", encoding="utf-8")
            rc, out = run_verify(proj)
        self.assertEqual(rc, 1, out)
        self.assertIn("no detectable stack", out)

    def test_makefile_without_test_target_reports_the_real_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj"
            proj.mkdir()
            (proj / "Makefile").write_text(".PHONY: build\nbuild:\n\t@exit 0\n",
                                           encoding="utf-8")
            rc, out = run_verify(proj)
        self.assertEqual(rc, 1, out)
        self.assertIn("Makefile", out)
        self.assertIn("test:", out)


class TestBuildVerifyFallsThroughToUsableToolchain(unittest.TestCase):
    def test_package_json_plus_working_makefile_uses_make(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj"
            proj.mkdir()
            (proj / "package.json").write_text('{"name":"x"}\n', encoding="utf-8")
            (proj / "Makefile").write_text(PASSING_MAKEFILE, encoding="utf-8")
            with path_containing(real=("make",)):
                rc, out = run_verify(proj)
        self.assertEqual(rc, 0, out)
        self.assertIn("BUILD-VERIFY: PASS", out)
        self.assertIn("make test", out)
        # The npm lane really was skipped -- the reader must be told the
        # verify is partial, not full-project coverage.
        self.assertIn("PARTIALLY verified", out)
        self.assertIn("npm", out)
        # Agents paste the BUILD-VERIFY line verbatim as a receipt, so the
        # caveat has to be ON that line and it has to be TRUE: it must name
        # the lane that RAN and the reason the other lane did not, and it must
        # never describe the lane that just passed as unverified.
        pass_lines = [ln for ln in out.splitlines()
                      if ln.startswith("BUILD-VERIFY: PASS")]
        self.assertEqual(len(pass_lines), 1, out)
        receipt = pass_lines[0]
        self.assertIn("PARTIAL: make test only", receipt)
        self.assertIn("npm not on PATH", receipt)
        self.assertNotRegex(
            receipt, r"(?i)(makefile|make test)[^;]*\bnot\b[^;]*verified",
            "the receipt must not list the lane that just PASSED as "
            "unverified: %r" % receipt)

    def test_package_json_plus_broken_makefile_still_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj"
            proj.mkdir()
            (proj / "package.json").write_text('{"name":"x"}\n', encoding="utf-8")
            (proj / "Makefile").write_text(".PHONY: test\ntest:\n\t@exit 3\n",
                                           encoding="utf-8")
            with path_containing(real=("make",)):
                rc, out = run_verify(proj)
        self.assertEqual(rc, 1, out)
        self.assertIn("BUILD-VERIFY: FAIL (exit", out)
        self.assertNotIn("BUILD-VERIFY: PASS", out)

    def test_js_project_with_tests_dir_is_not_handed_to_pytest(self):
        """A tests/ directory in a JS repo is not a Python stack signal.

        A stub pytest that always succeeds stands in for a real install: if the
        fall-through ever hands a JS repo to pytest, this turns green and the
        refusal turns into a bogus PASS.
        """
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj"
            proj.mkdir()
            (proj / "package.json").write_text('{"name":"x"}\n', encoding="utf-8")
            (proj / "tests").mkdir()
            (proj / "tests" / "app.test.js").write_text("// js\n", encoding="utf-8")
            with path_containing(fake=("pytest",)):
                rc, out = run_verify(proj)
        self.assertEqual(rc, 1, out)
        self.assertNotIn("running pytest", out)
        self.assertIn("npm", out)

    def test_python_project_tests_dir_heuristic_is_preserved(self):
        """No package.json: a bare tests/ dir still selects pytest as before."""
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj"
            proj.mkdir()
            (proj / "tests").mkdir()
            with path_containing(fake=("pytest",)):
                detected = build_verify._detect_command(str(proj))
                rc, out = run_verify(proj)
        self.assertIsNotNone(detected)
        self.assertEqual(detected[0][0], "pytest")
        self.assertEqual(rc, 0, out)


class TestBuildVerifyCli(unittest.TestCase):
    def test_cli_prints_the_accurate_refusal_and_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj"
            proj.mkdir()
            (proj / "package.json").write_text('{"name":"x"}\n', encoding="utf-8")
            env = dict(os.environ)
            with tempfile.TemporaryDirectory(prefix="bv-empty-bin-") as bindir:
                env["PATH"] = bindir
                r = subprocess.run(
                    [sys.executable, str(BUILD_VERIFY), str(proj),
                     "--timeout", "30"],
                    capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("BUILD-VERIFY: FAIL", r.stdout)
        self.assertNotIn("no detectable stack", r.stdout)
        self.assertIn("npm not on PATH", r.stdout)

    def test_selftest_still_green(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = build_verify.selftest()
        self.assertEqual(rc, 0, buf.getvalue())


if __name__ == "__main__":
    unittest.main()
