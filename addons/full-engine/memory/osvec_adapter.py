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
_SECRET_PATTERNS = [
    r"sk-[A-Za-z0-9]{16,}",                       # OpenAI-style
    r"AKIA[0-9A-Z]{16}",                          # AWS access key id
    r"ghp_[A-Za-z0-9]{20,}",                      # GitHub PAT
    r"xox[baprs]-[A-Za-z0-9-]{10,}",              # Slack
    r"AIza[0-9A-Za-z_\-]{20,}",                   # Google API key
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",        # PEM private key
    r"(?i)(api[_-]?key|secret|password|passwd|token)\s*[:=]\s*\S{6,}",
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
        return IdMapIndex(dim=dim, bit_width=BIT_WIDTH), "turbovec.IdMapIndex"
    except Exception:
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
        os.makedirs(STORE_DIR, exist_ok=True)

    # ---- write ----
    def add(self, text, memory_type, source_file="", memory_id=None, tags=None,
            run_slug=None):
        secret = looks_like_secret(text)
        if secret:
            raise ValueError(
                f"Refusing to store memory: it matches a secret pattern ({secret}). "
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
        uid = self.id_to_u64.pop(memory_id, None)
        if uid is None:
            return False
        self.sidecar.pop(str(uid), None)
        return bool(self.index.remove(np.uint64(uid)))

    # ---- persistence (index + side-car, kept in sync) ----
    def save(self):
        os.makedirs(STORE_DIR, exist_ok=True)
        self.index.write(INDEX_PATH)
        with open(SIDECAR_PATH, "w") as f:
            json.dump({"backend": self.backend, "dim": self.dim,
                       "embedder": getattr(self.embedder, "name", "?"),
                       "records": self.sidecar}, f, indent=2)

    def load(self):
        if not os.path.exists(SIDECAR_PATH):
            return self
        with open(SIDECAR_PATH) as f:
            blob = json.load(f)
        self.sidecar = blob.get("records", {})
        self.id_to_u64 = {r["memory_id"]: int(r["u64_id"]) for r in self.sidecar.values()}
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
                self.index.add_with_ids(vec, np.array([int(r["u64_id"])], dtype=np.uint64))
        # consistency check (mirrors turbovec's check_persisted_handles intent)
        if len(self.index) != len(self.sidecar):
            sys.stderr.write(
                f"WARNING: index ({len(self.index)}) and side-car ({len(self.sidecar)}) "
                "are out of sync.\n"
            )
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

    mem = ProjectMemory().load()
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
