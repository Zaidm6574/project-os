#!/usr/bin/env python3
"""Post-run transcript cost parser for Project OS.

Parses a Claude Code session .jsonl (and its sibling subagents/ dir) to measure
actual token usage and compute dollars per model.

Usage:
  python3 cost_actuals.py [--transcript PATH] [--prices JSON] [--write] [--target PATH]
  python3 cost_actuals.py --codex-sessions [--sessions-dir PATH]
  python3 cost_actuals.py --selftest

--transcript  Path to a .jsonl session file.  Default: auto-detect the most
              recently modified *.jsonl under ~/.claude/projects (plus any
              *.jsonl in a sibling subagents/ folder).
--prices      Path to a JSON file OR inline JSON string mapping model slug ->
              {"in": <$/1M input>, "out": <$/1M output>}.
              Default: built-in table (Haiku $1/$5, Sonnet $3/$15, Opus $5/$25).
--write       Replace the <!-- ACTUALS:START --> ... <!-- ACTUALS:END --> block
              in blackboard/09-cost-estimate.md (or in --target, if given).
--target      Override the destination file for --write.
--selftest    Feed a synthetic 2-message JSONL through the parser, assert the
              math, and exit 0.

Cache pricing: cache_read billed at 0.10x input rate; cache_creation at 1.25x.

Codex local session logs: ~/.codex/sessions stores event records with
payload.info.last_token_usage (per-turn usage) and payload.info.total_token_usage
(cumulative session usage). Sum last_token_usage for the preferred local
activity estimate, and use only the final total_token_usage per session file as
a lower cross-check. Never sum every total_token_usage row. Codex's
cached_input_tokens are cached reads/subset of input tokens; these logs do not
expose cache_creation_input_tokens/cache writes.

No network calls; stdlib only.
"""

import argparse
import json
import os
import pathlib
import sys
import tempfile

# Default price table (dollars per 1 million tokens).
DEFAULT_PRICES = {
    "haiku":  {"in": 1.00, "out":  5.00},
    "sonnet": {"in": 3.00, "out": 15.00},
    "opus":   {"in": 5.00, "out": 25.00},
}

CACHE_READ_FACTOR     = 0.10   # cache_read billed ~0.1x input price
CACHE_CREATION_FACTOR = 1.25   # cache_creation billed ~1.25x input price

CODEX_TOKEN_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _model_tier(model_slug):
    """Map a raw model slug to a canonical tier key (haiku / sonnet / opus)."""
    slug = model_slug.lower()
    if "haiku"  in slug:
        return "haiku"
    if "sonnet" in slug:
        return "sonnet"
    if "opus"   in slug:
        return "opus"
    return slug  # unknown tier — kept as-is so it appears in the table


def _find_latest_jsonl():
    """Return the most recently modified *.jsonl under ~/.claude/projects."""
    base = pathlib.Path.home() / ".claude" / "projects"
    if not base.exists():
        raise FileNotFoundError(
            "~/.claude/projects not found; use --transcript to specify a file"
        )
    candidates = list(base.rglob("*.jsonl"))
    if not candidates:
        raise FileNotFoundError(
            "No *.jsonl files found under ~/.claude/projects"
        )
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _load_prices(prices_arg):
    """Load the price table from a JSON string or file path."""
    if prices_arg is None:
        return DEFAULT_PRICES
    stripped = prices_arg.strip()
    if stripped.startswith("{"):
        raw = json.loads(stripped)
    else:
        with open(prices_arg) as fh:
            raw = json.load(fh)
    return {k.lower(): v for k, v in raw.items()}


def _collect_jsonl_files(transcript):
    """Return [main_file] plus any *.jsonl in a sibling subagents/ directory."""
    files = [transcript]
    subagents_dir = transcript.parent / "subagents"
    if subagents_dir.is_dir():
        files.extend(sorted(subagents_dir.glob("*.jsonl")))
    return files


def _empty_counts():
    return {"input": 0, "output": 0, "cache_creation": 0, "cache_read": 0}


def _parse_usage(files, main_file):
    """Parse usage from JSONL files.

    Returns (main_totals, sub_totals): dicts mapping tier -> count dict.
    main_totals covers the main session file; sub_totals covers everything else.
    """
    main_totals = {}
    sub_totals  = {}

    for fpath in files:
        target = main_totals if fpath == main_file else sub_totals
        try:
            with open(fpath) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    # usage may sit at the top level or inside a "message" key
                    usage = msg.get("usage") or msg.get("message", {}).get("usage")
                    if not usage:
                        continue
                    model = (
                        msg.get("model")
                        or msg.get("message", {}).get("model", "unknown")
                    )
                    tier = _model_tier(model)
                    if tier not in target:
                        target[tier] = _empty_counts()
                    target[tier]["input"]          += usage.get("input_tokens", 0)
                    target[tier]["output"]         += usage.get("output_tokens", 0)
                    target[tier]["cache_creation"] += usage.get("cache_creation_input_tokens", 0)
                    target[tier]["cache_read"]     += usage.get("cache_read_input_tokens", 0)
        except OSError:
            pass

    return main_totals, sub_totals


def _compute_cost(totals, prices):
    """Return {tier: cost_dollars} from a totals dict."""
    result = {}
    for tier, counts in totals.items():
        parts = _compute_cost_parts(counts, prices.get(tier, {"in": 0.0, "out": 0.0}))
        result[tier] = sum(parts.values())
    return result


def _compute_cost_parts(counts, price):
    """Return cost dollars by token category for one model tier."""
    in_p = price.get("in", 0.0)
    out_p = price.get("out", 0.0)
    return {
        "input": counts["input"] * in_p / 1_000_000,
        "output": counts["output"] * out_p / 1_000_000,
        "cache_creation": counts["cache_creation"] * in_p * CACHE_CREATION_FACTOR / 1_000_000,
        "cache_read": counts["cache_read"] * in_p * CACHE_READ_FACTOR / 1_000_000,
    }


def _fmt_tokens(value):
    return f"{value:,}"


def _fmt_pct(numerator, denominator):
    if denominator <= 0:
        return "n/a"
    return "%.1f%%" % (numerator / denominator * 100)


def _build_token_detail_rows(scope, totals, prices):
    rows = []
    for tier in sorted(totals):
        label = f"{scope} ({tier.capitalize()})"
        counts = totals[tier]
        parts = _compute_cost_parts(counts, prices.get(tier, {"in": 0.0, "out": 0.0}))
        rows.append(
            "| %s | %s | %s | %s | %s | $%.4f | $%.4f | $%.4f | $%.4f | $%.4f |"
            % (
                label,
                _fmt_tokens(counts["input"]),
                _fmt_tokens(counts["output"]),
                _fmt_tokens(counts["cache_creation"]),
                _fmt_tokens(counts["cache_read"]),
                parts["input"],
                parts["output"],
                parts["cache_creation"],
                parts["cache_read"],
                sum(parts.values()),
            )
        )
    return rows


def _cache_pressure_summary(all_totals, prices):
    total_cost = 0.0
    cache_write_cost = 0.0
    input_tokens = 0
    output_tokens = 0
    cache_creation_tokens = 0
    cache_read_tokens = 0

    for tier, counts in all_totals.items():
        parts = _compute_cost_parts(counts, prices.get(tier, {"in": 0.0, "out": 0.0}))
        total_cost += sum(parts.values())
        cache_write_cost += parts["cache_creation"]
        input_tokens += counts["input"]
        output_tokens += counts["output"]
        cache_creation_tokens += counts["cache_creation"]
        cache_read_tokens += counts["cache_read"]

    if total_cost <= 0:
        cost_share = "n/a"
    else:
        cost_share = "%.1f%%" % (cache_write_cost / total_cost * 100)
    if output_tokens <= 0:
        write_to_output = "n/a"
    else:
        write_to_output = "%.1fx" % (cache_creation_tokens / output_tokens)

    lines = [
        "",
        "### Cache-write pressure",
        "",
        "| Metric | Value |",
        "|---|---|",
        "| Cache-write cost share | %s |" % cost_share,
        "| Cache-write tokens / output tokens | %s |" % write_to_output,
        "| Uncached input tokens | %s |" % _fmt_tokens(input_tokens),
        "| Output tokens | %s |" % _fmt_tokens(output_tokens),
        "| Cached-write tokens | %s |" % _fmt_tokens(cache_creation_tokens),
        "| Cached-read tokens | %s |" % _fmt_tokens(cache_read_tokens),
    ]

    if total_cost > 0 and cache_write_cost / total_cost > 0.5:
        lines.append("")
        lines.append(
            "Recommendation: cache writes are more than half of measured AI workflow cost. "
            "At the next safe phase boundary, write a handoff packet or receipt and continue "
            "from the blackboard in a fresh session."
        )
    elif output_tokens > 0 and cache_creation_tokens / output_tokens >= 10:
        lines.append("")
        lines.append(
            "Recommendation: cache-write tokens are much larger than output tokens. "
            "Checkpoint the run and trim context before continuing."
        )

    return "\n".join(lines)


def _merge_totals(*groups):
    merged = {}
    for group in groups:
        for tier, counts in group.items():
            if tier not in merged:
                merged[tier] = _empty_counts()
            for key in merged[tier]:
                merged[tier][key] += counts.get(key, 0)
    return merged


# ---------------------------------------------------------------------------
# Codex local session log rollup
# ---------------------------------------------------------------------------

def _empty_codex_counts():
    return {key: 0 for key in CODEX_TOKEN_FIELDS}


def _add_codex_counts(dest, usage):
    for key in CODEX_TOKEN_FIELDS:
        value = usage.get(key, 0)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            dest[key] += int(value)


def _uncached_codex_input(counts):
    return max(0, counts.get("input_tokens", 0) - counts.get("cached_input_tokens", 0))


def _collect_codex_session_files(sessions_dir):
    if not sessions_dir.exists():
        raise FileNotFoundError("%s not found; use --sessions-dir to specify a folder" % sessions_dir)
    return sorted(sessions_dir.rglob("*.jsonl"))


def _parse_codex_session_usage(files):
    """Parse local Codex event logs without reading/persisting message text.

    Codex records usage under payload.info:
    - last_token_usage: per-turn usage. This is the primary rollup source.
    - total_token_usage: cumulative within the session file. Keep only the final
      value per file as a cross-check; summing every row wildly overcounts.
    """
    turn_totals = _empty_codex_counts()
    final_session_totals = _empty_codex_counts()
    cumulative_row_totals = _empty_codex_counts()
    stats = {
        "files_scanned": 0,
        "sessions_with_usage": 0,
        "usage_events": 0,
        "bad_cumulative_rows": 0,
        "turn_totals": turn_totals,
        "final_session_totals": final_session_totals,
        "cumulative_row_totals": cumulative_row_totals,
    }

    for fpath in files:
        stats["files_scanned"] += 1
        final_total_for_file = None
        file_has_usage = False
        try:
            with open(fpath, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    payload = event.get("payload")
                    if not isinstance(payload, dict):
                        continue
                    info = payload.get("info")
                    if not isinstance(info, dict):
                        continue
                    last_usage = info.get("last_token_usage")
                    if isinstance(last_usage, dict):
                        _add_codex_counts(turn_totals, last_usage)
                        stats["usage_events"] += 1
                        file_has_usage = True
                    total_usage = info.get("total_token_usage")
                    if isinstance(total_usage, dict):
                        final_total_for_file = total_usage
                        _add_codex_counts(cumulative_row_totals, total_usage)
                        stats["bad_cumulative_rows"] += 1
        except OSError:
            continue

        if file_has_usage:
            stats["sessions_with_usage"] += 1
        if isinstance(final_total_for_file, dict):
            _add_codex_counts(final_session_totals, final_total_for_file)

    return stats


def _render_codex_session_rollup(stats, sessions_dir):
    turn = stats["turn_totals"]
    final = stats["final_session_totals"]
    bad = stats["cumulative_row_totals"]
    rows = []
    for field in CODEX_TOKEN_FIELDS:
        rows.append(
            "| %s | %s | %s | %s |"
            % (
                field,
                _fmt_tokens(turn[field]),
                _fmt_tokens(final[field]),
                _fmt_tokens(bad[field]),
            )
        )
    rows.append(
        "| uncached_input_tokens | %s | %s | %s |"
        % (
            _fmt_tokens(_uncached_codex_input(turn)),
            _fmt_tokens(_uncached_codex_input(final)),
            _fmt_tokens(_uncached_codex_input(bad)),
        )
    )

    overcount_note = "n/a"
    if turn["total_tokens"] > 0:
        overcount_note = "%.1fx" % (bad["total_tokens"] / turn["total_tokens"])

    agreement_note = "matches"
    if turn != final:
        agreement_note = "differs; treat this as local activity, not billing-grade actuals"

    lines = [
        "## Codex local session token rollup",
        "",
        "Source: `%s`" % sessions_dir,
        "",
        "| Scope | Value |",
        "|---|---:|",
        "| Files scanned | %s |" % _fmt_tokens(stats["files_scanned"]),
        "| Sessions with usage | %s |" % _fmt_tokens(stats["sessions_with_usage"]),
        "| Usage events | %s |" % _fmt_tokens(stats["usage_events"]),
        "| Cached-input share of input | %s |" % _fmt_pct(turn["cached_input_tokens"], turn["input_tokens"]),
        "| Wrong cumulative-row overcount | %s |" % overcount_note,
        "| Final-session cross-check | %s |" % agreement_note,
        "",
        "| Field | Preferred local activity estimate: sum `last_token_usage` | Lower cross-check: final `total_token_usage` per session | Do not use: sum every `total_token_usage` row |",
        "|---|---:|---:|---:|",
    ]
    lines.extend(rows)
    lines.extend(
        [
            "",
            "Interpretation:",
            "",
            "- `cached_input_tokens` are cache reads/a subset of input tokens in Codex local logs.",
            "- Codex local logs do not expose `cache_creation_input_tokens`, so they cannot prove cache-write/filing-fee cost.",
            "- `total_token_usage` is cumulative inside each session file. Summing every row overcounts; use `last_token_usage` for local activity or only the final session total as a lower cross-check.",
            "- This is local saved-session activity, not guaranteed account-wide billing across devices or unsaved chats.",
        ]
    )
    return "\n".join(lines)


def run_codex_sessions(sessions_dir):
    files = _collect_codex_session_files(sessions_dir)
    stats = _parse_codex_session_usage(files)
    print(_render_codex_session_rollup(stats, sessions_dir))


def _build_table(main_costs, sub_costs, main_totals=None, sub_totals=None, prices=None):
    """Return a markdown table string for the ACTUALS block.

    The **main session file is the orchestrator loop** (the CEO/Opus thread that
    runs the waves). Its cost is summed across *all* orchestrator turns in that
    file and emitted as its own "Main loop / orchestrator" row(s), kept strictly
    separate from the per-tier subagent rows so the orchestrator's spend is never
    blended into the workers' spend.
    """
    lines = [
        "| Model | Est $ | Measured $ | Variance |",
        "|---|---|---|---|",
    ]
    for tier in sorted(main_costs):
        label = "Main loop / orchestrator (%s)" % tier.capitalize()
        lines.append("| %s | — | $%.4f | — |" % (label, main_costs[tier]))
    for tier in sorted(sub_costs):
        label = "Subagents (%s)" % tier.capitalize()
        lines.append("| %s | — | $%.4f | — |" % (label, sub_costs[tier]))
    main_total = sum(main_costs.values())
    sub_total  = sum(sub_costs.values())
    total = main_total + sub_total
    lines.append("| **Subtotal — orchestrator** | — | $%.4f | — |" % main_total)
    lines.append("| **Subtotal — subagents** | — | $%.4f | — |" % sub_total)
    lines.append("| **Total** | — | **$%.4f** | — |" % total)

    if main_totals is not None and sub_totals is not None and prices is not None:
        lines += [
            "",
            "### Token details",
            "",
            "| Scope | Input | Output | Cached Write | Cached Read | Input $ | Output $ | Cache Write $ | Cache Read $ | Measured $ |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        lines += _build_token_detail_rows("Main loop / orchestrator", main_totals, prices)
        lines += _build_token_detail_rows("Subagents", sub_totals, prices)
        lines.append(_cache_pressure_summary(_merge_totals(main_totals, sub_totals), prices))
    return "\n".join(lines)


def _update_markers(dest, table_md):
    """Replace content between ACTUALS markers in dest file."""
    text = dest.read_text()
    start_tag = "<!-- ACTUALS:START -->"
    end_tag   = "<!-- ACTUALS:END -->"
    si = text.find(start_tag)
    ei = text.find(end_tag)
    if si == -1 or ei == -1:
        raise ValueError("Markers not found in %s" % dest)
    new_text = (
        text[: si + len(start_tag)]
        + "\n"
        + table_md
        + "\n"
        + text[ei:]
    )
    dest.write_text(new_text)


# ---------------------------------------------------------------------------
# Core entry point
# ---------------------------------------------------------------------------

def run(transcript_path, prices, write, target_path):
    """Parse transcript, print actuals, optionally update the markdown file."""
    files      = _collect_jsonl_files(transcript_path)
    main_t, sub_t = _parse_usage(files, transcript_path)
    main_costs = _compute_cost(main_t, prices)
    sub_costs  = _compute_cost(sub_t,  prices)
    table_md   = _build_table(main_costs, sub_costs, main_t, sub_t, prices)

    block = (
        "## Actuals (estimate vs measured)\n\n"
        "<!-- ACTUALS:START -->\n"
        + table_md
        + "\n<!-- ACTUALS:END -->"
    )
    print(block)

    if write:
        if target_path is None:
            here = pathlib.Path(__file__).parent
            target_path = here.parent / "blackboard" / "09-cost-estimate.md"
        _update_markers(target_path, table_md)
        print("\nUpdated %s" % target_path, file=sys.stderr)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def selftest():
    """Feed a synthetic 2-message JSONL and assert the math.  Exit 0 on pass."""
    msg1 = {
        "model": "claude-opus-4-8",
        "usage": {
            "input_tokens": 1_000_000,
            "output_tokens": 200_000,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
    }
    msg2 = {
        "model": "claude-sonnet-4-6",
        "usage": {
            "input_tokens": 500_000,
            "output_tokens": 100_000,
            "cache_creation_input_tokens": 100_000,
            "cache_read_input_tokens": 50_000,
        },
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as fh:
        fh.write(json.dumps(msg1) + "\n")
        fh.write(json.dumps(msg2) + "\n")
        tmp = pathlib.Path(fh.name)

    try:
        main_totals, sub_totals = _parse_usage([tmp], tmp)

        # ---- opus ----
        # 1M input * $5/1M = $5.00
        # 200k output * $25/1M = $5.00
        # cache_creation = 0, cache_read = 0
        # Expected: $10.00
        opus_cost = _compute_cost({"opus": main_totals["opus"]}, DEFAULT_PRICES)["opus"]
        assert abs(opus_cost - 10.0) < 1e-9, "opus expected $10.00, got %r" % opus_cost

        # ---- sonnet ----
        # 500k input * $3/1M               = $1.50
        # 100k output * $15/1M             = $1.50
        # 100k cache_creation * $3/1M*1.25 = $0.375
        # 50k  cache_read     * $3/1M*0.10 = $0.015
        # Expected: $3.39
        sonnet_cost = _compute_cost({"sonnet": main_totals["sonnet"]}, DEFAULT_PRICES)["sonnet"]
        expected = 1.50 + 1.50 + 0.375 + 0.015
        assert abs(sonnet_cost - expected) < 1e-9, (
            "sonnet expected %r, got %r" % (expected, sonnet_cost)
        )

        # No subagent files -> sub_totals should be empty.
        assert sub_totals == {}, "expected empty sub_totals, got %r" % sub_totals

        # Table generation includes the orchestrator (main loop) label.
        table = _build_table({"opus": opus_cost, "sonnet": sonnet_cost}, {})
        assert "Main loop" in table, "table missing 'Main loop'"
        assert "orchestrator" in table.lower(), "table missing 'orchestrator'"
        assert "Opus" in table, "table missing 'Opus'"

        detail_table = _build_table(
            {"opus": opus_cost, "sonnet": sonnet_cost},
            {},
            main_totals,
            {},
            DEFAULT_PRICES,
        )
        assert "Token details" in detail_table, "table missing token details"
        assert "Cached Write" in detail_table, "table missing cache write column"
        assert "Cache-write pressure" in detail_table, "table missing pressure summary"
        assert "100,000" in detail_table, "table missing cache_creation token count"

    finally:
        tmp.unlink()

    # ---- orchestrator-summing test --------------------------------------
    # The main session file holds MULTIPLE Opus orchestrator turns; they must be
    # summed into ONE main-loop figure, and an Opus *subagent* must stay separate.
    base = pathlib.Path(tempfile.mkdtemp())
    try:
        main_file = base / "session.jsonl"
        orch_turn = {
            "model": "claude-opus-4-8",
            "usage": {
                "input_tokens": 1_000_000, "output_tokens": 0,
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
            },
        }
        with open(main_file, "w") as fh:
            # three separate orchestrator turns in the main loop
            fh.write(json.dumps(orch_turn) + "\n")
            fh.write(json.dumps(orch_turn) + "\n")
            fh.write(json.dumps(orch_turn) + "\n")

        subdir = base / "subagents"
        subdir.mkdir()
        sub_opus = {
            "model": "claude-opus-4-8",
            "usage": {
                "input_tokens": 2_000_000, "output_tokens": 0,
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
            },
        }
        with open(subdir / "evaluator.jsonl", "w") as fh:
            fh.write(json.dumps(sub_opus) + "\n")

        files = _collect_jsonl_files(main_file)
        main_t, sub_t = _parse_usage(files, main_file)

        # 3 orchestrator turns * 1M input each = 3M summed into the main loop.
        assert main_t["opus"]["input"] == 3_000_000, (
            "orchestrator turns not summed: %r" % main_t["opus"]
        )
        # Subagent Opus stays separate (2M), not folded into the orchestrator.
        assert sub_t["opus"]["input"] == 2_000_000, (
            "subagent opus leaked/lost: %r" % sub_t.get("opus")
        )

        main_costs = _compute_cost(main_t, DEFAULT_PRICES)
        sub_costs  = _compute_cost(sub_t, DEFAULT_PRICES)
        # 3M input * $5/1M = $15.00 orchestrator; 2M * $5/1M = $10.00 subagents.
        assert abs(main_costs["opus"] - 15.0) < 1e-9, main_costs["opus"]
        assert abs(sub_costs["opus"] - 10.0) < 1e-9, sub_costs["opus"]

        table = _build_table(main_costs, sub_costs, main_t, sub_t, DEFAULT_PRICES)
        assert "Main loop / orchestrator (Opus)" in table, table
        assert "Subagents (Opus)" in table, table
        assert "Subtotal — orchestrator" in table, table
        assert "Token details" in table, table
    finally:
        import shutil
        shutil.rmtree(base)

    # ---- cache-write pressure recommendation branch ---------------------
    # When cache-write cost exceeds half of total cost, the summary must
    # surface the fresh-session handoff recommendation (the >50% branch).
    high_pressure = _cache_pressure_summary(
        {"opus": {"input": 0, "output": 1, "cache_creation": 1_000_000, "cache_read": 0}},
        DEFAULT_PRICES,
    )
    assert "more than half" in high_pressure, (
        "pressure summary missing >50%% recommendation: %r" % high_pressure
    )
    # A low cache-pressure mix must NOT trip the recommendation.
    low_pressure = _cache_pressure_summary(
        {"opus": {"input": 1_000_000, "output": 1_000_000, "cache_creation": 0, "cache_read": 0}},
        DEFAULT_PRICES,
    )
    assert "more than half" not in low_pressure, (
        "pressure summary false-positive on low cache pressure: %r" % low_pressure
    )

    # ---- Codex local session rollup -------------------------------------
    # Codex logs have per-turn last_token_usage and cumulative total_token_usage.
    # The preferred all-chat local activity estimate sums last_token_usage. A
    # lower cross-check can sum only the final total_token_usage value per
    # session file. Summing every total_token_usage row is the overcount trap.
    codex_base = pathlib.Path(tempfile.mkdtemp())
    try:
        session_a = codex_base / "session-a.jsonl"
        session_b = codex_base / "session-b.jsonl"
        events_a = [
            {
                "type": "event_msg",
                "payload": {
                    "info": {
                        "last_token_usage": {
                            "input_tokens": 100,
                            "cached_input_tokens": 40,
                            "output_tokens": 10,
                            "reasoning_output_tokens": 3,
                            "total_tokens": 110,
                        },
                        "total_token_usage": {
                            "input_tokens": 100,
                            "cached_input_tokens": 40,
                            "output_tokens": 10,
                            "reasoning_output_tokens": 3,
                            "total_tokens": 110,
                        },
                    }
                },
            },
            {
                "type": "event_msg",
                "payload": {
                    "info": {
                        "last_token_usage": {
                            "input_tokens": 200,
                            "cached_input_tokens": 50,
                            "output_tokens": 20,
                            "reasoning_output_tokens": 6,
                            "total_tokens": 220,
                        },
                        "total_token_usage": {
                            "input_tokens": 300,
                            "cached_input_tokens": 90,
                            "output_tokens": 30,
                            "reasoning_output_tokens": 9,
                            "total_tokens": 330,
                        },
                    }
                },
            },
        ]
        events_b = [
            {
                "type": "event_msg",
                "payload": {
                    "info": {
                        "last_token_usage": {
                            "input_tokens": 50,
                            "cached_input_tokens": 0,
                            "output_tokens": 5,
                            "reasoning_output_tokens": 1,
                            "total_tokens": 55,
                        },
                        "total_token_usage": {
                            "input_tokens": 50,
                            "cached_input_tokens": 0,
                            "output_tokens": 5,
                            "reasoning_output_tokens": 1,
                            "total_tokens": 55,
                        },
                    }
                },
            }
        ]
        with open(session_a, "w", encoding="utf-8") as fh:
            for event in events_a:
                fh.write(json.dumps(event) + "\n")
        with open(session_b, "w", encoding="utf-8") as fh:
            for event in events_b:
                fh.write(json.dumps(event) + "\n")

        codex_stats = _parse_codex_session_usage([session_a, session_b])
        assert codex_stats["turn_totals"]["input_tokens"] == 350, codex_stats
        assert codex_stats["turn_totals"]["cached_input_tokens"] == 90, codex_stats
        assert codex_stats["turn_totals"]["output_tokens"] == 35, codex_stats
        assert codex_stats["turn_totals"] == codex_stats["final_session_totals"], codex_stats
        assert codex_stats["cumulative_row_totals"]["total_tokens"] == 495, codex_stats
        rendered_codex = _render_codex_session_rollup(codex_stats, codex_base)
        assert "sum `last_token_usage`" in rendered_codex, rendered_codex
        assert "local activity" in rendered_codex, rendered_codex
        assert "cache reads" in rendered_codex, rendered_codex
        assert "do not expose `cache_creation_input_tokens`" in rendered_codex, rendered_codex
    finally:
        import shutil
        shutil.rmtree(codex_base)

    print("cost_actuals selftest: OK")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Parse a Claude Code session JSONL and report cost actuals."
    )
    parser.add_argument(
        "--transcript",
        help="Path to a .jsonl session file (default: auto-detect latest).",
    )
    parser.add_argument(
        "--prices",
        help="Path to a JSON price file or inline JSON: model->{in,out} per 1M.",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Update the ACTUALS markers in blackboard/09-cost-estimate.md.",
    )
    parser.add_argument(
        "--target",
        help="Override destination markdown file for --write.",
    )
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="Run built-in assertions and exit 0 on success.",
    )
    parser.add_argument(
        "--codex-sessions",
        action="store_true",
        help="Roll up local Codex ~/.codex/sessions usage counters safely.",
    )
    parser.add_argument(
        "--sessions-dir",
        help="Codex sessions directory for --codex-sessions (default: ~/.codex/sessions).",
    )
    args = parser.parse_args()

    if args.selftest:
        sys.exit(selftest())

    if args.codex_sessions:
        sessions_dir = pathlib.Path(args.sessions_dir).expanduser() if args.sessions_dir else pathlib.Path.home() / ".codex" / "sessions"
        run_codex_sessions(sessions_dir)
        return

    prices = _load_prices(args.prices)

    if args.transcript:
        transcript_path = pathlib.Path(args.transcript)
    else:
        transcript_path = _find_latest_jsonl()

    target_path = pathlib.Path(args.target) if args.target else None
    run(transcript_path, prices, args.write, target_path)


if __name__ == "__main__":
    main()
