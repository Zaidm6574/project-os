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
import signal
import subprocess
import sys
import tempfile

DEFAULT_TIMEOUT = 120


def _safe_path(path: str) -> str:
    return os.path.abspath(path)


def _detect(project_path: str):
    """Detect the project's stack AND the cheapest runnable verify command.

    Returns ``(cmd, label, detected, blockers)``:

      cmd/label -- the command to run, or ``(None, None)`` when nothing here
                   is runnable.
      detected  -- stack markers actually found on disk. This can be non-empty
                   while ``cmd`` is None: the stack IS there, the toolchain is
                   not. Callers must not report that case as "no detectable
                   stack" -- that sends the reader hunting for a project-layout
                   problem that does not exist (audit 2026-07-26).
      blockers  -- why each detected lane could not be run, in detection order.
    """
    detected = []
    blockers = []

    pkg = os.path.join(project_path, "package.json")
    if os.path.isfile(pkg):
        detected.append("package.json")
        if shutil.which("npm"):
            return (["npm", "test"], "npm test", detected, blockers)
        # Fail-closed (2026-07-25): npm missing means BOTH "npm test" and
        # "npm run build" are guaranteed to fail the same way ("npm not
        # found"), not a real build/test result -- so never return a doomed
        # npm invocation. Refined 2026-07-26: keep looking instead of giving
        # up outright. A polyglot repo (JS front end + Python/Make back end)
        # can still be verified by a toolchain that IS installed; we only
        # refuse when nothing on disk is runnable, and we say so out loud
        # (see the partial-verify NOTE in verify()).
        blockers.append("npm not on PATH")

    pyproject = os.path.join(project_path, "pyproject.toml")
    setup = os.path.join(project_path, "setup.py")
    py_markers = []
    if os.path.isfile(pyproject):
        py_markers.append("pyproject.toml")
    if os.path.isfile(setup):
        py_markers.append("setup.py")
    # A bare tests/ directory is only a Python signal when nothing else has
    # claimed the project. Plenty of JS repos ship tests/, and handing one to
    # pytest yields "no tests collected" -- a false FAIL dressed up as a real
    # one.
    if not py_markers and not detected and os.path.isdir(
        os.path.join(project_path, "tests")
    ):
        py_markers.append("tests/")
    if py_markers:
        detected.extend(py_markers)
        if shutil.which("pytest"):
            return (["pytest", "-q"], "pytest", detected, blockers)
        blockers.append("pytest not on PATH")

    makefile = os.path.join(project_path, "Makefile")
    if os.path.isfile(makefile):
        detected.append("Makefile")
        body = None
        try:
            with open(makefile, encoding="utf-8", errors="replace") as fh:
                body = fh.read()
        except OSError as exc:
            blockers.append("Makefile unreadable (%s)" % exc)
        if body is not None:
            if not _has_test_target(body):
                blockers.append("Makefile has no `test:` target")
            elif not shutil.which("make"):
                blockers.append("make not on PATH")
            else:
                return (["make", "test"], "make test", detected, blockers)
    return (None, None, detected, blockers)


def _detect_command(project_path: str) -> tuple[list[str], str] | None:
    """Back-compat shim: the runnable command only, no diagnostics."""
    cmd, label, _detected, _blockers = _detect(project_path)
    if cmd is None:
        return None
    return (cmd, label)


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


def _kill_process_group(proc):
    """Best-effort kill of the child's entire process group."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except OSError:
            pass


def verify(project_path: str, timeout: int = DEFAULT_TIMEOUT,
           isolated: bool = False) -> int:
    full = _safe_path(project_path)
    if not os.path.isdir(full):
        print("BUILD-VERIFY: FAIL (not a directory)")
        return 1
    cmd, label, detected, blockers = _detect(full)
    if cmd is None:
        if detected:
            # The stack WAS detected; the toolchain to run it is missing. Say
            # exactly that -- the old "no detectable stack" line was false here
            # and sent readers looking for a layout bug (audit 2026-07-26).
            print("BUILD-VERIFY: FAIL (stack detected (%s) but no usable "
                  "toolchain: %s)"
                  % (", ".join(detected),
                     "; ".join(blockers) or "reason unknown"))
        else:
            print("BUILD-VERIFY: FAIL (no detectable stack: package.json/pyproject/Makefile)")
        return 1
    if blockers:
        # We moved past a detected-but-unrunnable lane. The result that follows
        # covers less than the whole project, so never let it read as full
        # coverage.
        print("build_verify: NOTE %s — falling back to %s; detected stack (%s) "
              "is only PARTIALLY verified"
              % ("; ".join(blockers), label, ", ".join(detected)))

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
    # Run the child in its own process group so a timeout kills the WHOLE tree.
    # subprocess.run(timeout=...) kills only the direct child, so a `make test`
    # that spawned a compiler or a dev server left those orphaned and still
    # running after we printed FAIL (audit 2026-07-25). Cheap containment, not
    # a sandbox -- the child is otherwise unrestricted (fs/network/cpu).
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=run_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        print("BUILD-VERIFY: FAIL (%s)" % exc)
        return _finish(1)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process_group(proc)
        try:
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        print("BUILD-VERIFY: FAIL (timeout after %ss)" % timeout)
        return _finish(1)
    if proc.returncode == 0:
        rc = _finish(0)
        if rc != 0:
            print("BUILD-VERIFY: FAIL (source tree changed)")
            return rc
        # Agents paste the BUILD-VERIFY line verbatim as a receipt, so a
        # partial verify has to carry its caveat ON that line -- a separate
        # NOTE line does not survive the copy/paste.
        # Name the lane that actually RAN, then the reasons the others did
        # not. Listing `detected` here was itself a false message: it includes
        # the lane that just passed, so the receipt claimed the verified lane
        # was unverified (audit 2026-07-26, repair round).
        if blockers:
            print("BUILD-VERIFY: PASS (PARTIAL: %s only; %s)"
                  % (label, "; ".join(blockers)))
        else:
            print("BUILD-VERIFY: PASS")
        return rc
    tail = (err or out or "").strip().splitlines()
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
