#!/usr/bin/env python3
"""evolution — evolution records for the evaluate/approve loop.

Implements the Worker + Evaluator + Evolution pattern (arXiv 2604.21003): every
harness/artifact variant gets recorded with
its evaluator score, and the next variant ALWAYS evolves from the best-scoring one
— not the latest one. The Evolution agent reads the complete history before
proposing a mutation to prompts, tools, or orchestration.

Records live at runs/<run>/evolution.json; a human-readable evolution.md table is
regenerated next to it on every write. Writes go through bb_lock.

Usage:
  python3 scripts/evolution.py record --run <name> --variant <id> --change "<what changed>" \
        --score 0.83 --verdict approve|reject|revise [--parent <id>] [--notes "..."]
  python3 scripts/evolution.py best   --run <name>
  python3 scripts/evolution.py next   --run <name>     # context block for the Evolution agent
  python3 scripts/evolution.py report --run <name>     # markdown table
"""
import os, sys, json, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # project-os/
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import bb_lock

VERDICTS = ("approve", "reject", "revise")


def paths(run):
    # 2026-07-25: --run was joined into ROOT unsanitized, so an absolute path
    # or a ../ traversal in --run escaped runs/ entirely (path traversal /
    # arbitrary-write). Reject anything that isn't a plain run-name segment,
    # and confirm the resolved directory still lands inside runs/.
    if not run or os.path.isabs(run) or run in (".", "..") or "/" in run or "\\" in run:
        print(f"invalid --run {run!r}: must be a single path segment, "
              f"no slashes and no absolute paths", file=sys.stderr)
        sys.exit(2)
    d = os.path.join(ROOT, "runs", run)
    runs_root = os.path.join(ROOT, "runs")
    if os.path.realpath(d) != os.path.join(os.path.realpath(runs_root), run):
        print(f"invalid --run {run!r}: escapes runs/", file=sys.stderr)
        sys.exit(2)
    return d, os.path.join(d, "evolution.json"), os.path.join(d, "evolution.md")


def load(jf):
    if os.path.exists(jf):
        with open(jf, encoding="utf-8") as f:
            return json.load(f)
    return {"schema": "evolution/v1", "records": []}


def best_of(data):
    scored = [r for r in data["records"] if r.get("score") is not None]
    return max(scored, key=lambda r: r["score"]) if scored else None


def render_md(data, mf, run):
    lines = [f"# Evolution Records — {run}", "",
             "Next variant always evolves from the **best-scoring** variant, not the latest.",
             "", "| Variant | Parent | Change | Score | Verdict | When |", "|---|---|---|---|---|---|"]
    for r in data["records"]:
        lines.append(f"| {r['variant']} | {r.get('parent') or '—'} | {r['change'][:70]} | "
                     f"{r.get('score','—')} | {r['verdict']} | {r['ts'][:16]} |")
    b = best_of(data)
    lines += ["", f"**Best so far:** {b['variant']} ({b['score']})" if b else "**Best so far:** none scored"]
    with open(mf, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    args = sys.argv[1:]

    def flag(name, default=None):
        if name in args:
            i = args.index(name)
            # 2026-07-26: a bare trailing flag (`evolution.py best --run` with
            # nothing after it) indexed past the end of argv and died with a raw
            # IndexError traceback instead of a usage refusal.
            if i + 1 >= len(args):
                print(f"evolution: {name} needs a value — nothing followed it.",
                      file=sys.stderr)
                sys.exit(2)
            v = args[i + 1]
            del args[i:i + 2]
            return v
        return default

    if not args:
        print(__doc__)
        sys.exit(2)
    cmd = args.pop(0)
    run = flag("--run")
    if not run:
        print("--run <name> is required", file=sys.stderr)
        sys.exit(2)
    d, jf, mf = paths(run)

    if cmd == "record":
        variant, change, verdict = flag("--variant"), flag("--change"), flag("--verdict")
        score, parent, notes = flag("--score"), flag("--parent"), flag("--notes", "")
        if not (variant and change and verdict):
            print("record needs --variant --change --verdict", file=sys.stderr)
            sys.exit(2)
        if verdict not in VERDICTS:
            print(f"verdict must be one of {VERDICTS}", file=sys.stderr)
            sys.exit(2)
        os.makedirs(d, exist_ok=True)
        if not bb_lock.acquire(jf, agent="evolution", wait=10):
            print("FAILED: could not lock evolution.json", file=sys.stderr)
            sys.exit(1)
        try:
            data = load(jf)
            data["records"].append({
                "variant": variant, "parent": parent, "change": change,
                "score": float(score) if score is not None else None,
                "verdict": verdict, "notes": notes,
                "ts": datetime.datetime.now().isoformat(timespec="seconds"),
            })
            with open(jf, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            render_md(data, mf, run)
            b = best_of(data)  # computed inside the lock — no post-release re-read race
        finally:
            bb_lock.release(jf, agent="evolution", force=True)
        print(f"recorded {variant} (score={score}); best so far: "
              f"{b['variant']} ({b['score']})" if b else f"recorded {variant}")
        sys.exit(0)

    data = load(jf)

    if cmd == "best":
        b = best_of(data)
        print(json.dumps(b, indent=2) if b else "no scored variants yet")
        sys.exit(0)

    if cmd == "report":
        render_md(data, mf, run)
        print(open(mf, encoding="utf-8").read())
        sys.exit(0)

    if cmd == "next":
        b = best_of(data)
        if not b:
            print("No scored variants yet — build variant v1 from the approved plan, "
                  "then record it here.")
            sys.exit(0)
        print(f"""## Evolution Agent context — run: {run}

**Evolve from:** {b['variant']} (score {b['score']}, verdict {b['verdict']})
Do NOT evolve from the latest variant unless it is also the best.

**Full history ({len(data['records'])} variants):**""")
        for r in data["records"]:
            print(f"- {r['variant']} <- {r.get('parent') or 'root'}: {r['change']} "
                  f"=> {r.get('score','—')} {r['verdict']}"
                  + (f" ({r['notes']})" if r.get("notes") else ""))
        print("""
**Your job:** propose exactly ONE mutation to the harness (worker prompt, rubric,
tools, or orchestration) applied to the best variant. State: what you change, why
the history says it will score higher, and what evaluator criterion it targets.
Record the result with `evolution.py record --parent {best}`.""".replace("{best}", b["variant"]))
        sys.exit(0)

    print(f"unknown command: {cmd}", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
