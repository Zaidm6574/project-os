#!/usr/bin/env python3
"""Locking regression tests for addons/full-engine/memory/osvec_adapter.py.

The defect these encode (verified by execution 2026-07-26): ProjectMemory.load()
took an EXCLUSIVE flock and released it only inside the "store does not exist
yet" early return. On the normal path -- a store that already has records --
load() returned still holding the exclusive lock, and only save() ever released
it. So a read-only consumer (`osvec_adapter.py search`, `stats`, or any process
that loads once and never writes) held an exclusive lock on the store for its
entire lifetime and blocked every other process's load().

The fix must not trade that for a lost update, so these tests pin BOTH halves of
the reader/writer discipline:

  * a read-only load() holds no lock when it returns, and does not block behind
    another reader's shared lock;
  * a load(for_update=True) still serializes a whole read-modify-write cycle
    against a concurrent writer, and every exit path (save, close, `with`, a
    raising load) gives the lock back.

numpy: osvec_adapter hard-imports numpy at module scope, and that is correct --
numpy is on its core add/search path. So on the below-floor CI leg (stock
python3, stdlib only) this whole module skips with an explicit reason rather
than failing, per the house rule in .github/workflows/test.yml.
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
OSVEC_PATH = ROOT / "addons" / "full-engine" / "memory" / "osvec_adapter.py"

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX
    fcntl = None

_SKIP = None
osvec = None
try:
    import numpy  # noqa: F401
except Exception as exc:  # pragma: no cover - below-floor CI leg
    _SKIP = (
        "numpy is not installed; osvec_adapter imports numpy at module scope "
        "(correctly -- it is on the add/search path), so its locking cannot be "
        "exercised here (%s)" % (exc.__class__.__name__,)
    )
else:
    spec = importlib.util.spec_from_file_location("osvec_lock_probe", OSVEC_PATH)
    osvec = importlib.util.module_from_spec(spec)
    sys.modules["osvec_lock_probe"] = osvec
    spec.loader.exec_module(osvec)

if _SKIP is None and fcntl is None:  # pragma: no cover - non-POSIX
    _SKIP = "fcntl is unavailable, so this platform has no flock to assert on"

# Generously long: it is only ever waited out when the fix has regressed and a
# call that must not block does. Kept high so a loaded CI box cannot flake it.
LOCK_TIMEOUT = 30.0


@unittest.skipUnless(_SKIP is None, _SKIP or "")
class OsvecStoreLocking(unittest.TestCase):
    """All tests run against a throwaway store, never the live one."""

    @classmethod
    def setUpClass(cls):
        """Pay numpy's one-time BLAS warm-up (seconds) outside any timed wait."""
        import shutil

        saved = (osvec.STORE_DIR, osvec.INDEX_PATH, osvec.SIDECAR_PATH)
        warm = tempfile.mkdtemp(prefix="osvec-lock-warmup-")
        try:
            osvec.STORE_DIR = warm
            osvec.INDEX_PATH = os.path.join(warm, "project.tvim")
            osvec.SIDECAR_PATH = os.path.join(warm, "project.sidecar.json")
            mem = osvec.ProjectMemory()
            mem.add("warm up numpy so timing assertions measure locking only",
                    "lesson", memory_id="warmup")
            mem.save()
            osvec.ProjectMemory().load().search("warm up", k=1)
        finally:
            osvec.STORE_DIR, osvec.INDEX_PATH, osvec.SIDECAR_PATH = saved
            shutil.rmtree(warm, ignore_errors=True)

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="osvec-lock-test-")
        self.addCleanup(self._cleanup)
        self._saved = (osvec.STORE_DIR, osvec.INDEX_PATH, osvec.SIDECAR_PATH)
        osvec.STORE_DIR = self.tmp
        osvec.INDEX_PATH = os.path.join(self.tmp, "project.tvim")
        osvec.SIDECAR_PATH = os.path.join(self.tmp, "project.sidecar.json")
        self.lock_path = os.path.join(self.tmp, "project.lock")
        self.assertNotEqual(
            os.path.realpath(self.tmp),
            os.path.realpath(os.path.join(OSVEC_PATH.parent, "store")),
            "test would have written into the live OSVec store",
        )

    def _cleanup(self):
        import shutil

        osvec.STORE_DIR, osvec.INDEX_PATH, osvec.SIDECAR_PATH = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers ----------------------------------------------------------- #
    def _seed(self, n=1):
        """Create a store that already HAS records (the leaking code path)."""
        mem = osvec.ProjectMemory()
        for i in range(n):
            mem.add("seed note number %d about project tiers" % i,
                    "lesson", memory_id="seed-%d" % i)
        mem.save()
        self.assertTrue(os.path.exists(osvec.SIDECAR_PATH))
        return mem

    def _lock_is_free(self, exclusive=True):
        """Can an unrelated fd take the store lock right now?

        flock() conflicts between distinct open file descriptions even inside
        one process, so this is a true probe of what the adapter holds.
        """
        want = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        fd = open(self.lock_path, "a+")
        try:
            fcntl.flock(fd.fileno(), want | fcntl.LOCK_NB)
        except OSError:
            return False
        else:
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
            return True
        finally:
            fd.close()

    def _run_with_timeout(self, fn):
        """Run fn in a daemon thread; return (finished, error).

        Used so a call that is supposed NOT to block cannot hang the suite if
        the fix regresses -- it reports a failure instead.
        """
        done = threading.Event()
        box = {}

        def body():
            try:
                box["value"] = fn()
            except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
                box["error"] = exc
            finally:
                done.set()

        t = threading.Thread(target=body, daemon=True)
        t.start()
        finished = done.wait(LOCK_TIMEOUT)
        return finished, box

    # -- the leak ---------------------------------------------------------- #
    def test_readonly_load_of_a_populated_store_holds_no_lock(self):
        """THE defect: load() returned still holding the exclusive flock."""
        self._seed()
        mem = osvec.ProjectMemory().load()
        self.assertEqual(len(mem.sidecar), 1)
        self.assertTrue(
            self._lock_is_free(exclusive=True),
            "load() returned still holding the store lock, so a read-only "
            "consumer blocks every other process's load() for its lifetime",
        )

    def test_readonly_load_of_an_empty_store_holds_no_lock(self):
        mem = osvec.ProjectMemory().load()
        self.assertEqual(mem.sidecar, {})
        self.assertTrue(self._lock_is_free(exclusive=True),
                        "load() of a brand-new store leaked the lock")

    def test_readonly_load_still_reads_and_searches(self):
        """Releasing the lock must not cost the reader its data."""
        self._seed(n=2)
        mem = osvec.ProjectMemory().load()
        hits = mem.search("seed note about project tiers", k=2)
        self.assertTrue(hits, "read-only load produced an unsearchable store")
        self.assertEqual(mem.stats()["count"], 2)
        self.assertTrue(self._lock_is_free(exclusive=True))

    def test_two_readers_do_not_block_each_other(self):
        """A reader must take a SHARED lock, not an exclusive one."""
        self._seed()
        holder = open(self.lock_path, "a+")
        fcntl.flock(holder.fileno(), fcntl.LOCK_SH)
        try:
            finished, box = self._run_with_timeout(
                lambda: len(osvec.ProjectMemory().load().sidecar))
        finally:
            # Release before asserting: if load() DID block on an exclusive
            # acquire, this unblocks the daemon thread so it can finish.
            fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
            holder.close()
        self.assertTrue(
            finished,
            "load() blocked for %.0fs behind another reader's shared lock, so "
            "it is still taking an exclusive lock to read" % LOCK_TIMEOUT,
        )
        self.assertIsNone(box.get("error"), box.get("error"))
        self.assertEqual(box.get("value"), 1)

    def test_a_reader_does_not_block_a_writer(self):
        """The end-to-end symptom: reader alive -> another process can write."""
        self._seed()
        reader = osvec.ProjectMemory().load()
        self.assertEqual(len(reader.sidecar), 1)

        def write():
            w = osvec.ProjectMemory()
            w.load(for_update=True)
            w.add("a second note added while a reader was alive", "lesson",
                  memory_id="written-during-read")
            w.save()
            return True

        finished, box = self._run_with_timeout(write)
        self.assertTrue(finished,
                        "a live read-only consumer blocked a writer's load()")
        self.assertIsNone(box.get("error"), box.get("error"))
        after = osvec.ProjectMemory().load()
        self.assertIn("written-during-read", after.id_to_u64)

    # -- writers stay safe -------------------------------------------------- #
    def test_load_for_update_holds_an_exclusive_lock_until_save(self):
        self._seed()
        mem = osvec.ProjectMemory()
        mem.load(for_update=True)
        self.assertFalse(
            self._lock_is_free(exclusive=False),
            "load(for_update=True) must hold an EXCLUSIVE lock through the "
            "read-modify-write section",
        )
        mem.add("a note written under the update lock", "lesson",
                memory_id="upd-1")
        mem.save()
        self.assertTrue(self._lock_is_free(exclusive=True),
                        "save() did not release the update lock")

    def test_concurrent_writers_do_not_lose_an_update(self):
        """Two load-mutate-save cycles must serialize, not clobber."""
        self._seed()
        start = threading.Barrier(2) if hasattr(threading, "Barrier") else None
        errors = []

        def writer(n):
            try:
                if start is not None:
                    start.wait(LOCK_TIMEOUT)
                mem = osvec.ProjectMemory()
                mem.load(for_update=True)
                # Widen the read-modify-write window so an unserialized
                # implementation reliably loses one of the two writes.
                import time

                time.sleep(0.3)
                mem.add("concurrent note %d" % n, "lesson",
                        memory_id="concurrent-%d" % n)
                mem.save()
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(i,), daemon=True)
                   for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(LOCK_TIMEOUT * 3)
            self.assertFalse(t.is_alive(), "a writer deadlocked")
        self.assertEqual(errors, [], "writer raised: %r" % (errors[:1],))

        final = osvec.ProjectMemory().load()
        self.assertEqual(
            sorted(final.id_to_u64),
            ["concurrent-0", "concurrent-1", "seed-0"],
            "a concurrent writer's update was lost: the load-mutate-save cycle "
            "is no longer serialized",
        )

    def test_save_without_a_prior_load_locks_and_releases(self):
        mem = osvec.ProjectMemory()
        mem.add("standalone note with no prior load", "lesson",
                memory_id="standalone")
        mem.save()
        self.assertTrue(self._lock_is_free(exclusive=True),
                        "save() leaked the lock it acquired itself")
        self.assertIn("standalone", osvec.ProjectMemory().load().id_to_u64)

    # -- every exit path gives the lock back -------------------------------- #
    def test_abandoned_update_load_is_released_by_close(self):
        self._seed()
        mem = osvec.ProjectMemory()
        mem.load(for_update=True)
        mem.close()
        self.assertTrue(self._lock_is_free(exclusive=True),
                        "close() did not release an abandoned update lock")
        mem.close()  # idempotent

    def test_update_load_is_released_by_the_context_manager(self):
        self._seed()
        with osvec.ProjectMemory() as mem:
            mem.load(for_update=True)
            self.assertFalse(self._lock_is_free(exclusive=False))
        self.assertTrue(self._lock_is_free(exclusive=True),
                        "__exit__ did not release the update lock")

    def test_failing_load_releases_the_lock(self):
        """A fail-closed load() must not leave the store locked."""
        self._seed(n=2)
        with open(osvec.SIDECAR_PATH) as f:
            blob = json.load(f)
        blob["records"].popitem()  # index count no longer matches side-car
        with open(osvec.SIDECAR_PATH, "w") as f:
            json.dump(blob, f)
        for for_update in (False, True):
            with self.subTest(for_update=for_update):
                with self.assertRaises(RuntimeError):
                    osvec.ProjectMemory().load(for_update=for_update)
                self.assertTrue(
                    self._lock_is_free(exclusive=True),
                    "a raising load() left the store locked",
                )

    # -- torn writes -------------------------------------------------------- #
    def test_a_failed_save_leaves_the_previous_sidecar_readable(self):
        """A save() that dies while SERIALISING the new side-car must not have
        touched the destination yet.

        Scope, corrected 2026-07-27: the crash injected here fires inside
        json.dump(), i.e. strictly before the swap, so what this pins is the
        build-a-complete-temp-file-first ORDER, plus temp cleanup and lock
        release on that path. It does NOT prove the swap itself is atomic --
        it passes unchanged if os.replace() is downgraded to a truncating copy,
        because the destination is never reached. The atomicity of the swap is
        pinned by the two tests below, which is where that claim now lives.
        """
        self._seed()
        sidecar = Path(osvec.SIDECAR_PATH)
        before = sidecar.read_text(encoding="utf-8")
        mem = osvec.ProjectMemory().load()
        mem.add("this write is going to blow up mid-dump", "lesson",
                memory_id="doomed")
        real_dump = osvec.json.dump

        def exploding_dump(*a, **kw):
            raise RuntimeError("simulated crash mid-write")

        osvec.json.dump = exploding_dump
        try:
            with self.assertRaises(RuntimeError):
                mem.save()
        finally:
            osvec.json.dump = real_dump
        self.assertEqual(
            sidecar.read_text(encoding="utf-8"), before,
            "a save() that died mid-write left the side-car truncated; "
            "readers (including brain.py, which reads it unlocked) then see a "
            "torn store",
        )
        # and no scratch file was left behind
        leftovers = [n for n in os.listdir(self.tmp)
                     if n.startswith(".project.sidecar.")]
        self.assertEqual(leftovers, [], "temp side-car files were left behind")
        self.assertTrue(self._lock_is_free(exclusive=True),
                        "a failed save() leaked the lock")

    def test_a_successful_save_swaps_the_sidecar_in_rather_than_rewriting_it(self):
        """The side-car must be REPLACED (a new inode swapped in), never
        rewritten over the existing one.

        This is the promise save()'s own comment makes to brain.py, which reads
        SIDECAR_PATH directly without taking the lock: such a reader sees the
        whole old file or the whole new one. Any implementation that streams the
        new bytes over the existing inode -- open(dst, "w"), shutil.copyfile,
        a copy loop -- breaks that promise SILENTLY, because the end state on
        disk is byte-identical to a correct run. Holding a reader's fd open
        across the save is what makes the difference observable: after an
        os.replace() that fd still yields the complete previous JSON, after an
        in-place rewrite it yields the new (or half-written) bytes.
        """
        self._seed()
        sidecar = Path(osvec.SIDECAR_PATH)
        before = sidecar.read_text(encoding="utf-8")
        before_ino = os.stat(osvec.SIDECAR_PATH).st_ino
        # An unlocked reader (brain.py's shape) that already has the file open.
        reader_fd = open(osvec.SIDECAR_PATH, encoding="utf-8")
        self.addCleanup(reader_fd.close)

        mem = osvec.ProjectMemory().load()
        mem.add("a note whose save must swap a new side-car in", "lesson",
                memory_id="swapped-in")
        mem.save()

        after = sidecar.read_text(encoding="utf-8")
        self.assertIn("swapped-in", after,
                      "precondition: save() did not write the new record")
        self.assertNotEqual(after, before,
                            "precondition: save() left the side-car unchanged")
        self.assertNotEqual(
            os.stat(osvec.SIDECAR_PATH).st_ino, before_ino,
            "save() rewrote the side-car over the SAME inode instead of "
            "swapping a completed temp file in; an unlocked reader can then "
            "observe a partially written store",
        )
        held = reader_fd.read()
        self.assertEqual(
            held, before,
            "the file a reader already had open was rewritten underneath it; "
            "the new side-car must be swapped in over a fresh inode so the "
            "previous one stays intact for anyone still reading it",
        )
        self.assertIn(
            "records", json.loads(held),
            "the side-car a reader was holding is no longer parseable JSON",
        )

    def test_a_crash_at_the_sidecar_swap_leaves_the_previous_file_complete(self):
        """Crash AT the swap -- the point the dump-crash test above cannot reach.

        Two things keep this from being a probe that passes for the wrong
        reason: the injection is scoped to SIDECAR_PATH as the DESTINATION, so
        it cannot fire inside the index write earlier in save() (which would
        leave the side-car untouched trivially), and `fired` asserts the seam
        was actually reached, so an implementation that writes the destination
        directly fails here instead of quietly never raising.
        """
        self._seed()
        sidecar = Path(osvec.SIDECAR_PATH)
        before = sidecar.read_text(encoding="utf-8")
        fired = []
        originals = {}

        def targets_sidecar(dst):
            try:
                return os.path.abspath(os.fspath(dst)) == \
                    os.path.abspath(osvec.SIDECAR_PATH)
            except TypeError:
                return False

        def arm(module, name):
            original = getattr(module, name)
            originals[(module, name)] = original

            def boom(src, dst, *a, **kw):
                if targets_sidecar(dst):
                    fired.append(name)
                    raise RuntimeError("simulated crash at the side-car swap")
                return original(src, dst, *a, **kw)

            setattr(module, name, boom)

        try:
            # Arm every plausible swap primitive rather than only os.replace, so
            # a correct implementation that swaps with os.rename still passes
            # (this pins the property, not one function name). An implementation
            # that rewrites the destination directly calls none of them, `fired`
            # stays empty and save() never raises -- which is the failure above.
            # Armed inside the try so a half-finished arming still gets undone:
            # these are process-global functions the rest of the suite needs.
            for module, name in ((osvec.os, "replace"), (osvec.os, "rename"),
                                 (osvec.shutil, "copyfile"),
                                 (osvec.shutil, "move")):
                arm(module, name)
            mem = osvec.ProjectMemory().load()
            mem.add("this write is going to blow up at the swap", "lesson",
                    memory_id="doomed-at-swap")
            with self.assertRaises(RuntimeError):
                mem.save()
        finally:
            for (module, name), original in originals.items():
                setattr(module, name, original)

        self.assertTrue(
            fired,
            "save() never handed SIDECAR_PATH to a swap primitive, so it wrote "
            "the destination directly; the new side-car must be built under a "
            "temp name and swapped in",
        )
        surviving = sidecar.read_text(encoding="utf-8")
        self.assertEqual(
            surviving, before,
            "a save() interrupted AT the swap changed the side-car; the "
            "previous file must survive byte-for-byte",
        )
        self.assertIn(
            "records", json.loads(surviving),
            "the side-car left behind by an interrupted swap is not parseable "
            "JSON; readers that skip the lock (brain.py) see a torn store",
        )
        leftovers = [n for n in os.listdir(self.tmp)
                     if n.startswith(".project.sidecar.")]
        self.assertEqual(leftovers, [],
                         "a swap that failed left its temp side-car behind")
        self.assertTrue(self._lock_is_free(exclusive=True),
                        "a save() that failed at the swap leaked the lock")


if __name__ == "__main__":
    unittest.main()
