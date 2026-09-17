"""Synthetic regressions for cost completeness, marker selection, and local URLs."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MEMORY = ROOT / "addons" / "full-engine" / "memory"
START = "<!-- ACTUALS:START -->"
END = "<!-- ACTUALS:END -->"


def load(name):
    spec = importlib.util.spec_from_file_location("audit_" + name, MEMORY / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cli(name, *args):
    return subprocess.run([sys.executable, str(MEMORY / (name + ".py")), *map(str, args)],
                          capture_output=True, text=True, timeout=20)


def block(table):
    return START + "\n" + table + "\n" + END + "\n"


def table(amount):
    return ("| Model | Est $ | Measured $ | Variance |\n|---|---|---|---|\n"
            "| Main loop (Opus) | — | $%.4f | — |\n"
            "| **Total** | — | **$%.4f** | — |" % (amount, amount))


def event(amount=100):
    counts = {"input_tokens": amount, "cached_input_tokens": 40,
              "output_tokens": 10, "total_tokens": amount + 10}
    return {"type": "event_msg", "payload": {"info": {
        "last_token_usage": counts, "total_token_usage": dict(counts)}}}


class Fixtures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="public-cost-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def cost_report(self, body, slug="run-a"):
        return self.write(self.base / "runs" / slug / "09-cost-estimate.md", body)


class CostCompleteness(Fixtures):
    def test_real_writer_partial_then_complete_cli(self):
        main = self.write(self.base / "sessions" / "main.jsonl", json.dumps({
            "model": "claude-opus-fixture", "usage": {"input_tokens": 1_000_000}}) + "\n")
        report = self.cost_report("# Synthetic report\n")
        prices = self.write(self.base / "prices.json", json.dumps({
            "opus": {"in": 5, "out": 25}, "sonnet": {"in": 3, "out": 15}}))
        args = ("--transcript", main, "--prices", prices, "--write", "--target", report)
        # Exercise actual CLI writing and subsequent portfolio consumption.
        env = dict(os.environ, BB_LOCK_DIR=str(self.base / "locks"))
        writer = subprocess.run([sys.executable, str(MEMORY / "cost_actuals.py"),
                                 *map(str, args)], env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(writer.returncode, 0, writer.stderr)
        self.assertIn("Not fully measured", report.read_text())
        partial = cli("cost_rollup", "--runs", report.parents[1])
        self.assertEqual(partial.returncode, 2, partial.stdout + partial.stderr)
        self.assertIn("$5.0000 (not fully measured)", partial.stdout)
        self.assertIn("All runs (recorded partial sum)", partial.stdout)
        self.assertIn("INCOMPLETE", partial.stdout)
        self.assertIn("run-a", partial.stderr)
        self.write(main.parent / "main" / "subagents" / "worker.jsonl", json.dumps({
            "model": "claude-sonnet-fixture", "usage": {"input_tokens": 1_000_000}}) + "\n")
        writer = subprocess.run([sys.executable, str(MEMORY / "cost_actuals.py"),
                                 *map(str, args)], env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(writer.returncode, 0, writer.stderr)
        complete = cli("cost_rollup", "--runs", report.parents[1])
        self.assertEqual(complete.returncode, 0, complete.stderr)
        self.assertIn("**$8.0000**", complete.stdout)
        self.assertNotIn("INCOMPLETE", complete.stdout)
        self.assertEqual(complete.stderr, "")

    def test_programmatic_api_preserves_partial_status_and_dict_values(self):
        ca, cr = load("cost_actuals"), load("cost_rollup")
        body = block(ca._build_table({"opus": 5}, {}))
        self.cost_report(body)
        parsed = cr._parse_actuals(body)
        self.assertEqual(parsed, {"opus": 5.0})
        self.assertIsInstance(parsed, dict)
        self.assertIn("not fully measured", cr.render(cr.rollup(self.base / "runs")))

    def test_no_measured_rows_remain_unknown_not_zero(self):
        ca, cr = load("cost_actuals"), load("cost_rollup")
        self.cost_report(block(ca._build_table({}, {})))
        result = cli("cost_rollup", "--runs", self.base / "runs")
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("run-a", result.stdout)
        self.assertIn("not fully measured", result.stdout)
        self.assertNotIn("No runs with recorded actuals", result.stdout)

    def test_explicit_measured_zero_is_complete(self):
        ca = load("cost_actuals")
        self.cost_report(block(ca._build_table({"opus": 0}, {"sonnet": 0})))
        result = cli("cost_rollup", "--runs", self.base / "runs")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("**$0.0000**", result.stdout)
        self.assertNotIn("INCOMPLETE", result.stdout)

    def test_unreadable_status_survives_programmatic_default(self):
        self.cost_report(block(table(5)))
        (self.base / "runs" / "bad" / "09-cost-estimate.md").mkdir(parents=True)
        cr = load("cost_rollup")
        rendered = cr.render(cr.rollup(self.base / "runs"))
        self.assertIn("EXCLUDES", rendered)
        self.assertIn("bad", rendered)
        self.assertIn("recorded partial sum", rendered)

    def test_invalid_utf8_report_is_reported_not_crashed(self):
        self.cost_report("x").write_bytes(b"\xff")
        result = cli("cost_rollup", "--runs", self.base / "runs")
        self.assertEqual(result.returncode, 2)
        self.assertIn("UNREADABLE", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


class ActualsSelection(Fixtures):
    def test_writer_and_reader_select_same_unfenced_block(self):
        ca, cr = load("cost_actuals"), load("cost_rollup")
        for fence in ("```", "~~~~"):
            with self.subTest(fence=fence):
                original = fence + "markdown\n" + block(table(100)) + fence + "\n\n" + block(table(1))
                report = self.cost_report(original)
                # Avoid a real lock directory while using the actual writer.
                old_lock_dir = ca.bb_lock.LOCK_DIR
                ca.bb_lock.LOCK_DIR = str(self.base / "locks")
                try:
                    ca._update_markers(report, ca._build_table({"opus": 5}, {"sonnet": 3}))
                finally:
                    ca.bb_lock.LOCK_DIR = old_lock_dir
                written = report.read_text()
                self.assertIn(block(table(100)), written)
                self.assertEqual(cr._parse_actuals(written), {"opus": 5, "sonnet": 3})

    def test_fenced_only_example_is_not_recorded_actuals(self):
        cr = load("cost_rollup")
        self.assertEqual(cr._parse_actuals("```md\n" + block(table(100)) + "```\n"), {})
        self.cost_report("```md\n" + block(table(100)) + "```\n")
        result = cli("cost_rollup", "--runs", self.base / "runs")
        self.assertEqual(result.returncode, 0)
        self.assertIn("No runs with recorded actuals", result.stdout)

    def test_ambiguous_reversed_missing_and_open_fence_layouts_are_incomplete(self):
        layouts = [block(table(5)) * 2, END + "\n" + table(5) + "\n" + START,
                   START + "\n" + table(5), block(table(5)) + "```\nexample"]
        for body in layouts:
            with self.subTest(body=body):
                self.cost_report(body)
                result = cli("cost_rollup", "--runs", self.base / "runs")
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertIn("INCOMPLETE", result.stdout)
                self.assertNotIn("**$5.0000**", result.stdout)

    def test_empty_actuals_is_unknown_but_no_block_is_legitimate_absence(self):
        self.cost_report(block("To be measured"))
        missing = cli("cost_rollup", "--runs", self.base / "runs")
        self.assertEqual(missing.returncode, 2)
        self.assertIn("no recorded dollar amounts", missing.stdout)
        self.cost_report("# No recorded actuals yet\n")
        absent = cli("cost_rollup", "--runs", self.base / "runs")
        self.assertEqual(absent.returncode, 0)
        self.assertIn("No runs with recorded actuals", absent.stdout)

    def test_installed_sibling_layout_imports_and_runs(self):
        installed = self.base / "installed"
        (installed / "memory").mkdir(parents=True)
        (installed / "scripts").mkdir()
        for name in ("cost_rollup.py", "cost_actuals.py"):
            shutil.copyfile(MEMORY / name, installed / "memory" / name)
        shutil.copyfile(ROOT / "scripts" / "bb_lock.py", installed / "scripts" / "bb_lock.py")
        report = self.cost_report(block(table(5)))
        result = subprocess.run([sys.executable, str(installed / "memory" / "cost_rollup.py"),
                                 "--runs", str(report.parents[1])], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("**$5.0000**", result.stdout)

    def test_copying_rollup_alone_explains_required_dependencies(self):
        standalone = self.base / "cost_rollup.py"
        shutil.copyfile(MEMORY / "cost_rollup.py", standalone)
        result = subprocess.run([sys.executable, str(standalone), "--selftest"],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 2)
        self.assertIn("requires sibling cost_actuals.py", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


class CodexInputs(Fixtures):
    def sessions(self):
        folder = self.base / "sessions"
        folder.mkdir(exist_ok=True)
        self.write(folder / "good.jsonl", json.dumps(event()) + "\n")
        return folder

    def test_directory_named_jsonl_is_reported_as_unreadable(self):
        folder = self.sessions()
        (folder / "unreadable.jsonl").mkdir()
        ca = load("cost_actuals")
        stats = ca._parse_codex_session_usage(ca._collect_codex_session_files(folder))
        self.assertEqual(stats["turn_totals"]["input_tokens"], 100)
        result = cli("cost_actuals", "--codex-sessions", "--sessions-dir", folder)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("| Unreadable files | 1 |", result.stdout)
        self.assertIn("INCOMPLETE", result.stdout)
        self.assertIn("unreadable.jsonl", result.stderr)
        self.assertIn("UNREADABLE", result.stderr)
        self.assertNotIn("| Final-session cross-check | matches |", result.stdout)

    def test_two_readable_sessions_count_both_without_incomplete_alarm(self):
        folder = self.sessions()
        self.write(folder / "second.jsonl", json.dumps(event()) + "\n")
        result = cli("cost_actuals", "--codex-sessions", "--sessions-dir", folder)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("| input_tokens | 200 | 200 | 200 |", result.stdout)
        self.assertIn("| Sessions with usage | 2 |", result.stdout)
        self.assertNotIn("INCOMPLETE", result.stdout)
        self.assertEqual(result.stderr, "")
        self.assertIn("no project, run, or time filter", result.stdout)
        self.assertIn("Events are not deduplicated", result.stdout)
        self.assertIn("do not prove complete usage or dollar cost", result.stdout)

    def test_invalid_encoding_and_malformed_json_cannot_pass_silently(self):
        folder = self.sessions()
        for content, label in ((b"\xff", "UNREADABLE"), (b'{"payload":\n', "MALFORMED")):
            with self.subTest(label=label):
                (folder / "bad.jsonl").write_bytes(content)
                result = cli("cost_actuals", "--codex-sessions", "--sessions-dir", folder)
                self.assertEqual(result.returncode, 2)
                self.assertIn(label, result.stderr)
                self.assertIn("INCOMPLETE", result.stdout)
                self.assertNotIn("Traceback", result.stderr)

    def test_nonobject_events_and_invalid_counters_are_diagnosed_atomically(self):
        folder = self.sessions()
        bad_records = [[], 42]
        for bad in (-1, True, 1.5, float("nan"), float("inf"), "100"):
            record = event()
            record["payload"]["info"]["last_token_usage"]["output_tokens"] = bad
            bad_records.append(record)
        self.write(folder / "bad.jsonl", "\n".join(map(json.dumps, bad_records)) + "\n")
        ca = load("cost_actuals")
        stats = ca._parse_codex_session_usage(ca._collect_codex_session_files(folder))
        self.assertEqual(stats["turn_totals"]["input_tokens"], 100)
        self.assertEqual(len(stats["invalid_records"]), 8)
        result = cli("cost_actuals", "--codex-sessions", "--sessions-dir", folder)
        self.assertEqual(result.returncode, 2)
        self.assertIn("INVALID SESSION RECORD", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_empty_selection_is_not_measured_zero(self):
        folder = self.base / "empty"
        folder.mkdir()
        result = cli("cost_actuals", "--codex-sessions", "--sessions-dir", folder)
        self.assertEqual(result.returncode, 2)
        self.assertIn("Not measured", result.stdout)
        self.assertIn("NOT MEASURED", result.stderr)

    def test_explicit_zero_counter_is_measured(self):
        folder = self.base / "zero"
        self.write(folder / "zero.jsonl", json.dumps({"payload": {"info": {
            "last_token_usage": {"input_tokens": 0},
            "total_token_usage": {"input_tokens": 0}}}}) + "\n")
        result = cli("cost_actuals", "--codex-sessions", "--sessions-dir", folder)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("| Usage events | 1 |", result.stdout)
        self.assertNotIn("Not measured", result.stdout)

    def test_sessions_directory_must_exist_and_be_directory(self):
        for path in (self.base / "missing", self.write(self.base / "file.jsonl", "")):
            with self.subTest(path=path.name):
                result = cli("cost_actuals", "--codex-sessions", "--sessions-dir", path)
                self.assertEqual(result.returncode, 2)
                self.assertIn("not a sessions directory" if path.exists() else "not found", result.stderr)
                self.assertNotIn("Traceback", result.stderr)


class CostPublicationFence(Fixtures):
    def setUp(self):
        super().setUp()
        self.ca = load("cost_actuals")
        spec = importlib.util.spec_from_file_location("audit_cost_fence_lock", ROOT / "scripts" / "bb_lock.py")
        self.lock = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.lock)
        self.lock.LOCK_DIR = str(self.base / "locks")
        self.ca.bb_lock = self.lock
        self.target = self.write(self.base / "report.md", block("original table"))

    def start_writer(self, result, errors):
        def write():
            try:
                result.append(self.ca._update_markers(self.target, "old writer table"))
            except BaseException as exc:
                errors.append(exc)
        thread = threading.Thread(target=write, name="paused-cost-writer")
        thread.start()
        return thread

    def test_takeover_after_successful_renew_cannot_overwrite_replacement(self):
        renewed, resume = threading.Event(), threading.Event()
        result, errors = [], []
        real_renew = self.lock.renew

        def paused_renew(*args, **kwargs):
            accepted = real_renew(*args, **kwargs)
            if threading.current_thread().name == "paused-cost-writer" and accepted:
                renewed.set()
                if not resume.wait(5):
                    raise RuntimeError("timed out waiting to resume after renewal")
            return accepted

        with mock.patch.object(self.lock, "renew", side_effect=paused_renew), \
                contextlib.redirect_stderr(io.StringIO()) as stderr:
            old = self.start_writer(result, errors)
            try:
                self.assertTrue(renewed.wait(2), "old writer never renewed")
                # Deterministic expiry; no timing-dependent sleep is needed.
                os.utime(self.lock.lock_path(str(self.target)), (1, 1))
                self.assertTrue(self.ca._update_markers(self.target, "replacement table"))
            finally:
                resume.set()
                old.join(3)

        self.assertFalse(old.is_alive())
        self.assertIn("replacement table", self.target.read_text())
        self.assertNotIn("old writer table", self.target.read_text())
        self.assertEqual(errors, [])
        self.assertEqual(result, [False])
        self.assertIn("lease lost", stderr.getvalue())
        self.assertEqual(list(self.base.glob("report.md.*.tmp")), [])

    def test_guard_remains_held_through_atomic_replacement(self):
        reached_replace, resume = threading.Event(), threading.Event()
        result, errors = [], []
        real_replace = self.ca.os.replace

        def paused_replace(src, dest):
            if str(dest) == str(self.target) and threading.current_thread().name == "paused-cost-writer":
                reached_replace.set()
                if not resume.wait(5):
                    raise RuntimeError("timed out waiting to publish cost report")
            return real_replace(src, dest)

        with mock.patch.object(self.ca.os, "replace", side_effect=paused_replace):
            old = self.start_writer(result, errors)
            try:
                self.assertTrue(reached_replace.wait(2), "old writer never reached publication")
                os.utime(self.lock.lock_path(str(self.target)), (1, 1))
                takeover = self.lock.acquire(str(self.target), agent="replacement", wait=0.05)
                if takeover:
                    self.lock.release(str(self.target), token=takeover)
                self.assertFalse(takeover, "replacement acquired while old publication was in flight")
            finally:
                resume.set()
                old.join(3)

        self.assertFalse(old.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(result, [True])
        self.assertIn("old writer table", self.target.read_text())
        self.assertTrue(self.ca._update_markers(self.target, "next writer table"))
        self.assertIn("next writer table", self.target.read_text())

    def test_current_lease_publishes_and_releases_normally(self):
        self.assertTrue(self.ca._update_markers(self.target, "current table"))
        self.assertIn("current table", self.target.read_text())
        self.assertFalse(os.path.exists(self.lock.lock_path(str(self.target))))
        self.assertTrue(self.ca._update_markers(self.target, "next table"))
        self.assertIn("next table", self.target.read_text())


class LocalUrlResolution(Fixtures):
    def setUp(self):
        super().setUp()
        self.site = self.base / "site"
        self.page = self.write(self.site / "pages" / "index.html", "")
        self.write(self.site / "assets" / "fixture.png", "synthetic asset")

    def check(self, reference):
        self.page.write_text('<img src="%s">' % reference)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            rc = load("browser_qa").check(str(self.page), root=str(self.site))
        return rc, output.getvalue()

    def test_root_relative_url_uses_declared_site_root_in_real_cli(self):
        self.page.write_text('<img src="/assets/fixture.png">')
        result = cli("browser_qa", self.page, "--root", self.site)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("QA: PASS", result.stdout)

    def test_document_relative_and_parent_within_site_still_pass(self):
        self.write(self.page.parent / "local.txt", "fixture")
        for ref in ("local.txt", "../assets/fixture.png", "?view=1"):
            with self.subTest(ref=ref):
                self.assertEqual(self.check(ref)[0], 0)

    def test_root_relative_query_and_fragment_do_not_change_asset_path(self):
        self.assertEqual(self.check("/assets/fixture.png?size=1#view")[0], 0)

    def test_encoded_spaces_hash_and_question_are_path_characters(self):
        self.write(self.site / "assets" / "a b#c?d.png", "fixture")
        self.assertEqual(self.check("/assets/a%20b%23c%3Fd.png?size=1#fragment")[0], 0)

    def test_external_schemes_and_protocol_relative_urls_are_not_local(self):
        for ref in ("//cdn.example.invalid/image.png", "HTTPS://example.invalid/missing",
                    "tel:+10000000000", "mailto:example@example.invalid", "data:image/png,fixture",
                    "blob:https://example.invalid/image", "#heading"):
            with self.subTest(ref=ref):
                self.assertEqual(self.check(ref)[0], 0)

    def test_missing_local_file_still_fails(self):
        rc, output = self.check("/assets/missing.png")
        self.assertEqual(rc, 1)
        self.assertIn("broken", output)

    def test_escape_and_similar_prefix_are_rejected_even_if_file_exists(self):
        self.write(self.base / "site-other" / "outside.txt", "fixture")
        for ref in ("../../site-other/outside.txt", "/../site-other/outside.txt",
                    "/%2e%2e/site-other/outside.txt"):
            with self.subTest(ref=ref):
                rc, output = self.check(ref)
                self.assertEqual(rc, 1)
                self.assertIn("leaves the declared site root", output)

    def test_symlink_outside_site_is_not_proof_of_served_asset(self):
        outside = self.write(self.base / "outside.txt", "fixture")
        (self.site / "assets" / "linked.txt").symlink_to(outside)
        rc, output = self.check("/assets/linked.txt")
        self.assertEqual(rc, 1)
        self.assertIn("leaves the declared site root", output)

    def test_existing_os_path_is_not_a_root_relative_web_asset(self):
        outside = self.write(self.base / "outside.txt", "fixture")
        self.assertEqual(self.check(str(outside))[0], 1)

    def test_bad_encoded_path_and_invalid_url_fail_without_crash(self):
        for ref in ("/assets/%FF.png", "/assets/%00.png", "//[invalid"):
            with self.subTest(ref=ref):
                self.assertEqual(self.check(ref)[0], 1)

    def test_target_must_be_in_declared_root(self):
        result = cli("browser_qa", self.page, "--root", self.base / "other-root")
        self.assertEqual(result.returncode, 1)
        self.assertIn("declared site root", result.stdout)


if __name__ == "__main__":
    unittest.main()
