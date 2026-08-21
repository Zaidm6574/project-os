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
import os, sys, json, datetime, math, stat, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # project-os/
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import bb_lock

VERDICTS = ("approve", "reject", "revise")


def paths(run):
    if (not run or os.path.isabs(run) or run in (".", "..")
            or "/" in run or "\\" in run):
        print(f"invalid --run {run!r}: must be a single path segment, "
              "no slashes and no absolute paths", file=sys.stderr)
        sys.exit(2)
    root_real = os.path.realpath(ROOT)
    runs = os.path.join(ROOT, "runs")
    runs_real = os.path.realpath(runs)
    d = os.path.join(runs, run)
    d_real = os.path.realpath(d)
    try:
        runs_inside_root = os.path.commonpath((root_real, runs_real)) == root_real
        run_inside_runs = os.path.commonpath((runs_real, d_real)) == runs_real
    except ValueError:
        runs_inside_root = run_inside_runs = False
    if not runs_inside_root:
        print("invalid --run: runs/ resolves outside the Project OS root", file=sys.stderr)
        sys.exit(2)
    if not run_inside_runs or (os.path.lexists(d) and os.path.islink(d)):
        print(f"invalid --run {run!r}: escapes runs/", file=sys.stderr)
        sys.exit(2)
    return d, os.path.join(d, "evolution.json"), os.path.join(d, "evolution.md")


def _atomic_write_text(path, text):
    """Publish durable state through a temp file + os.replace.

    Writing in place (open(path, "w")) truncates the real file before the new
    bytes land, so a crash in between leaves a stub and the run's entire
    evolution history is unrecoverable. Same pattern as
    scripts/plan_artifact.py and scripts/brain_archive.py: write a sibling
    temp, fsync it, then rename over the target — a rename is atomic, so a
    reader sees either the whole old file or the whole new one.
    """
    directory = os.path.dirname(path) or "."
    mode = None
    if os.path.exists(path):
        mode = stat.S_IMODE(os.stat(path).st_mode)
    fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".",
                               dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
        tmp = None
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def load(jf):
    if os.path.exists(jf):
        try:
            with open(jf, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            detail = getattr(exc, "msg", str(exc)).replace("\n", " ")
            raise ValueError(f"{jf}: malformed evolution JSON ({detail})") from None
    else:
        data = {"schema": "evolution/v1", "records": []}
    if not isinstance(data, dict) or not isinstance(data.get("records"), list):
        raise ValueError(f"{jf}: evolution data must be an object with a records list")
    for number, record in enumerate(data["records"], 1):
        if not isinstance(record, dict):
            raise ValueError(f"{jf}: record {number} must be an object")
        for key in ("variant", "change", "verdict", "ts"):
            if not isinstance(record.get(key), str) or not record[key]:
                raise ValueError(f"{jf}: record {number} has invalid {key!r}")
        score = record.get("score")
        if (score is not None
                and (isinstance(score, bool) or not isinstance(score, (int, float))
                     or not math.isfinite(score))):
            raise ValueError(f"{jf}: record {number} has an invalid score")
    return data


def load_or_exit(jf):
    try:
        return load(jf)
    except ValueError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        sys.exit(2)


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
    _atomic_write_text(mf, "\n".join(lines) + "\n")


def main():
    args = sys.argv[1:]

    def flag(name, default=None):
        if name in args:
            i = args.index(name)
            if i + 1 >= len(args):
                print(f"missing value for {name}", file=sys.stderr)
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
    try:
        d, jf, mf = paths(run)
    except ValueError as exc:
        print(f"usage error: {exc}", file=sys.stderr)
        sys.exit(2)

    if cmd == "record":
        variant, change, verdict = flag("--variant"), flag("--change"), flag("--verdict")
        score, parent, notes = flag("--score"), flag("--parent"), flag("--notes", "")
        if not (variant and change and verdict):
            print("record needs --variant --change --verdict", file=sys.stderr)
            sys.exit(2)
        if verdict not in VERDICTS:
            print(f"verdict must be one of {VERDICTS}", file=sys.stderr)
            sys.exit(2)
        if score is not None:
            try:
                score = float(score)
            except ValueError:
                print(f"--score must be a number, got {score!r}", file=sys.stderr)
                sys.exit(2)
            if not math.isfinite(score):
                print(f"--score must be a finite number, got {score!r}", file=sys.stderr)
                sys.exit(2)
        os.makedirs(d, exist_ok=True)
        token = bb_lock.acquire(jf, agent="evolution", wait=10)
        if not token:
            print("FAILED: could not lock evolution.json", file=sys.stderr)
            sys.exit(1)
        try:
            data = load_or_exit(jf)
            data["records"].append({
                "variant": variant, "parent": parent, "change": change,
                "score": score,
                "verdict": verdict, "notes": notes,
                "ts": datetime.datetime.now().isoformat(timespec="seconds"),
            })
            _atomic_write_text(jf, json.dumps(data, indent=2))
            render_md(data, mf, run)
            b = best_of(data)  # computed inside the lock — no post-release re-read race
        finally:
            bb_lock.release(jf, agent="evolution", token=token)
        print(f"recorded {variant} (score={score}); best so far: "
              f"{b['variant']} ({b['score']})" if b else f"recorded {variant}")
        sys.exit(0)

    data = load_or_exit(jf)

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
