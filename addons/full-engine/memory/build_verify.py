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


def _tree_fingerprint(root: str) -> dict:
    """relpath -> sha256 for every file under root. Used to PROVE the source
    tree survived an isolated verify unchanged, rather than asserting it."""
    import hashlib

    out = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            try:
                with open(full, "rb") as fh:
                    out[rel] = hashlib.sha256(fh.read()).hexdigest()
            except OSError:
                out[rel] = "<unreadable>"
    return out


def verify(project_path: str, timeout: int = DEFAULT_TIMEOUT,
           isolated: bool = False) -> int:
    full = _safe_path(project_path)
    if not os.path.isdir(full):
        print("BUILD-VERIFY: FAIL (not a directory)")
        return 1
    detected = _detect_command(full)
    if not detected:
        print("BUILD-VERIFY: FAIL (no detectable stack: package.json/pyproject/Makefile)")
        return 1
    cmd, label = detected

    # Isolated-copy damage guard (hardening 2026-07-22): the verify command
    # runs against a DISPOSABLE copy, so a test/build step that writes files
    # cannot mutate the tree under verification. The source is fingerprinted
    # before and after so "unchanged" is proven, not assumed.
    scratch = None
    run_dir = full
    before = None
    if isolated:
        print("BUILD-VERIFY-MODE: isolated-copy")
        before = _tree_fingerprint(full)
        scratch = tempfile.mkdtemp(prefix="build-verify-isolated-")
        run_dir = os.path.join(scratch, "worktree")
        try:
            shutil.copytree(full, run_dir, symlinks=True)
        except OSError as exc:
            shutil.rmtree(scratch, ignore_errors=True)
            print("BUILD-VERIFY: FAIL (could not create isolated copy: %s)" % exc)
            return 1

    def _finish(code: int) -> int:
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)
        if before is not None:
            if _tree_fingerprint(full) == before:
                print("SOURCE-TREE: unchanged (verified by before/after digest)")
            else:
                print("SOURCE-TREE: CHANGED — isolation breached, treat this "
                      "verify as damage")
                return 1
        return code

    print("build_verify: running %s in %s" % (label, run_dir))
    try:
        proc = subprocess.run(
            cmd,
            cwd=run_dir,
            timeout=timeout,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired:
        print("BUILD-VERIFY: FAIL (timeout after %ss)" % timeout)
        return _finish(1)
    except FileNotFoundError as exc:
        print("BUILD-VERIFY: FAIL (%s)" % exc)
        return _finish(1)
    if proc.returncode == 0:
        rc = _finish(0)
        print("BUILD-VERIFY: PASS" if rc == 0 else "BUILD-VERIFY: FAIL (source tree changed)")
        return rc
    tail = (proc.stderr or proc.stdout or "").strip().splitlines()
    if tail:
        print("  last: %s" % tail[-1][:200])
    print("BUILD-VERIFY: FAIL (exit %s)" % proc.returncode)
    return _finish(1)


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
    ap.add_argument("--isolated", action="store_true",
                    help="run against a disposable copy; the source tree is "
                         "digest-verified unchanged (packet contract lines: "
                         "BUILD-VERIFY-MODE / SOURCE-TREE)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        sys.exit(selftest())
    if not args.path:
        ap.error("path is required (or use --selftest)")
    sys.exit(verify(args.path, timeout=args.timeout, isolated=args.isolated))


if __name__ == "__main__":
    main()
