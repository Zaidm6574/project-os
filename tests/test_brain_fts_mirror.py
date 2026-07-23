"""Tmp-isolated tests for the brain FTS mirror — never touch a live brain or db."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MIRROR_PATH = ROOT / "addons" / "full-engine" / "memory" / "brain_fts_mirror.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


mirror = load_module(MIRROR_PATH, "brain_fts_mirror_under_test")


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


RECORDS = [
    {"id": "r1", "ts": "2026-07-01", "type": "lesson", "text": "Release gate requires a signed receipt before publish"},
    {"id": "r2", "ts": "2026-07-02", "type": "decision", "text": "Event claim uses insert once semantics to stay idempotent"},
    {"id": "r3", "ts": "2026-07-03", "type": "lesson", "text": "Verify the actual model in the first agent transcript"},
    {"kind": "note", "text": "bare record without id, as seen in older exports"},
]


class BrainFtsMirrorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.jsonl = root / "shared-brain.jsonl"
        self.db = root / "mirror.db"
        write_jsonl(self.jsonl, RECORDS)

    def tearDown(self):
        self.temp.cleanup()

    def rebuild(self):
        return mirror.rebuild(self.jsonl, self.db)

    def test_rebuild_reports_full_count_and_parity_holds(self):
        result = self.rebuild()
        self.assertEqual(4, result["records"])
        self.assertEqual([], mirror.verify(self.jsonl, self.db))

    def test_query_finds_exact_keywords_ranked(self):
        self.rebuild()
        hits = mirror.query("signed receipt", brain_path=self.jsonl, db_path=self.db)
        self.assertEqual(1, len(hits))
        self.assertEqual("r1", hits[0]["id"])

    def test_query_covers_records_without_id(self):
        self.rebuild()
        hits = mirror.query("bare record", brain_path=self.jsonl, db_path=self.db)
        self.assertEqual(1, len(hits))
        self.assertEqual("", hits[0]["id"])

    def test_appended_line_is_drift_and_query_refuses(self):
        self.rebuild()
        with self.jsonl.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"id": "r5", "text": "new lesson after rebuild"}) + "\n")
        problems = mirror.verify(self.jsonl, self.db)
        self.assertTrue(problems)
        with self.assertRaises(mirror.MirrorError):
            mirror.query("lesson", brain_path=self.jsonl, db_path=self.db)

    def test_edited_line_is_drift_even_with_same_count(self):
        # near-miss: same record count, one line silently altered
        self.rebuild()
        lines = self.jsonl.read_text(encoding="utf-8").splitlines()
        rec = json.loads(lines[1])
        rec["text"] = "Event claim uses read then insert"
        lines[1] = json.dumps(rec)
        self.jsonl.write_text("\n".join(lines) + "\n", encoding="utf-8")
        problems = mirror.verify(self.jsonl, self.db)
        self.assertTrue(any("mismatch" in p for p in problems))

    def test_malformed_jsonl_fails_loudly_not_silently(self):
        self.jsonl.write_text('{"id": "ok", "text": "fine"}\n{broken\n', encoding="utf-8")
        with self.assertRaises(mirror.MirrorError):
            mirror.rebuild(self.jsonl, self.db)

    def test_rebuild_after_drift_restores_parity(self):
        self.rebuild()
        with self.jsonl.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"id": "r5", "text": "new lesson after rebuild"}) + "\n")
        self.rebuild()
        self.assertEqual([], mirror.verify(self.jsonl, self.db))
        hits = mirror.query("new lesson after rebuild", brain_path=self.jsonl, db_path=self.db)
        self.assertEqual(["r5"], [h["id"] for h in hits])

    def test_query_connection_is_read_only(self):
        self.rebuild()
        con = mirror.open_readonly(self.db)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                con.execute("DELETE FROM records")
        finally:
            con.close()

    def test_missing_db_refuses_with_rebuild_hint(self):
        with self.assertRaises(mirror.MirrorError):
            mirror.verify(self.jsonl, self.db)

    def test_tampered_fts_index_is_drift_and_query_refuses(self):
        # a plain SELECT on an external-content FTS table is answered from the
        # records table, so a damaged index is invisible to count checks —
        # verify() must catch it via the docsize shadow table
        self.rebuild()
        con = sqlite3.connect(self.db)
        try:
            line_no, text = con.execute(
                "SELECT line_no, text FROM records ORDER BY line_no LIMIT 1"
            ).fetchone()
            con.execute(
                "INSERT INTO records_fts(records_fts, rowid, text) VALUES ('delete', ?, ?)",
                (line_no, text),
            )
            con.commit()
        finally:
            con.close()
        problems = mirror.verify(self.jsonl, self.db)
        self.assertTrue(any("fts index" in p for p in problems))
        with self.assertRaises(mirror.MirrorError):
            mirror.query("signed receipt", brain_path=self.jsonl, db_path=self.db)

    def test_count_preserving_index_poisoning_is_drift(self):
        # replacing indexed terms while keeping the document count constant
        # must still be caught — the term-signature check, not the doc count,
        # is what detects this (judge pass-2 finding)
        self.rebuild()
        con = sqlite3.connect(self.db)
        try:
            line_no, text = con.execute(
                "SELECT line_no, text FROM records ORDER BY line_no LIMIT 1"
            ).fetchone()
            con.execute(
                "INSERT INTO records_fts(records_fts, rowid, text) VALUES ('delete', ?, ?)",
                (line_no, text),
            )
            con.execute(
                "INSERT INTO records_fts(rowid, text) VALUES (?, ?)",
                (line_no, "poisoned replacement terms"),
            )
            con.commit()
        finally:
            con.close()
        problems = mirror.verify(self.jsonl, self.db)
        self.assertTrue(any("signature" in p for p in problems))
        with self.assertRaises(mirror.MirrorError):
            mirror.query("signed receipt", brain_path=self.jsonl, db_path=self.db)

    def test_row_swap_poisoning_is_drift(self):
        # swapping the indexed text of two rows preserves every per-row count;
        # only a position-aware (instance-mode) signature catches it
        self.rebuild()
        con = sqlite3.connect(self.db)
        try:
            rows = con.execute(
                "SELECT line_no, text FROM records ORDER BY line_no LIMIT 2"
            ).fetchall()
            (ln1, t1), (ln2, t2) = rows
            for ln, t in ((ln1, t1), (ln2, t2)):
                con.execute(
                    "INSERT INTO records_fts(records_fts, rowid, text) VALUES ('delete', ?, ?)",
                    (ln, t),
                )
            con.execute("INSERT INTO records_fts(rowid, text) VALUES (?, ?)", (ln1, t2))
            con.execute("INSERT INTO records_fts(rowid, text) VALUES (?, ?)", (ln2, t1))
            con.commit()
        finally:
            con.close()
        problems = mirror.verify(self.jsonl, self.db)
        self.assertTrue(any("signature" in p for p in problems))

    def test_special_characters_in_db_path_survive_readonly_uri(self):
        # paths containing ?, #, or % must not break the file: URI used for
        # read-only opens (judge pass-2 finding)
        weird = Path(self.temp.name) / "odd %41 #frag ?query"
        weird.mkdir()
        db = weird / "mirror.db"
        mirror.rebuild(self.jsonl, db)
        self.assertEqual([], mirror.verify(self.jsonl, db))
        hits = mirror.query("signed receipt", brain_path=self.jsonl, db_path=db)
        self.assertEqual(["r1"], [h["id"] for h in hits])

    def test_damaged_db_missing_tables_is_mirror_error_not_traceback(self):
        con = sqlite3.connect(self.db)
        con.execute("CREATE TABLE unrelated (x)")
        con.commit()
        con.close()
        with self.assertRaises(mirror.MirrorError) as raised:
            mirror.verify(self.jsonl, self.db)
        self.assertIn("rebuild", str(raised.exception))

    def test_invalid_match_syntax_is_mirror_error_not_traceback(self):
        self.rebuild()
        with self.assertRaises(mirror.MirrorError):
            mirror.query('"', brain_path=self.jsonl, db_path=self.db)

    def test_default_brain_path_symlink_escape_is_refused(self):
        # same containment rule as brain/brain.py: a symlinked default brain
        # file must not let the mirror index content outside the project
        fresh = load_module(MIRROR_PATH, "brain_fts_mirror_symlink_escape")
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            project = base / "project"
            outside = base / "outside"
            (project / "brain").mkdir(parents=True)
            (project / "memory").mkdir()
            outside.mkdir()
            external = outside / "external.jsonl"
            write_jsonl(external, RECORDS)
            link = project / "brain" / "shared-brain.jsonl"
            link.symlink_to(external)

            with mock.patch.object(fresh, "ROOT", project), mock.patch.object(
                fresh, "BRAIN_JSONL", link
            ), mock.patch.object(fresh, "MIRROR_DB", project / "memory" / "mirror.db"):
                with self.assertRaises(fresh.MirrorError) as raised:
                    fresh.rebuild()
                self.assertIn("outside the project", str(raised.exception))

    def test_installed_layout_default_paths_work_end_to_end(self):
        # proves HERE.parent/brain resolution against the real installed shape
        # (install_full_engine.py copies addon memory/ and brain/ as siblings)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target"
            (target / "memory").mkdir(parents=True)
            (target / "brain").mkdir()
            script = target / "memory" / "brain_fts_mirror.py"
            script.write_text(MIRROR_PATH.read_text(encoding="utf-8"), encoding="utf-8")
            write_jsonl(target / "brain" / "shared-brain.jsonl", RECORDS)

            def run(*args):
                return subprocess.run(
                    [sys.executable, str(script), *args],
                    capture_output=True, text=True, timeout=60,
                )

            rebuilt = run("rebuild")
            self.assertEqual(0, rebuilt.returncode, rebuilt.stdout + rebuilt.stderr)
            self.assertTrue((target / "memory" / "brain-fts-mirror.db").is_file())
            checked = run("check")
            self.assertEqual(0, checked.returncode, checked.stdout + checked.stderr)
            queried = run("query", "signed receipt")
            self.assertEqual(0, queried.returncode, queried.stdout + queried.stderr)
            self.assertIn("r1", queried.stdout)


if __name__ == "__main__":
    unittest.main()
