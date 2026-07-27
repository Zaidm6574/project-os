#!/usr/bin/env python3
"""Three live security guards that no test pinned (audit 2026-07-27).

Each of the three was proven UNPINNED before this file was written: the
mutation named in each class docstring was applied to a scratch copy of the
tree and `python3 -m unittest discover -s tests` still reported
`Ran 831 tests ... OK (skipped=28)`. A guard the suite cannot miss the loss of
is a guard a refactor deletes silently, which is exactly how #3 below shipped.

1. scripts/promptsmith.py `_path_within` -- the `dirname(real) == base` half.
   commonpath() alone only proves "somewhere UNDER out-dir"; the dirname half
   is what pins the file DIRECTLY in out-dir (and therefore pins any `.tmp`
   sibling the writer derives from it).
2. scripts/brain_archive.py -- the O_NOFOLLOW flag on the archive open. Its
   two flanking layers (the islink() pre-check, and the st_nlink check on the
   open fd) are already tested; O_NOFOLLOW's own contribution is only visible
   once the pre-check is neutered, so the test below neuters it deliberately.
3. scripts/install_full_engine.py main() -- returned 0 even after RECORDING
   refusals that pointed OUT of the target, while the sibling installer
   returned 1 on the identical input. install.sh IS `set -eu` and runs the two
   installers as plain commands, so the hardcoded 0 was the ONLY thing keeping
   a --full-engine leg that refused half its writes from failing the install:
   `./install.sh <target> --full-engine` printed REFUSED lines and exited 0.

Stdlib only; passes on /usr/bin/python3 3.9.6.
"""

import contextlib
import functools
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROMPTSMITH = os.path.join(ROOT, "scripts", "promptsmith.py")
BRAIN_ARCHIVE_PY = os.path.join(ROOT, "scripts", "brain_archive.py")
FULL_ENGINE = os.path.join(ROOT, "scripts", "install_full_engine.py")
INSTALL_SH = os.path.join(ROOT, "install.sh")
BRAIN_ENV = "PROJECT_OS_SHARED_BRAIN"

_PYTHON_CANDIDATES = ("python3", "python", "python3.13", "python3.12",
                      "python3.11", "python3.10", "python3.14")


@functools.lru_cache(maxsize=None)
def _path_has_python310():
    """install.sh needs a >=3.10 interpreter on PATH; probe it the same way."""
    for candidate in _PYTHON_CANDIDATES:
        path = shutil.which(candidate)
        if not path:
            continue
        try:
            probe = subprocess.run(
                [path, "-c",
                 "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"],
                capture_output=True, timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if probe.returncode == 0:
            return True
    return False


def _sandbox_env(home):
    """Env with HOME redirected and no bytecode written into the sandbox."""
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


# --------------------------------------------------------------------------
# 1. promptsmith --packet-id must land DIRECTLY in --out-dir
# --------------------------------------------------------------------------

class PromptsmithPacketIdStaysDirectlyInOutDir(unittest.TestCase):
    """Mutation: delete `and os.path.dirname(real) == base` from _path_within.

    Proven unpinned 2026-07-27 -- with that half removed the whole suite was
    still `Ran 831 ... OK`, while

        --out-dir <d> --packet-id nested/sneaky

    exited 0 and wrote <d>/nested/sneaky-worker-prompt.md and
    <d>/nested/sneaky-rubric.md. The printed JSON summary reports the packet as
    written, so a reader following it looks in <d> and finds nothing: the
    packet is real, in a directory nobody named.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = self.tmp.name
        self.out_dir = os.path.join(self.base, "packets")
        # A pre-existing subdirectory of out-dir. It has to exist for the
        # UNGUARDED write to succeed rather than die on a missing parent --
        # otherwise the test would "pass" for the wrong reason (a crash), not
        # because the guard refused.
        self.nested = os.path.join(self.out_dir, "nested")
        os.makedirs(self.nested)
        self.brief = os.path.join(self.base, "brief.md")
        with open(self.brief, "w", encoding="utf-8") as f:
            f.write("# Brief\n\n## DON'T\n- never use neon\n")

    def _run(self, packet_id, out_dir=None):
        return subprocess.run(
            [sys.executable, PROMPTSMITH,
             "--task", "build the hero section",
             "--brief-file", self.brief,
             "--out-dir", out_dir or self.out_dir,
             "--packet-id", packet_id,
             "--no-index"],
            capture_output=True, text=True, timeout=300,
        )

    def _tree(self, root):
        found = set()
        for dirpath, _dirnames, filenames in os.walk(root):
            for name in filenames:
                found.add(os.path.relpath(os.path.join(dirpath, name), root))
        return found

    def test_nested_subdirectory_packet_id_is_refused(self):
        before = self._tree(self.base)
        r = self._run("nested/sneaky")
        self.assertNotEqual(
            r.returncode, 0,
            "a packet id naming a SUBDIRECTORY of --out-dir was accepted:\n%s"
            % r.stdout)
        for name in ("sneaky-worker-prompt.md", "sneaky-rubric.md"):
            self.assertFalse(
                os.path.exists(os.path.join(self.nested, name)),
                "promptsmith wrote %s into a subdirectory of --out-dir" % name)
        # Nothing at all was written -- not a partial first-file leak, and no
        # stray *.tmp sibling either.
        self.assertEqual(self._tree(self.base), before)
        self.assertIn("refusing --packet-id", r.stderr)

    def test_deeply_nested_packet_id_is_refused(self):
        """`a/b/c` is the same escape one level further down."""
        deep = os.path.join(self.nested, "deeper")
        os.makedirs(deep)
        before = self._tree(self.base)
        r = self._run("nested/deeper/sneaky")
        self.assertNotEqual(r.returncode, 0,
                            "a two-level nested packet id was accepted:\n%s"
                            % r.stdout)
        self.assertEqual(self._tree(self.base), before)

    def test_sibling_directory_sharing_the_out_dir_name_prefix_is_refused(self):
        """Near miss: `<out_dir>-evil` is NOT inside `<out_dir>`.

        The classic way this containment check gets broken in a refactor is
        `real.startswith(base)`, which says True for
        `/tmp/x/packets-evil/pwned` against the base `/tmp/x/packets`. This
        case is a regression pin for the commonpath idiom, not a killer for
        the dirname mutation: it must be refused under BOTH.
        """
        evil = os.path.join(self.base, "packets-evil")
        os.makedirs(evil)
        r = self._run(os.path.join("..", "packets-evil", "pwned"))
        self.assertNotEqual(
            r.returncode, 0,
            "a prefix-sibling directory escape was accepted:\n%s" % r.stdout)
        self.assertEqual(os.listdir(evil), [],
                         "promptsmith wrote into a sibling of --out-dir")

    def test_plain_packet_id_still_writes_both_files(self):
        """The other direction: the guard must not refuse an ordinary id.

        A containment check that refuses everything is not a fix, it is an
        outage -- so pin that the normal path still produces both files,
        directly in --out-dir.
        """
        pid = "psmith-2026-07-27-hero-section"
        r = self._run(pid)
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        for suffix in ("-worker-prompt.md", "-rubric.md"):
            path = os.path.join(self.out_dir, pid + suffix)
            self.assertTrue(os.path.isfile(path), "missing %s" % path)
            self.assertEqual(os.path.dirname(os.path.realpath(path)),
                             os.path.realpath(self.out_dir))
        summary = json.loads(r.stdout[r.stdout.index("{"):])
        self.assertEqual(summary["packet_id"], pid)


# --------------------------------------------------------------------------
# 2. brain_archive: O_NOFOLLOW is load-bearing on its own
# --------------------------------------------------------------------------

_SEQ = [0]

OLD_INTEREST = {"id": "interest-old-topic", "type": "interest",
                "ts": "2020-01-02T00:00:00Z", "text": "stale interest row"}
KEEP_LESSON = {"id": "lesson-keep", "type": "lesson",
               "ts": "2026-07-01T00:00:00Z", "text": "durable lesson row"}
VICTIM_BYTES = b"signed client contract - the only copy\n"


def _load_brain_archive(brain_path):
    """Fresh scripts/brain_archive.py instance with BRAIN=brain_path.

    BRAIN/ARCHIVE are read from the environment at import time, so each test
    needs its own instance pointed at its own sandbox.
    """
    _SEQ[0] += 1
    name = "brain_archive_nofollow_%d" % _SEQ[0]
    old = os.environ.get(BRAIN_ENV)
    os.environ[BRAIN_ENV] = brain_path
    try:
        spec = importlib.util.spec_from_file_location(name, BRAIN_ARCHIVE_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        if old is None:
            os.environ.pop(BRAIN_ENV, None)
        else:
            os.environ[BRAIN_ENV] = old
    return mod


class BrainArchiveONofollowIsLoadBearing(unittest.TestCase):
    """Mutation: drop `| getattr(os, "O_NOFOLLOW", 0)` from the archive open.

    Proven unpinned 2026-07-27 -- the whole suite stayed
    `Ran 831 ... OK (skipped=28)` with the flag removed. The reason no ordinary
    test can see it is layering: a symlink planted at the archive name is
    caught FIRST by the islink() pre-check (`_escapes_brain_dir`), and the
    st_nlink check on the open fd only fires for HARD links, so O_NOFOLLOW is
    the only guard whose removal changes nothing observable. Its real job is
    the TOCTOU window -- the link appears AFTER the pre-check passed -- which
    has no deterministic exploit.

    So this is a defence-in-depth test, and it is honest about that: it
    NEUTERS the flanking pre-check on a freshly loaded module instance (which
    is precisely the state the TOCTOU race produces: a pre-check that already
    said "clean") and then proves the sink itself still refuses. The st_nlink
    layer is not neutered because it cannot catch this input: the symlink
    target is created by the open itself and has st_nlink == 1, so under the
    mutation it passes that check and the write lands on the victim file --
    which is what the assertions below detect.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.brain_dir = os.path.join(self.tmp.name, "central-brain")
        self.outside_dir = os.path.join(self.tmp.name, "elsewhere")
        os.makedirs(self.brain_dir)
        os.makedirs(self.outside_dir)
        self.brain = os.path.join(self.brain_dir, "shared-brain.jsonl")
        self.victim = os.path.join(self.outside_dir, "contract.txt")
        with open(self.victim, "wb") as f:
            f.write(VICTIM_BYTES)
        with open(self.brain, "w", encoding="utf-8") as f:
            for row in (OLD_INTEREST, KEEP_LESSON):
                f.write(json.dumps(row) + "\n")
        self.mod = _load_brain_archive(self.brain)
        # No live lock, and never shell out to the real mneme rebuild.
        self.mod.bb_lock = types.SimpleNamespace(
            acquire=lambda target, agent="unknown", wait=10.0: True,
            release=lambda target, agent="unknown", force=False, token=None: True)
        self.mod.subprocess = types.SimpleNamespace(
            run=lambda *a, **k: types.SimpleNamespace(stdout="", stderr=""))
        self.archive = self.mod.ARCHIVE

    def _neuter_the_islink_precheck(self):
        """Make the flanking path check report 'clean' for every path.

        This is the state a TOCTOU winner leaves behind, and it is the only
        way to observe what O_NOFOLLOW contributes on its own.
        """
        self.mod._escapes_brain_dir = lambda path: ""

    def _apply(self, ids=("interest-old-topic",)):
        out, err = io.StringIO(), io.StringIO()
        ns = types.SimpleNamespace(ids=list(ids), interest_days=60)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = self.mod.cmd_apply(ns)
        return rc, out.getvalue(), err.getvalue()

    def _brain_ids(self):
        ids = []
        with open(self.brain, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    ids.append(json.loads(line).get("id"))
        return ids

    def test_symlinked_archive_is_refused_by_the_open_itself(self):
        self._neuter_the_islink_precheck()
        os.symlink(self.victim, self.archive)

        rc, _out, err = self._apply()

        self.assertEqual(rc, 2, "the archive open accepted a symlink: %s" % err)
        with open(self.victim, "rb") as f:
            victim_now = f.read()
        self.assertEqual(
            victim_now, VICTIM_BYTES,
            "archived lesson text was appended to a file outside the brain "
            "directory through a symlink at the archive name")
        self.assertTrue(os.path.islink(self.archive),
                        "the planted link was replaced instead of refused")
        # Refused BEFORE the active brain was rewritten: nothing was moved out
        # of it, so no entry can go missing from both files.
        self.assertEqual(self._brain_ids(),
                         ["interest-old-topic", "lesson-keep"],
                         "the active brain was rewritten after the refusal")

    def test_dangling_symlinked_archive_does_not_create_the_outside_file(self):
        """O_CREAT through a DANGLING link creates the far file, so refuse it.

        `os.path.exists()` is False for a broken link, which is exactly why
        the sink cannot be allowed to decide by looking first.
        """
        self._neuter_the_islink_precheck()
        missing = os.path.join(self.outside_dir, "brand-new.jsonl")
        os.symlink(missing, self.archive)

        rc, _out, err = self._apply()

        self.assertEqual(rc, 2, "a dangling symlinked archive was accepted: %s" % err)
        self.assertFalse(os.path.exists(missing),
                         "the archive open created a file outside the brain "
                         "directory through a dangling link")
        self.assertEqual(self._brain_ids(),
                         ["interest-old-topic", "lesson-keep"])

    def test_ordinary_archive_still_archives(self):
        """The other direction: with the pre-check neutered and NO link, the
        archive must still be written. A sink that refuses unconditionally
        would satisfy the two tests above while breaking the tool."""
        self._neuter_the_islink_precheck()

        rc, out, err = self._apply()

        self.assertEqual(rc, 0, err)
        self.assertIn("archived 1", out)
        with open(self.archive, encoding="utf-8") as f:
            archived = [json.loads(ln) for ln in f if ln.strip()]
        self.assertEqual([o["id"] for o in archived], ["interest-old-topic"])
        self.assertEqual(self._brain_ids(), ["lesson-keep"])


# --------------------------------------------------------------------------
# 3. install_full_engine must EXIT NONZERO when it refuses writes
# --------------------------------------------------------------------------

class FullEngineInstallerExitsNonzeroOnEscapingRefusal(unittest.TestCase):
    """No mutation needed: this defect was LIVE.

    Measured 2026-07-27 on the unmodified tree -- a --target whose memory/ is
    a symlink out of the target produced 13 `REFUSED` lines and
    `returncode=0`. scripts/setup_project_os.py returns 1 on the identical
    input (report_refusals: nonzero iff some refusal escaped the target).
    install.sh runs both against the same --target under `set -eu`, so the
    hardcoded 0 was the only reason the --full-engine leg reported overall
    success while roughly a third of the add-on was never installed
    (end-to-end pin in InstallShFullEngineLegReportsFailure below).

    Both directions are pinned: an ESCAPING refusal must fail the run, and a
    refusal that stays INSIDE the target (this repo's own AGENTS.md ->
    CLAUDE.md convention) must not.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = self.tmp.name
        self.home = os.path.join(self.base, "home")
        self.outside = os.path.join(self.base, "outside")
        self.target = os.path.join(self.base, "target")
        for d in (self.home, self.outside, self.target):
            os.makedirs(d)
        # the add-on installer refuses to run without a starter workspace
        with open(os.path.join(self.target, "AGENTS.md"), "w") as f:
            f.write("# Project OS\n")
        os.makedirs(os.path.join(self.target, "blackboard"))
        with open(os.path.join(self.target, "blackboard",
                               "00-project-goal.md"), "w") as f:
            f.write("# Goal\n")

    def _run(self, *extra):
        return subprocess.run(
            [sys.executable, FULL_ENGINE, "--target", self.target] + list(extra),
            capture_output=True, text=True, timeout=300,
            env=_sandbox_env(self.home),
        )

    def test_refusal_pointing_outside_the_target_fails_the_run(self):
        with open(os.path.join(self.outside, "keep.txt"), "w") as f:
            f.write("keep\n")
        os.symlink(self.outside, os.path.join(self.target, "memory"))

        r = self._run()

        self.assertIn("REFUSED", r.stdout + r.stderr,
                      "no refusal was recorded at all, so this proves nothing")
        self.assertNotEqual(
            r.returncode, 0,
            "install_full_engine refused writes that pointed OUT of the "
            "target and still exited 0; install.sh has no `set -e`, so the "
            "run reports success.\nstdout:\n%s\nstderr:\n%s"
            % (r.stdout, r.stderr))
        self.assertEqual(sorted(os.listdir(self.outside)), ["keep.txt"],
                         "the installer wrote outside the target")

    def test_exit_status_matches_the_sibling_installer(self):
        """The two installers install.sh drives must agree on this signal."""
        os.symlink(self.outside, os.path.join(self.target, "memory"))
        r = self._run()
        setup = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "setup_project_os.py"),
             "--target", self.target],
            capture_output=True, text=True, timeout=300,
            env=_sandbox_env(self.home),
        )
        self.assertNotEqual(setup.returncode, 0,
                            "the sibling installer's own contract changed")
        self.assertEqual(
            r.returncode, setup.returncode,
            "the two installers install.sh runs against the SAME --target "
            "disagree on the exit status for an escaping refusal "
            "(full-engine=%s, setup=%s)" % (r.returncode, setup.returncode))

    def test_clean_install_still_exits_zero(self):
        """The other direction: no refusals must stay exit 0."""
        r = self._run()
        self.assertEqual(r.returncode, 0,
                         "a clean full-engine install failed\nstdout:\n%s\n"
                         "stderr:\n%s" % (r.stdout, r.stderr))
        self.assertNotIn("REFUSED", r.stdout + r.stderr)

    def test_refusal_contained_inside_the_target_still_exits_zero(self):
        """A link that stays inside the target is reported, not fatal.

        This repo ships `AGENTS.md -> CLAUDE.md`; refusing to write through it
        is correct, but it is not a reason to fail an otherwise good install,
        and setup_project_os.py deliberately returns 0 for it.
        """
        os.makedirs(os.path.join(self.target, "memory"))
        inside = os.path.join(self.target, "inside.txt")
        with open(inside, "w") as f:
            f.write("# mine\n")
        os.symlink(inside, os.path.join(self.target, "memory", "goal_guard.py"))

        r = self._run()

        self.assertIn("REFUSED", r.stdout + r.stderr,
                      "the contained link was written through instead of "
                      "refused")
        self.assertEqual(
            r.returncode, 0,
            "a refusal that stays INSIDE the target failed the install\n"
            "stdout:\n%s\nstderr:\n%s" % (r.stdout, r.stderr))
        with open(inside) as f:
            self.assertEqual(f.read(), "# mine\n",
                             "the installer wrote through a contained link")


class InstallShFullEngineLegReportsFailure(unittest.TestCase):
    """The exit code has to survive the shell that actually runs it.

    install.sh IS `set -eu` (line 2) and runs
    `"$PYTHON" "$FULL_ENGINE_SCRIPT" "$@"` as a plain command, so the moment
    install_full_engine.py stops hardcoding 0 the whole install reports
    failure. Measured 2026-07-27 with a target whose brain/ is a link out of
    the target -- `./install.sh <target> --full-engine`: rc=0 with 5 REFUSED
    lines before the fix, rc=1 with 6 after.

    brain/ is the symlink (not memory/) on purpose: setup_project_os.py runs
    FIRST under the same `set -e` and refuses a symlinked memory/ itself, so
    the run would abort before the leg under test ever started. brain/ is
    created only by the add-on installer, which isolates this to the
    full-engine leg.
    """

    @unittest.skipUnless(_path_has_python310(),
                         "install.sh requires Python >=3.10 on PATH")
    def test_escaping_refusal_in_the_full_engine_leg_fails_install_sh(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = os.path.join(tmp, "home")
            outside = os.path.join(tmp, "outside")
            target = os.path.join(tmp, "target")
            for d in (home, outside, target):
                os.makedirs(d)
            with open(os.path.join(outside, "keep.txt"), "w") as f:
                f.write("keep\n")
            os.symlink(outside, os.path.join(target, "brain"))

            proc = subprocess.run(
                ["sh", INSTALL_SH, target, "--full-engine"],
                capture_output=True, text=True, timeout=600,
                env=_sandbox_env(home),
            )

            self.assertIn(
                "REFUSED", proc.stdout + proc.stderr,
                "no refusal happened, so the exit status proves nothing")
            self.assertNotEqual(
                proc.returncode, 0,
                "install.sh reported overall SUCCESS after the --full-engine "
                "leg refused writes that pointed out of the target\n"
                "stdout:\n%s\nstderr:\n%s" % (proc.stdout, proc.stderr))
            self.assertEqual(sorted(os.listdir(outside)), ["keep.txt"],
                             "install.sh wrote outside the target")


if __name__ == "__main__":
    unittest.main()
