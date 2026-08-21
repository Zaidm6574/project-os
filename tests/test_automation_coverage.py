"""Coverage added by the 2026-07-26 fix wave: harvest scan/apply end to end,
evolution CLI input validation, and the os_nightly automation-log marker append
(manual notes above the marker must survive heartbeat runs).

Everything runs against temp dirs; no personal data, no network, no Ollama
required (lexical embedder is forced).
"""
import contextlib
import builtins
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import harvest  # noqa: E402
import os_nightly  # noqa: E402


class TestHarvestScanApply(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        td = Path(self.tmp.name)
        self.runs = td / "runs"
        self.packets = td / "packets"
        self.brain = td / "central-brain" / "shared-brain.jsonl"
        self.index = td / "mneme_index.json"
        self.run_dir = self.runs / "demo-run"
        self.run_dir.mkdir(parents=True)
        self.packets.mkdir()
        env = {
            "PROJECT_OS_SHARED_BRAIN": str(self.brain),
            "MNEME_INDEX": str(self.index),
            "MNEME_EMBEDDER": "lexical",
            "OSVEC_EMBEDDER": "lexical",
            "BB_LOCK_DIR": str(td / "locks"),
        }
        patchers = [
            mock.patch.object(harvest, "RUNS", str(self.runs)),
            mock.patch.object(harvest, "PACKETS", str(self.packets)),
            mock.patch.object(harvest, "SHARED_BRAIN", str(self.brain)),
            mock.patch.dict(os.environ, env),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def _write_harvest_md(self, body):
        (self.run_dir / "19-memory-harvest.md").write_text(body)

    def test_scan_then_apply_happy_path(self):
        self._write_harvest_md(
            "## Lessons\n"
            "| Lesson | Approved For Reuse? |\n|---|---|\n"
            "| always pin the checker contract in plan validation | Yes |\n"
        )
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = harvest.cmd_scan("demo-run")
        self.assertEqual(rc, 0)
        staged = list(self.packets.glob("harvest-demo-run-*.jsonl"))
        self.assertEqual(len(staged), 1, out.getvalue())
        rows = [json.loads(l) for l in staged[0].read_text().splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertIn("pin the checker contract", rows[0]["text"])
        self.assertEqual(rows[0]["type"], "lesson")
        self.assertFalse((self.run_dir / ".harvested").exists(),
                         "scan with staged proposals must NOT mark the run harvested")
        with contextlib.redirect_stdout(io.StringIO()):
            rc = harvest.cmd_apply(str(staged[0]))
        self.assertEqual(rc, 0)
        brain_rows = [json.loads(l)
                      for l in self.brain.read_text().splitlines() if l.strip()]
        self.assertEqual(len(brain_rows), 1)
        self.assertIn("pin the checker contract", brain_rows[0]["text"])
        self.assertTrue((self.run_dir / ".harvested").exists())

    def test_scan_never_stages_rejected_rows_even_when_text_mentions_reject(self):
        self._write_harvest_md(
            "## Lessons\n"
            "| Lesson | Approved For Reuse? |\n|---|---|\n"
            "| never reject user input silently without an error message | Yes |\n"
            "| private incident details worth hiding | Rejected |\n"
            "| second private incident worth hiding | Private-only |\n"
        )
        with contextlib.redirect_stdout(io.StringIO()):
            rc = harvest.cmd_scan("demo-run")
        self.assertEqual(rc, 0)
        staged = list(self.packets.glob("harvest-demo-run-*.jsonl"))
        self.assertEqual(len(staged), 1)
        texts = [json.loads(l)["text"]
                 for l in staged[0].read_text().splitlines()]
        self.assertEqual(len(texts), 1, texts)
        self.assertIn("never reject user input silently", texts[0])

    def test_scan_reports_skipped_eval_log_fallback_honestly(self):
        self._write_harvest_md("## Unrecognized Notes\n- candidate row\n")
        (self.run_dir / "12-evaluation-log.md").write_text(
            "| step | verdict |\n|---|---|\n| build | failed badly, revise it |\n")
        with contextlib.redirect_stdout(io.StringIO()) as out, \
                contextlib.redirect_stderr(io.StringIO()) as err:
            rc = harvest.cmd_scan("demo-run")
        # A harvest file whose headings the scanner cannot read is a refusal,
        # not a clean "nothing to stage": marking it harvested would retire the
        # run and lose every lesson in it.
        self.assertEqual(rc, 2)
        msg = (out.getvalue() + err.getvalue()).lower()
        self.assertIn("fallback skipped", msg)
        self.assertNotIn("no eval-log hits", msg,
                         "must not claim the eval log was checked when it was not")
        self.assertFalse((self.run_dir / ".harvested").exists(),
                         "unreadable harvest file must not retire the run")

    def test_apply_validates_entire_batch_before_first_append(self):
        self.brain.parent.mkdir(parents=True)
        self.brain.write_bytes(b'{"id":"existing","text":"keep"}\n')
        self.index.write_bytes(b'{"index":"unchanged"}\n')
        marker = self.run_dir / ".harvested"
        marker.write_bytes(b"existing marker\n")
        proposals = Path(self.tmp.name) / "proposals.jsonl"
        valid = json.dumps({
            "id": "new", "project_id": "demo-run", "type": "lesson",
            "text": "a valid proposal that must not append",
        })
        before = (self.brain.read_bytes(), self.index.read_bytes(), marker.read_bytes())

        invalid_lines = (
            "{broken",
            "42",
            '["not", "an", "object"]',
            '{"project_id":"demo-run","value":NaN}',
            '{"project_id":"demo-run","value":Infinity}',
            '{"project_id":"demo-run","value":-Infinity}',
            '{"project_id":"demo-run","value":1e999}',
        )
        for invalid in invalid_lines:
            with self.subTest(invalid=invalid):
                proposals.write_text(valid + "\n\n" + invalid + "\n", encoding="utf-8")
                run = mock.Mock(return_value=subprocess.CompletedProcess(
                    [], 0, stdout="appended\n", stderr=""))
                caught = None
                with mock.patch.object(harvest.subprocess, "run", run), \
                     contextlib.redirect_stderr(io.StringIO()) as err:
                    try:
                        rc = harvest.cmd_apply(str(proposals))
                    except Exception as exc:  # regression guard: parser exceptions must not leak
                        caught, rc = exc, None
                self.assertIsNone(caught, f"validation leaked {type(caught).__name__}: {caught}")
                self.assertEqual(rc, 2)
                self.assertIn("REFUSED", err.getvalue())
                self.assertIn("line 3", err.getvalue())
                self.assertNotIn("Traceback", err.getvalue())
                run.assert_not_called()
                self.assertEqual(
                    (self.brain.read_bytes(), self.index.read_bytes(), marker.read_bytes()),
                    before)


class TestEvolutionCLI(unittest.TestCase):
    def setUp(self):
        # copy the scripts into a temp ROOT so runs/<name>/ lands in the
        # sandbox, never in the live repo tree
        self.tmp = tempfile.TemporaryDirectory()
        td = Path(self.tmp.name)
        scripts = td / "scripts"
        scripts.mkdir()
        for name in ("evolution.py", "bb_lock.py"):
            shutil.copy(str(SCRIPTS / name), str(scripts / name))
        self.evolution = scripts / "evolution.py"
        self.root = td
        self.env = dict(os.environ)
        self.env["BB_LOCK_DIR"] = str(td / "locks")
        self.addCleanup(self.tmp.cleanup)

    def _run(self, *args):
        return subprocess.run([sys.executable, str(self.evolution), *args],
                              capture_output=True, text=True, env=self.env)

    def test_record_then_best_round_trip(self):
        r = self._run("record", "--run", "r1", "--variant", "v1",
                      "--change", "initial harness", "--score", "0.7",
                      "--verdict", "revise")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self._run("record", "--run", "r1", "--variant", "v2",
                      "--parent", "v1", "--change", "tighter rubric",
                      "--score", "0.9", "--verdict", "approve")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads((self.root / "runs" / "r1" / "evolution.json").read_text())
        self.assertEqual(len(data["records"]), 2)
        self.assertEqual(data["records"][1]["score"], 0.9)
        self.assertTrue((self.root / "runs" / "r1" / "evolution.md").exists())
        b = self._run("best", "--run", "r1")
        self.assertEqual(b.returncode, 0, b.stderr)
        self.assertEqual(json.loads(b.stdout)["variant"], "v2")

    def test_bad_score_is_a_clean_usage_error(self):
        r = self._run("record", "--run", "r1", "--variant", "v1",
                      "--change", "x", "--verdict", "approve",
                      "--score", "not-a-number")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--score", r.stderr)
        self.assertNotIn("traceback", r.stderr.lower())
        self.assertFalse((self.root / "runs" / "r1").exists(),
                         "a rejected --score must fail before touching disk")

    def test_non_finite_scores_are_rejected_before_touching_disk(self):
        for bad in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(score=bad):
                r = self._run("record", "--run", "r1", "--variant", "v1",
                              "--change", "x", "--verdict", "approve",
                              "--score", bad)
                self.assertEqual(r.returncode, 2)
                self.assertIn("finite", r.stderr.lower())
                self.assertNotIn("traceback", r.stderr.lower())
        self.assertFalse((self.root / "runs" / "r1").exists(),
                         "a rejected non-finite score must fail before touching disk")

    def test_trailing_score_flag_is_a_clean_usage_error(self):
        r = self._run("record", "--run", "r1", "--variant", "v1",
                      "--change", "x", "--verdict", "approve", "--score")
        self.assertEqual(r.returncode, 2)
        self.assertIn("missing value for --score", r.stderr)
        self.assertNotIn("traceback", r.stderr.lower())


class TestHarvestBrainNormsTolerance(unittest.TestCase):
    def test_brain_norms_tolerates_non_dict_lines(self):
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td) / "shared-brain.jsonl"
            brain.write_text(
                '42\n["a", "b"]\n{oops\n'
                + json.dumps({"text": "an existing lesson long enough to norm"}) + "\n",
                encoding="utf-8")
            with mock.patch.object(harvest, "SHARED_BRAIN", str(brain)):
                norms = harvest.brain_norms()
        self.assertEqual(len(norms), 1)


class TestNightlyStuckPlans(unittest.TestCase):
    def test_non_dict_plan_json_is_unreadable_not_a_crash(self):
        with tempfile.TemporaryDirectory() as td:
            plans = Path(td)
            (plans / "bad.json").write_text('["not", "an", "object"]')
            (plans / "good.json").write_text(json.dumps(
                {"id": "p1", "status": "running",
                 "steps": [{"done": True}, {}, "not-a-step"]}))
            old = time.time() - 90 * 86400
            os.utime(plans / "bad.json", (old, old))
            os.utime(plans / "good.json", (old, old))
            with mock.patch.object(os_nightly, "PLANS", str(plans)):
                out = os_nightly.stuck_plans()
        self.assertIn("bad.json (unreadable)", out)
        self.assertTrue(any("p1 (1/3 steps)" in s for s in out), out)


class TestNightlyLogMarkerAppend(unittest.TestCase):
    ENTRY_START = "<!-- os-nightly:entry:start -->"
    ENTRY_END = "<!-- os-nightly:entry:end -->"

    def _patched(self, td):
        return (mock.patch.object(os_nightly, "LOG", str(Path(td) / "22-automation-log.md")),
                mock.patch.object(os_nightly.bb_lock, "LOCK_DIR", str(Path(td) / "locks")))

    def test_manual_text_above_marker_survives_two_runs(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "22-automation-log.md"
            log.write_text("# 22 — Automation Log\n\n"
                           "keep me: manual install notes\n\n"
                           + os_nightly.MARKER + "\n")
            p1, p2 = self._patched(td)
            with p1, p2:
                self.assertTrue(os_nightly.write_entry(["gauge: OK", "locks reaped: 0"]))
                self.assertTrue(os_nightly.write_entry(["gauge: OK second run"]))
            text = log.read_text()
            self.assertEqual(text.count(os_nightly.MARKER), 1)
            head, below = text.split(os_nightly.MARKER)
            self.assertIn("keep me: manual install notes", head)
            self.assertIn("gauge: OK second run", below)
            self.assertIn("locks reaped: 0", below)
            self.assertLess(below.index("second run"), below.index("locks reaped"),
                            "newest entry must come first")

    def test_manual_text_below_marker_survives_marker_aware_run(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "22-automation-log.md"
            log.write_text(
                "# 22 — Automation Log\n\n"
                + os_nightly.MARKER
                + "\n\nkeep me: misplaced manual note\n\n"
                + "## 2026-07-01T08:23\n- gauge: legacy entry\n\n",
                encoding="utf-8",
            )
            p1, p2 = self._patched(td)
            with p1, p2:
                self.assertTrue(os_nightly.write_entry(["gauge: fresh entry"]))
            text = log.read_text(encoding="utf-8")
            self.assertIn("keep me: misplaced manual note", text)
            self.assertIn("gauge: fresh entry", text)
            self.assertIn("gauge: legacy entry", text)

    def test_manual_text_between_marker_and_a_managed_entry_survives(self):
        """Prose sitting BEFORE a sentinel-owned entry must be relocated, not lost.

        Mutation coverage: the partition loop appends two distinct manual
        slices — the tail after the last entry, and the run before each entry
        start. Dropping the second append left every existing nightly test
        green, so this pins the before-an-entry position specifically.
        """
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "22-automation-log.md"
            p1, p2 = self._patched(td)
            with p1, p2:
                self.assertTrue(os_nightly.write_entry(["gauge: run one"]))
                seeded = log.read_text(encoding="utf-8")
                head, below = seeded.split(os_nightly.MARKER, 1)
                # A human pastes a note between the marker and the machine block.
                log.write_text(
                    head + os_nightly.MARKER
                    + "\n\nMANUAL-BEFORE-ENTRY: keep this note.\n" + below,
                    encoding="utf-8",
                )
                self.assertTrue(os_nightly.write_entry(["gauge: run two"]))
            text = log.read_text(encoding="utf-8")
            self.assertIn("MANUAL-BEFORE-ENTRY: keep this note.", text)
            # Relocated above the marker, where manual content is preserved.
            self.assertIn("MANUAL-BEFORE-ENTRY", text.split(os_nightly.MARKER)[0])
            self.assertIn("gauge: run one", text)
            self.assertIn("gauge: run two", text)
            self.assertEqual(text.count(os_nightly.MARKER), 1)

    def test_legacy_manual_section_is_not_evicted_at_entry_cap(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "22-automation-log.md"
            legacy_entries = "".join(
                "## 2026-07-%02dT08:23\n- gauge: legacy %s\n\n" % (day, day)
                for day in range(1, os_nightly.MAX_ENTRIES + 1)
            )
            log.write_text(
                os_nightly.HEADER
                + legacy_entries
                + "## Operator notes\n\nkeep me beyond the entry cap\n",
                encoding="utf-8",
            )
            p1, p2 = self._patched(td)
            with p1, p2:
                self.assertTrue(os_nightly.write_entry(["gauge: fresh entry"]))
            text = log.read_text(encoding="utf-8")
            self.assertIn("## Operator notes", text)
            self.assertIn("keep me beyond the entry cap", text)

    def test_missing_marker_is_added_after_legacy_content(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "22-automation-log.md"
            log.write_text(os_nightly.HEADER
                           + "## 2026-07-01T08:23\n- gauge: legacy entry\n\n")
            p1, p2 = self._patched(td)
            with p1, p2:
                self.assertTrue(os_nightly.write_entry(["gauge: fresh entry"]))
            text = log.read_text()
            self.assertEqual(text.count(os_nightly.MARKER), 1)
            head, below = text.split(os_nightly.MARKER)
            self.assertIn("gauge: fresh entry", below)
            self.assertIn("gauge: legacy entry", head)
            self.assertNotIn("gauge: legacy entry", below)

    def test_fresh_log_is_created_with_marker(self):
        with tempfile.TemporaryDirectory() as td:
            p1, p2 = self._patched(td)
            with p1, p2:
                self.assertTrue(os_nightly.write_entry(["gauge: first ever"]))
            text = (Path(td) / "22-automation-log.md").read_text()
            self.assertEqual(text.count(os_nightly.MARKER), 1)
            self.assertIn("gauge: first ever", text.split(os_nightly.MARKER)[1])

    def test_new_entries_are_wrapped_in_per_entry_sentinels(self):
        with tempfile.TemporaryDirectory() as td:
            p1, p2 = self._patched(td)
            with p1, p2:
                self.assertTrue(os_nightly.write_entry(["gauge: owned entry"]))
            text = (Path(td) / "22-automation-log.md").read_text(encoding="utf-8")
            self.assertEqual(text.count(self.ENTRY_START), 1)
            self.assertEqual(text.count(self.ENTRY_END), 1)
            self.assertLess(text.index(self.ENTRY_START), text.index("gauge: owned entry"))
            self.assertLess(text.index("gauge: owned entry"), text.index(self.ENTRY_END))

    def test_duplicate_nightly_markers_fail_closed_without_modifying(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "22-automation-log.md"
            original = (
                "# 22 — Automation Log\n\n"
                + os_nightly.MARKER + "\n\nmanual\n\n"
                + os_nightly.MARKER + "\n"
            ).encode("utf-8")
            log.write_bytes(original)
            p1, p2 = self._patched(td)
            with p1, p2, contextlib.redirect_stderr(io.StringIO()) as err:
                result = os_nightly.write_entry(["gauge: must not commit"])
            self.assertFalse(result)
            self.assertEqual(log.read_bytes(), original)
            self.assertIn("marker", err.getvalue().lower())

    def test_legacy_timestamp_gauge_prose_remains_raw_before_new_marker(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "22-automation-log.md"
            original = (
                "# 22 — Automation Log\n\n"
                + "".join(
                    "## 2026-06-%02dT08:23\n- gauge: operator note %s\n\n" % (day, day)
                    for day in range(1, os_nightly.MAX_ENTRIES + 3)
                )
            ).encode("utf-8")
            log.write_bytes(original)
            p1, p2 = self._patched(td)
            with p1, p2:
                self.assertTrue(os_nightly.write_entry(["gauge: fresh owned entry"]))
            before, marker, below = log.read_bytes().partition(
                os_nightly.MARKER.encode("utf-8"))
            self.assertEqual(marker, os_nightly.MARKER.encode("utf-8"))
            self.assertEqual(before, original)
            self.assertIn(b"operator note 1", before)
            self.assertIn(
                ("operator note %s" % (os_nightly.MAX_ENTRIES + 2)).encode(), before)
            self.assertEqual(below.count(self.ENTRY_START.encode()), 1)

    def test_entry_cap_applies_only_to_sentinel_owned_entries(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "22-automation-log.md"
            manual = "## 2026-05-01T08:23\n- gauge: manual lookalike\n\n"
            log.write_text(
                "# 22 — Automation Log\n\n" + os_nightly.MARKER + "\n\n" + manual,
                encoding="utf-8",
            )
            p1, p2 = self._patched(td)
            with p1, p2:
                for index in range(os_nightly.MAX_ENTRIES + 2):
                    self.assertTrue(os_nightly.write_entry([
                        "gauge: managed %02d" % index]))
            text = log.read_text(encoding="utf-8")
            self.assertIn("gauge: manual lookalike", text)
            self.assertEqual(text.count(self.ENTRY_START), os_nightly.MAX_ENTRIES)
            self.assertEqual(text.count(self.ENTRY_END), os_nightly.MAX_ENTRIES)
            self.assertIn("gauge: managed 31", text)
            self.assertNotIn("gauge: managed 00", text)

    def test_nightly_write_normalizes_generated_content_to_detected_crlf(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "22-automation-log.md"
            raw_prefix = (
                b"# 22 \xe2\x80\x94 Automation Log\r\n\r\n"
                b"manual prefix with spaces  \r\n\r\n"
            )
            log.write_bytes(
                raw_prefix + os_nightly.MARKER.encode("utf-8") + b"\r\n\r\n")
            p1, p2 = self._patched(td)
            with p1, p2:
                self.assertTrue(os_nightly.write_entry(["gauge: CRLF entry"]))
            data = log.read_bytes()
            self.assertTrue(data.startswith(
                raw_prefix + os_nightly.MARKER.encode("utf-8") + b"\r\n"))
            self.assertNotIn(b"\n", data.replace(b"\r\n", b""))

    def test_manual_slice_below_marker_is_preserved_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "22-automation-log.md"
            manual_slice = b"misplaced manual text  \r\n\tindented detail\r\n\r\n"
            log.write_bytes(
                b"# 22 \xe2\x80\x94 Automation Log\r\n\r\n"
                + os_nightly.MARKER.encode("utf-8") + b"\r\n\r\n"
                + manual_slice)
            p1, p2 = self._patched(td)
            with p1, p2:
                self.assertTrue(os_nightly.write_entry(["gauge: fresh entry"]))
            before, marker, _below = log.read_bytes().partition(
                os_nightly.MARKER.encode("utf-8"))
            self.assertEqual(marker, os_nightly.MARKER.encode("utf-8"))
            self.assertIn(manual_slice, before)

    def test_stale_writer_cannot_erase_a_replacement_owner_entry(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "22-automation-log.md"
            log.write_text("# 22 — Automation Log\n\n" + os_nightly.MARKER + "\n",
                           encoding="utf-8")
            lock_dir = Path(td) / "locks"
            reached_write = threading.Event()
            resume_stale = threading.Event()
            old_result = []
            real_open = builtins.open

            def gated_open(file, mode="r", *args, **kwargs):
                path = os.fspath(file) if isinstance(file, (str, os.PathLike)) else ""
                is_log_write = (
                    threading.current_thread().name == "stale-nightly-writer"
                    and "w" in mode
                    and (path == str(log)
                         or Path(path).name.startswith(".os-nightly-")))
                if is_log_write:
                    reached_write.set()
                    if not resume_stale.wait(5):
                        raise RuntimeError("test timed out waiting to resume stale writer")
                return real_open(file, mode, *args, **kwargs)

            p1 = mock.patch.object(os_nightly, "LOG", str(log))
            p2 = mock.patch.object(os_nightly.bb_lock, "LOCK_DIR", str(lock_dir))
            p3 = mock.patch.object(os_nightly.bb_lock, "STALE_AFTER_SEC", 0.08)
            p4 = mock.patch.object(os_nightly.bb_lock, "POLL_SEC", 0.005)
            with p1, p2, p3, p4, \
                    mock.patch("builtins.open", side_effect=gated_open), \
                    contextlib.redirect_stderr(io.StringIO()) as err:
                stale = threading.Thread(
                    target=lambda: old_result.append(
                        os_nightly.write_entry(["gauge: stale owner"])),
                    name="stale-nightly-writer")
                stale.start()
                self.assertTrue(reached_write.wait(2), "stale writer never reached mutation")
                time.sleep(0.12)
                replacement_result = os_nightly.write_entry(
                    ["gauge: replacement owner"])
                resume_stale.set()
                stale.join(3)

            self.assertFalse(stale.is_alive())
            self.assertTrue(replacement_result)
            self.assertEqual(old_result, [False])
            text = log.read_text(encoding="utf-8")
            self.assertIn("gauge: replacement owner", text)
            self.assertNotIn("gauge: stale owner", text)
            self.assertIn("lease lost", err.getvalue().lower())

    def test_release_failure_is_reported_and_returns_false(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "22-automation-log.md"
            real_release = os_nightly.bb_lock.release

            def release_but_report_failure(*args, **kwargs):
                real_release(*args, **kwargs)
                return False

            p1, p2 = self._patched(td)
            with p1, p2, mock.patch.object(
                    os_nightly.bb_lock, "release",
                    side_effect=release_but_report_failure), \
                    contextlib.redirect_stderr(io.StringIO()) as err:
                result = os_nightly.write_entry(["gauge: committed but release failed"])
            self.assertFalse(result)
            self.assertIn("release", err.getvalue().lower())
            self.assertIn("FAILED", err.getvalue())
            self.assertTrue(log.exists(), "the diagnostic must be honest about a post-commit failure")


class TestNightlyGaugeCrash(unittest.TestCase):
    def test_crashed_gauge_reports_exit_code_and_stderr(self):
        fake = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="",
            stderr="Traceback (most recent call last):\n  boom\nValueError: kaput\n")
        with mock.patch.object(os_nightly.subprocess, "run", return_value=fake):
            status, code = os_nightly.run_gauge()
        self.assertEqual(code, 1)
        self.assertIn("CRASHED", status)
        self.assertIn("exit 1", status)
        self.assertIn("ValueError: kaput", status)
        self.assertNotIn("no output", status)

    def test_nonzero_exit_with_output_is_a_severity_not_a_crash(self):
        fake = subprocess.CompletedProcess(
            args=[], returncode=3, stdout="OVERALL: n/a — brain missing\n", stderr="")
        with mock.patch.object(os_nightly.subprocess, "run", return_value=fake):
            status, code = os_nightly.run_gauge()
        self.assertEqual(code, 3)
        self.assertIn("OVERALL", status)
        self.assertNotIn("CRASHED", status)


if __name__ == "__main__":
    unittest.main()
