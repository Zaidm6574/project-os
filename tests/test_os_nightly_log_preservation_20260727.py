"""os_nightly must ADD to the automation log, not rebuild the file around its entry.

Three defects of one write, all reproduced on c71964d before this module existed:

  1. ``write_entry()`` split the log on ``"\\n## "`` and rebuilt it as
     ``HEADER + new entry + old entries``, so everything above the first entry
     was replaced by the six-line HEADER constant. The shipped
     ``blackboard-template/22-automation-log.md`` has no ``## `` heading at
     all, so the FIRST heartbeat of every install discarded its whole 34-line
     preamble -- the per-field legend, the launchd plist, and the
     ``launchctl bootstrap`` line that README.md sends a reader to read. The
     installer does not ship ``blackboard-template/`` into the target, so in an
     installed project that snippet then exists nowhere.
     ``tests/test_docs_claims_20260726.py``'s ``ReadmeLaunchdPointerTests``
     stayed green throughout because its ``resolve()`` deliberately maps
     ``blackboard/...`` to ``blackboard-template/...`` -- a claim about what
     users RECEIVE must be checked against what the installer ships. That
     choice is right for its own purpose; the gap is that nothing observed the
     file once the heartbeat had run. This module observes exactly that.

  2. The same rewrite silently deleted any operator note added above the first
     entry, and the MAX_ENTRIES cap could not tell a hand-written ``## ``
     block from a machine one, so a manual block was rotated out after 30
     runs. The template does say "Do not edit by hand", so this is a user
     writing where they were told not to -- but deleting their text without a
     word is still the wrong answer, and it costs nothing to keep it.

  3. The rewrite went through ``open(LOG, "w")``, which truncates the
     destination and only then writes. Anything that interrupts the gap
     (ENOSPC, SIGKILL, power loss) leaves a zero-byte log: the whole 30-entry
     drift history destroyed while appending one entry.

Every test below was mutation-verified against the pre-fix ``write_entry``.
Stdlib only; nothing here writes to the live blackboard/ tree.
"""
import importlib.util
import io
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SETUP = SCRIPTS / "setup_project_os.py"
NIGHTLY = SCRIPTS / "os_nightly.py"
TEMPLATE_LOG = ROOT / "blackboard-template" / "22-automation-log.md"

# A machine entry heading, exactly as write_entry() writes it.
MACHINE_HEADING = re.compile(r"^## \d{4}-\d{2}-\d{2}T\d{2}:\d{2}", re.M)


def _read(path):
    with io.open(str(path), encoding="utf-8") as f:
        return f.read()


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# The README claim and the way it is parsed have exactly one home:
# tests/test_docs_claims_20260726.py. Load it by path rather than copying its
# regex here -- a second copy of a doc-pinning pattern is how the drift these
# suites exist to catch gets shipped.
_DOCS_CLAIMS = _load("docs_claims_for_nightly", Path(__file__).with_name(
    "test_docs_claims_20260726.py"))


class ShippedLogPreambleSurvivesTheHeartbeat(unittest.TestCase):
    """A real install, then a real heartbeat, then re-check what README promises.

    ``tests/test_docs_claims_20260726.py`` checks the claim against the
    template (what users receive). This checks it against the workspace they
    actually end up with after the daily agent has fired once.
    """

    # Same markers the docs-claims module uses to recognise a launchd snippet,
    # plus the exact string README.md names.
    MARKERS = _DOCS_CLAIMS.ReadmeLaunchdPointerTests.MARKERS + ("launchctl bootstrap",)

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="os-nightly-preamble-")
        cls.target = os.path.join(cls._tmp, "installed")
        cls.home = os.path.join(cls._tmp, "home")
        os.makedirs(cls.home)
        done = subprocess.run(
            [sys.executable, str(SETUP), "--target", cls.target],
            capture_output=True, text=True, timeout=300)
        if done.returncode != 0:
            raise unittest.SkipTest("installer failed:\n" + done.stdout + done.stderr)
        cls.log = os.path.join(cls.target, "blackboard", "22-automation-log.md")
        cls.before = _read(cls.log)
        env = dict(os.environ)
        env["HOME"] = cls.home
        env["BB_LOCK_DIR"] = os.path.join(cls.home, "locks")
        cls.proc = subprocess.run(
            [sys.executable, os.path.join(cls.target, "scripts", "os_nightly.py")],
            cwd=cls.target, env=env, capture_output=True, text=True, timeout=300)
        cls.after = _read(cls.log)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_the_installer_really_ships_the_snippet(self):
        """Guards the guard: if this fails the probe is checking nothing."""
        for marker in self.MARKERS:
            with self.subTest(marker=marker):
                self.assertIn(marker, self.before,
                              "a fresh install no longer ships %r in the "
                              "automation log; this test's premise is gone" % marker)

    def test_the_heartbeat_did_run(self):
        """Near-miss control: preserving the head must not mean writing nothing."""
        self.assertNotIn("Traceback (most recent call last)",
                         self.proc.stderr, self.proc.stderr)
        self.assertTrue(MACHINE_HEADING.search(self.after),
                        "the heartbeat wrote no dated entry at all:\n"
                        + self.proc.stdout + self.proc.stderr)
        self.assertIn("gauge:", self.after)

    def test_the_shipped_preamble_survives_the_first_heartbeat(self):
        for marker in self.MARKERS:
            with self.subTest(marker=marker):
                self.assertIn(
                    marker, self.after,
                    "one os_nightly run deleted %r from "
                    "blackboard/22-automation-log.md. The heartbeat may only "
                    "add entries below the preamble it was given." % marker)

    def test_the_readme_pointer_still_resolves_in_a_run_workspace(self):
        """README.md:136 names a file; that file must still hold the snippet."""
        line = _DOCS_CLAIMS.paragraph_with(
            (ROOT / "README.md").read_text(encoding="utf-8"),
            "os_nightly.py", "launchd")
        self.assertTrue(line, "README no longer says where the launchd snippet lives")
        refs = _DOCS_CLAIMS.ReadmeLaunchdPointerTests.CLAIM.findall(line)
        self.assertTrue(refs, "README's launchd sentence names no checkable path: " + line)
        holders = []
        for ref in refs:
            path = os.path.join(self.target, ref)
            if not os.path.isfile(path):
                continue
            text = _read(path)
            if all(m in text for m in self.MARKERS):
                holders.append(ref)
        self.assertTrue(
            holders,
            "README tells a reader the snippet is in %s, but after one "
            "heartbeat no such file in an installed workspace contains it (%s)."
            % (refs, ", ".join(self.MARKERS)))


class _InProcessLog(unittest.TestCase):
    """Drives write_entry() directly against a throwaway log file."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="os-nightly-log-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bb = os.path.join(self.tmp, "blackboard")
        os.makedirs(self.bb)
        self.log = os.path.join(self.bb, "22-automation-log.md")
        self.nightly = _load("os_nightly_log_under_test", NIGHTLY)
        self.nightly.LOG = self.log
        # The lock is exercised by tests/test_bb_lock_*; here it must not
        # compete for the process-wide open()/os.fdopen patches below.
        self.calls = []
        self.nightly.bb_lock.acquire = lambda *a, **k: self.calls.append("acquire") or True
        self.nightly.bb_lock.release = lambda *a, **k: self.calls.append("release")

    def ship_template(self):
        shutil.copyfile(str(TEMPLATE_LOG), self.log)

    def read(self):
        return _read(self.log)

    def beat(self, n=1):
        for _ in range(n):
            self.assertTrue(self.nightly.write_entry(["gauge: OVERALL: OK"]))


class ManualTextInTheLogIsNotDeleted(_InProcessLog):

    NOTE = "IMPORTANT MANUAL NOTE: rotate the Figma key before Friday."

    def test_prose_above_the_first_entry_survives(self):
        self.ship_template()
        with io.open(self.log, "a", encoding="utf-8") as f:
            f.write("\n" + self.NOTE + "\n")
        self.beat(3)
        self.assertIn(self.NOTE, self.read(),
                      "the heartbeat deleted an operator note that sat above "
                      "the first entry, and said nothing about it")

    def test_a_hand_written_heading_is_not_rotated_out_by_the_cap(self):
        self.ship_template()
        self.beat(1)
        with io.open(self.log, "a", encoding="utf-8") as f:
            f.write("\n## MANUAL NOTE\n\n- do not delete\n")
        self.beat(self.nightly.MAX_ENTRIES + 5)
        self.assertIn("MANUAL NOTE", self.read(),
                      "the 30-entry cap counted a hand-written block as a "
                      "machine entry and rotated it out")

    def test_machine_entries_are_still_capped(self):
        """Near-miss control: keeping text must not mean keeping everything."""
        self.ship_template()
        self.beat(self.nightly.MAX_ENTRIES + 12)
        kept = len(MACHINE_HEADING.findall(self.read()))
        self.assertEqual(
            kept, self.nightly.MAX_ENTRIES,
            "the cap no longer holds: %d dated entries kept, expected %d"
            % (kept, self.nightly.MAX_ENTRIES))

    def test_a_missing_log_is_created_with_the_header(self):
        """Control: with nothing to preserve the module's own HEADER is used."""
        self.assertFalse(os.path.exists(self.log))
        self.beat(1)
        text = self.read()
        self.assertTrue(text.startswith("# 22"), text[:120])
        self.assertTrue(MACHINE_HEADING.search(text))

    def test_the_newest_entry_is_first(self):
        """Control: preserving the head must not turn a prepend into an append."""
        self.ship_template()
        self.beat(1)
        first = self.read()
        self.nightly.write_entry(["gauge: SECOND RUN MARKER"])
        body = self.read()
        self.assertLess(body.index("SECOND RUN MARKER"), body.index("OVERALL: OK"),
                        "newest entry is no longer first:\n" + body)
        self.assertIn("OVERALL: OK", body)
        self.assertTrue(first)


class LogWriteIsAtomic(_InProcessLog):
    """A crash mid-write must not cost the history that was already on disk."""

    ENTRIES = 4

    def seed(self):
        """A log with real history in it, whatever the preamble ends up being.

        Deliberately does NOT assert on the preamble: this class isolates the
        truncating rewrite, so it must fail on the pre-fix code for the crash
        and not for the header defect the class above owns.
        """
        self.ship_template()
        self.beat(self.ENTRIES)
        seeded = self.read()
        self.assertEqual(len(MACHINE_HEADING.findall(seeded)), self.ENTRIES,
                         "the seed did not build the history this test protects")
        return seeded

    def _inject(self, where):
        """Make one write point die with ENOSPC, and nothing else."""
        real_fdopen, real_replace = os.fdopen, os.replace
        log = os.path.abspath(self.log)
        boom = OSError(28, "No space left on device")

        class _Patch(object):
            def __enter__(inner):
                import builtins

                inner.builtins = builtins
                inner.real_builtin_open = builtins.open

                def open_(file, mode="r", *a, **k):
                    if "w" in mode and os.path.abspath(str(file)) == log:
                        handle = inner.real_builtin_open(file, mode, *a, **k)
                        if where == "truncate":
                            # open(..., "w") has ALREADY emptied the file by the
                            # time it returns. Dying here is the cheap kill -9
                            # / power-loss case, and it is the one that used to
                            # cost the whole log.
                            handle.close()
                            raise boom
                        if where == "write":
                            return _Broken(handle)
                        return handle
                    return inner.real_builtin_open(file, mode, *a, **k)

                def fdopen_(fd, *a, **k):
                    if where == "fdopen":
                        os.close(fd)
                        raise boom
                    handle = real_fdopen(fd, *a, **k)
                    if where == "write":
                        return _Broken(handle)
                    return handle

                def replace_(src, dst, *a, **k):
                    if where == "replace":
                        raise boom
                    return real_replace(src, dst, *a, **k)

                builtins.open = open_
                os.fdopen = fdopen_
                os.replace = replace_
                return inner

            def __exit__(inner, *exc):
                inner.builtins.open = inner.real_builtin_open
                os.fdopen = real_fdopen
                os.replace = real_replace
                return False

        class _Broken(object):
            """Writes a little, then dies -- an ENOSPC in the middle of a write."""

            def __init__(self, handle):
                self._h = handle
                self._done = False

            def write(self, text):
                if self._done:
                    raise boom
                self._done = True
                self._h.write(text[:24])
                self._h.flush()
                raise boom

            def __getattr__(self, name):
                return getattr(self._h, name)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                try:
                    self._h.close()
                except Exception:
                    pass
                return False

        return _Patch()

    def _write_under(self, where):
        """Run one entry through the injection; return (crashed, text_after)."""
        crashed = False
        try:
            with self._inject(where):
                if not self.nightly.write_entry(["gauge: THE ENTRY THAT DIED"]):
                    crashed = True
        except OSError:
            crashed = True
        return crashed, _read(self.log)

    def test_history_survives_a_failure_at_every_write_point(self):
        """Every point a rewrite can die at, not just the ones a fix avoids.

        The injection is deliberately not aimed at the pre-fix implementation:
        ``truncate``/``write`` are the points ``open(LOG, "w")`` passes
        through, and ``fdopen``/``replace`` are the ones a temp-file-and-rename
        passes through. Whichever implementation is under test, at least two of
        the four bite it, and the invariant is the same for all four -- either
        the entry landed and the old entries are all still there, or nothing
        moved at all. Nothing in between.
        """
        for where in ("truncate", "write", "fdopen", "replace"):
            with self.subTest(failed_at=where):
                shutil.rmtree(self.bb, ignore_errors=True)
                os.makedirs(self.bb)
                before = self.seed()
                crashed, after = self._write_under(where)
                if crashed:
                    self.assertEqual(
                        after, before,
                        "a failure at %r left the automation log changed: %d "
                        "bytes and %d entries before, %d bytes and %d entries "
                        "after. An interrupted append must cost nothing -- "
                        "build the text, write it to a sibling temp file, then "
                        "os.replace() it on."
                        % (where, len(before), self.ENTRIES, len(after),
                           len(MACHINE_HEADING.findall(after))))
                else:
                    # The injection did not reach this implementation. The
                    # write therefore has to have fully succeeded.
                    self.assertIn("THE ENTRY THAT DIED", after)
                    self.assertEqual(
                        len(MACHINE_HEADING.findall(after)), self.ENTRIES + 1,
                        "the write reported success at %r but lost entries" % where)

    def test_the_swap_happens_inside_the_lock(self):
        """The atomic rename must not be moved out from under bb_lock."""
        self.ship_template()
        self.beat(1)
        self.assertEqual(self.calls, ["acquire", "release"])

    def test_the_swap_does_not_change_the_logs_permissions(self):
        """tempfile.mkstemp() always creates 0600 and rename carries that over.

        A naive temp-file rewrite therefore silently re-permissions the file it
        replaces -- the same trap scripts/brain_archive.py documents. Here it
        would go the other way: a log a team can read becomes owner-only.
        """
        self.ship_template()
        os.chmod(self.log, 0o644)
        self.beat(2)
        self.assertEqual(
            stat.S_IMODE(os.stat(self.log).st_mode), 0o644,
            "the heartbeat changed the automation log's mode to %s"
            % oct(stat.S_IMODE(os.stat(self.log).st_mode)))

    def test_a_stale_temp_file_is_not_left_behind(self):
        """Crash-safety must not be bought with litter in blackboard/."""
        before = self.seed()
        crashed, after = self._write_under("write")
        self.assertTrue(crashed, "the ENOSPC injection did not bite the writer")
        leftovers = [n for n in os.listdir(self.bb)
                     if n != os.path.basename(self.log)]
        self.assertEqual(leftovers, [],
                         "a failed write left %s behind in blackboard/" % leftovers)
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
