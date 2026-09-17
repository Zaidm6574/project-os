#!/usr/bin/env python3
"""Roll up measured cost actuals across all runs into one markdown table.

Scans runs/*/09-cost-estimate.md, reads the measured $ rows inside each run's
<!-- ACTUALS:START --> ... <!-- ACTUALS:END --> block (written by
cost_actuals.py), and sums cost by run and by model tier (haiku/sonnet/opus).
Prints a markdown table to stdout — a portfolio view across every run.

Usage:
  python3 memory/cost_rollup.py [--runs DIR]
  python3 memory/cost_rollup.py --selftest

Exits 2 when the rollup cannot be trusted as the portfolio total: --runs points
at a directory that does not exist, or a run's 09-cost-estimate.md exists but
could not be read, has an invalid ACTUALS block, or records incomplete
measurement. Readable partial amounts remain labeled as recorded partial sums.

Standard library only. No network access.
"""
import argparse
import importlib.util
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MARK_START = "<!-- ACTUALS:START -->"
MARK_END = "<!-- ACTUALS:END -->"
DOLLARS_RE = re.compile(r"\$([0-9][0-9,]*\.?[0-9]*)")

# A standard installation supplies both siblings plus scripts/bb_lock.py.
# Load by path so direct importlib users and the installed CLI use the same
# fence-aware locator as the writer, without duplicating Markdown parsing.
_spec = importlib.util.spec_from_file_location(
    "_rollup_cost_actuals", os.path.join(HERE, "cost_actuals.py"))
_actuals = importlib.util.module_from_spec(_spec)
try:
    _spec.loader.exec_module(_actuals)
except (OSError, RuntimeError) as exc:
    message = "cost_rollup requires sibling cost_actuals.py and Project OS scripts/bb_lock.py: %s" % exc
    if __name__ == "__main__":
        sys.stderr.write("error: %s\n" % message)
        raise SystemExit(2)
    raise RuntimeError(message) from exc


class Actuals(dict):
    """Tier amounts with completeness metadata; preserves the dict API."""

    def __init__(self):
        super().__init__()
        self.has_actuals = False
        self.incomplete_reasons = []


class Rollup(dict):
    """Run amounts retaining unreadable inputs even without an output list."""

    def __init__(self):
        super().__init__()
        self.unreadable = []


def _tier(label):
    low = label.lower()
    for t in ("haiku", "sonnet", "opus"):
        if t in low:
            return t
    return "other"


def _parse_actuals(text):
    """Return dict-like tier sums and incomplete_reasons for one real block."""
    out = Actuals()
    if not text:
        return out
    try:
        layout = _actuals._marker_layout(text, "cost report")
    except _actuals.CostActualsError as exc:
        out.incomplete_reasons.append(str(exc))
        return out
    if layout is None:
        return out
    out.has_actuals = True
    start, end = layout
    block = text[start + len(MARK_START):end]
    for line in block.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) < 3:
            continue
        label = cells[0]
        low = label.lower()
        if re.search(r"\bnot (?:fully )?measured\b", cells[2], re.IGNORECASE):
            out.incomplete_reasons.append("%s: %s" % (label, cells[2]))
        # 2026-07-25: guard against blank label — set("") <= set(...) is True,
        # so an empty/whitespace Model cell was mistaken for a separator row
        # and its $ amount silently dropped instead of counted.
        if low.startswith("model") or "total" in low or (label and set(label) <= set("-: ")):
            continue  # header, subtotal, total, or separator row
        m = DOLLARS_RE.search(cells[2])
        if not m:
            continue
        amount = float(m.group(1).replace(",", ""))
        out[_tier(label)] = out.get(_tier(label), 0.0) + amount
    if not out and not out.incomplete_reasons:
        out.incomplete_reasons.append("ACTUALS block contains no recorded dollar amounts")
    return out


def rollup(runs_dir, unreadable=None):
    """Return {run_slug: {tier: $}} for every run with parseable actuals.

    Values preserve the dict API and carry incomplete_reasons. The returned
    dict carries unreadable paths; render() retains both caveats automatically.
    Pass a list as `unreadable` to collect those paths separately as well.
    Converting the results to plain dicts discards this metadata.

    2026-07-27: `except OSError: continue` conflated "this run has no
    09-cost-estimate.md" (a legitimate skip — this function only promises runs
    with parseable actuals) with "the file is there but unreadable". The second
    case dropped a run's real measured spend, so the **All runs** row silently
    understated the portfolio (three runs at $100/$250/$400 rendered $350.0000
    once one file was mode 000). Mirrors the `unreadable` tracking cost_actuals.py
    already does for transcripts.
    """
    result = Rollup()
    if not os.path.isdir(runs_dir):
        return result
    for slug in sorted(os.listdir(runs_dir)):
        d = os.path.join(runs_dir, slug)
        if not os.path.isdir(d) or slug.startswith("_"):
            continue
        cost_path = os.path.join(d, "09-cost-estimate.md")
        try:
            with open(cost_path, encoding="utf-8") as fh:
                per_tier = _parse_actuals(fh.read())
        except FileNotFoundError:
            continue  # no cost file recorded for this run — nothing to read
        except (OSError, UnicodeError):
            # Present but unreadable (mode 000, a directory in its place, an
            # I/O error): the spend is real, so never drop it quietly.
            if unreadable is not None:
                unreadable.append(cost_path)
            result.unreadable.append(cost_path)
            continue
        if per_tier or per_tier.incomplete_reasons:
            result[slug] = per_tier
    return result


def _unreadable_note(unreadable):
    """Markdown footnote naming the runs excluded from the grand total."""
    return ("\n> **INCOMPLETE — grand total EXCLUDES %d unreadable run(s):** %s\n"
            "> These cost files exist but could not be read; their amounts are unknown.\n"
            "> The recorded sum is not a complete portfolio total." % (
                len(unreadable), ", ".join(unreadable)))


def render(data, unreadable=None):
    unreadable = list(dict.fromkeys(list(unreadable or []) + list(getattr(data, "unreadable", []))))
    partial = {slug: per.incomplete_reasons for slug, per in data.items()
               if getattr(per, "incomplete_reasons", None)}
    incomplete = bool(unreadable or partial)
    tiers = sorted({t for per in data.values() for t in per})
    heading = "Recorded partial sum $" if incomplete else "Run total $"
    lines = ["| " + " | ".join(["Run"] + [t.capitalize() for t in tiers] + [heading]) + " |",
             "|" + "---|" * (len(tiers) + 2)]
    col_totals = {t: 0.0 for t in tiers}
    grand = 0.0
    for slug in sorted(data):
        per = data[slug]
        cells = []
        run_total = 0.0
        for t in tiers:
            v = per.get(t, 0.0)
            col_totals[t] += v
            run_total += v
            cells.append("$%.4f" % v if v else "—")
        grand += run_total
        value = "$%.4f" % run_total
        if slug in partial:
            value += " (not fully measured)"
        lines.append("| " + " | ".join([slug] + cells + [value]) + " |")
    tot_cells = ["$%.4f" % col_totals[t] for t in tiers]
    total_label = "**All runs (recorded partial sum)**" if incomplete else "**All runs**"
    lines.append("| " + " | ".join([total_label] + tot_cells + ["**$%.4f**" % grand]) + " |")
    if partial:
        lines.append("\n> **INCOMPLETE measurement:** " + "; ".join(
            "%s (%s)" % (slug, "; ".join(reasons)) for slug, reasons in sorted(partial.items()))
            + ". Recorded amounts do not establish the missing cost or a complete total.")
    if unreadable:
        # The caveat belongs in the rendered markdown, not only on stderr: this
        # table gets pasted into reports, where a bare **All runs** row would
        # read as the whole portfolio.
        lines.append(_unreadable_note(unreadable))
    lines.append("\nRecorded ACTUALS rows only; totals do not establish complete provider billing, current prices, or run attribution.")
    return "\n".join(lines)


def selftest():
    import tempfile
    base = tempfile.mkdtemp()
    try:
        for slug, op, sub in (("run-a", 15.0, 10.0), ("run-b", 2.5, 0.0)):
            d = os.path.join(base, slug)
            os.makedirs(d)
            body = (
                "## Actuals\n%s\n"
                "| Model | Est $ | Measured $ | Variance |\n"
                "|---|---|---|---|\n"
                "| Main loop / orchestrator (Opus) | — | $%.4f | — |\n"
                "| Subagents (Sonnet) | — | $%.4f | — |\n"
                "| **Total** | — | **$%.4f** | — |\n%s\n"
                % (MARK_START, op, sub, op + sub, MARK_END)
            )
            with open(os.path.join(d, "09-cost-estimate.md"), "w") as fh:
                fh.write(body)
        data = rollup(base)
        assert set(data) == {"run-a", "run-b"}, data
        # The **Total** row must be skipped, so run-a opus == 15.0 (not 25.0).
        assert abs(data["run-a"]["opus"] - 15.0) < 1e-9, data["run-a"]
        assert abs(data["run-a"]["sonnet"] - 10.0) < 1e-9, data["run-a"]
        assert abs(data["run-b"]["opus"] - 2.5) < 1e-9, data["run-b"]
        table = render(data)
        assert "All runs" in table, table
        # grand total = 15+10+2.5 = 27.5
        assert "$27.5000" in table, table
        # 2026-07-27: a cost file that exists but cannot be read must be
        # reported, not dropped from the grand total. A directory standing in
        # for the file reproduces that branch even when running as root.
        os.makedirs(os.path.join(base, "run-broken", "09-cost-estimate.md"))
        unreadable = []
        partial = rollup(base, unreadable)
        assert set(partial) == {"run-a", "run-b"}, partial
        assert len(unreadable) == 1 and "run-broken" in unreadable[0], unreadable
        noted = render(partial, unreadable)
        assert "EXCLUDES" in noted, noted
        # Mirror direction: a run with no cost file at all is still skipped
        # silently — it has nothing to read, so it is not "unreadable".
        os.makedirs(os.path.join(base, "run-no-costs"))
        unreadable2 = []
        rollup(base, unreadable2)
        assert unreadable2 == unreadable, unreadable2
    finally:
        import shutil
        shutil.rmtree(base)
    print("cost_rollup selftest: OK")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Roll up cost actuals across runs.")
    ap.add_argument("--runs", default=os.path.join(ROOT, "runs"),
                    help="runs directory to scan (default: <project>/runs)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if not os.path.isdir(args.runs):
        # 2026-07-27: a typo'd path used to print the same "no runs with
        # recorded actuals" line as a genuinely empty portfolio and exit 0.
        sys.stderr.write(
            "NO SUCH RUNS DIRECTORY: %s\n"
            "Nothing was scanned — this is not an empty portfolio.\n" % args.runs)
        return 2
    unreadable = []
    data = rollup(args.runs, unreadable)
    if not data:
        print("No runs with recorded actuals found under %s" % args.runs)
        if unreadable:
            print(_unreadable_note(unreadable))
    else:
        print("# Cost rollup across runs\n")
        print(render(data, unreadable))
    if unreadable:
        sys.stderr.write(
            "\nUNREADABLE COST FILE(S): %s\n"
            "These exist but could not be opened/read, so the total above is\n"
            "INCOMPLETE; omitted amounts are unknown.\n" % ", ".join(unreadable))
        return 2
    partial = [slug for slug, per in data.items() if per.incomplete_reasons]
    if partial:
        sys.stderr.write("\nINCOMPLETE MEASUREMENT: %s. Recorded partial sums only.\n"
                         % ", ".join(partial))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
