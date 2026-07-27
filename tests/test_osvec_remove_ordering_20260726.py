#!/usr/bin/env python3
"""Regression tests for ProjectMemory.remove() ordering in
addons/full-engine/memory/osvec_adapter.py (b4cc70f hunk, previously unreached).

The defect the guarded hunk fixed (audit 2026-07-25): remove() used to pop()
the memory_id out of id_to_u64 AND pop() the record out of the side-car FIRST,
and only then ask the vector index to remove the u64 id. On an index/side-car
desync -- the id is still in both mappings but the vector is already gone from
the index, so index.remove() returns False -- the old ordering therefore
DELETED the record from both mappings while REPORTING failure. The caller was
told "not removed", yet the text/metadata were silently destroyed, and the
pre-existing desync was silently "healed" by data loss instead of surfaced.

The fixed ordering checks the index FIRST and only mutates the mappings after
the index confirms the removal, so a failing remove() leaves the record fully
intact (correct survivor set, desync still detectable).

Reachability note: this state cannot be built through load() -- load() now
fails closed on a count mismatch -- so the desync is constructed in-memory on
the real module objects (evicting one vector from the live index out from
under the mappings), which is exactly the state the hunk's comment names:
"index.remove() returns False".

numpy: osvec_adapter hard-imports numpy at module scope, and that is correct
(numpy is on its core add/search path). On the below-floor CI leg (stock
/usr/bin/python3, stdlib only) this whole module skips with an explicit
reason, per the house rule in .github/workflows/test.yml; it earns its keep on
the full local interpreter.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OSVEC_PATH = ROOT / "addons" / "full-engine" / "memory" / "osvec_adapter.py"

_SKIP = None
osvec = None
np = None
try:
    import numpy as np  # noqa: F401
except Exception as exc:  # pragma: no cover - below-floor CI leg
    _SKIP = (
        "numpy is not installed; osvec_adapter imports numpy at module scope "
        "(correctly -- it is on the add/search path), so remove() cannot be "
        "exercised here (%s)" % (exc.__class__.__name__,)
    )
else:
    spec = importlib.util.spec_from_file_location(
        "osvec_remove_ordering_probe", OSVEC_PATH)
    osvec = importlib.util.module_from_spec(spec)
    sys.modules["osvec_remove_ordering_probe"] = osvec
    spec.loader.exec_module(osvec)


@unittest.skipUnless(_SKIP is None, _SKIP or "")
class OsvecRemoveOrdering(unittest.TestCase):
    """All tests run against a throwaway store, never the live one."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="osvec-remove-ordering-")
        self.addCleanup(self._cleanup)
        self._saved = (osvec.STORE_DIR, osvec.INDEX_PATH, osvec.SIDECAR_PATH)
        osvec.STORE_DIR = self.tmp
        osvec.INDEX_PATH = os.path.join(self.tmp, "project.tvim")
        osvec.SIDECAR_PATH = os.path.join(self.tmp, "project.sidecar.json")
        self.assertNotEqual(
            os.path.realpath(self.tmp),
            os.path.realpath(os.path.join(OSVEC_PATH.parent, "store")),
            "test would have written into the live OSVec store",
        )

    def _cleanup(self):
        osvec.STORE_DIR, osvec.INDEX_PATH, osvec.SIDECAR_PATH = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers ----------------------------------------------------------- #
    def _memory_with_two_records(self):
        mem = osvec.ProjectMemory()
        mem.add("note alpha about tier selection for simple tasks",
                "lesson", memory_id="alpha")
        mem.add("note beta about flat agent waves and token cost",
                "lesson", memory_id="beta")
        return mem

    def _desync(self, mem, memory_id):
        """Evict one vector from the live index out from under the mappings.

        This is the exact precondition the guarded hunk names: memory_id is
        still in id_to_u64 and the side-car, but index.remove() for its u64
        id will return False because the vector is already gone.
        """
        uid = mem.id_to_u64[memory_id]
        self.assertTrue(bool(mem.index.remove(np.uint64(uid))),
                        "test setup: could not evict the vector")
        # Confirm the constructed state is a real desync.
        self.assertIn(memory_id, mem.id_to_u64)
        self.assertIn(str(uid), mem.sidecar)
        self.assertNotEqual(len(mem.index), len(mem.sidecar))
        return uid

    # -- the guarded ordering ---------------------------------------------- #
    def test_failed_index_remove_does_not_destroy_the_record(self):
        """THE hunk: a remove() the index refuses must not delete the record.

        Under the pre-fix ordering (pop both mappings first, ask the index
        last) this reports False AND silently destroys alpha's text/metadata
        from both mappings.
        """
        mem = self._memory_with_two_records()
        uid = self._desync(mem, "alpha")

        self.assertFalse(
            mem.remove("alpha"),
            "remove() reported success for an id the index could not remove",
        )
        self.assertIn(
            "alpha", mem.id_to_u64,
            "a failing remove() deleted the id_to_u64 mapping anyway: the "
            "record was destroyed while the caller was told 'not removed'",
        )
        self.assertIn(
            str(uid), mem.sidecar,
            "a failing remove() deleted the side-car record anyway: the "
            "text/metadata were silently destroyed",
        )
        self.assertEqual(
            mem.sidecar[str(uid)]["text"],
            "note alpha about tier selection for simple tasks",
            "the surviving side-car record was corrupted",
        )

    def test_failed_remove_leaves_the_desync_detectable_on_disk(self):
        """The downstream observable: the desync must surface, not be
        silently 'healed' by data loss.

        With the fixed ordering, alpha's record survives in the persisted
        side-car, so the store still shows index != side-car (which load()
        refuses, fail closed). With the old ordering, alpha was destroyed
        from the side-car too, so the persisted store looks healthy and the
        data loss is invisible.
        """
        mem = self._memory_with_two_records()
        self._desync(mem, "alpha")
        self.assertFalse(mem.remove("alpha"))
        mem.save()

        with open(osvec.SIDECAR_PATH) as f:
            blob = json.load(f)
        persisted_ids = sorted(r["memory_id"] for r in blob["records"].values())
        self.assertEqual(
            persisted_ids, ["alpha", "beta"],
            "the record a failing remove() should have preserved is missing "
            "from the persisted side-car: the desync was silently healed by "
            "destroying data",
        )

    def test_failed_remove_leaves_the_survivor_removable(self):
        """After a refused remove(), the healthy record must still be intact
        and removable through the same API."""
        mem = self._memory_with_two_records()
        self._desync(mem, "alpha")
        self.assertFalse(mem.remove("alpha"))

        beta_uid = mem.id_to_u64["beta"]
        self.assertTrue(mem.remove("beta"),
                        "a healthy record stopped being removable after a "
                        "refused remove()")
        self.assertNotIn("beta", mem.id_to_u64)
        self.assertNotIn(str(beta_uid), mem.sidecar)

    # -- mirror direction: success must still remove everywhere ------------ #
    def test_successful_remove_still_removes_from_all_three(self):
        """The fix must not overcorrect into never mutating: when the index
        confirms the removal, the id must leave id_to_u64, the side-car, and
        the index."""
        mem = self._memory_with_two_records()
        uid = mem.id_to_u64["alpha"]

        self.assertTrue(mem.remove("alpha"))
        self.assertNotIn("alpha", mem.id_to_u64)
        self.assertNotIn(str(uid), mem.sidecar)
        self.assertNotIn(np.uint64(uid), mem.index)
        self.assertEqual(len(mem.index), len(mem.sidecar),
                         "a successful remove() left the store desynced")

    def test_remove_of_an_unknown_id_returns_false_and_touches_nothing(self):
        mem = self._memory_with_two_records()
        before = (dict(mem.id_to_u64), sorted(mem.sidecar), len(mem.index))
        self.assertFalse(mem.remove("never-stored"))
        self.assertEqual(
            (dict(mem.id_to_u64), sorted(mem.sidecar), len(mem.index)), before)


if __name__ == "__main__":
    unittest.main()
