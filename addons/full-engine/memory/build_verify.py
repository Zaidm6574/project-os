#!/usr/bin/env python3
"""Detect project stack and run the cheapest verify command.

Given a project path, looks for package.json, pyproject.toml, or Makefile and
runs the cheapest available verify (npm test, npm run build, pytest, make test)
with a timeout. Prints BUILD-VERIFY: PASS or BUILD-VERIFY: FAIL.

Usage:
  python3 memory/build_verify.py <project_path> [--timeout SECONDS]
  python3 memory/build_verify.py --selftest

Standard library only. No network access (subprocess only).
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

DEFAULT_TIMEOUT = 120


def _safe_path(path: str) -> str:
    return os.path.abspath(path)


def _detect_command(project_path: str) -> tuple[list[str], str] | None:
    pkg = os.path.join(project_path, "package.json")
    if os.path.isfile(pkg):
        if shutil.which("npm"):
            return (["npm", "test"], "npm test")
        return (["npm", "run", "build"], "npm run build")
    pyproject = os.path.join(project_path, "pyproject.toml")
    setup = os.path.join(project_path, "setup.py")
    if os.path.isfile(pyproject) or os.path.isfile(setup) or os.path.isdir(
        os.path.join(project_path, "tests")
    ):
        if shutil.which("pytest"):
            return (["pytest", "-q"], "pytest")
    makefile = os.path.join(project_path, "Makefile")
    if os.path.isfile(makefile):
        with open(makefile, encoding="utf-8", errors="replace") as fh:
            body = fh.read()
        if _has_test_target(body) and shutil.which("make"):
            return (["make", "test"], "make test")
    return None


def _has_test_target(body: str) -> bool:
    return bool(re.search(r"^test\s*:", body, re.MULTILINE))


def verify(project_path: str, timeout: int = DEFAULT_TIMEOUT) -> int:
    full = _safe_path(project_path)
    if not os.path.isdir(full):
        print("BUILD-VERIFY: FAIL (not a directory)")
        return 1
    detected = _detect_command(full)
    if not detected:
        print("BUILD-VERIFY: FAIL (no detectable stack: package.json/pyproject/Makefile)")
        return 1
    cmd, label = detected
    print("build_verify: running %s in %s" % (label, full))
    try:
        proc = subprocess.run(
            cmd,
            cwd=full,
            timeout=timeout,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired:
        print("BUILD-VERIFY: FAIL (timeout after %ss)" % timeout)
        return 1
    except FileNotFoundError as exc:
        print("BUILD-VERIFY: FAIL (%s)" % exc)
        return 1
    if proc.returncode == 0:
        print("BUILD-VERIFY: PASS")
        return 0
    tail = (proc.stderr or proc.stdout or "").strip().splitlines()
    if tail:
        print("  last: %s" % tail[-1][:200])
    print("BUILD-VERIFY: FAIL (exit %s)" % proc.returncode)
    return 1


def selftest() -> int:
    with tempfile.TemporaryDirectory(prefix="build-verify-selftest-") as tmp:
        makefile = os.path.join(tmp, "Makefile")
        with open(makefile, "w", encoding="utf-8") as fh:
            fh.write(".PHONY: test\ntest:\n\t@echo ok\n")
        assert verify(tmp, timeout=30) == 0
        with open(makefile, "w", encoding="utf-8") as fh:
            fh.write(".PHONY: test\ntest:\n\t@exit 1\n")
        assert verify(tmp, timeout=30) == 1
    print("build_verify selftest: OK")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Run cheapest build/test verify for a project.")
    ap.add_argument("path", nargs="?", help="project directory")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        sys.exit(selftest())
    if not args.path:
        ap.error("path is required (or use --selftest)")
    sys.exit(verify(args.path, timeout=args.timeout))


if __name__ == "__main__":
    main()
