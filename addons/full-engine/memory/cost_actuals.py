#!/usr/bin/env python3
"""Post-run transcript cost parser for Project OS.

Parses a Claude Code session .jsonl (and its sibling subagents/ dir) to measure
actual token usage and compute dollars per model.

Usage:
  python3 cost_actuals.py [--transcript PATH] [--prices JSON] [--write] [--target PATH]
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
        p = prices.get(tier, {"in": 0.0, "out": 0.0})
        in_p  = p.get("in",  0.0)
        out_p = p.get("out", 0.0)
        cost = (
            counts["input"]          * in_p  / 1_000_000
            + counts["output"]       * out_p / 1_000_000
            + counts["cache_creation"] * in_p * CACHE_CREATION_FACTOR / 1_000_000
            + counts["cache_read"]   * in_p  * CACHE_READ_FACTOR      / 1_000_000
        )
        result[tier] = cost
    return result


def _build_table(main_costs, sub_costs):
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
    table_md   = _build_table(main_costs, sub_costs)

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

        table = _build_table(main_costs, sub_costs)
        assert "Main loop / orchestrator (Opus)" in table, table
        assert "Subagents (Opus)" in table, table
        assert "Subtotal — orchestrator" in table, table
    finally:
        import shutil
        shutil.rmtree(base)

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
    args = parser.parse_args()

    if args.selftest:
        sys.exit(selftest())

    prices = _load_prices(args.prices)

    if args.transcript:
        transcript_path = pathlib.Path(args.transcript)
    else:
        transcript_path = _find_latest_jsonl()

    target_path = pathlib.Path(args.target) if args.target else None
    run(transcript_path, prices, args.write, target_path)


if __name__ == "__main__":
    main()
