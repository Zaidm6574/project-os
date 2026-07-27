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
could not be read (its spend is then missing from the **All runs** row, which is
footnoted in the rendered table as well as flagged on stderr).

Standard library only. No network access.
"""
import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MARK_START = "<!-- ACTUALS:START -->"
MARK_END = "<!-- ACTUALS:END -->"
DOLLARS_RE = re.compile(r"\$([0-9][0-9,]*\.?[0-9]*)")


def _tier(label):
    low = label.lower()
    for t in ("haiku", "sonnet", "opus"):
        if t in low:
            return t
    return "other"


def _parse_actuals(text):
    """Return {tier: summed_$} for one run's ACTUALS block (skips subtotal/total)."""
    out = {}
    if not text or MARK_START not in text or MARK_END not in text:
        return out
    block = text.split(MARK_START, 1)[1].split(MARK_END, 1)[0]
    for line in block.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) < 3:
            continue
        label = cells[0]
        low = label.lower()
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
    return out


def rollup(runs_dir, unreadable=None):
    """Return {run_slug: {tier: $}} for every run with parseable actuals.

    Pass a list as `unreadable` to collect the cost files that EXIST but could
    not be opened/read. Callers MUST report those before presenting the grand
    total as the portfolio's spend.

    2026-07-27: `except OSError: continue` conflated "this run has no
    09-cost-estimate.md" (a legitimate skip — this function only promises runs
    with parseable actuals) with "the file is there but unreadable". The second
    case dropped a run's real measured spend, so the **All runs** row silently
    understated the portfolio (three runs at $100/$250/$400 rendered $350.0000
    once one file was mode 000). Mirrors the `unreadable` tracking cost_actuals.py
    already does for transcripts.
    """
    result = {}
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
        except OSError:
            # Present but unreadable (mode 000, a directory in its place, an
            # I/O error): the spend is real, so never drop it quietly.
            if unreadable is not None:
                unreadable.append(cost_path)
            continue
        if per_tier:
            result[slug] = per_tier
    return result


def _unreadable_note(unreadable):
    """Markdown footnote naming the runs excluded from the grand total."""
    return ("\n> **INCOMPLETE — grand total EXCLUDES %d unreadable run(s):** %s\n"
            "> These cost files exist but could not be read, so the **All runs**\n"
            "> row above UNDERSTATES real spend." % (
                len(unreadable), ", ".join(unreadable)))


def render(data, unreadable=None):
    tiers = sorted({t for per in data.values() for t in per})
    lines = ["| Run | " + " | ".join(t.capitalize() for t in tiers) + " | Run total $ |",
             "|---|" + "---|" * (len(tiers) + 1)]
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
        lines.append("| %s | %s | $%.4f |" % (slug, " | ".join(cells), run_total))
    tot_cells = " | ".join("$%.4f" % col_totals[t] for t in tiers)
    lines.append("| **All runs** | %s | **$%.4f** |" % (tot_cells, grand))
    if unreadable:
        # The caveat belongs in the rendered markdown, not only on stderr: this
        # table gets pasted into reports, where a bare **All runs** row would
        # read as the whole portfolio.
        lines.append(_unreadable_note(unreadable))
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
            "INCOMPLETE and UNDERSTATES real spend.\n" % ", ".join(unreadable))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
