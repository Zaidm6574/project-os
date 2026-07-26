#!/usr/bin/env python3
"""
Project OS - OSVec memory adapter.

What this is
------------
A small, real local vector-memory layer for the Project OS blackboard. OSVec is
the Project OS memory layer; when the `turbovec` package is installed, it uses
TurboVec underneath. The key design point: the vector index stores vectors keyed
by u64 ids, not the original text. A working memory layer needs three parts:

  1. an Embedder            (text -> float32 vector)
  2. an IdMap-style vector index  (.tvim ; stable u64 ids, O(1) remove)
  3. a JSON side-car        (u64 id -> the text + metadata)

This mirrors the normal TurboVec persistence shape: `.tvim` plus a JSON side-car
of handle -> payload.

Runs today with zero network and zero model download: the default embedder is a
deterministic hashing embedder (good enough to demo recall and to be useful for
short notes). Swap in real embeddings by passing a different Embedder.

If `turbovec` is not installed, it falls back to a tiny brute-force numpy index
with the same interface, so you can try OSVec immediately and install TurboVec
later for speed/compression.

Safety: refuses to store anything that looks like an API key / password / secret.

CLI
---
  python osvec_adapter.py selftest
  python osvec_adapter.py add --text "Beginner users prefer Solo tier first" \
        --type user-preference --source blackboard/01-user-memory.md --id pref-001
  python osvec_adapter.py search --query "which tier for a simple task" -k 3
  python osvec_adapter.py stats
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import shutil
import re
import sys
import time
from dataclasses import dataclass, asdict
from typing import List, Optional

try:
    import numpy as np
except Exception:  # pragma: no cover
    sys.stderr.write("This tool needs numpy: pip install numpy --break-system-packages\n")
    raise

try:
    import fcntl  # POSIX only; this add-on's install target may not have it
except ImportError:  # pragma: no cover
    fcntl = None

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
DIM = 1024          # multiple of 8 and <= 65536 for the TurboVec backend
BIT_WIDTH = 4       # 2 = smallest, 4 = best recall
HERE = os.path.dirname(os.path.abspath(__file__))
STORE_DIR = os.path.join(HERE, "store")
INDEX_PATH = os.path.join(STORE_DIR, "project.tvim")
SIDECAR_PATH = os.path.join(STORE_DIR, "project.sidecar.json")

# --------------------------------------------------------------------------- #
# Secret scanning - never let credentials into memory
# --------------------------------------------------------------------------- #
# MUST stay byte-identical to SECRET_PATTERNS in brain/brain.py. Vector memory
# and the brain are two doors into the same store, and this copy had drifted to
# a 7-pattern list whose `sk-[A-Za-z0-9]{16,}` could not cross a hyphen or an
# underscore -- so sk-ant-, sk-proj-, and every modern key format walked in
# through the door the brain had already locked. The secret-pattern parity test
# fails if these lists ever diverge again (audit 2026-07-25).
_SECRET_PATTERNS = [
    r"(?<![A-Za-z0-9_])sk-[A-Za-z0-9_\-]{16,}",
    r"(?<![A-Za-z0-9_])sk_(live|test)_[A-Za-z0-9]{16,}",
    r"(?<![A-Za-z0-9_])rk_(live|test)_[A-Za-z0-9]{16,}",
    r"AKIA[0-9A-Z]{16}",
    r"ASIA[0-9A-Z]{16}",
    r"(?<![A-Za-z0-9_])ghp_[A-Za-z0-9]{20,}",
    r"(?<![A-Za-z0-9_])gho_[A-Za-z0-9]{20,}",
    r"(?<![A-Za-z0-9_])ghs_[A-Za-z0-9]{20,}",
    r"github_pat_[A-Za-z0-9_]{20,}",
    r"(?<![A-Za-z0-9_])AIza[0-9A-Za-z_\-]{20,}",
    r"(?<![A-Za-z0-9_])ya29\.[A-Za-z0-9_\-]{20,}",
    r"(?<![A-Za-z0-9_])xox[baprs]-[A-Za-z0-9\-]{10,}",
    r"(?<![A-Za-z0-9_])figd_[A-Za-z0-9_\-]{20,}",
    r"SG\.[A-Za-z0-9_\-]{20,}",
    r"\bAC[0-9a-fA-F]{32}\b",
    r"\bSK[0-9a-fA-F]{32}\b",
    r"(?<![A-Za-z0-9_])glpat-[A-Za-z0-9_\-]{16,}",
    r"(?<![A-Za-z0-9_])dop_v1_[A-Za-z0-9]{32,}",
    r"(?<![A-Za-z0-9_])npm_[A-Za-z0-9]{30,}",
    r"(?<![A-Za-z0-9_])hf_[A-Za-z0-9]{30,}",
    r"(?<![A-Za-z0-9_])ntn_[A-Za-z0-9]{40,}",
    r"(?<![A-Za-z0-9_])lin_api_[A-Za-z0-9]{30,}",
    r"(?<![A-Za-z0-9_])vercel_[A-Za-z0-9]{20,}",
    r"(?i)https://[0-9a-f]{32}@[\w.\-]+/\d+",
    r"(?i)AccountKey\s*=\s*[A-Za-z0-9+/]{40,}={0,2}",
    r"https://hooks\.slack\.com/services/T[A-Za-z0-9/]{20,}",
    r"eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.",
    r"(?i)\b(postgres(ql)?|mysql|mongodb(\+srv)?|redis|amqp)://[^\s:@/]+:[^\s@/]+@",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"(?i)(api[_-]?key|secret|password|passwd|token|bearer)\s*[:=]\s*\S{6,}",
]
_SECRET_RE = [re.compile(p) for p in _SECRET_PATTERNS]


def looks_like_secret(text: str) -> Optional[str]:
    for rx in _SECRET_RE:
        if rx.search(text):
            return rx.pattern
    return None


# --------------------------------------------------------------------------- #
# Embedders
# --------------------------------------------------------------------------- #
class HashingEmbedder:
    """Deterministic, dependency-free embedder (feature-hashing / 'hashing trick').

    Shared words -> overlapping dimensions -> higher cosine similarity. Not as good
    as a trained model, but it runs anywhere, instantly, and is reproducible.
    """

    name = "hashing-v1"
    dim = DIM

    _token_re = re.compile(r"[a-z0-9]+")

    def _tokens(self, text: str) -> List[str]:
        t = text.lower()
        words = self._token_re.findall(t)
        grams = [w[i:i + 4] for w in words for i in range(max(1, len(w) - 3))]  # char 4-grams
        return words + grams

    def embed(self, texts: List[str]) -> "np.ndarray":
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for r, text in enumerate(texts):
            for tok in self._tokens(text):
                h = hashlib.blake2b(tok.encode("utf-8"), digest_size=8).digest()
                idx = int.from_bytes(h[:4], "little") % self.dim
                sign = 1.0 if (h[4] & 1) else -1.0
                out[r, idx] += sign
            n = float(np.linalg.norm(out[r]))
            if n > 0:
                out[r] /= n
        return out


def stable_u64(memory_id: str) -> int:
    """Map a human-readable id to a stable uint64 for OSVec."""
    d = hashlib.blake2b(memory_id.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(d, "little")  # 0 .. 2**64-1


# --------------------------------------------------------------------------- #
# Index backends: real OSVec, or a numpy brute-force fallback
# --------------------------------------------------------------------------- #
class _BruteForceIndex:
    """Tiny stand-in with the slice of IdMapIndex's API we use. numpy only."""

    backend = "bruteforce-fallback"

    def __init__(self, dim: int, bit_width: int = BIT_WIDTH):
        self.dim = dim
        self._ids: List[int] = []
        self._vecs = np.zeros((0, dim), dtype=np.float32)

    def add_with_ids(self, vectors, ids):
        self._vecs = np.vstack([self._vecs, np.asarray(vectors, dtype=np.float32)])
        self._ids.extend(int(i) for i in ids)

    def search(self, queries, k, allowlist=None):
        q = np.asarray(queries, dtype=np.float32)
        if self._vecs.shape[0] == 0:
            return np.zeros((q.shape[0], 0)), np.zeros((q.shape[0], 0), dtype=np.uint64)
        sims = q @ self._vecs.T                       # cosine (vectors are normalized)
        allow = None if allowlist is None else set(int(a) for a in allowlist)
        out_scores, out_ids = [], []
        for row in sims:
            order = np.argsort(-row)
            picked = [j for j in order if (allow is None or self._ids[j] in allow)][:k]
            out_scores.append([float(row[j]) for j in picked])
            out_ids.append([np.uint64(self._ids[j]) for j in picked])
        width = max((len(r) for r in out_ids), default=0)
        S = np.zeros((len(out_ids), width), dtype=np.float32)
        I = np.zeros((len(out_ids), width), dtype=np.uint64)
        for r, (s, i) in enumerate(zip(out_scores, out_ids)):
            S[r, :len(s)] = s
            I[r, :len(i)] = i
        return S, I

    def remove(self, id) -> bool:
        if int(id) in self._ids:
            j = self._ids.index(int(id))
            del self._ids[j]
            self._vecs = np.delete(self._vecs, j, axis=0)
            return True
        return False

    def contains(self, id) -> bool:
        return int(id) in self._ids

    def __contains__(self, id) -> bool:
        return self.contains(id)

    def __len__(self) -> int:
        return len(self._ids)

    def write(self, path):
        np.savez(path + ".npz", ids=np.array(self._ids, dtype=np.uint64), vecs=self._vecs)

    @classmethod
    def load(cls, path, dim=DIM):
        idx = cls(dim)
        data = np.load(path + ".npz")
        idx._ids = [int(i) for i in data["ids"]]
        idx._vecs = data["vecs"].astype(np.float32)
        return idx


def _new_index(dim: int):
    try:
        from turbovec import IdMapIndex  # type: ignore
    except ImportError:
        # Expected, quiet path: turbovec isn't installed.
        return _BruteForceIndex(dim), _BruteForceIndex.backend
    try:
        return IdMapIndex(dim=dim, bit_width=BIT_WIDTH), "turbovec.IdMapIndex"
    except Exception as exc:
        # turbovec IS installed but the real index construction failed (bad
        # native build, corrupt lib, bad args, etc). This used to be caught by
        # the same bare `except Exception` as the ImportError case, so a real
        # break silently downgraded to the bruteforce fallback with no signal
        # (audit 2026-07-25). Warn loudly instead of swallowing it.
        sys.stderr.write(
            f"WARNING: turbovec.IdMapIndex failed to construct ({exc!r}); "
            "falling back to bruteforce index.\n"
        )
        return _BruteForceIndex(dim), _BruteForceIndex.backend


# --------------------------------------------------------------------------- #
# Memory record + store
# --------------------------------------------------------------------------- #
@dataclass
class MemoryRecord:
    memory_id: str
    u64_id: int
    text: str
    memory_type: str
    source_file: str
    tags: List[str]
    created_at: str
    run_slug: str = ""


class ProjectMemory:
    VALID_TYPES = {
        "user-preference", "project-pattern", "research-finding",
        "decision", "risk", "agent-packet", "lesson",
    }

    def __init__(self, embedder=None):
        self.embedder = embedder or HashingEmbedder()
        self.dim = self.embedder.dim
        self.index, self.backend = _new_index(self.dim)
        self.sidecar = {}          # str(u64_id) -> record dict
        self.id_to_u64 = {}        # memory_id -> u64_id
        self._lock_fd = None       # see _acquire_lock for the locking discipline
        self._lock_shared = False  # True while the held lock is LOCK_SH
        os.makedirs(STORE_DIR, exist_ok=True)

    # ---- locking (audit 2026-07-25, lock-mode fix 2026-07-26) ----
    # save() used to write index.write() + json.dump() with no locking at all,
    # so two concurrent CLI invocations (load -> add -> save) could each load
    # the same pre-write state and the second save() would silently clobber
    # the first's write. The first fix took an EXCLUSIVE flock in load() and
    # released it only in save() -- which meant a read-only consumer
    # (`search`, `stats`, or any long-lived process that loads once and never
    # writes) held an exclusive lock on the store for its whole lifetime and
    # blocked every other process's load().
    #
    # The discipline now is the standard reader/writer one:
    #
    #   * load()               takes LOCK_SH and RELEASES it before returning.
    #                          Any number of readers proceed in parallel, and
    #                          none of them can observe a half-written store
    #                          because a writer holds LOCK_EX across its write.
    #   * load(for_update=True) takes LOCK_EX and HOLDS it through save(), so a
    #                          whole read-modify-write cycle is serialized
    #                          against other writers (no lost updates). Use
    #                          close()/`with` if such a load is abandoned
    #                          without saving.
    #   * save()               takes LOCK_EX (if not already held) and releases
    #                          it when the write completes.
    #
    # Best effort only where fcntl isn't available (non-POSIX) or the lock file
    # can't be opened -- never blocks the caller on that failure. save() also
    # replaces the side-car atomically, so even an unlocked reader (or a crash
    # mid-write) can never see a truncated JSON file.
    def _acquire_lock(self, shared: bool = False):
        if fcntl is None:
            return
        if self._lock_fd is not None:
            if self._lock_shared == shared:
                return
            # Mode change (a shared reader that now wants to write). Drop and
            # retake rather than relying on flock conversion semantics; the
            # caller that needs an uninterrupted read-modify-write section is
            # expected to have asked for for_update=True up front.
            self._release_lock()
        fd = None
        try:
            os.makedirs(STORE_DIR, exist_ok=True)
            fd = open(os.path.join(STORE_DIR, "project.lock"), "a+")
            fcntl.flock(fd.fileno(),
                        fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
        except OSError:
            if fd is not None:
                fd.close()
            self._lock_fd = None
            self._lock_shared = False
            return
        self._lock_fd = fd
        self._lock_shared = shared

    def _release_lock(self):
        if self._lock_fd is None:
            return
        try:
            fcntl.flock(self._lock_fd.fileno(), fcntl.LOCK_UN)
        finally:
            self._lock_fd.close()
            self._lock_fd = None
            self._lock_shared = False

    def close(self):
        """Release any held store lock. Safe to call more than once.

        Only a `load(for_update=True)` that is abandoned without calling save()
        needs this; read-only loads and completed saves release on their own.
        """
        self._release_lock()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    # ---- write ----
    def add(self, text, memory_type, source_file="", memory_id=None, tags=None,
            run_slug=None):
        # Scan EVERY field a human can paste into, not just `text`. brain.py had
        # the identical hole: a secret in `tags` or `source` synced untouched
        # because only the body was checked (audit 2026-07-25).
        #
        # `run_slug` says "EVERY field" and means it: it is CLI-exposed as
        # --run-slug, it is folded into `memory_id` a few lines below, and
        # save() persists it to the side-car on disk -- so a key pasted there
        # leaked exactly like the `tags` hole above. It was the one free-text
        # parameter this loop still missed (adversarial verify 2026-07-26).
        # `memory_type` is deliberately absent: it is not free text, it is
        # rejected below unless it is a member of the closed VALID_TYPES set.
        # test_osvec_scan_covers_every_free_text_parameter_of_add fails if a
        # future parameter is added without being scanned or justified here.
        for field, value in (("text", text), ("source_file", source_file),
                             ("memory_id", memory_id), ("tags", tags),
                             ("run_slug", run_slug)):
            for chunk in (value if isinstance(value, (list, tuple)) else [value]):
                if not isinstance(chunk, str):
                    continue
                secret = looks_like_secret(chunk)
                if secret:
                    raise ValueError(
                        f"Refusing to store memory: field '{field}' matches a "
                        f"secret pattern ({secret}). "
                        "Never put API keys/passwords in OSVec."
                    )
        if memory_type not in self.VALID_TYPES:
            raise ValueError(f"memory_type must be one of {sorted(self.VALID_TYPES)}")
        memory_id = memory_id or f"{memory_type}-{int(time.time()*1000)}"
        run_slug = (run_slug or "").strip()
        # Namespace per run: prefix the logical id with '<run_slug>/' so two runs
        # minting the same logical id (e.g. decision-001) cannot silently
        # overwrite each other. Omitting run_slug preserves the global store.
        if run_slug and not memory_id.startswith(run_slug + "/"):
            memory_id = f"{run_slug}/{memory_id}"
        uid = stable_u64(memory_id)

        # update semantics: if this memory_id exists, remove the old vector first
        if memory_id in self.id_to_u64:
            old = self.id_to_u64[memory_id]
            self.index.remove(np.uint64(old))
            self.sidecar.pop(str(old), None)

        vec = self.embedder.embed([text]).astype(np.float32)
        self.index.add_with_ids(vec, np.array([uid], dtype=np.uint64))
        rec = MemoryRecord(memory_id, uid, text, memory_type, source_file,
                           tags or [], time.strftime("%Y-%m-%dT%H:%M:%S"),
                           run_slug)
        self.sidecar[str(uid)] = asdict(rec)
        self.id_to_u64[memory_id] = uid
        return rec

    # ---- read ----
    def search(self, query, k=5, allowlist_types=None):
        if len(self.index) == 0:
            return []
        q = self.embedder.embed([query]).astype(np.float32)
        allowlist = None
        if allowlist_types:
            allowlist = np.array(
                [int(r["u64_id"]) for r in self.sidecar.values()
                 if r["memory_type"] in allowlist_types],
                dtype=np.uint64,
            )
            if allowlist.size == 0:
                return []
        scores, ids = self.index.search(q, k, allowlist=allowlist)
        out = []
        for score, uid in zip(scores[0], ids[0]):
            rec = self.sidecar.get(str(int(uid)))
            if rec:
                out.append({"score": float(score), **rec})
        return out

    def remove(self, memory_id) -> bool:
        uid = self.id_to_u64.get(memory_id)
        if uid is None:
            return False
        # Check the index BEFORE mutating id_to_u64/sidecar (audit 2026-07-25):
        # this used to pop() both mappings first and only then ask the index to
        # remove, so an index/sidecar desync (index.remove() returns False) still
        # deleted the record from both mappings while reporting failure.
        if not bool(self.index.remove(np.uint64(uid))):
            return False
        self.id_to_u64.pop(memory_id, None)
        self.sidecar.pop(str(uid), None)
        return True

    # ---- persistence (index + side-car, kept in sync) ----
    def save(self):
        # Acquire the exclusive lock if this instance isn't already holding one
        # (a read-only load() released it, or load() was never called at all --
        # e.g. a fresh add()-then-save()) so the write is still guarded.
        self._acquire_lock(shared=False)
        try:
            os.makedirs(STORE_DIR, exist_ok=True)
            self.index.write(INDEX_PATH)
            # Write to a sibling temp file and os.replace() it: readers that
            # don't take the lock (brain.py reads SIDECAR_PATH directly) and
            # readers running after a crashed write see either the old file or
            # the new one, never a truncated one.
            fd, tmp_path = tempfile.mkstemp(prefix=".project.sidecar.",
                                            suffix=".json", dir=STORE_DIR)
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump({"backend": self.backend, "dim": self.dim,
                               "embedder": getattr(self.embedder, "name", "?"),
                               "records": self.sidecar}, f, indent=2)
                os.replace(tmp_path, SIDECAR_PATH)
            except BaseException:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                raise
        finally:
            # Release after the write completes: this ends the load-mutate-save
            # critical section this instance started, letting the next waiting
            # process's load() proceed against our just-written state.
            self._release_lock()

    def load(self, for_update: bool = False):
        """Read the store into this instance.

        for_update=False (default) is a READ: it takes a shared lock for the
        duration of the read and releases it before returning, so read-only
        consumers never block anyone. Holding an exclusive lock here leaked it
        for the whole process lifetime -- a `search`/`stats` process blocked
        every other process's load() until it exited (verified 2026-07-26).

        for_update=True keeps the exclusive lock held until save() (or
        close()), which is what a read-modify-write caller needs to be safe
        against a concurrent writer's lost update.
        """
        self._acquire_lock(shared=not for_update)
        held = False
        try:
            if os.path.exists(SIDECAR_PATH):
                with open(SIDECAR_PATH) as f:
                    blob = json.load(f)
                self.sidecar = blob.get("records", {})
                self.id_to_u64 = {r["memory_id"]: int(r["u64_id"])
                                  for r in self.sidecar.values()}
                try:
                    if self.backend.startswith("turbovec"):
                        from turbovec import IdMapIndex  # type: ignore
                        self.index = IdMapIndex.load(INDEX_PATH)
                    else:
                        self.index = _BruteForceIndex.load(INDEX_PATH, self.dim)
                except Exception:
                    # Rebuild from side-car text if the binary index is missing/out of sync.
                    self.index, self.backend = _new_index(self.dim)
                    for r in self.sidecar.values():
                        vec = self.embedder.embed([r["text"]]).astype(np.float32)
                        self.index.add_with_ids(
                            vec, np.array([int(r["u64_id"])], dtype=np.uint64))
                # consistency check (mirrors turbovec's check_persisted_handles
                # intent). Used to only sys.stderr.write() a warning and still
                # `return self` on a known-bad state -- callers had no way to
                # tell a healthy load from a corrupted one short of grepping
                # stderr (audit 2026-07-25). Fail closed: refuse to hand back a
                # store we know is inconsistent.
                if len(self.index) != len(self.sidecar):
                    raise RuntimeError(
                        f"OSVec store is inconsistent: index ({len(self.index)}) and "
                        f"side-car ({len(self.sidecar)}) record counts differ. Refusing "
                        "to load a known-bad store."
                    )
            held = for_update
        finally:
            # Release unless this is an explicit for_update load that succeeded.
            # In particular a raising load() must not leave a lock behind: the
            # caller is abandoning this instance, and a waiting process would
            # otherwise block until we happened to be garbage collected.
            if not held:
                self._release_lock()
        return self

    def stats(self):
        by_type = {}
        for r in self.sidecar.values():
            by_type[r["memory_type"]] = by_type.get(r["memory_type"], 0) + 1
        return {"backend": self.backend, "dim": self.dim,
                "embedder": getattr(self.embedder, "name", "?"),
                "count": len(self.sidecar), "by_type": by_type}


# --------------------------------------------------------------------------- #
# CLI + selftest
# --------------------------------------------------------------------------- #
def _selftest() -> int:
    """Run the selftest against a THROWAWAY store.

    This used to instantiate ProjectMemory() on the default store, so running
    the selftest overwrote the user's real vector memory -- verified by md5 of
    project.sidecar.json changing across a run (audit 2026-07-25). The private
    fork already guarded this; canon did not.
    """
    global STORE_DIR, INDEX_PATH, SIDECAR_PATH
    saved = (STORE_DIR, INDEX_PATH, SIDECAR_PATH)
    tmp = tempfile.mkdtemp(prefix="osvec-selftest-")
    STORE_DIR = tmp
    INDEX_PATH = os.path.join(tmp, "project.tvim")
    SIDECAR_PATH = os.path.join(tmp, "project.sidecar.json")
    try:
        return _selftest_body()
    finally:
        STORE_DIR, INDEX_PATH, SIDECAR_PATH = saved
        shutil.rmtree(tmp, ignore_errors=True)


def _selftest_body() -> int:
    if os.path.realpath(STORE_DIR) == os.path.realpath(
            os.path.join(HERE, "store")):
        raise RuntimeError("selftest refuses to use the live OSVec store")
    print("selftest store:", STORE_DIR)
    print("backend:", end=" ")
    mem = ProjectMemory()
    print(mem.backend)
    # The default embedder is lexical (shared words / char n-grams), so these
    # queries share vocabulary with their target note. Swap in a real embedder
    # (sentence-transformers / an API) for semantic matching across paraphrases.
    mem.add("For a simple task, prefer the Solo tier before escalating to a full swarm",
            "user-preference", "blackboard/01-user-memory.md", "pref-solo")
    mem.add("Prefer flat agent waves; deep agent recursion multiplies token cost",
            "lesson", "blackboard/12-evaluation-log.md", "lesson-recursion")
    mem.add("OSVec stores vectors by u64 id and needs a JSON side-car for the text",
            "project-pattern", "blackboard/10-osvec-index.md", "pat-turbovec")

    res = mem.search("which tier should I use for a simple task?", k=3)
    assert res, "search returned nothing"
    top = res[0]["memory_id"]
    print(f"top hit for 'tier for a simple task': {top} ({res[0]['score']:.3f})")
    assert top == "pref-solo", f"expected pref-solo, got {top}"

    # type-filtered (allowlist) search
    res2 = mem.search("deep recursion token cost", k=2, allowlist_types={"lesson"})
    assert res2 and res2[0]["memory_id"] == "lesson-recursion", "allowlist search failed"
    print("type-filtered search OK:", res2[0]["memory_id"])
    # per-run namespacing: same logical id in two runs must coexist
    a = mem.add("Run A decided to ship the MVP first", "decision",
                memory_id="decision-001", run_slug="run-alpha")
    b = mem.add("Run B decided to start with research", "decision",
                memory_id="decision-001", run_slug="run-beta")
    assert a.memory_id == "run-alpha/decision-001", a.memory_id
    assert b.memory_id == "run-beta/decision-001", b.memory_id
    assert a.u64_id != b.u64_id, "namespaced ids collided"
    assert a.run_slug == "run-alpha" and b.run_slug == "run-beta"
    assert str(a.u64_id) in mem.sidecar and str(b.u64_id) in mem.sidecar, \
        "namespaced records did not coexist"
    # remove the run-scoped probes so the persistence count below stays at 3
    assert mem.remove("run-alpha/decision-001") is True
    assert mem.remove("run-beta/decision-001") is True
    print("per-run namespacing OK: two decision-001 coexist across runs")
    # secret refusal
    try:
        mem.add("my key is sk-ABCDEFGHIJKLMNOP1234567890", "lesson", memory_id="bad")
        print("FAIL: secret was not blocked"); return 1
    except ValueError:
        print("secret correctly refused")
    # persistence roundtrip
    mem.save()
    mem2 = ProjectMemory().load()
    assert len(mem2.sidecar) == 3, "roundtrip lost records"
    assert mem2.remove("lesson-recursion") is True
    print("persistence + remove OK; final count:", len(mem2.sidecar))
    print("SELFTEST PASSED")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Project OS OSVec memory adapter")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("selftest")
    sub.add_parser("stats")
    a = sub.add_parser("add")
    a.add_argument("--text", required=True)
    a.add_argument("--type", required=True, dest="mtype")
    a.add_argument("--source", default="")
    a.add_argument("--id", default=None)
    a.add_argument("--tags", default="")
    a.add_argument("--run-slug", default=None, dest="run_slug",
                   help="namespace this record under runs/<slug>/ (prefixes the id)")
    s = sub.add_parser("search")
    s.add_argument("--query", required=True)
    s.add_argument("-k", type=int, default=5)
    s.add_argument("--types", default="")
    r = sub.add_parser("remove")
    r.add_argument("--id", required=True)
    args = ap.parse_args()

    if args.cmd == "selftest":
        sys.exit(_selftest())

    # `add`/`remove` are read-modify-write cycles, so they load for update and
    # hold the exclusive lock through save(). `stats`/`search` are pure reads:
    # they must not hold a lock while they print. `with` guarantees the lock is
    # released even if the command body raises before save().
    writing = args.cmd in ("add", "remove")
    with ProjectMemory() as mem:
        mem.load(for_update=writing)
        if args.cmd == "stats":
            print(json.dumps(mem.stats(), indent=2))
        elif args.cmd == "add":
            rec = mem.add(args.text, args.mtype, args.source, args.id,
                          [t for t in args.tags.split(",") if t],
                          run_slug=args.run_slug)
            mem.save()
            print("stored:", rec.memory_id, "(u64", rec.u64_id, ")")
        elif args.cmd == "search":
            types = set(t for t in args.types.split(",") if t) or None
            for hit in mem.search(args.query, args.k, types):
                print(f"  {hit['score']:.3f}  [{hit['memory_type']}]  {hit['memory_id']}: {hit['text'][:80]}")
        elif args.cmd == "remove":
            print("removed" if mem.remove(args.id) else "not found")
            mem.save()


if __name__ == "__main__":
    main()
