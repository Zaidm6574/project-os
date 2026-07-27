#!/usr/bin/env python3
"""Security guards for scripts/brain_archive.py (audit 2026-07-26).

1. Symlink-through-a-predictable-temp-path (CRITICAL). The rewrite of the
   active brain went through a fixed "<brain>.tmp" name opened with plain
   open(..., "w"). open() FOLLOWS symlinks, so anyone able to create a file in
   the brain directory could pre-plant a link there and have the archive run
   truncate an arbitrary file the user can write -- and because os.replace()
   does NOT follow links, the planted link was then renamed over the brain,
   leaving the active brain aliased to the file it had just destroyed. The fix
   creates the temp file with tempfile.mkstemp(), whose O_CREAT|O_EXCL refuses
   any pre-existing path (symlinks included), and refuses outright when the
   brain or archive path is itself a link out of the brain directory.

2. Silent permission widening (MEDIUM). The temp file was created by the
   process umask (0644 on a stock account) and rename carried that mode onto
   the destination, so archiving handed a deliberately 0600 brain -- lesson
   text harvested from every project -- back as world-readable, with no error
   and no message. Same for the archive file's first append. The fix stats the
   destination and reproduces its mode, defaulting to 0600 (not the umask)
   when there is nothing to copy from.

Every test asserts the FILESYSTEM (bytes and st_mode), not the wording of the
output. Each loads a fresh module instance with PROJECT_OS_SHARED_BRAIN inside
a tempfile sandbox and stubs that instance's bb_lock/subprocess, so no live
lock is taken, the real mneme rebuild never runs, and nothing outside the
sandbox is touched. The umask is pinned so the permission assertions mean the
same thing on every machine. Stdlib only; passes on python 3.9.6.
"""

import contextlib
import importlib.util
import io
import json
import os
import stat
import sys
import tempfile
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRAIN_ARCHIVE_PY = os.path.join(ROOT, "scripts", "brain_archive.py")
ENV = "PROJECT_OS_SHARED_BRAIN"

_SEQ = [0]

# Rows shaped like the shipped shared-brain schema. The "interest" row is old
# enough to be an auto-candidate at the default 60d; the lesson row stays.
OLD_INTEREST = {"id": "interest-old-topic", "type": "interest",
                "ts": "2020-01-02T00:00:00Z", "text": "stale interest row"}
KEEP_LESSON = {"id": "lesson-keep", "type": "lesson",
               "ts": "2026-07-01T00:00:00Z", "text": "durable lesson row"}

# Stand-in for "an arbitrary file the user can write" outside the brain tree.
VICTIM_BYTES = b"signed client contract - the only copy\n"


def _load_brain_archive(brain_path):
    """Import a fresh scripts/brain_archive.py instance with BRAIN=brain_path.

    BRAIN/ARCHIVE are resolved from the environment at import time, so every
    test needs its own instance pointed at its own sandbox.
    """
    _SEQ[0] += 1
    name = "brain_archive_security_%d" % _SEQ[0]
    old = os.environ.get(ENV)
    os.environ[ENV] = brain_path
    try:
        spec = importlib.util.spec_from_file_location(name, BRAIN_ARCHIVE_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        if old is None:
            os.environ.pop(ENV, None)
        else:
            os.environ[ENV] = old
    return mod


def _stub_side_effects(mod):
    """Stub the loaded instance's bb_lock and subprocess (no lock, no rebuild)."""
    mod.bb_lock = types.SimpleNamespace(
        acquire=lambda target, agent="unknown", wait=10.0: True,
        release=lambda target, agent="unknown", force=False, token=None: True)
    mod.subprocess = types.SimpleNamespace(
        run=lambda *a, **k: types.SimpleNamespace(stdout="", stderr=""))


def _write_rows(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for o in rows:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")


def _read_rows(path):
    if not os.path.isfile(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _ids(rows):
    return [o.get("id") for o in rows]


def _mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


class _Sandbox(unittest.TestCase):
    """Brain directory + an unrelated 'outside' directory, umask pinned."""

    def setUp(self):
        # Pin the umask: the defect these tests catch is "the file came back
        # wearing the umask default instead of its own mode", which is only a
        # visible difference when the umask is known (0o022 -> 0644).
        old_umask = os.umask(0o022)
        self.addCleanup(os.umask, old_umask)
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
        os.chmod(self.victim, 0o600)

    def make_brain(self, rows=None, mode=0o600):
        _write_rows(self.brain, rows if rows is not None
                    else [OLD_INTEREST, KEEP_LESSON])
        os.chmod(self.brain, mode)
        self.mod = _load_brain_archive(self.brain)
        _stub_side_effects(self.mod)
        return self.mod

    def apply(self, ids=("interest-old-topic",), interest_days=60):
        out, err = io.StringIO(), io.StringIO()
        ns = types.SimpleNamespace(ids=list(ids) if ids else None,
                                   interest_days=interest_days)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = self.mod.cmd_apply(ns)
        return rc, out.getvalue(), err.getvalue()

    def assert_victim_untouched(self):
        with open(self.victim, "rb") as f:
            self.assertEqual(
                f.read(), VICTIM_BYTES,
                "an unrelated file OUTSIDE the brain directory was written "
                "through a pre-planted symlink; the archive run destroyed it")
        self.assertEqual(_mode(self.victim), 0o600)

    def backups(self):
        return sorted(p for p in os.listdir(self.brain_dir)
                      if ".pre-archive-" in p)


class PrePlantedTempSymlinkIsNeverWrittenThrough(_Sandbox):
    """Fix 1: the rewrite must not follow anything already sitting at its path."""

    def test_symlink_at_predicted_tmp_path_leaves_outside_file_untouched(self):
        self.make_brain()
        planted = self.brain + ".tmp"  # the name the old code always used
        os.symlink(self.victim, planted)

        rc, printed, _ = self.apply()

        # The whole point: assert the filesystem, not the message.
        self.assert_victim_untouched()
        self.assertTrue(
            os.path.islink(planted) and
            os.path.realpath(os.readlink(planted)) ==
            os.path.realpath(self.victim),
            "the planted link was consumed instead of ignored")
        self.assertFalse(
            os.path.islink(self.brain),
            "the active brain was replaced by the planted symlink, so it now "
            "aliases a file outside the brain directory")
        # ...and the archive still did its real job.
        self.assertEqual(rc, 0)
        self.assertIn("archived 1", printed)
        self.assertEqual(_ids(_read_rows(self.brain)), ["lesson-keep"])
        self.assertEqual(_ids(_read_rows(self.mod.ARCHIVE)),
                         ["interest-old-topic"])

    def test_symlink_at_predicted_tmp_path_keeps_brain_private(self):
        """The mode fix must survive the symlink case too (0600 stays 0600)."""
        self.make_brain(mode=0o600)
        os.symlink(self.victim, self.brain + ".tmp")

        rc, _, _ = self.apply()

        self.assertEqual(rc, 0)
        self.assert_victim_untouched()
        self.assertEqual(_mode(self.brain), 0o600)


class PrePlantedBackupSymlinkIsNeverWrittenThrough(_Sandbox):
    """The backup name is timestamped to the second, i.e. equally guessable."""

    def freeze_stamp(self):
        self.mod.time = types.SimpleNamespace(
            strftime=lambda fmt: "20260726-000000")
        return self.brain + ".pre-archive-20260726-000000"

    def test_link_at_the_backup_name_is_stepped_over_not_followed(self):
        self.make_brain()
        base = self.freeze_stamp()
        os.symlink(self.victim, base)

        rc, printed, errs = self.apply()

        self.assertEqual(rc, 0, printed + errs)
        self.assert_victim_untouched()
        # The real backup went to a sibling name, and apply printed that name.
        real = base + "-2"
        self.assertTrue(os.path.isfile(real) and not os.path.islink(real))
        self.assertIn("(backup: %s)" % real, printed)
        self.assertEqual(_ids(_read_rows(real)),
                         ["interest-old-topic", "lesson-keep"])

    def test_every_backup_name_planted_refuses_instead_of_archiving(self):
        self.make_brain()
        base = self.freeze_stamp()
        for n in range(1, 51):
            os.symlink(self.victim, base if n == 1 else "%s-%d" % (base, n))
        before = open(self.brain, encoding="utf-8").read()

        rc, printed, errs = self.apply()

        self.assertEqual(rc, 2, printed + errs)
        self.assertIn("REFUSED", errs)
        self.assert_victim_untouched()
        self.assertEqual(open(self.brain, encoding="utf-8").read(), before)
        self.assertFalse(os.path.exists(self.mod.ARCHIVE))


class BrainPermissionsSurviveArchiving(_Sandbox):
    """Fix 2: reproduce the destination's mode, never the umask default."""

    def test_private_brain_is_still_private_after_apply(self):
        self.make_brain(mode=0o600)

        rc, _, _ = self.apply()

        self.assertEqual(rc, 0)
        self.assertEqual(
            _mode(self.brain), 0o600,
            "archiving widened the shared brain's permissions; its lesson "
            "text is now readable by every account on the machine")
        # Content preserved, not just permissions.
        self.assertEqual(_ids(_read_rows(self.brain)), ["lesson-keep"])
        self.assertEqual(_read_rows(self.brain)[0]["text"],
                         KEEP_LESSON["text"])

    def test_new_archive_is_no_wider_than_the_brain(self):
        """The archive holds the same lesson text; it must inherit 0600 too."""
        self.make_brain(mode=0o600)

        rc, _, _ = self.apply()

        self.assertEqual(rc, 0)
        self.assertEqual(
            _mode(self.mod.ARCHIVE), 0o600,
            "the archive file was created world-readable from a private "
            "brain, so the moved lessons leaked anyway")

    def test_backup_keeps_the_brains_mode(self):
        self.make_brain(mode=0o600)

        rc, _, _ = self.apply()

        self.assertEqual(rc, 0)
        names = self.backups()
        self.assertEqual(len(names), 1, names)
        backup = os.path.join(self.brain_dir, names[0])
        self.assertEqual(_mode(backup), 0o600)
        self.assertEqual(_ids(_read_rows(backup)),
                         ["interest-old-topic", "lesson-keep"])

    def test_deliberately_shared_brain_keeps_its_wider_mode(self):
        """NEAR-MISS: preserve, don't force-narrow. A 0644 brain stays 0644."""
        self.make_brain(mode=0o644)

        rc, _, _ = self.apply()

        self.assertEqual(rc, 0)
        self.assertEqual(
            _mode(self.brain), 0o644,
            "a brain its owner made group/world readable was narrowed to "
            "0600 behind their back")
        self.assertEqual(_mode(self.mod.ARCHIVE), 0o644)
        self.assertEqual(_ids(_read_rows(self.brain)), ["lesson-keep"])


class SymlinkOutOfTheBrainDirectoryIsRefused(_Sandbox):
    """A brain path that leaves its own directory is refused, not followed."""

    def test_brain_symlinked_outside_is_refused_and_nothing_moves(self):
        outside_brain = os.path.join(self.outside_dir, "someone-elses.jsonl")
        _write_rows(outside_brain, [OLD_INTEREST, KEEP_LESSON])
        os.chmod(outside_brain, 0o600)
        os.symlink(outside_brain, self.brain)
        self.mod = _load_brain_archive(self.brain)
        _stub_side_effects(self.mod)

        rc, printed, errs = self.apply()

        self.assertEqual(rc, 2, printed + errs)
        self.assertIn("REFUSED", errs)
        self.assertTrue(
            os.path.islink(self.brain),
            "the refusal still consumed the user's symlink and left a "
            "regular file in its place")
        self.assertEqual(
            _ids(_read_rows(outside_brain)),
            ["interest-old-topic", "lesson-keep"],
            "entries were moved out of a file outside the brain directory")
        self.assertFalse(os.path.exists(self.mod.ARCHIVE))
        self.assertEqual(self.backups(), [])

    def test_archive_symlinked_outside_is_refused_before_any_append(self):
        """The archive carries the same lesson text: an escaping link leaks it."""
        self.make_brain(mode=0o600)
        os.symlink(self.victim, self.mod.ARCHIVE)
        before = open(self.brain, encoding="utf-8").read()

        rc, printed, errs = self.apply()

        self.assertEqual(rc, 2, printed + errs)
        self.assertIn("REFUSED", errs)
        self.assert_victim_untouched()  # not one archived lesson appended to it
        self.assertEqual(open(self.brain, encoding="utf-8").read(), before)
        self.assertEqual(self.backups(), [])

    def test_brain_symlinked_inside_the_directory_is_refused(self):
        """A DELIBERATE reversal: an in-directory link is refused too.

        This used to assert the opposite — that flipping an active-store link
        was a supported layout, so a link resolving inside the brain directory
        was allowed. An adversary took that apart the same day: with the
        ARCHIVE aliased to the BRAIN, the moved entry was appended to the brain
        and then overwritten by the rewrite, so it survived in NEITHER file
        while the tool printed "archived 1" and the module docstring promised
        nothing is ever deleted.

        A link inside the directory cannot be told apart from a link an
        attacker planted there, and the write paths cannot make either one
        safe. Refusing with a clear message is honest; the operator can point
        PROJECT_OS_SHARED_BRAIN at the store directly instead.
        """
        store = os.path.join(self.brain_dir, "store-a.jsonl")
        _write_rows(store, [OLD_INTEREST, KEEP_LESSON])
        os.chmod(store, 0o600)
        os.symlink(store, self.brain)
        self.mod = _load_brain_archive(self.brain)
        _stub_side_effects(self.mod)

        rc, printed, errs = self.apply()

        self.assertEqual(rc, 2, printed + errs)
        self.assertIn("REFUSED", errs)
        # the store keeps BOTH rows: nothing was archived, nothing was lost
        self.assertEqual(_ids(_read_rows(store)),
                         ["interest-old-topic", "lesson-keep"])
        self.assertFalse(os.path.exists(self.mod.ARCHIVE))


class OrdinaryArchiveRunStillWorks(_Sandbox):
    """NEAR-MISS: no symlinks anywhere -- behave exactly as documented."""

    def test_plain_apply_moves_one_entry_and_reports_it(self):
        self.make_brain(mode=0o600)

        rc, printed, errs = self.apply()

        self.assertEqual(rc, 0, printed + errs)
        self.assertEqual(errs, "")
        self.assertIn("archived 1 -> %s" % self.mod.ARCHIVE, printed)
        self.assertIn("active brain: 2 -> 1 entries (backup: ", printed)
        self.assertEqual(_ids(_read_rows(self.brain)), ["lesson-keep"])
        archived = _read_rows(self.mod.ARCHIVE)
        self.assertEqual(_ids(archived), ["interest-old-topic"])
        self.assertIn("archived", archived[0])  # date stamp added on the way in
        names = self.backups()
        self.assertEqual(len(names), 1, names)
        self.assertIn("(backup: %s)" % os.path.join(self.brain_dir, names[0]),
                      printed)
        self.assertEqual(
            [p for p in os.listdir(self.brain_dir) if p.endswith(".tmp")], [],
            "a temp file was left behind in the brain directory")

    def test_apply_by_interest_days_still_selects_the_old_row(self):
        self.make_brain(mode=0o600)

        rc, printed, errs = self.apply(ids=None)

        self.assertEqual(rc, 0, printed + errs)
        self.assertIn("archived 1", printed)
        self.assertEqual(_ids(_read_rows(self.brain)), ["lesson-keep"])
        self.assertEqual(_mode(self.brain), 0o600)

    def test_two_archives_in_the_same_second_keep_both_backups(self):
        """Backups are 'kept alongside': a name clash must not clobber one."""
        second = dict(OLD_INTEREST, id="interest-second")
        self.make_brain(rows=[OLD_INTEREST, second, KEEP_LESSON], mode=0o600)
        # Freeze the timestamp so both runs compute the same backup name.
        self.mod.time = types.SimpleNamespace(
            strftime=lambda fmt: "20260726-000000")

        rc1, printed1, _ = self.apply()
        rc2, printed2, _ = self.apply(ids=("interest-second",))

        self.assertEqual((rc1, rc2), (0, 0), printed1 + printed2)
        names = self.backups()
        self.assertEqual(len(names), 2, "the second run overwrote the first "
                                        "run's backup: %s" % names)
        first = _read_rows(os.path.join(self.brain_dir, names[0]))
        self.assertEqual(_ids(first),
                         ["interest-old-topic", "interest-second",
                          "lesson-keep"])
        self.assertEqual(_ids(_read_rows(self.brain)), ["lesson-keep"])
        for name in names:
            self.assertEqual(_mode(os.path.join(self.brain_dir, name)), 0o600)


class HardLinkAtTheArchiveIsRefused(_Sandbox):
    """os.path.islink() is BLIND to a hard link.

    The first version of this guard checked the PATH with islink(), and the
    archive append opened with O_CREAT|O_APPEND but neither O_EXCL nor
    O_NOFOLLOW. A hard link pre-planted at the archive name therefore appended
    every archived lesson — the text harvested from every project — into an
    arbitrary same-filesystem file, with no race at all: rc=0, empty stderr,
    "archived 1" printed (adversary 2026-07-26).

    Only the open FILE DESCRIPTOR can see this, which is why the check is
    fstat(fd).st_nlink and not another path test.
    """

    def test_a_hard_link_at_the_archive_name_is_refused(self):
        self.make_brain()
        os.link(self.victim, self.mod.ARCHIVE)

        rc, printed, errs = self.apply()

        self.assertEqual(rc, 2, printed + errs)
        self.assertIn("REFUSED", errs)
        self.assert_victim_untouched()

    def test_an_ordinary_archive_run_is_unaffected(self):
        """NEAR-MISS: st_nlink is 1 for a normal file — the gate must not fire."""
        self.make_brain()

        rc, printed, errs = self.apply()

        self.assertEqual(rc, 0, printed + errs)
        self.assertIn("archived 1", printed)
        self.assertEqual(_ids(_read_rows(self.mod.ARCHIVE)),
                         ["interest-old-topic"])


class SymlinkSwappedInAfterTheCheckCannotRedirectTheRewrite(_Sandbox):
    """The check-then-write window, closed by not re-resolving the path.

    _atomic_write_preserving_mode used to call os.path.realpath(dest) at write
    time. That re-followed a link planted AFTER the caller's check, so the
    rewrite landed on the link's target — destroying it and re-aliasing the
    brain to it — which is byte-for-byte the outcome the guard was added to
    prevent. os.replace() does not follow a symlink, so writing to the literal
    path replaces the LINK and leaves whatever it pointed at alone.
    """

    def test_a_link_planted_at_write_time_does_not_reach_its_target(self):
        self.make_brain()
        text = "".join(json.dumps(r) + "\n" for r in [KEEP_LESSON])
        os.unlink(self.brain)
        os.symlink(self.victim, self.brain)

        self.mod._atomic_write_preserving_mode(self.brain, text)

        self.assert_victim_untouched()


if __name__ == "__main__":
    unittest.main()
