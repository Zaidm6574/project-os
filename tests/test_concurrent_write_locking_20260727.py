#!/usr/bin/env python3
"""Concurrent-write hardening for the full-engine brain + vector store (2026-07-27).

Two unlocked concurrent-write paths that lose/corrupt data under contention:

(a) addons/full-engine/brain/central_brain.py :: append_new() did an UNLOCKED
    read-modify-append on shared-brain.jsonl. The dedup read (which ids already
    exist?) and the append were not serialised, so two agents re-pushing the
    same logical record BOTH read the pre-append snapshot, BOTH judged it new,
    and BOTH appended it -- a duplicated record and an over-counted return in
    the cross-project brain. scripts/brain_archive.py guards that same file with
    scripts/bb_lock.py; the fix reuses that exact lock so the two serialise
    against each other instead of racing.

(b) addons/full-engine/memory/osvec_adapter.py :: _BruteForceIndex.write() did a
    plain np.savez(), which truncates the target zip and streams it out. A crash
    / full disk / killed rebuild left project.tvim.npz TORN -- a half-written
    zip np.load() cannot read -- destroying the whole index. save() already
    holds an exclusive flock, so the remaining hole was atomicity: the fix
    writes a sibling temp file and os.replace()s it into place, so an
    interrupted write leaves the PREVIOUS index intact.

Determinism, not timing:
  * (a) drives a controlled interleave (monkeypatched read seam + events) so the
    duplicate is produced iff the lock is absent. The only bounded wait is the
    one that distinguishes "the other thread is correctly blocked on the lock"
    from "it proceeded"; it changes latency, never the asserted record count.
  * (b) simulates the interrupted write directly (monkeypatched np.savez that
    tears the file it is handed, then raises). No threads, fully deterministic.

Both halves are mutation-verified: revert the source fix and the matching test
FAILS. See the lane report for the three-step proof.

CI floor note: osvec_adapter hard-imports numpy at module scope (correctly -- it
is on the add/search path), so on the stdlib-only CI floor (/usr/bin/python3,
no numpy) the osvec half skips with an explicit reason, exactly as
tests/test_osvec_lock_20260726.py does. The central_brain half needs no numpy
and runs on the floor.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CENTRAL_BRAIN_PATH = ROOT / "addons" / "full-engine" / "brain" / "central_brain.py"
OSVEC_PATH = ROOT / "addons" / "full-engine" / "memory" / "osvec_adapter.py"

try:
    import fcntl  # noqa: F401
except ImportError:  # pragma: no cover - non-POSIX
    fcntl = None

# A generous cap so a loaded box cannot flake the interleave: it is only ever
# waited out when the OTHER thread is (correctly) blocked on the lock, and the
# asserted record count does not depend on it.
JOIN_TIMEOUT = 30.0


def _load_isolated_central_brain():
    """Load a fresh central_brain whose bb_lock uses an ISOLATED lock dir.

    bb_lock reads BB_LOCK_DIR once at import; central_brain loads bb_lock at
    import. So set BB_LOCK_DIR first, then load central_brain fresh (unique
    module name, not cached) so its bb_lock picks up our throwaway dir and the
    test never touches ~/.project-os/locks.
    """
    lock_dir = tempfile.mkdtemp(prefix="central-brain-lockdir-")
    os.environ["BB_LOCK_DIR"] = lock_dir
    spec = importlib.util.spec_from_file_location(
        "central_brain_conc_probe", CENTRAL_BRAIN_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["central_brain_conc_probe"] = module
    spec.loader.exec_module(module)
    return module, lock_dir


_CB_SKIP = None
cb = None
_CB_LOCK_DIR = None
_CB_DEFECT = None
if fcntl is None:
    _CB_SKIP = "fcntl is unavailable; bb_lock's advisory locking cannot run here"
else:
    try:
        cb, _CB_LOCK_DIR = _load_isolated_central_brain()
    except Exception as exc:
        # Not a skip: central_brain imports only stdlib plus its sibling brain,
        # so failing to load it is a defect in the tree, not a thin environment.
        _CB_DEFECT = "could not load central_brain: %r" % (exc,)
    else:
        if getattr(cb, "_BB_LOCK", None) is None:
            # A MISSING LOCK IS THE DEFECT THIS FILE EXISTS TO CATCH, so it must
            # never be a skip. Skipping here reported "OK (skipped=3)" when the
            # fix was reverted -- fake green, this repo's signature failure.
            # Recorded and raised per-test in setUp so it fails loudly instead.
            _CB_DEFECT = (
                "central_brain._BB_LOCK is not wired, so append_new's "
                "read-modify-append runs unserialised and concurrent agents can "
                "lose records -- the regression this test guards")


@unittest.skipUnless(_CB_SKIP is None, _CB_SKIP or "")
class CentralBrainAppendLocking(unittest.TestCase):
    """append_new()'s read-modify-append must be serialised under bb_lock."""

    def setUp(self):
        if _CB_DEFECT is not None:
            self.fail(_CB_DEFECT)
        self.tmp = tempfile.mkdtemp(prefix="central-brain-conc-")
        self.addCleanup(self._cleanup)
        self.brain = Path(self.tmp) / "shared-brain.jsonl"
        self.brain.touch()

    def _cleanup(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _records_with_id(self, rid):
        return [r for r in cb.read_jsonl(self.brain) if r.get("id") == rid]

    def test_concurrent_append_of_same_id_is_serialised_not_duplicated(self):
        """Two agents pushing the same record must land exactly one copy.

        Interleave (driven so the outcome is deterministic per lock state):

          1. Thread A calls append_new. Inside -- AFTER the dedup read, still
             holding the lock if one is taken -- it parks on `a_may_write`.
          2. Only once A has read do we start Thread B on the SAME record id.
          3. With the lock: B blocks on acquire() and never reaches its read
             (so `b_has_read` never fires and we simply wait it out). Without
             the lock: B reads the still-empty snapshot, appends, and fires
             `b_has_read`.
          4. We release A. It appends against its own snapshot.

        Locked  -> A writes, releases; B then reads a NON-empty file and skips
                   -> 1 copy.
        Unlocked-> A and B both read "absent" and both write -> 2 copies.
        """
        rid = "dup-race-1"
        record = {"id": rid, "type": "lesson", "text": "same logical lesson"}

        a_has_read = threading.Event()
        a_may_write = threading.Event()
        b_has_read = threading.Event()

        original_read = cb.read_jsonl

        def interleaved_read(path):
            result = original_read(path)
            name = threading.current_thread().name
            if name == "A":
                a_has_read.set()
                # Park inside the critical section so B has a chance to race.
                a_may_write.wait(JOIN_TIMEOUT)
            elif name == "B":
                b_has_read.set()
            return result

        errors = {}

        def call(name):
            def body():
                try:
                    cb.append_new(self.brain, [dict(record)])
                except BaseException as exc:  # noqa: BLE001 - reported below
                    errors[name] = exc
            t = threading.Thread(name=name, target=body)
            return t

        cb.read_jsonl = interleaved_read
        try:
            ta = call("A")
            tb = call("B")
            ta.start()
            self.assertTrue(a_has_read.wait(JOIN_TIMEOUT),
                            "thread A never reached its dedup read")
            tb.start()
            # If the lock works, B is now blocked on acquire() and will not
            # read; time out and proceed. If it does not, B reads/appends fast.
            b_has_read.wait(3.0)
            a_may_write.set()
            ta.join(JOIN_TIMEOUT)
            tb.join(JOIN_TIMEOUT)
        finally:
            cb.read_jsonl = original_read

        self.assertFalse(ta.is_alive(), "thread A did not finish")
        self.assertFalse(tb.is_alive(), "thread B did not finish")
        self.assertEqual(errors, {}, "append_new raised: %r" % (errors,))

        copies = self._records_with_id(rid)
        self.assertEqual(
            len(copies), 1,
            "unlocked read-modify-append let two agents both append the same "
            "record: expected 1 copy, found %d. append_new must serialise the "
            "dedup-read + append under bb_lock (as scripts/brain_archive.py "
            "does for this file)." % len(copies),
        )
        raw = [ln for ln in self.brain.read_text().splitlines() if ln.strip()]
        self.assertEqual(len(raw), 1, "file holds duplicate/extra lines: %r" % raw)


# --------------------------------------------------------------------------- #
# (b) osvec_adapter index write atomicity
# --------------------------------------------------------------------------- #
_OSVEC_SKIP = None
osvec = None
try:
    import numpy as np  # noqa: F401
except Exception as exc:  # pragma: no cover - below-floor CI leg (no numpy)
    _OSVEC_SKIP = (
        "numpy is not installed; osvec_adapter imports numpy at module scope, "
        "so its index write cannot be exercised here (%s)"
        % (exc.__class__.__name__,)
    )
else:
    spec = importlib.util.spec_from_file_location("osvec_atomic_probe", OSVEC_PATH)
    osvec = importlib.util.module_from_spec(spec)
    sys.modules["osvec_atomic_probe"] = osvec
    spec.loader.exec_module(osvec)


def _torn_savez_factory():
    """A np.savez stand-in that TEARS the file it is handed, then crashes.

    Faithful to a real interrupted np.savez: it opens the destination zip in
    write mode (truncating any prior content) and dies before finishing, so the
    target is left as an unreadable partial zip.
    """
    def torn_savez(file, *args, **kwargs):
        name = os.fspath(file)
        if not name.endswith(".npz"):
            name += ".npz"
        with open(name, "wb") as fh:
            fh.write(b"PK\x03\x04 torn partial zip, write interrupted")
        raise RuntimeError("simulated crash mid index write")
    return torn_savez


@unittest.skipUnless(_OSVEC_SKIP is None, _OSVEC_SKIP or "")
class OsvecIndexAtomicWrite(unittest.TestCase):
    """An interrupted index write must leave the PREVIOUS index intact."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="osvec-atomic-")
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_interrupted_bruteforce_write_preserves_previous_index(self):
        """Direct _BruteForceIndex.write() unit: torn new write != lost old index."""
        dim = osvec.DIM
        base = os.path.join(self.tmp, "project.tvim")

        good = osvec._BruteForceIndex(dim)
        good.add_with_ids(np.ones((2, dim), dtype=np.float32),
                          np.array([11, 22], dtype=np.uint64))
        good.write(base)
        self.assertEqual(sorted(osvec._BruteForceIndex.load(base, dim)._ids),
                         [11, 22], "precondition: valid index did not round-trip")

        replacement = osvec._BruteForceIndex(dim)
        replacement.add_with_ids(np.zeros((3, dim), dtype=np.float32),
                                 np.array([7, 8, 9], dtype=np.uint64))

        original_savez = osvec.np.savez
        osvec.np.savez = _torn_savez_factory()
        try:
            with self.assertRaises(RuntimeError):
                replacement.write(base)
        finally:
            osvec.np.savez = original_savez

        # No leftover temp files littering the store dir.
        self.assertEqual(
            [f for f in os.listdir(self.tmp) if f != "project.tvim.npz"], [],
            "interrupted atomic write left a temp file behind: %r"
            % os.listdir(self.tmp),
        )
        # The whole point: the previous index is still loadable and unchanged.
        try:
            recovered = osvec._BruteForceIndex.load(base, dim)
        except Exception as exc:  # pragma: no cover - this IS the regression
            self.fail("interrupted write TORE the previous index: %r" % (exc,))
        self.assertEqual(
            sorted(recovered._ids), [11, 22],
            "interrupted index write corrupted/replaced the previous index",
        )

    def test_interrupted_save_preserves_previous_on_disk_index(self):
        """End-to-end: a save() whose index write is interrupted keeps the
        previously-persisted index file readable (integration over the fix)."""
        saved = (osvec.STORE_DIR, osvec.INDEX_PATH, osvec.SIDECAR_PATH)
        osvec.STORE_DIR = self.tmp
        osvec.INDEX_PATH = os.path.join(self.tmp, "project.tvim")
        osvec.SIDECAR_PATH = os.path.join(self.tmp, "project.sidecar.json")
        # Force the bruteforce backend even where turbovec is installed, so we
        # exercise the fallback writer this fix hardens.
        original_new_index = osvec._new_index
        osvec._new_index = lambda dim: (osvec._BruteForceIndex(dim),
                                        osvec._BruteForceIndex.backend)
        self.assertNotEqual(
            os.path.realpath(self.tmp),
            os.path.realpath(os.path.join(OSVEC_PATH.parent, "store")),
            "test would have written into the live OSVec store",
        )
        try:
            mem = osvec.ProjectMemory()
            for i in range(3):
                mem.add("seed note %d about project tiers" % i, "lesson",
                        memory_id="seed-%d" % i)
            mem.save()
            expected = sorted(
                osvec._BruteForceIndex.load(osvec.INDEX_PATH, mem.dim)._ids)
            self.assertEqual(len(expected), 3, "precondition: 3 seeds persisted")

            mem.add("a fourth note added before the crash", "lesson",
                    memory_id="seed-3")
            original_savez = osvec.np.savez
            osvec.np.savez = _torn_savez_factory()
            try:
                with self.assertRaises(RuntimeError):
                    mem.save()
            finally:
                osvec.np.savez = original_savez

            try:
                recovered = sorted(
                    osvec._BruteForceIndex.load(osvec.INDEX_PATH, mem.dim)._ids)
            except Exception as exc:  # pragma: no cover - this IS the regression
                self.fail("interrupted save() TORE the on-disk index: %r" % (exc,))
            self.assertEqual(
                recovered, expected,
                "interrupted save() left the on-disk index torn/changed; the "
                "previous complete index must survive",
            )
        finally:
            osvec._new_index = original_new_index
            osvec.STORE_DIR, osvec.INDEX_PATH, osvec.SIDECAR_PATH = saved


if __name__ == "__main__":
    unittest.main()
