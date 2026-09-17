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
later for speed/compression. NumPy is an optional full-engine dependency: the
module still imports and `--help` works without it; data commands return a
short install hint instead of a traceback.

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
import copy
import contextlib
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, asdict
from typing import List, Optional

try:
    import numpy as np
    _NUMPY_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - exercised on stock installs
    # Keep the optional add-on importable so that discovery and --help remain
    # useful on a stdlib-only Python. The first data operation calls
    # _require_numpy() and produces a concise, actionable error instead.
    np = None
    _NUMPY_IMPORT_ERROR = exc


def _require_numpy():
    if np is None:
        detail = " (%s)" % _NUMPY_IMPORT_ERROR if _NUMPY_IMPORT_ERROR else ""
        raise OSVecError(
            "OSVec data commands need optional dependency numpy%s; install it "
            "with `python3 -m pip install numpy` or use memory/mneme_adapter.py "
            "for the stdlib-only memory layer" % detail
        )
    return np

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
DIM = 1024          # multiple of 8 and <= 65536 for the TurboVec backend
BIT_WIDTH = 4       # 2 = smallest, 4 = best recall
HERE = os.path.dirname(os.path.abspath(__file__))
STORE_DIR = os.path.join(HERE, "store")
INDEX_PATH = os.path.join(STORE_DIR, "project.tvim")
SIDECAR_PATH = os.path.join(STORE_DIR, "project.sidecar.json")
MANIFEST_PATH = os.path.join(STORE_DIR, "project.manifest.json")


class OSVecError(RuntimeError):
    """Clean, user-facing persistence or integrity failure."""


SIDECAR_SCHEMA = "osvec-sidecar/v2"
MANIFEST_SCHEMA = "osvec-manifest/v1"

# --------------------------------------------------------------------------- #
# Secret scanning - never let credentials into memory
# --------------------------------------------------------------------------- #
def _load_secret_patterns():
    """Import the shared denylist, or fail loudly. Never degrade silently."""
    candidates = (
        os.path.abspath(os.path.join(HERE, "..", "..", "..", "scripts")),
        os.path.abspath(os.path.join(HERE, "..", "scripts")),
    )
    for scripts_dir in candidates:
        if not os.path.isfile(os.path.join(scripts_dir, "secret_patterns.py")):
            continue
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        return __import__("secret_patterns")
    raise OSVecError(
        "Project OS scripts/secret_patterns.py is required for the privacy gate")


_secret_patterns = _load_secret_patterns()
_SECRET_RE = _secret_patterns.SECRET_PATTERNS
# Compatibility manifest for the original OSVec/brain parity audit. Runtime
# matching intentionally uses the shared module above; this list makes any
# source-level drift visible to the older portable installer test as well.
_SECRET_PATTERNS = [
    r"(?<![A-Za-z0-9_])sk-[A-Za-z0-9_\-]{16,}",
    r"(?<![A-Za-z0-9_])sk_(live|test)_[A-Za-z0-9]{16,}",
    r"(?<![A-Za-z0-9_])rk_(live|test)_[A-Za-z0-9]{16,}",
    r"AKIA[0-9A-Z]{16}",
    r"ASIA[0-9A-Z]{16}",
    r"(?<![A-Za-z0-9_])gh[pousr]_[A-Za-z0-9]{20,}",
    r"github_pat_[A-Za-z0-9_]{20,}",
    r"(?<![A-Za-z0-9_])AIza[0-9A-Za-z_\-]{20,}",
    r"(?<![A-Za-z0-9_])(?:ya29\.[A-Za-z0-9_\-]{20,}|GOCSPX-[A-Za-z0-9_\-]{20,})",
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
    r"(?i)(?:api[_-]?key|secret|password|passwd|token)\s*[:=]\s*\S{6,}|(?:authorization\s*[:=]\s*)?bearer\s+(?!token\b)[A-Za-z0-9._~+/=-]{8,}",
]
looks_like_secret = _secret_patterns.looks_like_secret


def _secret_in_value(value, path, seen):
    if isinstance(value, str):
        pattern = looks_like_secret(value)
        return (path, pattern) if pattern else None
    if isinstance(value, dict):
        marker = id(value)
        if marker in seen:
            return None
        seen.add(marker)
        for key, child in value.items():
            if isinstance(key, str):
                pattern = looks_like_secret(key)
                if pattern:
                    return "%s key" % path, pattern
            found = _secret_in_value(child, "%s.%s" % (path, key), seen)
            if found:
                return found
        return None
    if isinstance(value, (list, tuple)):
        marker = id(value)
        if marker in seen:
            return None
        seen.add(marker)
        for index, child in enumerate(value):
            found = _secret_in_value(child, "%s[%s]" % (path, index), seen)
            if found:
                return found
    return None


def _secret_field(fields):
    """Return (field path, pattern) for secret-like nested persisted metadata."""
    for field, value in fields:
        found = _secret_in_value(value, field, set())
        if found:
            return found
    return None


def _raise_for_secret_fields(fields, error_type=ValueError):
    match = _secret_field(fields)
    if match:
        field, pattern = match
        raise error_type(
            "Refusing to store memory: %s matches a secret pattern (%s). "
            "Never put API keys/passwords in OSVec." % (field, pattern)
        )


def _sha256_file(path):
    _require_regular_unique_file(path, "OSVec persistence leaf")
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_regular_unique_file(path, label):
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise OSVecError("%s is missing or unreadable: %s" % (label, exc)) from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise OSVecError("%s must be a regular non-symlink file" % label)
    if metadata.st_nlink != 1:
        raise OSVecError("%s must not be hardlinked (link count is %s)" % (
            label, metadata.st_nlink
        ))
    return metadata


def _fsync_file(path):
    with open(path, "rb") as handle:
        os.fsync(handle.fileno())


def _fsync_directory(path):
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _unlink_if_present(path):
    if not path:
        return
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def _prepare_store_directory(store_dir):
    """Create a real store directory without following a final symlink leaf."""
    directory = os.path.abspath(store_dir)
    if os.path.lexists(directory) and os.path.islink(directory):
        raise OSVecError("OSVec store directory must not be a symlink")
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError as exc:
        raise OSVecError("OSVec store directory is unavailable: %s" % exc) from exc
    if os.path.islink(directory):
        raise OSVecError("OSVec store directory must not be a symlink")
    if not os.path.isdir(directory):
        raise OSVecError("OSVec store path is not a directory")
    return directory


@contextlib.contextmanager
def _store_lock(store_dir=None, shared=False):
    """Lock an OSVec store; readers use shared mode, writers exclusive mode."""
    directory = _prepare_store_directory(store_dir or STORE_DIR)
    # Keep the historical sentinel for store-layout integrity checks. The
    # advisory project.lock must also persist: unlinking it while another
    # reader/waiter holds its inode lets a new writer lock a different inode.
    sentinel = os.path.join(directory, ".osvec.lock")
    sentinel_fd = os.open(sentinel, os.O_RDWR | os.O_CREAT, 0o600)
    os.close(sentinel_fd)
    lock_path = os.path.join(directory, "project.lock")
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(lock_path, flags, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


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
        _require_numpy()
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
        _require_numpy()
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
        target = path + ".npz"
        directory = os.path.dirname(os.path.abspath(target)) or "."
        descriptor, staged = tempfile.mkstemp(
            prefix=".osvec-index-", suffix=".npz", dir=directory
        )
        os.close(descriptor)
        try:
            np.savez(staged, ids=np.array(self._ids, dtype=np.uint64), vecs=self._vecs)
            os.replace(staged, target)
            staged = None
        finally:
            if staged is not None:
                try:
                    os.unlink(staged)
                except FileNotFoundError:
                    pass

    @classmethod
    def load(cls, path, dim=DIM):
        idx = cls(dim)
        with np.load(path + ".npz", allow_pickle=False) as data:
            if set(data.files) != {"ids", "vecs"}:
                raise ValueError("fallback index must contain exactly ids and vecs")
            ids = np.asarray(data["ids"])
            vecs = np.asarray(data["vecs"])
        if ids.ndim != 1:
            raise ValueError("fallback index ids must be one-dimensional")
        if vecs.ndim != 2 or vecs.shape[1] != dim:
            raise ValueError(
                "fallback index vector dimension mismatch: expected %s, got %s"
                % (dim, vecs.shape)
            )
        if vecs.shape[0] != ids.shape[0]:
            raise ValueError("fallback index id/vector count mismatch")
        parsed_ids = [int(i) for i in ids]
        if len(set(parsed_ids)) != len(parsed_ids):
            raise ValueError("fallback index contains duplicate ids")
        idx._ids = parsed_ids
        idx._vecs = vecs.astype(np.float32)
        return idx


def _new_index(dim: int):
    try:
        from turbovec import IdMapIndex  # type: ignore
        return IdMapIndex(dim=dim, bit_width=BIT_WIDTH), "turbovec.IdMapIndex"
    except Exception:
        return _BruteForceIndex(dim), _BruteForceIndex.backend


def _index_artifact_path(index_path, backend):
    if backend == _BruteForceIndex.backend:
        return index_path + ".npz"
    if isinstance(backend, str) and backend.startswith("turbovec"):
        return index_path
    raise OSVecError("unsupported OSVec backend: %r" % (backend,))


def _load_index(index_path, backend, dim):
    artifact = _index_artifact_path(index_path, backend)
    _require_regular_unique_file(artifact, "OSVec index")
    try:
        if backend == _BruteForceIndex.backend:
            return _BruteForceIndex.load(index_path, dim)
        from turbovec import IdMapIndex  # type: ignore
        return IdMapIndex.load(index_path)
    except OSVecError:
        raise
    except Exception as exc:
        raise OSVecError(
            "OSVec index is corrupt or incompatible: %s" % exc
        ) from exc


def _empty_index_for_backend(dim, backend):
    if backend == _BruteForceIndex.backend:
        return _BruteForceIndex(dim)
    if isinstance(backend, str) and backend.startswith("turbovec"):
        try:
            from turbovec import IdMapIndex  # type: ignore
            return IdMapIndex(dim=dim, bit_width=BIT_WIDTH)
        except Exception as exc:
            raise OSVecError(
                "OSVec backend is unavailable while merging concurrent writes: %s"
                % exc
            ) from exc
    raise OSVecError("unsupported OSVec backend: %r" % (backend,))


def _index_from_records(records, backend, embedder):
    index = _empty_index_for_backend(embedder.dim, backend)
    if records:
        ordered = list(records.values())
        vectors = embedder.embed([record["text"] for record in ordered]).astype(
            np.float32
        )
        ids = np.array([record["u64_id"] for record in ordered], dtype=np.uint64)
        index.add_with_ids(vectors, ids)
    return index


def _read_json_object(path, label):
    _require_regular_unique_file(path, label)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, ValueError) as exc:
        raise OSVecError("%s is unreadable or corrupt: %s" % (label, exc)) from exc
    if not isinstance(value, dict):
        raise OSVecError("%s must be a JSON object" % label)
    return value


def _validate_record(key, record, valid_types):
    if not isinstance(key, str) or not key:
        raise OSVecError("OSVec sidecar record keys must be non-empty strings")
    if not isinstance(record, dict):
        raise OSVecError("OSVec sidecar record %s must be an object" % key)

    memory_id = record.get("memory_id")
    uid = record.get("u64_id")
    text = record.get("text")
    memory_type = record.get("memory_type")
    source_file = record.get("source_file", "")
    tags = record.get("tags", [])
    created_at = record.get("created_at", "")
    run_slug = record.get("run_slug", "")

    if not isinstance(memory_id, str) or not memory_id:
        raise OSVecError("OSVec sidecar record %s has an invalid memory_id" % key)
    if isinstance(uid, bool) or not isinstance(uid, int) or not 0 <= uid < 2 ** 64:
        raise OSVecError("OSVec sidecar record %s has an invalid u64_id" % key)
    if key != str(uid):
        raise OSVecError("OSVec sidecar key/u64_id mismatch for %s" % memory_id)
    if stable_u64(memory_id) != uid:
        raise OSVecError("OSVec sidecar memory_id/u64_id mismatch for %s" % memory_id)
    if not isinstance(text, str) or not text:
        raise OSVecError("OSVec sidecar record %s has invalid text" % memory_id)
    if not isinstance(memory_type, str) or memory_type not in valid_types:
        raise OSVecError("OSVec sidecar record %s has invalid memory_type" % memory_id)
    if not isinstance(source_file, str):
        raise OSVecError("OSVec sidecar record %s has invalid source_file" % memory_id)
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        raise OSVecError("OSVec sidecar record %s has invalid tags" % memory_id)
    if not isinstance(created_at, str):
        raise OSVecError("OSVec sidecar record %s has invalid created_at" % memory_id)
    if not isinstance(run_slug, str):
        raise OSVecError("OSVec sidecar record %s has invalid run_slug" % memory_id)
    if run_slug and not memory_id.startswith(run_slug + "/"):
        raise OSVecError("OSVec sidecar record %s has a run_slug mismatch" % memory_id)

    _raise_for_secret_fields(
        (
            ("text", text),
            ("memory id", memory_id),
            ("source", source_file),
            ("tags", tags),
            ("run slug", run_slug),
        ),
        error_type=OSVecError,
    )
    normalized = dict(record)
    normalized.update(
        source_file=source_file,
        tags=list(tags),
        created_at=created_at,
        run_slug=run_slug,
    )
    return normalized


def _validate_sidecar_blob(blob, dim, embedder_name, valid_types):
    _raise_for_secret_fields((("sidecar", blob),), error_type=OSVecError)
    schema = blob.get("schema")
    if schema not in (None, SIDECAR_SCHEMA):
        raise OSVecError("unsupported OSVec sidecar schema: %r" % (schema,))
    backend = blob.get("backend")
    _index_artifact_path("index", backend)  # validates the backend name
    stored_dim = blob.get("dim")
    if isinstance(stored_dim, bool) or not isinstance(stored_dim, int):
        raise OSVecError("OSVec sidecar dim must be an integer")
    if stored_dim != dim:
        raise OSVecError(
            "OSVec sidecar dimension mismatch: expected %s, found %s"
            % (dim, stored_dim)
        )
    stored_embedder = blob.get("embedder")
    if not isinstance(stored_embedder, str) or stored_embedder != embedder_name:
        raise OSVecError(
            "OSVec sidecar embedder mismatch: expected %s, found %r"
            % (embedder_name, stored_embedder)
        )
    records = blob.get("records")
    if not isinstance(records, dict):
        raise OSVecError("OSVec sidecar records must be a JSON object")
    declared_count = blob.get("count")
    if declared_count is not None and (
            isinstance(declared_count, bool)
            or not isinstance(declared_count, int)
            or declared_count != len(records)):
        raise OSVecError("OSVec sidecar record count mismatch")

    normalized = {}
    memory_ids = set()
    for key, record in records.items():
        checked = _validate_record(key, record, valid_types)
        if checked["memory_id"] in memory_ids:
            raise OSVecError("OSVec sidecar contains duplicate memory ids")
        memory_ids.add(checked["memory_id"])
        normalized[key] = checked
    return backend, normalized, schema


def _validate_index_matches(index, records):
    try:
        index_count = len(index)
    except Exception as exc:
        raise OSVecError("OSVec index count could not be read: %s" % exc) from exc
    if index_count != len(records):
        raise OSVecError(
            "OSVec index/sidecar count mismatch: index=%s sidecar=%s"
            % (index_count, len(records))
        )
    for key in records:
        uid = int(key)
        try:
            present = bool(index.contains(np.uint64(uid)))
        except Exception as exc:
            raise OSVecError("OSVec index membership check failed: %s" % exc) from exc
        if not present:
            raise OSVecError(
                "OSVec index/sidecar id mismatch: index is missing %s" % uid
            )


def _validate_manifest_blob(manifest, sidecar_blob, index_artifact, sidecar_path):
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise OSVecError("unsupported or missing OSVec manifest schema")
    expected = {
        "backend": sidecar_blob.get("backend"),
        "dim": sidecar_blob.get("dim"),
        "embedder": sidecar_blob.get("embedder"),
        "count": len(sidecar_blob.get("records", {})),
        "index_file": os.path.basename(index_artifact),
        "sidecar_file": os.path.basename(sidecar_path),
    }
    for field, value in expected.items():
        if manifest.get(field) != value:
            raise OSVecError("OSVec manifest %s mismatch" % field)
    for field in ("index_sha256", "sidecar_sha256"):
        value = manifest.get(field)
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise OSVecError("OSVec manifest has an invalid %s" % field)


def _write_json_temp(directory, prefix, value):
    descriptor, path = tempfile.mkstemp(prefix=prefix, suffix=".json", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = None
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        return path
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        _unlink_if_present(path)
        raise


def _backup_regular_file(directory, path):
    if not os.path.lexists(path):
        return None
    _require_regular_unique_file(path, "OSVec persistence leaf")
    descriptor, backup_path = tempfile.mkstemp(
        prefix=".osvec-backup-", dir=directory
    )
    try:
        with open(path, "rb") as source, os.fdopen(descriptor, "wb") as backup:
            descriptor = None
            shutil.copyfileobj(source, backup, length=1024 * 1024)
            backup.flush()
            os.fsync(backup.fileno())
        return backup_path
    except Exception as exc:
        if descriptor is not None:
            os.close(descriptor)
        _unlink_if_present(backup_path)
        raise OSVecError(
            "OSVec could not preserve the last good store before commit: %s" % exc
        ) from exc


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

    def __init__(self, embedder=None, store_dir=None, index_path=None,
                 sidecar_path=None, manifest_path=None):
        _require_numpy()
        self.embedder = embedder or HashingEmbedder()
        self.dim = self.embedder.dim
        self.index, self.backend = _new_index(self.dim)
        self.sidecar = {}          # str(u64_id) -> record dict
        self.id_to_u64 = {}        # memory_id -> u64_id
        self._base_sidecar = {}
        self._lock_context = None
        self._lock_shared = False
        explicit_store = store_dir is not None
        requested_index = os.path.abspath(
            index_path or (os.path.join(store_dir, "project.tvim")
                           if explicit_store else INDEX_PATH))
        requested_sidecar = os.path.abspath(
            sidecar_path or (os.path.join(store_dir, "project.sidecar.json")
                            if explicit_store else SIDECAR_PATH))
        if store_dir is None and os.path.dirname(requested_index) != os.path.dirname(requested_sidecar):
            raise OSVecError("OSVec index and sidecar must share one store directory")
        self.store_dir = _prepare_store_directory(
            store_dir or os.path.dirname(requested_sidecar))
        self.index_path = requested_index
        self.sidecar_path = requested_sidecar
        requested_manifest = os.path.abspath(
            manifest_path or (os.path.join(self.store_dir, "project.manifest.json")
                              if explicit_store else MANIFEST_PATH))
        # Older callers changed the three original globals before constructing
        # a memory. Treat their sidecar directory as authoritative and derive
        # its manifest rather than writing to the import-time default store.
        if manifest_path is None and os.path.dirname(requested_manifest) != self.store_dir:
            requested_manifest = os.path.join(self.store_dir, "project.manifest.json")
        self.manifest_path = requested_manifest
        for path in (self.index_path, self.sidecar_path, self.manifest_path):
            if os.path.dirname(path) != self.store_dir:
                raise OSVecError("OSVec persistence files must share one store directory")

    def _acquire_lock(self, shared=False):
        if self._lock_context is not None:
            if self._lock_shared == shared:
                return
            self._release_lock()
        context = _store_lock(self.store_dir, shared=shared)
        context.__enter__()
        self._lock_context = context
        self._lock_shared = shared

    def _release_lock(self):
        if self._lock_context is None:
            return
        context, self._lock_context = self._lock_context, None
        self._lock_shared = False
        context.__exit__(None, None, None)

    def close(self):
        self._release_lock()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    # ---- write ----
    def add(self, text, memory_type, source_file="", memory_id=None, tags=None,
            run_slug=None):
        if not isinstance(text, str) or not text:
            raise ValueError("text must be a non-empty string")
        if memory_type not in self.VALID_TYPES:
            raise ValueError(f"memory_type must be one of {sorted(self.VALID_TYPES)}")
        if memory_id is not None and (not isinstance(memory_id, str) or not memory_id):
            raise ValueError("memory_id must be a non-empty string")
        if not isinstance(source_file, str):
            raise ValueError("source_file must be a string")
        if tags is None:
            tags = []
        if not isinstance(tags, (list, tuple)) or any(
                not isinstance(tag, str) for tag in tags):
            raise ValueError("tags must be a list of strings")
        tags = list(tags)
        if run_slug is not None and not isinstance(run_slug, str):
            raise ValueError("run_slug must be a string")
        run_slug = (run_slug or "").strip()
        # Keep the scan visibly adjacent to this public write boundary. Apart
        # from stopping metadata leaks, the explicit loop is a portable audit
        # contract for installers that cannot import numpy to exercise add().
        for field, value in (
            ("text", text),
            ("memory_id", memory_id),
            ("source_file", source_file),
            ("tags", tags),
            ("run_slug", run_slug),
        ):
            found = _secret_in_value(value, field, set())
            if found:
                field_path, pattern = found
                raise ValueError(
                    "Refusing to store memory: %s matches a secret pattern (%s). "
                    "Never put API keys/passwords in OSVec." % (field_path, pattern)
                )
        memory_id = memory_id or f"{memory_type}-{uuid.uuid4().hex}"
        # Namespace per run: prefix the logical id with '<run_slug>/' so two runs
        # minting the same logical id (e.g. decision-001) cannot silently
        # overwrite each other. Omitting run_slug preserves the global store.
        if run_slug and not memory_id.startswith(run_slug + "/"):
            memory_id = f"{run_slug}/{memory_id}"
        uid = stable_u64(memory_id)

        existing_at_uid = self.sidecar.get(str(uid))
        if existing_at_uid and existing_at_uid.get("memory_id") != memory_id:
            raise OSVecError("stable u64 collision between distinct memory ids")

        # update semantics: if this memory_id exists, remove the old vector first
        if memory_id in self.id_to_u64:
            old = self.id_to_u64[memory_id]
            if not self.index.remove(np.uint64(old)):
                raise OSVecError("OSVec index/sidecar mismatch while updating memory")
            self.sidecar.pop(str(old), None)

        vec = self.embedder.embed([text]).astype(np.float32)
        self.index.add_with_ids(vec, np.array([uid], dtype=np.uint64))
        rec = MemoryRecord(memory_id, uid, text, memory_type, source_file,
                           tags, time.strftime("%Y-%m-%dT%H:%M:%S"),
                           run_slug)
        self.sidecar[str(uid)] = asdict(rec)
        self.id_to_u64[memory_id] = uid
        return rec

    # ---- read ----
    def search(self, query, k=5, allowlist_types=None):
        if isinstance(k, bool) or not isinstance(k, int) or k < 0:
            raise ValueError("k must be a nonnegative integer")
        if k == 0:
            return []
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
        if not self.index.remove(np.uint64(uid)):
            return False
        self.id_to_u64.pop(memory_id, None)
        self.sidecar.pop(str(uid), None)
        return True

    # ---- persistence (index + side-car, kept in sync) ----
    def _merge_concurrent_changes_unlocked(self):
        latest = ProjectMemory(
            embedder=self.embedder,
            store_dir=self.store_dir,
            index_path=self.index_path,
            sidecar_path=self.sidecar_path,
            manifest_path=self.manifest_path,
        )._load_unlocked()
        if latest.sidecar == self._base_sidecar:
            return

        base = self._base_sidecar
        local = self.sidecar
        changed = {
            key: copy.deepcopy(record)
            for key, record in local.items()
            if base.get(key) != record
        }
        removed = set(base) - set(local)
        merged = copy.deepcopy(latest.sidecar)

        for key in removed:
            latest_record = latest.sidecar.get(key)
            if latest_record is not None and latest_record != base[key]:
                raise OSVecError(
                    "OSVec concurrent write conflict for %s"
                    % base[key].get("memory_id", key)
                )
            merged.pop(key, None)

        for key, local_record in changed.items():
            base_record = base.get(key)
            latest_record = latest.sidecar.get(key)
            if latest_record != base_record and latest_record != local_record:
                raise OSVecError(
                    "OSVec concurrent write conflict for %s"
                    % local_record.get("memory_id", key)
                )
            merged[key] = local_record

        self.backend = latest.backend
        self.index = _index_from_records(merged, self.backend, self.embedder)
        self.sidecar = merged
        self.id_to_u64 = {
            record["memory_id"]: int(record["u64_id"])
            for record in merged.values()
        }

    def save(self):
        self._acquire_lock(shared=False)
        try:
            self._merge_concurrent_changes_unlocked()
            self._save_unlocked()
            self._base_sidecar = copy.deepcopy(self.sidecar)
        finally:
            self._release_lock()

    def _save_unlocked(self):
        _prepare_store_directory(self.store_dir)
        embedder_name = getattr(self.embedder, "name", "?")
        sidecar_blob = {
            "schema": SIDECAR_SCHEMA,
            "backend": self.backend,
            "dim": self.dim,
            "embedder": embedder_name,
            "count": len(self.sidecar),
            "records": self.sidecar,
        }
        _, checked_records, _ = _validate_sidecar_blob(
            sidecar_blob, self.dim, embedder_name, self.VALID_TYPES
        )
        _validate_index_matches(self.index, checked_records)

        descriptor, staged_index_base = tempfile.mkstemp(
            prefix=".osvec-index-", suffix=".tvim", dir=self.store_dir
        )
        os.close(descriptor)
        _unlink_if_present(staged_index_base)
        staged_index_artifact = _index_artifact_path(staged_index_base, self.backend)
        staged_sidecar = None
        staged_manifest = None
        backups = {}
        preserve_backups = False
        try:
            try:
                self.index.write(staged_index_base)
            except Exception as exc:
                raise OSVecError("OSVec index staging failed: %s" % exc) from exc
            if not os.path.isfile(staged_index_artifact):
                raise OSVecError("OSVec index staging did not produce a persistence file")
            _fsync_file(staged_index_artifact)

            staged_loaded = _load_index(staged_index_base, self.backend, self.dim)
            _validate_index_matches(staged_loaded, checked_records)

            staged_sidecar = _write_json_temp(
                self.store_dir, ".osvec-sidecar-", sidecar_blob
            )
            staged_blob = _read_json_object(staged_sidecar, "staged OSVec sidecar")
            staged_backend, staged_records, _ = _validate_sidecar_blob(
                staged_blob, self.dim, embedder_name, self.VALID_TYPES
            )
            if staged_backend != self.backend:
                raise OSVecError("staged OSVec sidecar backend mismatch")
            _validate_index_matches(staged_loaded, staged_records)

            final_index_artifact = _index_artifact_path(
                self.index_path, self.backend
            )
            manifest_blob = {
                "schema": MANIFEST_SCHEMA,
                "backend": self.backend,
                "dim": self.dim,
                "embedder": embedder_name,
                "count": len(staged_records),
                "index_file": os.path.basename(final_index_artifact),
                "index_sha256": _sha256_file(staged_index_artifact),
                "sidecar_file": os.path.basename(self.sidecar_path),
                "sidecar_sha256": _sha256_file(staged_sidecar),
            }
            staged_manifest = _write_json_temp(
                self.store_dir, ".osvec-manifest-", manifest_blob
            )
            _validate_manifest_blob(
                _read_json_object(staged_manifest, "staged OSVec manifest"),
                sidecar_blob,
                final_index_artifact,
                self.sidecar_path,
            )

            final_paths = (
                final_index_artifact,
                self.sidecar_path,
                self.manifest_path,
            )
            backups = {
                path: _backup_regular_file(self.store_dir, path)
                for path in final_paths
            }
            _fsync_directory(self.store_dir)
            try:
                os.replace(staged_index_artifact, final_index_artifact)
                staged_index_artifact = None
                os.replace(staged_sidecar, self.sidecar_path)
                staged_sidecar = None
                _fsync_directory(self.store_dir)
                os.replace(staged_manifest, self.manifest_path)
                staged_manifest = None
                _fsync_directory(self.store_dir)
            except Exception as exc:
                rollback_errors = []
                for path in final_paths:
                    backup_path = backups[path]
                    try:
                        if backup_path is None:
                            _unlink_if_present(path)
                        else:
                            os.replace(backup_path, path)
                            backups[path] = None
                    except Exception as rollback_exc:
                        rollback_errors.append(
                            "%s: %s" % (os.path.basename(path), rollback_exc)
                        )
                try:
                    _fsync_directory(self.store_dir)
                except OSError as rollback_exc:
                    rollback_errors.append("directory fsync: %s" % rollback_exc)
                if rollback_errors:
                    preserve_backups = True
                    raise OSVecError(
                        "OSVec commit failed and rollback was incomplete; "
                        "recovery backups were retained (%s)"
                        % "; ".join(rollback_errors)
                    ) from exc
                raise OSVecError(
                    "OSVec commit failed; the last good store was restored: %s"
                    % exc
                ) from exc
        finally:
            _unlink_if_present(staged_index_base)
            _unlink_if_present(staged_index_artifact)
            _unlink_if_present(staged_sidecar)
            _unlink_if_present(staged_manifest)
            if not preserve_backups:
                for backup_path in backups.values():
                    _unlink_if_present(backup_path)

    def load(self, for_update=False):
        """Load under a shared read lock or a held exclusive update lock."""
        self._acquire_lock(shared=not for_update)
        loaded = False
        try:
            result = self._load_unlocked()
            loaded = True
            return result
        finally:
            if not (for_update and loaded):
                self._release_lock()

    def _load_unlocked(self):
        _prepare_store_directory(self.store_dir)
        if not os.path.lexists(self.sidecar_path):
            partial_paths = (
                self.index_path,
                self.index_path + ".npz",
                self.manifest_path,
            )
            if any(os.path.exists(path) for path in partial_paths):
                raise OSVecError("OSVec sidecar is missing from a partial store")
            return self
        blob = _read_json_object(self.sidecar_path, "OSVec sidecar")
        embedder_name = getattr(self.embedder, "name", "?")
        backend, records, schema = _validate_sidecar_blob(
            blob, self.dim, embedder_name, self.VALID_TYPES
        )
        index_artifact = _index_artifact_path(self.index_path, backend)

        if os.path.lexists(self.manifest_path):
            manifest = _read_json_object(self.manifest_path, "OSVec manifest")
            _validate_manifest_blob(
                manifest, blob, index_artifact, self.sidecar_path
            )
            if not os.path.isfile(index_artifact):
                raise OSVecError("OSVec index is missing")
            if _sha256_file(index_artifact) != manifest["index_sha256"]:
                raise OSVecError("OSVec index integrity mismatch")
            if _sha256_file(self.sidecar_path) != manifest["sidecar_sha256"]:
                raise OSVecError("OSVec sidecar integrity mismatch")
        elif schema == SIDECAR_SCHEMA:
            raise OSVecError("OSVec manifest is missing for the current sidecar schema")

        index = _load_index(self.index_path, backend, self.dim)
        _validate_index_matches(index, records)
        self.backend = backend
        self.index = index
        self.sidecar = records
        self.id_to_u64 = {
            record["memory_id"]: int(record["u64_id"])
            for record in records.values()
        }
        self._base_sidecar = copy.deepcopy(records)
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
    with tempfile.TemporaryDirectory(prefix="project-os-osvec-selftest-") as tmp:
        test_store = os.path.join(tmp, "store")
        print("backend:", end=" ")
        mem = ProjectMemory(store_dir=test_store)
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
            mem.add("my key is " + "sk-" + "ABCDEFGHIJKLMNOP1234567890",
                    "lesson", memory_id="bad")
            print("FAIL: secret was not blocked")
            return 1
        except ValueError:
            print("secret correctly refused")
        # persistence roundtrip
        mem.save()
        mem2 = ProjectMemory(store_dir=test_store).load()
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
        return _selftest()

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
    return 0


def _run_cli():
    try:
        return main()
    except (OSVecError, ValueError, OSError) as exc:
        sys.stderr.write("osvec: %s\n" % exc)
        return 2


if __name__ == "__main__":
    sys.exit(_run_cli())
