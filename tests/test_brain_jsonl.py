"""JSONL integrity tests for the shared-brain toolchain (2026-07 judge loop):
brain_append must emit exactly one canonical object per physical line and
refuse non-object JSON; brain_scale and brain_archive must tolerate scalar,
malformed, and multi-line-split lines (skip + count, never crash, never drop);
archive paths must be derived from the real filename suffix, not str.replace.
"""
import contextlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import warnings
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import brain_archive  # noqa: E402
import brain_scale  # noqa: E402


def run_py(script, *args, env_extra=None, stdin=None):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True, text=True, env=env, input=stdin)


class BrainEnvMixin(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.brain = Path(self.tmp.name) / "central-brain" / "shared-brain.jsonl"
        self.env = {
            "PROJECT_OS_SHARED_BRAIN": str(self.brain),
            "MNEME_INDEX": str(Path(self.tmp.name) / "mneme_index.json"),
            "MNEME_EMBEDDER": "lexical",
            "BB_LOCK_DIR": str(Path(self.tmp.name) / "locks"),
        }

    def tearDown(self):
        self.tmp.cleanup()


MESSY_LINES = [
    json.dumps({"id": "old-1", "kind": "interest", "text": "stale", "ts": "2020-01-01"}),
    "42",                      # scalar — valid JSON, not an object
    "{oops",                   # malformed
    '{"id": "frag",',          # multi-line object split across two physical lines
    '"text": "fragment"}',
    json.dumps({"id": "keep-1", "kind": "lesson", "text": "fine", "ts": "2026-07-01"}),
]
MESSY_SKIPPED = 4  # scalar + malformed + two fragment halves


class TestBrainAppendCanonicalLine(BrainEnvMixin):
    def test_trailing_value_flags_are_clean_usage_errors(self):
        cases = (
            (("--line",), "--line"),
            (("--line", "--no-reindex"), "--line"),
            (("--line", "{}", "--agent"), "--agent"),
            (("--line", "{}", "--agent", "--no-reindex"), "--agent"),
        )
        for args, flag in cases:
            with self.subTest(flag=flag):
                r = run_py(SCRIPTS / "brain_append.py", *args,
                           env_extra=self.env)
                self.assertEqual(r.returncode, 2)
                self.assertIn(f"missing value for {flag}", r.stderr)
                self.assertNotIn("Traceback", r.stderr)
                self.assertEqual(len(r.stderr.splitlines()), 1)
        self.assertFalse(self.brain.exists())

    def test_multiline_object_lands_as_one_physical_line(self):
        pretty = '{\n  "kind": "lesson",\n  "text": "multi-line input"\n}'
        r = run_py(SCRIPTS / "brain_append.py", "--line", pretty,
                   "--no-reindex", env_extra=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = self.brain.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1, "must append exactly one physical line")
        # The writer normalizes the legacy "kind" alias onto the canonical
        # "type" key and stamps a deterministic id, so assert the text survived
        # and the discriminator landed canonically rather than pinning the old
        # shape (readers still accept "kind" on rows already on disk).
        record = json.loads(lines[0])
        self.assertEqual(record["text"], "multi-line input")
        self.assertEqual(record["type"], "lesson")
        self.assertNotIn("kind", record)
        self.assertTrue(record["id"], "writer must stamp an id")

    def test_multiline_via_stdin_lands_as_one_physical_line(self):
        pretty = '{\n  "kind": "lesson",\n  "text": "stdin"\n}\n'
        r = run_py(SCRIPTS / "brain_append.py", "--no-reindex",
                   env_extra=self.env, stdin=pretty)
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = self.brain.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["text"], "stdin")

    def test_scalar_array_string_are_refused_and_file_untouched(self):
        for bad in ("42", "[1, 2]", '"just a string"', "null", "true"):
            r = run_py(SCRIPTS / "brain_append.py", "--line", bad,
                       "--no-reindex", env_extra=self.env)
            self.assertEqual(r.returncode, 2, f"{bad!r} must be refused")
            self.assertIn("REFUSED", r.stderr)
        self.assertFalse(self.brain.exists(),
                         "refused appends must not create the brain file")

    def test_non_finite_numbers_are_refused_and_file_untouched(self):
        for bad in (
            '{"value": NaN}',
            '{"value": Infinity}',
            '{"value": -Infinity}',
            '{"value": 1e999}',
        ):
            with self.subTest(payload=bad):
                r = run_py(SCRIPTS / "brain_append.py", "--line", bad,
                           "--no-reindex", env_extra=self.env)
                self.assertEqual(r.returncode, 2)
                self.assertIn("REFUSED", r.stderr)
                self.assertNotIn("Traceback", r.stderr)
        self.assertFalse(self.brain.exists(),
                         "non-finite JSON must not create the brain file")

    def test_refusal_leaves_existing_content_unchanged(self):
        self.brain.parent.mkdir(parents=True)
        before = json.dumps({"kind": "lesson", "text": "existing"}) + "\n"
        self.brain.write_text(before, encoding="utf-8")
        r = run_py(SCRIPTS / "brain_append.py", "--line", "[1, 2]",
                   "--no-reindex", env_extra=self.env)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.brain.read_text(encoding="utf-8"), before)


class TestBrainScaleMalformedTolerance(BrainEnvMixin):
    def _seed_messy(self):
        self.brain.parent.mkdir(parents=True)
        self.brain.write_text("\n".join(MESSY_LINES) + "\n", encoding="utf-8")

    def test_gauge_completes_and_reports_skip_count(self):
        self._seed_messy()
        r = run_py(SCRIPTS / "brain_scale.py", env_extra=self.env)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn(r.returncode, (0, 1, 2), r.stderr)
        self.assertIn(f"skipped {MESSY_SKIPPED} malformed lines", r.stdout)
        self.assertIn("OVERALL", r.stdout)

    def test_json_output_carries_skip_count_and_stale_interest(self):
        self._seed_messy()
        r = run_py(SCRIPTS / "brain_scale.py", "--json", env_extra=self.env)
        self.assertNotIn("Traceback", r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["context"]["skipped_malformed_lines"], MESSY_SKIPPED)
        self.assertEqual(out["context"]["stale_interest_gt60d"], 1)

    def test_non_string_embedder_metadata_falls_back_without_traceback(self):
        self.brain.parent.mkdir(parents=True)
        self.brain.write_text(
            json.dumps({"kind": "lesson", "text": "safe"}) + "\n",
            encoding="utf-8")
        index = Path(self.tmp.name) / "mneme_index.json"
        index.write_text(
            json.dumps({"embedder": {"unexpected": "object"}, "entries": []}),
            encoding="utf-8")
        empty_root = Path(self.tmp.name) / "project"
        empty_root.mkdir()

        stdout = io.StringIO()
        with mock.patch.object(brain_scale, "SHARED_BRAIN", str(self.brain)), \
                mock.patch.object(brain_scale, "OSVEC", str(index)), \
                mock.patch.object(brain_scale, "TASTE_INV", str(Path(self.tmp.name) / "taste.json")), \
                mock.patch.object(brain_scale, "ROOT", str(empty_root)), \
                mock.patch.object(sys, "argv", ["brain_scale.py", "--json"]), \
                contextlib.redirect_stdout(stdout):
            with self.assertRaises(SystemExit) as caught:
                brain_scale.main()

        self.assertEqual(caught.exception.code, 0)
        self.assertEqual(json.loads(stdout.getvalue())["context"]["embedder"], "none")

    def test_count_json_entries_closes_the_index_file(self):
        index = Path(self.tmp.name) / "mneme_index.json"
        index.write_text(json.dumps({"entries": []}), encoding="utf-8")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ResourceWarning)
            self.assertEqual(brain_scale.count_json_entries(str(index)), 0)
        self.assertFalse(
            [item for item in caught if issubclass(item.category, ResourceWarning)],
            caught)


class TestBrainArchiveMalformedTolerance(BrainEnvMixin):
    def _seed_messy(self):
        self.brain.parent.mkdir(parents=True)
        self.brain.write_text("\n".join(MESSY_LINES) + "\n", encoding="utf-8")

    def test_candidates_completes_and_reports_skip_count(self):
        self._seed_messy()
        r = run_py(SCRIPTS / "brain_archive.py", "candidates",
                   "--interest-days", "60", env_extra=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"skipped {MESSY_SKIPPED} malformed lines", r.stdout)
        self.assertIn("old-1", r.stdout)

    def test_apply_archives_stale_and_preserves_malformed_lines_verbatim(self):
        self._seed_messy()
        r = run_py(SCRIPTS / "brain_archive.py", "apply",
                   "--interest-days", "60", env_extra=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"skipped {MESSY_SKIPPED} malformed lines", r.stdout)
        archive = self.brain.parent / "shared-brain-archive.jsonl"
        archived = [json.loads(l) for l in
                    archive.read_text(encoding="utf-8").splitlines() if l.strip()]
        self.assertEqual([o["id"] for o in archived], ["old-1"])
        active = self.brain.read_text(encoding="utf-8").splitlines()
        for raw in ("42", "{oops", '{"id": "frag",', '"text": "fragment"}'):
            self.assertIn(raw, active,
                          "malformed lines must survive apply byte-identically")
        self.assertNotIn(MESSY_LINES[0], active)

    def test_apply_preserves_malformed_line_whitespace_byte_for_byte(self):
        self.brain.parent.mkdir(parents=True)
        old = json.dumps(
            {"id": "old-1", "kind": "interest", "text": "stale", "ts": "2020-01-01"}
        )
        malformed = b"  {oops  \n"
        self.brain.write_bytes(old.encode("utf-8") + b"\n" + malformed)

        r = run_py(SCRIPTS / "brain_archive.py", "apply",
                   "--interest-days", "60", env_extra=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.brain.read_bytes(), malformed)

    def test_apply_preserves_malformed_crlf_bytes_exactly(self):
        self.brain.parent.mkdir(parents=True)
        old = json.dumps(
            {"id": "old-1", "kind": "interest", "text": "stale", "ts": "2020-01-01"}
        ).encode("utf-8") + b"\n"
        malformed = b"  {oops  \r\n"
        self.brain.write_bytes(old + malformed)

        r = run_py(SCRIPTS / "brain_archive.py", "apply",
                   "--interest-days", "60", env_extra=self.env)

        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.brain.read_bytes(), malformed)

    def test_apply_preserves_private_mode_and_archive_is_not_more_permissive(self):
        self.brain.parent.mkdir(parents=True)
        rows = [
            {"id": "old", "kind": "interest", "text": "stale", "ts": "2020-01-01"},
            {"id": "keep", "kind": "lesson", "text": "keep"},
        ]
        self.brain.write_text("".join(json.dumps(o) + "\n" for o in rows),
                              encoding="utf-8")
        os.chmod(self.brain, 0o600)
        fixed_mtime = 1_700_000_000_000_000_000
        os.utime(self.brain, ns=(fixed_mtime, fixed_mtime))

        r = run_py(SCRIPTS / "brain_archive.py", "apply",
                   "--interest-days", "60", env_extra=self.env)

        self.assertEqual(r.returncode, 0, r.stderr)
        archive = self.brain.parent / "shared-brain-archive.jsonl"
        self.assertEqual(stat.S_IMODE(self.brain.stat().st_mode), 0o600)
        self.assertEqual(self.brain.stat().st_mtime_ns, fixed_mtime)
        archive_mode = stat.S_IMODE(archive.stat().st_mode)
        self.assertEqual(archive_mode & ~0o600, 0,
                         "archive permissions exceed the private source mode")

    def test_apply_reads_the_brain_only_after_acquiring_the_lock(self):
        self.brain.parent.mkdir(parents=True)
        rows = [
            {"id": "old", "kind": "interest", "text": "stale", "ts": "2020-01-01"},
            {"id": "keep", "kind": "lesson", "text": "keep", "ts": "2026-07-01"},
        ]
        self.brain.write_text("".join(json.dumps(o) + "\n" for o in rows),
                              encoding="utf-8")
        archive = self.brain.parent / "shared-brain-archive.jsonl"
        lock_dir = Path(self.tmp.name) / "locks"
        real_acquire = brain_archive.bb_lock.acquire

        def acquire_after_concurrent_append(target, agent="brain-archive", wait=15):
            with self.brain.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(
                    {"id": "late", "kind": "lesson", "text": "arrived while waiting"}
                ) + "\n")
            return real_acquire(target, agent=agent, wait=wait)

        completed = subprocess.CompletedProcess([], 0, stdout="index rebuilt\n", stderr="")
        with mock.patch.object(brain_archive, "BRAIN", str(self.brain)), \
             mock.patch.object(brain_archive, "ARCHIVE", str(archive)), \
             mock.patch.object(brain_archive.bb_lock, "LOCK_DIR", str(lock_dir)), \
             mock.patch.object(brain_archive.bb_lock, "acquire",
                               side_effect=acquire_after_concurrent_append), \
             mock.patch.object(brain_archive.subprocess, "run", return_value=completed):
            rc = brain_archive.cmd_apply(SimpleNamespace(ids=None, interest_days=60))

        self.assertEqual(rc, 0)
        active = [json.loads(line) for line in self.brain.read_text().splitlines()]
        self.assertEqual([row["id"] for row in active], ["keep", "late"])

    def test_apply_releases_lock_on_early_returns(self):
        self.brain.parent.mkdir(parents=True)
        self.brain.write_text(
            json.dumps({"id": "keep", "kind": "lesson", "text": "keep"}) + "\n",
            encoding="utf-8")
        archive = self.brain.parent / "shared-brain-archive.jsonl"

        for args, expected_rc in (
            (SimpleNamespace(ids=["missing"], interest_days=60), 2),
            (SimpleNamespace(ids=None, interest_days=60), 0),
        ):
            with self.subTest(args=args):
                release = mock.Mock()
                with mock.patch.object(brain_archive, "BRAIN", str(self.brain)), \
                     mock.patch.object(brain_archive, "ARCHIVE", str(archive)), \
                     mock.patch.object(brain_archive.bb_lock, "acquire",
                                       return_value="archive-token"), \
                     mock.patch.object(brain_archive.bb_lock, "renew", return_value=True), \
                     mock.patch.object(brain_archive.bb_lock, "release", release):
                    rc = brain_archive.cmd_apply(args)
                self.assertEqual(rc, expected_rc)
                release.assert_called_once_with(
                    str(self.brain), agent="brain-archive", token="archive-token")
                self.assertFalse(any(
                    thread.name == "brain-archive-renew" and thread.is_alive()
                    for thread in threading.enumerate()),
                    "early return left the renewal thread running")

    def test_apply_releases_token_when_renewal_thread_cannot_start(self):
        lease = mock.Mock()
        lease.start.side_effect = RuntimeError("thread unavailable")
        release = mock.Mock()

        with mock.patch.object(brain_archive.bb_lock, "acquire",
                               return_value="archive-token"), \
             mock.patch.object(brain_archive.bb_lock, "release", release), \
             mock.patch.object(brain_archive, "_LeaseRenewer", return_value=lease), \
             self.assertRaisesRegex(RuntimeError, "thread unavailable"):
            brain_archive.cmd_apply(SimpleNamespace(ids=None, interest_days=60))

        lease.stop.assert_called_once_with()
        release.assert_called_once_with(
            brain_archive.BRAIN, agent="brain-archive", token="archive-token")

    def test_apply_releases_token_when_renewer_construction_fails(self):
        release = mock.Mock()
        with mock.patch.object(brain_archive.bb_lock, "acquire",
                               return_value="archive-token"), \
             mock.patch.object(brain_archive.bb_lock, "release", release), \
             mock.patch.object(brain_archive, "_LeaseRenewer",
                               side_effect=RuntimeError("renewer unavailable")), \
             self.assertRaisesRegex(RuntimeError, "renewer unavailable"):
            brain_archive.cmd_apply(SimpleNamespace(ids=None, interest_days=60))

        release.assert_called_once_with(
            brain_archive.BRAIN, agent="brain-archive", token="archive-token")

    def test_apply_releases_token_when_renewer_shutdown_fails(self):
        lease = mock.Mock()
        lease.stop.side_effect = RuntimeError("join failed")
        release = mock.Mock()
        with mock.patch.object(brain_archive.bb_lock, "acquire",
                               return_value="archive-token"), \
             mock.patch.object(brain_archive.bb_lock, "release", release), \
             mock.patch.object(brain_archive, "_LeaseRenewer", return_value=lease), \
             mock.patch.object(brain_archive, "_rows", return_value=([], 0)), \
             self.assertRaisesRegex(RuntimeError, "join failed"):
            brain_archive.cmd_apply(SimpleNamespace(ids=None, interest_days=60))

        release.assert_called_once_with(
            brain_archive.BRAIN, agent="brain-archive", token="archive-token")

    def test_apply_renews_short_lease_during_slow_backup_so_late_append_survives(self):
        self.brain.parent.mkdir(parents=True)
        rows = [
            {"id": "old", "kind": "interest", "text": "stale", "ts": "2020-01-01"},
            {"id": "keep", "kind": "lesson", "text": "keep"},
        ]
        self.brain.write_text("".join(json.dumps(o) + "\n" for o in rows),
                              encoding="utf-8")
        archive = self.brain.parent / "shared-brain-archive.jsonl"
        lock_dir = Path(self.tmp.name) / "locks"
        copy_started = threading.Event()
        writer_done = threading.Event()
        renewed_twice = threading.Event()
        renewal_count = [0]
        renewal_count_lock = threading.Lock()
        writer_errors = []
        real_copy2 = brain_archive.shutil.copy2
        real_renew = brain_archive.bb_lock.renew

        def counted_renew(target, token):
            owned = real_renew(target, token)
            if owned:
                with renewal_count_lock:
                    renewal_count[0] += 1
                    if renewal_count[0] >= 2:
                        renewed_twice.set()
            return owned

        def slow_copy(src, dst):
            result = real_copy2(src, dst)
            copy_started.set()
            # Pre-fix, the writer reaps the 50 ms lease and appends while this
            # stale snapshot is paused. With renewal, two live refreshes prove
            # the background loop is running and the writer must wait.
            for _ in range(100):
                if writer_done.is_set() or renewed_twice.is_set():
                    break
                writer_done.wait(0.005)
            return result

        def writer():
            try:
                if not copy_started.wait(2):
                    raise AssertionError("archive never reached the delayed backup")
                token = brain_archive.bb_lock.acquire(
                    str(self.brain), agent="late-writer", wait=2)
                if not token:
                    raise AssertionError("late writer never acquired the brain lock")
                try:
                    with self.brain.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps({
                            "id": "late", "kind": "lesson", "text": "late append",
                        }) + "\n")
                        fh.flush()
                        os.fsync(fh.fileno())
                finally:
                    brain_archive.bb_lock.release(
                        str(self.brain), agent="late-writer", token=token)
                writer_done.set()
            except BaseException as exc:
                writer_errors.append(exc)
                writer_done.set()

        completed = subprocess.CompletedProcess([], 0, stdout="index rebuilt\n", stderr="")
        writer_thread = threading.Thread(target=writer, name="late-brain-writer")
        with mock.patch.object(brain_archive, "BRAIN", str(self.brain)), \
             mock.patch.object(brain_archive, "ARCHIVE", str(archive)), \
             mock.patch.object(brain_archive.bb_lock, "LOCK_DIR", str(lock_dir)), \
             mock.patch.object(brain_archive.bb_lock, "STALE_AFTER_SEC", 0.05), \
             mock.patch.object(brain_archive.bb_lock, "POLL_SEC", 0.005), \
             mock.patch.object(brain_archive.bb_lock, "renew",
                               side_effect=counted_renew), \
             mock.patch.object(brain_archive.shutil, "copy2", side_effect=slow_copy), \
             mock.patch.object(brain_archive.subprocess, "run", return_value=completed):
            stale_lock = Path(brain_archive.bb_lock.lock_path(str(self.brain)))
            writer_thread.start()
            rc = brain_archive.cmd_apply(SimpleNamespace(ids=None, interest_days=60))
            writer_thread.join(3)

        self.assertFalse(writer_thread.is_alive(), "late writer did not finish")
        self.assertEqual(writer_errors, [])
        self.assertEqual(rc, 0)
        self.assertTrue(renewed_twice.is_set(),
                        "archive did not continuously renew its short lease")
        active = [json.loads(line) for line in self.brain.read_text().splitlines()]
        self.assertEqual([row["id"] for row in active], ["keep", "late"])
        archived = [json.loads(line) for line in archive.read_text().splitlines()]
        self.assertEqual([row["id"] for row in archived], ["old"])
        self.assertFalse(stale_lock.exists(),
                         "archive or writer left a stale lock behind")

    def test_apply_aborts_before_mutation_when_fencing_token_is_lost(self):
        self.brain.parent.mkdir(parents=True)
        rows = [
            {"id": "old", "kind": "interest", "text": "stale", "ts": "2020-01-01"},
            {"id": "keep", "kind": "lesson", "text": "keep"},
        ]
        self.brain.write_text("".join(json.dumps(o) + "\n" for o in rows),
                              encoding="utf-8")
        archive = self.brain.parent / "shared-brain-archive.jsonl"
        lost = threading.Event()
        real_copy2 = brain_archive.shutil.copy2

        def copy_then_lose_lease(src, dst):
            result = real_copy2(src, dst)
            with self.brain.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "id": "late", "kind": "lesson", "text": "new owner append",
                }) + "\n")
            lost.set()
            return result

        def renew(_target, _token):
            return not lost.is_set()

        release = mock.Mock()
        run = mock.Mock(return_value=subprocess.CompletedProcess(
            [], 0, stdout="index rebuilt\n", stderr=""))
        with mock.patch.object(brain_archive, "BRAIN", str(self.brain)), \
             mock.patch.object(brain_archive, "ARCHIVE", str(archive)), \
             mock.patch.object(brain_archive.bb_lock, "acquire",
                               return_value="expired-token"), \
             mock.patch.object(brain_archive.bb_lock, "renew", side_effect=renew), \
             mock.patch.object(brain_archive.bb_lock, "release", release), \
             mock.patch.object(brain_archive.shutil, "copy2",
                               side_effect=copy_then_lose_lease), \
             mock.patch.object(brain_archive.subprocess, "run", run), \
             contextlib.redirect_stderr(io.StringIO()) as err:
            rc = brain_archive.cmd_apply(SimpleNamespace(ids=None, interest_days=60))

        self.assertEqual(rc, 1)
        self.assertIn("lease lost", err.getvalue())
        active = [json.loads(line) for line in self.brain.read_text().splitlines()]
        self.assertEqual([row["id"] for row in active], ["old", "keep", "late"])
        self.assertFalse(archive.exists(), "lost owner must not mutate archive")
        self.assertFalse(Path(str(self.brain) + ".tmp").exists())
        run.assert_not_called()
        release.assert_called_once_with(
            str(self.brain), agent="brain-archive", token="expired-token")

    def test_final_fence_blocks_token_swap_until_active_replace_finishes(self):
        self.brain.parent.mkdir(parents=True)
        rows = [
            {"id": "old", "kind": "interest", "text": "stale", "ts": "2020-01-01"},
            {"id": "keep", "kind": "lesson", "text": "keep"},
        ]
        self.brain.write_text("".join(json.dumps(o) + "\n" for o in rows),
                              encoding="utf-8")
        archive = self.brain.parent / "shared-brain-archive.jsonl"
        lock_dir = Path(self.tmp.name) / "locks"
        replace_entered = threading.Event()
        writer_attempted = threading.Event()
        writer_done = threading.Event()
        stop_writer = threading.Event()
        writer_errors = []
        active_replacements = []
        real_replace = brain_archive.os.replace
        real_acquire = brain_archive.bb_lock.acquire

        def observed_acquire(target, agent="unknown", wait=10):
            if agent == "final-fence-writer":
                writer_attempted.set()
            return real_acquire(target, agent=agent, wait=wait)

        def delayed_replace(src, dst):
            if os.path.abspath(dst) != os.path.abspath(self.brain):
                return real_replace(src, dst)
            self.assertEqual(Path(src).parent, self.brain.parent)
            self.assertTrue(str(src).endswith(".tmp"))
            active_replacements.append((str(src), str(dst)))
            # Enter the real final fence with a normal live lease. Only now
            # age the old owner's lock: preparation speed must not determine
            # whether publication reaches the boundary this test exercises.
            os.utime(stale_lock, (1, 1))
            replace_entered.set()
            self.assertTrue(writer_attempted.wait(2),
                            "late writer never attempted acquisition")
            # With the guard absent, the contender can reap the aged lease
            # and append before replacement; the final content assertion
            # below must catch that lost append, not merely trust this wait.
            self.assertFalse(
                writer_done.wait(0.20),
                "contender finished while active replacement still held the fence")
            return real_replace(src, dst)

        def writer():
            try:
                if not replace_entered.wait(2):
                    raise AssertionError("archive never reached active replacement")
                if stop_writer.is_set():
                    return
                token = brain_archive.bb_lock.acquire(
                    str(self.brain), agent="final-fence-writer", wait=2)
                if not token:
                    raise AssertionError("writer could not acquire after lease expiry")
                try:
                    with self.brain.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps({
                            "id": "late", "kind": "lesson", "text": "late append",
                        }) + "\n")
                        fh.flush()
                        os.fsync(fh.fileno())
                finally:
                    if not brain_archive.bb_lock.release(
                            str(self.brain), agent="final-fence-writer", token=token):
                        raise AssertionError("late writer could not release its token")
                writer_done.set()
            except BaseException as exc:
                writer_errors.append(exc)
                writer_done.set()

        lease = mock.Mock()
        lease.verify.return_value = True
        completed = subprocess.CompletedProcess([], 0, stdout="index rebuilt\n", stderr="")
        writer_thread = threading.Thread(target=writer, name="final-fence-writer")
        with mock.patch.object(brain_archive, "BRAIN", str(self.brain)), \
             mock.patch.object(brain_archive, "ARCHIVE", str(archive)), \
             mock.patch.object(brain_archive, "_LeaseRenewer", return_value=lease), \
             mock.patch.object(brain_archive.bb_lock, "LOCK_DIR", str(lock_dir)), \
             mock.patch.object(brain_archive.bb_lock, "POLL_SEC", 0.005), \
             mock.patch.object(brain_archive.bb_lock, "acquire", side_effect=observed_acquire), \
             mock.patch.object(brain_archive.os, "replace", side_effect=delayed_replace), \
             mock.patch.object(brain_archive.subprocess, "run", return_value=completed), \
             contextlib.redirect_stderr(io.StringIO()) as err:
            stale_lock = Path(brain_archive.bb_lock.lock_path(str(self.brain)))
            writer_thread.start()
            try:
                rc = brain_archive.cmd_apply(SimpleNamespace(ids=None, interest_days=60))
            finally:
                # Wake and stop a contender even if publication raises before
                # its signal, then finish it before mocks/tempfiles disappear.
                stop_writer.set()
                replace_entered.set()
                writer_thread.join(3)

        self.assertFalse(writer_thread.is_alive(), "final-fence writer did not finish")
        self.assertEqual(writer_errors, [])
        self.assertEqual(len(active_replacements), 1,
                         "fixture did not intercept the active-store replacement")
        self.assertTrue(writer_attempted.is_set(), "late writer did not try to acquire")
        self.assertTrue(writer_done.is_set(), "late writer did not complete")
        active = [json.loads(line) for line in self.brain.read_text().splitlines()]
        self.assertEqual([row["id"] for row in active], ["keep", "late"])
        archived = [json.loads(line) for line in archive.read_text().splitlines()]
        self.assertEqual([row["id"] for row in archived], ["old"])
        self.assertFalse(stale_lock.exists(), "final fencing left a stale lock")
        if rc != 0:
            self.assertIn("release", err.getvalue().lower())

    def test_apply_returns_nonzero_when_token_release_fails(self):
        lease = mock.Mock()
        release = mock.Mock(return_value=False)
        with mock.patch.object(brain_archive.bb_lock, "acquire",
                               return_value="archive-token"), \
             mock.patch.object(brain_archive.bb_lock, "release", release), \
             mock.patch.object(brain_archive, "_LeaseRenewer", return_value=lease), \
             mock.patch.object(brain_archive, "_rows", return_value=([], 0)), \
             contextlib.redirect_stderr(io.StringIO()) as err:
            rc = brain_archive.cmd_apply(SimpleNamespace(ids=None, interest_days=60))

        self.assertEqual(rc, 1)
        self.assertIn("release", err.getvalue().lower())
        release.assert_called_once_with(
            brain_archive.BRAIN, agent="brain-archive", token="archive-token")

    def test_apply_treats_all_non_finite_numbers_as_malformed_verbatim(self):
        self.brain.parent.mkdir(parents=True)
        finite_old = (
            '{"id":"finite-old","kind":"interest","text":"archive me",'
            '"ts":"2020-01-01"}\n')
        non_finite = [
            '{"id":"nan-old","kind":"interest","ts":"2020-01-01","v":NaN}\n',
            '{"id":"inf-old","kind":"interest","ts":"2020-01-01","v":Infinity}\n',
            '{"id":"ninf-old","kind":"interest","ts":"2020-01-01","v":-Infinity}\n',
            '{"id":"overflow-old","kind":"interest","ts":"2020-01-01","v":1e999}\n',
        ]
        self.brain.write_text(finite_old + "".join(non_finite), encoding="utf-8")

        r = run_py(SCRIPTS / "brain_archive.py", "apply",
                   "--interest-days", "60", env_extra=self.env)

        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("skipped 4 malformed lines", r.stdout)
        self.assertEqual(self.brain.read_text(encoding="utf-8"), "".join(non_finite))
        archive = self.brain.parent / "shared-brain-archive.jsonl"
        archived = [json.loads(line) for line in archive.read_text().splitlines()]
        self.assertEqual([row["id"] for row in archived], ["finite-old"])
        for forbidden in ("NaN", "Infinity", "1e999"):
            self.assertNotIn(forbidden, archive.read_text(encoding="utf-8"))

    def test_apply_reports_mneme_rebuild_failure_after_consistent_rewrite(self):
        self.brain.parent.mkdir(parents=True)
        rows = [
            {"id": "old", "kind": "interest", "text": "stale", "ts": "2020-01-01"},
            {"id": "keep", "kind": "lesson", "text": "keep"},
        ]
        self.brain.write_text("".join(json.dumps(o) + "\n" for o in rows),
                              encoding="utf-8")
        archive = self.brain.parent / "shared-brain-archive.jsonl"
        failed = subprocess.CompletedProcess(
            [], 9, stdout="", stderr="embedding backend unavailable\n")
        lock_dir = Path(self.tmp.name) / "locks"

        with mock.patch.object(brain_archive, "BRAIN", str(self.brain)), \
             mock.patch.object(brain_archive, "ARCHIVE", str(archive)), \
             mock.patch.object(brain_archive.bb_lock, "LOCK_DIR", str(lock_dir)), \
             mock.patch.object(brain_archive.subprocess, "run", return_value=failed), \
             contextlib.redirect_stderr(io.StringIO()) as err:
            rc = brain_archive.cmd_apply(SimpleNamespace(ids=None, interest_days=60))

        self.assertNotEqual(rc, 0)
        self.assertIn("Mneme rebuild failed", err.getvalue())
        self.assertIn("embedding backend unavailable", err.getvalue())
        active = [json.loads(line) for line in self.brain.read_text().splitlines()]
        archived = [json.loads(line) for line in archive.read_text().splitlines()]
        self.assertEqual([row["id"] for row in active], ["keep"])
        self.assertEqual([row["id"] for row in archived], ["old"])
        self.assertEqual(len(list(self.brain.parent.glob("*.pre-archive-*"))), 1)


class TestArchivePathDerivation(BrainEnvMixin):
    def test_suffixless_and_double_suffix_names(self):
        for fn in (brain_archive.archive_path, brain_scale.archive_path):
            self.assertEqual(fn("/b/shared-brain.jsonl"),
                             "/b/shared-brain-archive.jsonl")
            self.assertEqual(fn("/b/brain"), "/b/brain-archive.jsonl")
            self.assertEqual(fn("/b/x.jsonl.bak"), "/b/x.jsonl.bak-archive.jsonl")

    def test_jsonl_in_directory_name_is_not_rewritten(self):
        for fn in (brain_archive.archive_path, brain_scale.archive_path):
            self.assertEqual(fn("/data/.jsonl-store/brain.jsonl"),
                             "/data/.jsonl-store/brain-archive.jsonl")

    def test_apply_on_suffixless_brain_writes_a_separate_archive_file(self):
        # regression: str.replace('.jsonl', ...) was a no-op for a suffixless
        # path, so ARCHIVE == BRAIN and the apply rewrite destroyed the moved
        # rows entirely
        brain = Path(self.tmp.name) / "brain"
        env = dict(self.env)
        env["PROJECT_OS_SHARED_BRAIN"] = str(brain)
        rows = [
            {"id": "old-1", "kind": "interest", "text": "stale", "ts": "2020-01-01"},
            {"id": "keep-1", "kind": "lesson", "text": "fine", "ts": "2026-07-01"},
        ]
        brain.write_text("".join(json.dumps(o) + "\n" for o in rows),
                         encoding="utf-8")
        r = run_py(SCRIPTS / "brain_archive.py", "apply",
                   "--interest-days", "60", env_extra=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        archive = Path(self.tmp.name) / "brain-archive.jsonl"
        self.assertTrue(archive.exists())
        archived = [json.loads(l) for l in
                    archive.read_text(encoding="utf-8").splitlines() if l.strip()]
        active = [json.loads(l) for l in
                  brain.read_text(encoding="utf-8").splitlines() if l.strip()]
        self.assertEqual([o["id"] for o in archived], ["old-1"])
        self.assertEqual([o["id"] for o in active], ["keep-1"])


class TestRawKeyEntriesAreNotPlaceholders(BrainEnvMixin):
    """A legitimate entry using the key "_raw" must never be mistaken for the
    malformed-line placeholder (out-of-band sentinel, not a magic dict shape)."""

    def test_legit_raw_key_entries_survive_apply_as_valid_json(self):
        self.brain.parent.mkdir(parents=True, exist_ok=True)
        rows = [
            {"id": "old-1", "kind": "interest", "text": "stale", "ts": "2020-01-01"},
            {"_raw": "i am a LEGIT json object with one key"},
            {"_raw": 5},
            {"id": "keep-1", "kind": "lesson", "text": "fine", "ts": "2026-07-01"},
        ]
        self.brain.write_text("".join(json.dumps(o) + "\n" for o in rows),
                              encoding="utf-8")
        r = run_py(SCRIPTS / "brain_archive.py", "apply",
                   "--interest-days", "60", env_extra=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        active = [json.loads(l) for l in
                  self.brain.read_text(encoding="utf-8").splitlines() if l.strip()]
        self.assertIn({"_raw": "i am a LEGIT json object with one key"}, active)
        self.assertIn({"_raw": 5}, active)
        self.assertNotIn("old-1", [o.get("id") for o in active])
        archive = self.brain.parent / "shared-brain-archive.jsonl"
        archived = [json.loads(l) for l in
                    archive.read_text(encoding="utf-8").splitlines() if l.strip()]
        self.assertEqual([o["id"] for o in archived], ["old-1"])
        self.assertFalse(Path(str(self.brain) + ".tmp").exists())


class TestMnemeAdapterArchivePath(BrainEnvMixin):
    """mneme_adapter shares the archive-tier contract with the brain scripts."""

    def _adapter(self, brain_path):
        sys.path.insert(0, str(ROOT / "memory"))
        try:
            import mneme_adapter
        finally:
            sys.path.pop(0)
        mneme_adapter.SHARED_BRAIN = str(brain_path)
        mneme_adapter.ROOT = self.tmp.name
        return mneme_adapter

    def test_archive_path_matches_brain_script_rule(self):
        adapter = self._adapter(self.brain)
        self.assertEqual(adapter._archive_path("/d/shared-brain.jsonl"),
                         "/d/shared-brain-archive.jsonl")
        self.assertEqual(adapter._archive_path("/d/brain"),
                         "/d/brain-archive.jsonl")
        self.assertEqual(adapter._archive_path("/d/x.jsonl.bak"),
                         "/d/x.jsonl.bak-archive.jsonl")
        self.assertEqual(adapter._archive_path("/notes.jsonl/brain"),
                         "/notes.jsonl/brain-archive.jsonl")

    def test_suffixless_brain_is_not_indexed_as_its_own_archive(self):
        brain = Path(self.tmp.name) / "brain"
        brain.write_text(
            json.dumps({"id": "l1", "kind": "lesson", "text": "only once"}) + "\n",
            encoding="utf-8")
        adapter = self._adapter(brain)
        items = adapter._gather()
        lesson_ids = [(i, src) for i, src, _ in items if i == "l1"]
        self.assertEqual(lesson_ids, [("l1", "lesson")])

    def test_gather_tolerates_non_dict_lines(self):
        self.brain.parent.mkdir(parents=True, exist_ok=True)
        self.brain.write_text("\n".join(MESSY_LINES) + "\n", encoding="utf-8")
        adapter = self._adapter(self.brain)
        items = adapter._gather()
        ids = [i for i, _, _ in items]
        self.assertIn("old-1", ids)
        self.assertIn("keep-1", ids)


if __name__ == "__main__":
    unittest.main()
