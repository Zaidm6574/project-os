#!/usr/bin/env python3
"""Read-only SQLite/FTS5 mirror of brain/shared-brain.jsonl for exact recall.

Complements the OSVec store (memory/osvec_adapter.py): OSVec serves semantic
retrieval and may legitimately return zero when a query does not match; this
mirror serves deterministic keyword/phrase lookups (BM25-ranked) that must
never silently miss. The JSONL stays the only write plane — the mirror is
rebuilt from it and refuses queries when it no longer matches (fail-closed;
parity = source sha256 + record count + per-record sha256 + full FTS index
term signature).

Usage:
  python3 memory/brain_fts_mirror.py rebuild            # (re)build mirror db
  python3 memory/brain_fts_mirror.py check              # exit 1 on drift
  python3 memory/brain_fts_mirror.py query "signed receipt" [--limit 5]

The generated database (memory/brain-fts-mirror.db) is derived local data:
it can contain your full brain text, so it is gitignored and excluded from
the installers — never commit or ship it.

Unlike the export/import tools, malformed JSONL lines are a hard error here:
a parity mirror must represent the source exactly or refuse to exist.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BRAIN_JSONL = ROOT / "brain" / "shared-brain.jsonl"
MIRROR_DB = HERE / "brain-fts-mirror.db"


class MirrorError(RuntimeError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _contained(path: Path, label: str) -> Path:
    """Resolve a default path and refuse anything outside the project copy.

    Same containment rule as brain/brain.py: a symlinked default (for example
    brain/shared-brain.jsonl pointing at an external file) must not let the
    mirror index or write content outside this project.
    """
    root = Path(ROOT).resolve()
    real = Path(path).resolve()
    if real != root and root not in real.parents:
        raise MirrorError(f"refuse: {label} '{path}' resolves outside the project ({root})")
    return real


def _resolve_paths(brain_path: Path | None, db_path: Path | None) -> tuple[Path, Path]:
    """Default paths pass the containment gate; explicit overrides are the caller's."""
    if brain_path is None:
        brain_path = _contained(BRAIN_JSONL, "brain jsonl")
    if db_path is None:
        db_path = _contained(MIRROR_DB, "mirror db")
    return Path(brain_path), Path(db_path)


def read_records(brain_path: Path) -> tuple[list[dict], str]:
    """Parse the JSONL strictly; return (records, source sha256)."""
    if not brain_path.is_file():
        raise MirrorError(f"brain jsonl not found: {brain_path}")
    raw = brain_path.read_bytes()
    records = []
    for line_no, line in enumerate(raw.decode("utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            rec = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise MirrorError(f"malformed JSONL at line {line_no}: {exc}") from exc
        if not isinstance(rec, dict):
            raise MirrorError(f"non-object JSONL record at line {line_no}")
        records.append(
            {
                "line_no": line_no,
                "record_id": str(rec.get("id") or rec.get("memory_id") or ""),
                "ts": str(rec.get("ts") or rec.get("created_at") or ""),
                "type": str(rec.get("type") or rec.get("kind") or ""),
                "text": str(rec.get("text") or rec.get("lesson") or ""),
                "raw": stripped,
                "record_sha256": sha256_bytes(stripped.encode("utf-8")),
            }
        )
    return records, sha256_bytes(raw)


def _create_schema(con: sqlite3.Connection) -> None:
    try:
        con.executescript(
            """
            CREATE TABLE records (
              line_no INTEGER PRIMARY KEY,
              record_id TEXT NOT NULL,
              ts TEXT NOT NULL,
              type TEXT NOT NULL,
              text TEXT NOT NULL,
              raw TEXT NOT NULL,
              record_sha256 TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE records_fts USING fts5(
              text, record_id UNINDEXED, content='records', content_rowid='line_no'
            );
            """
        )
    except sqlite3.OperationalError as exc:
        raise MirrorError(
            f"this Python's SQLite lacks the FTS5 extension ({exc}); "
            "the mirror cannot be built without it"
        ) from exc


def _insert_records(con: sqlite3.Connection, records: list[dict]) -> None:
    con.executemany(
        "INSERT INTO records VALUES (:line_no, :record_id, :ts, :type, :text, :raw, :record_sha256)",
        records,
    )
    con.execute("INSERT INTO records_fts(records_fts) VALUES ('rebuild')")


def _fts_index_signature(con: sqlite3.Connection) -> str:
    """Hash the full FTS term index (term, doc-count, occurrence-count).

    The FTS table is external-content, so plain SELECTs are answered from the
    records table and cannot see a damaged or poisoned index. The fts5vocab
    dump reads the index itself; any term added, removed, or moved between
    documents changes this signature. The temp schema stays writable even on
    a read-only database connection.
    """
    # 'instance' mode exposes (term, doc, col, offset) — row ownership and token
    # positions included, so swapping two rows' indexed text or reordering tokens
    # (which row-level counts cannot see) still changes the signature.
    con.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS temp.mirror_vocab "
        "USING fts5vocab('main', 'records_fts', 'instance')"
    )
    digest = hashlib.sha256()
    for term, doc, col, offset in con.execute(
        "SELECT term, doc, col, offset FROM temp.mirror_vocab ORDER BY term, doc, offset"
    ):
        digest.update(f"{term}\x1f{doc}\x1f{col}\x1f{offset}\x1e".encode("utf-8"))
    return digest.hexdigest()


def _expected_index_signature(records: list[dict]) -> str:
    """Signature a correct index for these records would have (fresh in-memory build)."""
    con = sqlite3.connect(":memory:")
    try:
        _create_schema(con)
        _insert_records(con, records)
        return _fts_index_signature(con)
    finally:
        con.close()


def rebuild(brain_path: Path | None = None, db_path: Path | None = None) -> dict:
    brain_path, db_path = _resolve_paths(brain_path, db_path)
    records, source_sha = read_records(brain_path)
    fd, tmp_name = tempfile.mkstemp(dir=str(db_path.parent), suffix=".db.tmp")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        con = sqlite3.connect(tmp)
        try:
            _create_schema(con)
            con.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            _insert_records(con, records)
            con.executemany(
                "INSERT INTO meta VALUES (?, ?)",
                [
                    ("source_path", str(brain_path)),
                    ("source_sha256", source_sha),
                    ("record_count", str(len(records))),
                ],
            )
            con.commit()
        finally:
            con.close()
        os.replace(tmp, db_path)
    finally:
        tmp.unlink(missing_ok=True)
    return {"records": len(records), "source_sha256": source_sha, "db": str(db_path)}


def open_readonly(db_path: Path) -> sqlite3.Connection:
    if not db_path.is_file():
        raise MirrorError(f"mirror db not found: {db_path} — run rebuild first")
    # percent-escape so paths containing ?, #, or % survive URI parsing
    quoted = urllib.parse.quote(str(db_path), safe="/")
    return sqlite3.connect(f"file:{quoted}?mode=ro", uri=True)


def verify(brain_path: Path | None = None, db_path: Path | None = None) -> list[str]:
    """Return a list of drift problems; empty means parity holds."""
    brain_path, db_path = _resolve_paths(brain_path, db_path)
    records, source_sha = read_records(brain_path)
    problems = []
    con = open_readonly(db_path)
    try:
        try:
            meta = dict(con.execute("SELECT key, value FROM meta"))
            if meta.get("source_sha256") != source_sha:
                problems.append("source sha256 mismatch (jsonl changed since rebuild)")
            stored_count = con.execute("SELECT count(*) FROM records").fetchone()[0]
            if stored_count != len(records):
                problems.append(f"record count mismatch: db {stored_count} vs jsonl {len(records)}")
            stored = dict(con.execute("SELECT line_no, record_sha256 FROM records"))
            for rec in records:
                if stored.get(rec["line_no"]) != rec["record_sha256"]:
                    problems.append(f"per-record hash mismatch at jsonl line {rec['line_no']}")
                    break
            if _fts_index_signature(con) != _expected_index_signature(records):
                problems.append(
                    "fts index term signature mismatch (index damaged, poisoned, or stale)"
                )
        except sqlite3.DatabaseError as exc:
            raise MirrorError(
                f"mirror db is damaged or not a mirror ({exc}) — "
                "run: python3 memory/brain_fts_mirror.py rebuild"
            ) from exc
    finally:
        con.close()
    return problems


def query(terms: str, limit: int = 10, brain_path: Path | None = None,
          db_path: Path | None = None) -> list[dict]:
    brain_path, db_path = _resolve_paths(brain_path, db_path)
    problems = verify(brain_path, db_path)
    if problems:
        raise MirrorError(
            "mirror is stale, refusing to answer: " + "; ".join(problems)
            + " — run: python3 memory/brain_fts_mirror.py rebuild"
        )
    con = open_readonly(db_path)
    try:
        try:
            rows = con.execute(
                """
                SELECT r.record_id, r.ts, r.type,
                       snippet(records_fts, 0, '[', ']', ' … ', 18) AS snip
                FROM records_fts JOIN records r ON r.line_no = records_fts.rowid
                WHERE records_fts MATCH ?
                ORDER BY bm25(records_fts)
                LIMIT ?
                """,
                (terms, limit),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            raise MirrorError(f"invalid FTS query syntax: {terms!r} ({exc})") from exc
    finally:
        con.close()
    return [{"id": r[0], "ts": r[1], "type": r[2], "snippet": r[3]} for r in rows]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("rebuild")
    sub.add_parser("check")
    q = sub.add_parser("query")
    q.add_argument("terms")
    q.add_argument("--limit", type=int, default=10)
    ap.add_argument("--brain", default=None, help="override brain jsonl path (tests)")
    ap.add_argument("--db", default=None, help="override mirror db path (tests)")
    args = ap.parse_args(argv)
    brain = Path(args.brain) if args.brain else None
    db = Path(args.db) if args.db else None
    try:
        if args.cmd == "rebuild":
            result = rebuild(brain, db)
            print(f"rebuilt: {result['records']} records, source sha {result['source_sha256'][:12]}…")
            return 0
        if args.cmd == "check":
            problems = verify(brain, db)
            if problems:
                print("DRIFT: " + "; ".join(problems))
                return 1
            print("OK: mirror matches jsonl (sha256 + count + per-record hashes + fts index signature).")
            return 0
        hits = query(args.terms, args.limit, brain, db)
        if not hits:
            print("no matches")
            return 0
        for hit in hits:
            print(f"{hit['id'] or '(no id)'} | {hit['ts']} | {hit['type']} | {hit['snippet']}")
        return 0
    except MirrorError as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
