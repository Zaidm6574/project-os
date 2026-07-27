#!/usr/bin/env python3
"""evolution.py rewrote its own history in place, so a crash mid-write ate it.

Reproduced end-to-end on 2026-07-27 against the shipped CLI, before the fix:

    evolution.json: 636 bytes, 3 records      # v1/v2/v3, best = v2
    4th `record` crashes mid-write
    evolution.json: 512 bytes
    UNPARSEABLE: JSONDecodeError: Unterminated string starting at: line 25
    best   rc=1 json.decoder.JSONDecodeError: Unterminated string ...
    next   rc=1 json.decoder.JSONDecodeError: Unterminated string ...
    report rc=1 json.decoder.JSONDecodeError: Unterminated string ...
    record rc=1 json.decoder.JSONDecodeError: Unterminated string ...

Both writers opened the destination with mode "w", which truncates the real
file before a single new byte exists. Anything that interrupts the write --
a crash, a kill, a full disk, a quota -- leaves the only copy of the run's
evolution history destroyed, and the tool can then neither read its history
nor append to it. `report` is the worse surface: it looks read-only and it
runs OUTSIDE the bb_lock critical section, yet it rewrote evolution.md the
same way (433 bytes of table -> 200 bytes, 0 rows).

The crash here is delivered by the OS, not by a monkeypatch: RLIMIT_FSIZE is
lowered for the child, so the kernel refuses the write once the file passes
the limit. CPython sets SIGXFSZ to SIG_IGN, so it surfaces as
`OSError: [Errno 27] File too large` out of the write -- exactly how a full
disk or an exceeded quota arrives in production. That mechanism is
deliberately implementation-agnostic: it knows nothing about open() vs a
temp file, so it cannot be satisfied by renaming things. Every crash test
also asserts the crash actually fired (non-zero exit), because a test whose
injected failure silently stops firing is the failure mode this repo keeps
shipping.

Sibling writers already do this correctly -- scripts/brain_archive.py
(_atomic_rewrite) and scripts/plan_artifact.py (packet staging). This file
pins evolution.py to the same contract.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

try:
    import resource
except ImportError:  # pragma: no cover - non-POSIX
    resource = None

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO, "scripts")

_CAN_LIMIT_FILE_SIZE = (
    os.name == "posix"
    and resource is not None
    and hasattr(resource, "RLIMIT_FSIZE")
)

SEED = [("v1", "0.40"), ("v2", "0.81"), ("v3", "0.63")]


class _EvolutionSandbox(unittest.TestCase):
    """A copy of the shipped tree; evolution.py derives ROOT from its own path,
    so nothing here can reach the real runs/, blackboard/ or lock directory."""

    run_name = "20260727-crash"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="evolution-atomic-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        os.makedirs(os.path.join(self.tmp, "scripts"))
        os.makedirs(os.path.join(self.tmp, "runs"))
        for name in ("evolution.py", "bb_lock.py"):
            shutil.copy(os.path.join(SCRIPTS, name),
                        os.path.join(self.tmp, "scripts", name))
        self.script = os.path.join(self.tmp, "scripts", "evolution.py")
        self.rundir = os.path.join(self.tmp, "runs", self.run_name)
        self.jf = os.path.join(self.rundir, "evolution.json")
        self.mf = os.path.join(self.rundir, "evolution.md")
        self.env = dict(os.environ)
        self.env["HOME"] = self.tmp
        self.env["BB_LOCK_DIR"] = os.path.join(self.tmp, "locks")
        self.env["PYTHONDONTWRITEBYTECODE"] = "1"

    def evo(self, argv, fsize=None):
        """Drive the real CLI. fsize lowers RLIMIT_FSIZE for the child only."""
        pre = None
        if fsize is not None:
            def pre():  # noqa: E306
                resource.setrlimit(resource.RLIMIT_FSIZE, (fsize, fsize))
        return subprocess.run([sys.executable, self.script] + argv,
                              cwd=self.tmp, env=self.env, preexec_fn=pre,
                              capture_output=True, text=True, timeout=120)

    def seed(self, records=SEED):
        for i, (variant, score) in enumerate(records, 1):
            proc = self.evo(["record", "--run", self.run_name,
                             "--variant", variant,
                             "--change", "variant %d change" % i,
                             "--verdict", "approve", "--score", score])
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def variants(self):
        with open(self.jf, encoding="utf-8") as f:
            return [r["variant"] for r in json.load(f)["records"]]

    @staticmethod
    def read(path, mode="rb"):
        with open(path, mode, **({} if "b" in mode else {"encoding": "utf-8"})) as f:
            return f.read()

    def assert_crashed(self, proc, what):
        self.assertNotEqual(
            proc.returncode, 0,
            "%s completed instead of crashing -- the injected mid-write "
            "failure never fired, so this test proves nothing:\n%s\n%s"
            % (what, proc.stdout, proc.stderr))


@unittest.skipUnless(_CAN_LIMIT_FILE_SIZE, "needs RLIMIT_FSIZE")
class RecordCrashMidWriteKeepsTheHistory(_EvolutionSandbox):
    """`record` truncated evolution.json before writing the new copy."""

    def test_history_survives_a_crash_during_the_json_write(self):
        self.seed()
        before = self.read(self.jf)
        self.assertEqual(self.variants(), ["v1", "v2", "v3"])

        # Big enough for bb_lock's own lockfile, too small for the 4-record
        # history the child is about to write.
        limit = max(256, len(before) - 64)
        crash = self.evo(["record", "--run", self.run_name, "--variant", "v4",
                          "--change", "the record that crashes",
                          "--verdict", "approve", "--score", "0.99"],
                         fsize=limit)
        self.assert_crashed(crash, "record under RLIMIT_FSIZE")

        after = self.read(self.jf)
        try:
            recovered = json.loads(after)
        except ValueError as e:
            self.fail("evolution.json was destroyed by the interrupted write: "
                      "%d bytes -> %d bytes, %s: %s\nleft on disk: %r"
                      % (len(before), len(after), type(e).__name__, e,
                         after[:80]))
        self.assertEqual([r["variant"] for r in recovered["records"]],
                         ["v1", "v2", "v3"],
                         "the crash lost committed records")
        self.assertEqual(after, before,
                         "an interrupted write must leave the previous history "
                         "byte-for-byte intact")

    def test_the_tool_can_still_read_and_extend_its_history_after_a_crash(self):
        self.seed()
        limit = max(256, os.path.getsize(self.jf) - 64)
        crash = self.evo(["record", "--run", self.run_name, "--variant", "v4",
                          "--change", "the record that crashes",
                          "--verdict", "approve", "--score", "0.99"],
                         fsize=limit)
        self.assert_crashed(crash, "record under RLIMIT_FSIZE")

        for cmd in ("best", "next", "report"):
            proc = self.evo([cmd, "--run", self.run_name])
            self.assertEqual(proc.returncode, 0,
                             "`%s` could not read the history after an "
                             "interrupted write:\n%s" % (cmd, proc.stderr))
        best = self.evo(["best", "--run", self.run_name])
        self.assertEqual(json.loads(best.stdout)["variant"], "v2")

        again = self.evo(["record", "--run", self.run_name, "--variant", "v5",
                          "--change", "after the crash", "--verdict", "approve",
                          "--score", "0.50"])
        self.assertEqual(again.returncode, 0,
                         "the run could not be extended after an interrupted "
                         "write:\n" + again.stderr)
        self.assertEqual(self.variants(), ["v1", "v2", "v3", "v5"])


@unittest.skipUnless(_CAN_LIMIT_FILE_SIZE, "needs RLIMIT_FSIZE")
class ReportCrashMidWriteKeepsTheMarkdown(_EvolutionSandbox):
    """render_md() is reached from `record` (inside bb_lock) and from `report`
    (outside it). This pins the outside-the-lock path: `report` reads like a
    read-only command and still rewrote evolution.md in place."""

    run_name = "20260727-mdcrash"

    def test_markdown_survives_a_crash_during_the_report_write(self):
        self.seed()
        before = self.read(self.mf)
        self.assertEqual(before.count(b"\n| v"), 3, before)

        limit = max(128, len(before) // 2)
        crash = self.evo(["report", "--run", self.run_name], fsize=limit)
        self.assert_crashed(crash, "report under RLIMIT_FSIZE")

        after = self.read(self.mf)
        self.assertEqual(
            after, before,
            "report destroyed the rendered history it was only meant to "
            "print: %d bytes -> %d bytes, %d table rows -> %d, "
            "'Best so far' line %s"
            % (len(before), len(after), before.count(b"\n| v"),
               after.count(b"\n| v"),
               "kept" if b"Best so far" in after else "GONE"))


class EvolutionFilePermissions(_EvolutionSandbox):
    """A temp-file rewrite hands the destination the TEMP file's mode. Staging
    at 0600 and restoring the destination's own mode is the same contract
    scripts/brain_archive.py carries."""

    run_name = "20260727-modes"

    def test_new_history_files_are_private(self):
        self.seed(SEED[:1])
        for path in (self.jf, self.mf):
            mode = stat.S_IMODE(os.stat(path).st_mode)
            self.assertEqual(
                mode, 0o600,
                "%s was created world/group readable (%s)"
                % (os.path.basename(path), oct(mode)))

    def test_an_existing_files_mode_is_preserved_not_reset(self):
        self.seed(SEED[:1])
        os.chmod(self.jf, 0o640)
        os.chmod(self.mf, 0o644)
        proc = self.evo(["record", "--run", self.run_name, "--variant", "v9",
                         "--change", "second", "--verdict", "approve",
                         "--score", "0.7"])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(stat.S_IMODE(os.stat(self.jf).st_mode), 0o640,
                         "evolution.json's mode was not preserved")
        self.assertEqual(stat.S_IMODE(os.stat(self.mf).st_mode), 0o644,
                         "evolution.md's mode was not preserved")


class StagingNameIsNotPredictable(_EvolutionSandbox):
    """The obvious wrong fix -- stage at `<dest>.tmp` with a plain open() --
    stops the truncation but re-opens the hole scripts/brain_archive.py was
    hardened against on 2026-07-26: a symlink pre-planted at the predictable
    staging name redirects the write outside the tree. mkstemp() closes it
    with O_CREAT|O_EXCL, which refuses ANY pre-existing path, symlink
    included."""

    run_name = "20260727-staging"

    def test_symlinks_planted_at_guessable_staging_names_are_never_written(self):
        self.seed(SEED[:1])
        outside = os.path.join(self.tmp, "outside")
        os.makedirs(outside)
        planted = []
        for base in ("evolution.json", "evolution.md"):
            for pattern in ("%s.tmp", ".%s.tmp", "%s.new", "%s~"):
                link = os.path.join(self.rundir, pattern % base)
                target = os.path.join(outside, os.path.basename(link) + ".victim")
                with open(target, "w", encoding="utf-8") as f:
                    f.write("PRECIOUS\n")
                os.symlink(target, link)
                planted.append(target)

        proc = self.evo(["record", "--run", self.run_name, "--variant", "v2",
                         "--change", "second", "--verdict", "approve",
                         "--score", "0.9"])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.variants(), ["v1", "v2"])
        for target in planted:
            self.assertEqual(
                self.read(target, "r"), "PRECIOUS\n",
                "a symlink at a guessable staging name redirected the write "
                "onto %s" % target)


class OrdinaryEvolutionRunsAreUnaffected(_EvolutionSandbox):
    """Near-miss controls: a fix that breaks the happy path is its own defect."""

    run_name = "20260727-control"

    def test_record_best_next_and_report_all_still_work(self):
        self.seed()
        self.assertEqual(self.variants(), ["v1", "v2", "v3"])

        best = self.evo(["best", "--run", self.run_name])
        self.assertEqual(best.returncode, 0, best.stderr)
        self.assertEqual(json.loads(best.stdout)["variant"], "v2")

        nxt = self.evo(["next", "--run", self.run_name])
        self.assertEqual(nxt.returncode, 0, nxt.stderr)
        self.assertIn("**Evolve from:** v2", nxt.stdout)

        rep = self.evo(["report", "--run", self.run_name])
        self.assertEqual(rep.returncode, 0, rep.stderr)
        self.assertIn("| v2 |", rep.stdout)

    def test_the_written_files_keep_their_exact_shape(self):
        self.seed()
        raw = self.read(self.jf, "r")
        self.assertEqual(raw, json.dumps(json.loads(raw), indent=2),
                         "evolution.json is no longer indent=2 JSON with no "
                         "trailing newline")
        md = self.read(self.mf, "r")
        self.assertTrue(md.startswith("# Evolution Records — " + self.run_name))
        self.assertTrue(md.endswith("**Best so far:** v2 (0.81)\n"), repr(md[-60:]))

    def test_a_successful_write_leaves_no_temp_files_behind(self):
        self.seed()
        self.assertEqual(sorted(os.listdir(self.rundir)),
                         ["evolution.json", "evolution.md"],
                         "staging litter was left in the run directory")


if __name__ == "__main__":
    unittest.main()
