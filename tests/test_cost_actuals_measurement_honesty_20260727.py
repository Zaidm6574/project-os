#!/usr/bin/env python3
"""Regression guards for two cost_actuals measurement-honesty defects (2026-07-27).

Defect 1 -- subagent spend measured as a confident $0.0000.
    `_collect_jsonl_files` probed `transcript.parent / "subagents"`. In the real
    Claude Code transcript layout the main session file and its subagent tree are
    SIBLINGS of each other, not parent/child:

        <slug>/<uuid>.jsonl                                <- main transcript
        <slug>/<uuid>/subagents/agent-*.jsonl              <- subagents
        <slug>/<uuid>/subagents/workflows/wf_*/agent-*.jsonl

    so the probed path resolved to `<slug>/subagents`, which never exists. Every
    subagent transcript went unread and the table printed
    `| **Subtotal - subagents** | - | $0.0000 | - |` under a column headed
    "Measured $", exit 0, no warning -- and `/deliver` bakes that into the
    blackboard via `--write`. A lookup that finds nothing is indistinguishable
    from a real zero, so none of the three honesty guards (unreadable / missing /
    unpriced_tiers) can fire. The plain `glob("*.jsonl")` also missed the
    `subagents/workflows/wf_*/` tier entirely.

Defect 2 -- a crash-truncated JSONL line was dropped in silence.
    `_parse_usage` swallowed `json.JSONDecodeError` with a bare `continue`, so a
    transcript truncated mid-write produced a smaller total that was still
    presented as *measured* and still accepted by `--write`. The module's own
    sibling gate proves the inconsistency: an unreadable FILE refuses --write
    with exit 2, while an unreadable LINE exited 0 and wrote the understated
    number. Doctrine in this module is explicit that cost actuals must be
    measured, not partial, and that a silent zero is worse than a loud refusal.

Every test here was mutation-verified: reverting the matching hunk in
addons/full-engine/memory/cost_actuals.py makes it FAIL.
"""

import contextlib
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COST_ACTUALS = os.path.join(
    ROOT, "addons", "full-engine", "memory", "cost_actuals.py"
)

MARKED_TARGET = (
    "# Cost estimate\n\n"
    "<!-- ACTUALS:START -->\n"
    "SENTINEL-NOT-YET-MEASURED\n"
    "<!-- ACTUALS:END -->\n"
)

# 1M input @ $5/1M + 200k output @ $25/1M = $10.00
ORCHESTRATOR_TURN = {
    "model": "claude-opus-4-8",
    "usage": {
        "input_tokens": 1_000_000,
        "output_tokens": 200_000,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    },
}
# 4M input @ $3/1M + 1M output @ $15/1M = $27.00
SUBAGENT_TURN = {
    "model": "claude-sonnet-4-5",
    "usage": {
        "input_tokens": 4_000_000,
        "output_tokens": 1_000_000,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    },
}


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), "w") as fh:
        for record in records:
            fh.write(json.dumps(record) + "\n")
    return path


@contextlib.contextmanager
def _sandbox_home(home):
    """Point HOME at a sandbox so nothing reaches the real ~/.claude tree."""
    old = os.environ.get("HOME")
    os.environ["HOME"] = str(home)
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            yield out, err
    finally:
        if old is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old


class _CostActualsCase(unittest.TestCase):
    def setUp(self):
        self.cost = _load("cost_actuals_measurement_honesty", COST_ACTUALS)


# ---------------------------------------------------------------------------
# Defect 1: the real (nested) subagent layout
# ---------------------------------------------------------------------------

class RealSubagentLayoutIsMeasured(_CostActualsCase):
    """`<slug>/<uuid>.jsonl` + `<slug>/<uuid>/subagents/*.jsonl`."""

    def _real_layout(self, home):
        """Build the layout Claude Code actually writes. Returns the main file."""
        slug = pathlib.Path(home) / ".claude" / "projects" / "-demo"
        main = slug / "aaaa-1111.jsonl"
        _write_jsonl(main, [ORCHESTRATOR_TURN])
        _write_jsonl(slug / "aaaa-1111" / "subagents" / "agent-a1.jsonl",
                     [SUBAGENT_TURN])
        return main

    def test_collect_finds_subagents_beside_the_uuid_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            main = self._real_layout(tmp)
            expected = main.parent / "aaaa-1111" / "subagents" / "agent-a1.jsonl"

            files = self.cost._collect_jsonl_files(main)

            self.assertIn(
                expected, files,
                "the real <uuid>/subagents/ transcript was never opened, so its "
                "spend is reported as a confident $0.0000 'Measured $'",
            )

    def test_collect_recurses_into_the_workflows_tier(self):
        """43 of 102 subagents/ dirs on a real machine nest one level deeper."""
        with tempfile.TemporaryDirectory() as tmp:
            main = self._real_layout(tmp)
            nested = (
                main.parent / "aaaa-1111" / "subagents" / "workflows"
                / "wf_f76f7ddb-036" / "agent-ac4e0f13.jsonl"
            )
            _write_jsonl(nested, [SUBAGENT_TURN])

            files = self.cost._collect_jsonl_files(main)

            self.assertIn(
                nested, files,
                "subagents/workflows/wf_*/agent-*.jsonl went unread: a plain "
                "glob('*.jsonl') does not descend into the workflows tier",
            )

    def test_flat_sibling_layout_is_still_collected(self):
        """The historical flat layout must keep working (no regression)."""
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            main = _write_jsonl(base / "session.jsonl", [ORCHESTRATOR_TURN])
            flat = _write_jsonl(
                base / "subagents" / "evaluator.jsonl", [SUBAGENT_TURN]
            )

            files = self.cost._collect_jsonl_files(main)

            self.assertIn(flat, files)

    def test_no_subagents_anywhere_collects_only_the_main_file(self):
        """Near-miss control: the probe must not invent files that do not exist."""
        with tempfile.TemporaryDirectory() as tmp:
            main = _write_jsonl(
                pathlib.Path(tmp) / "session.jsonl", [ORCHESTRATOR_TURN]
            )

            self.assertEqual(self.cost._collect_jsonl_files(main), [main])

    def test_the_main_transcript_is_never_double_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            main = self._real_layout(tmp)

            files = self.cost._collect_jsonl_files(main)

            self.assertEqual(
                files.count(main), 1, "main transcript collected twice: %r" % files
            )

    def test_run_reports_the_subagent_subtotal_not_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            main = self._real_layout(tmp)
            with _sandbox_home(tmp) as (out, _err):
                rc = self.cost.run(main, dict(self.cost.DEFAULT_PRICES), False, None)
            table = out.getvalue()

            self.assertEqual(rc, 0, table)
            self.assertIn(
                "| **Subtotal — subagents** | — | $27.0000 | — |", table,
                "subagent spend still reported as $0.0000:\n%s" % table,
            )
            self.assertIn("| **Total** | — | **$37.0000** | — |", table, table)

    def test_write_records_the_subagent_spend(self):
        with tempfile.TemporaryDirectory() as tmp:
            main = self._real_layout(tmp)
            target = pathlib.Path(tmp) / "09-cost-estimate.md"
            target.write_text(MARKED_TARGET)

            with _sandbox_home(tmp):
                rc = self.cost.run(
                    main, dict(self.cost.DEFAULT_PRICES), True, target
                )

            self.assertEqual(rc, 0)
            written = target.read_text()
            self.assertIn("| **Total** | — | **$37.0000** | — |", written, written)

    def test_cli_autodetect_measures_the_real_layout_end_to_end(self):
        """The exact path /deliver takes: no --transcript, HOME-relative."""
        with tempfile.TemporaryDirectory() as tmp:
            self._real_layout(tmp)
            env = dict(os.environ)
            env["HOME"] = tmp

            result = subprocess.run(
                [sys.executable, COST_ACTUALS],
                capture_output=True, text=True, env=env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(
                "| **Total** | — | **$37.0000** | — |", result.stdout,
                "auto-detected run understated the total:\n%s" % result.stdout,
            )


# ---------------------------------------------------------------------------
# Defect 2: a malformed JSONL line must be loud and must block --write
# ---------------------------------------------------------------------------

CORRUPT_LINES = (
    json.dumps(ORCHESTRATOR_TURN) + "\n"
    + json.dumps(ORCHESTRATOR_TURN) + "\n"
    + '{"model":"claude-opus-4-8","usage":{"input_tok' + "\n"
    + json.dumps(ORCHESTRATOR_TURN) + "\n"
)


class MalformedTranscriptLineIsLoudAndBlocksWrite(_CostActualsCase):
    def _corrupt(self, tmp):
        path = pathlib.Path(tmp) / "session.jsonl"
        path.write_text(CORRUPT_LINES)
        return path

    def test_parse_usage_reports_the_malformed_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._corrupt(tmp)

            _main, _sub, unreadable, malformed = self.cost._parse_usage(
                [path], path
            )

            self.assertEqual(unreadable, [])
            self.assertEqual(
                malformed, [(path, 3)],
                "a truncated transcript line was dropped without a trace, so "
                "the understated total is still presented as measured",
            )

    def test_clean_transcript_reports_no_malformed_lines(self):
        """Near-miss control: blank lines and valid lines must not be flagged."""
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "session.jsonl"
            path.write_text(
                json.dumps(ORCHESTRATOR_TURN) + "\n"
                + "\n"
                + "   \n"
                + json.dumps({"message": "hello"}) + "\n"
                + json.dumps(ORCHESTRATOR_TURN) + "\n"
            )

            _main, _sub, unreadable, malformed = self.cost._parse_usage(
                [path], path
            )

            self.assertEqual(unreadable, [])
            self.assertEqual(malformed, [], "false positive on a clean transcript")

    def test_report_run_names_the_file_and_line_on_stderr(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._corrupt(tmp)
            with _sandbox_home(tmp) as (_out, err):
                rc = self.cost.run(path, dict(self.cost.DEFAULT_PRICES), False, None)

            self.assertEqual(rc, 0, "a read-only report must still succeed")
            self.assertIn("MALFORMED", err.getvalue().upper(), err.getvalue())
            self.assertIn("session.jsonl", err.getvalue())
            self.assertIn("3", err.getvalue())

    def test_write_refuses_and_leaves_the_target_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._corrupt(tmp)
            target = pathlib.Path(tmp) / "09-cost-estimate.md"
            target.write_text(MARKED_TARGET)

            with _sandbox_home(tmp):
                rc = self.cost.run(
                    path, dict(self.cost.DEFAULT_PRICES), True, target
                )

            self.assertEqual(
                rc, 2,
                "run(--write) recorded a knowingly-understated figure as a "
                "measured actual; the same module refuses (exit 2) when a whole "
                "FILE is unreadable",
            )
            self.assertIn("SENTINEL-NOT-YET-MEASURED", target.read_text())

    def test_a_clean_transcript_still_writes(self):
        """The gate must not fire on good input."""
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_jsonl(
                pathlib.Path(tmp) / "session.jsonl", [ORCHESTRATOR_TURN]
            )
            target = pathlib.Path(tmp) / "09-cost-estimate.md"
            target.write_text(MARKED_TARGET)

            with _sandbox_home(tmp):
                rc = self.cost.run(
                    path, dict(self.cost.DEFAULT_PRICES), True, target
                )

            self.assertEqual(rc, 0)
            self.assertNotIn("SENTINEL-NOT-YET-MEASURED", target.read_text())

    def test_cli_write_exits_2_on_a_corrupt_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._corrupt(tmp)
            target = pathlib.Path(tmp) / "09-cost-estimate.md"
            target.write_text(MARKED_TARGET)
            env = dict(os.environ)
            env["HOME"] = tmp

            result = subprocess.run(
                [sys.executable, COST_ACTUALS, "--transcript", str(path),
                 "--target", str(target), "--write"],
                capture_output=True, text=True, env=env,
            )

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("SENTINEL-NOT-YET-MEASURED", target.read_text())

    def test_a_malformed_line_in_a_subagent_file_is_also_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            slug = pathlib.Path(tmp) / ".claude" / "projects" / "-demo"
            main = _write_jsonl(slug / "aaaa-1111.jsonl", [ORCHESTRATOR_TURN])
            sub = slug / "aaaa-1111" / "subagents" / "agent-a1.jsonl"
            sub.parent.mkdir(parents=True, exist_ok=True)
            sub.write_text(CORRUPT_LINES)

            files = self.cost._collect_jsonl_files(main)
            _m, _s, unreadable, malformed = self.cost._parse_usage(files, main)

            self.assertEqual(unreadable, [])
            self.assertEqual(malformed, [(sub, 3)], malformed)


class CodexRollupCountsMalformedLines(_CostActualsCase):
    """`_parse_codex_session_usage` swallowed the same JSONDecodeError."""

    def test_stats_and_render_surface_the_malformed_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = pathlib.Path(tmp) / "session-a.jsonl"
            good = {
                "type": "event_msg",
                "payload": {
                    "info": {
                        "last_token_usage": {
                            "input_tokens": 100, "cached_input_tokens": 40,
                            "output_tokens": 10, "reasoning_output_tokens": 3,
                            "total_tokens": 110,
                        },
                        "total_token_usage": {
                            "input_tokens": 100, "cached_input_tokens": 40,
                            "output_tokens": 10, "reasoning_output_tokens": 3,
                            "total_tokens": 110,
                        },
                    }
                },
            }
            session.write_text(
                json.dumps(good) + "\n" + '{"type":"event_msg","payl' + "\n"
            )

            stats = self.cost._parse_codex_session_usage([session])

            self.assertEqual(
                stats["malformed_lines"], [(session, 2)],
                "a truncated Codex event line was dropped silently; the rollup "
                "understates local activity with no warning",
            )
            rendered = self.cost._render_codex_session_rollup(
                stats, pathlib.Path(tmp)
            )
            self.assertIn("Malformed", rendered, rendered)


class SelftestStillPasses(unittest.TestCase):
    def test_selftest(self):
        result = subprocess.run(
            [sys.executable, COST_ACTUALS, "--selftest"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("cost_actuals selftest: OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
