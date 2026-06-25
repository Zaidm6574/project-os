#!/usr/bin/env python3
"""Mechanical end-of-run closure check for a run directory.

Verifies the invariants that make a run actually "done" and prints a checklist
plus a machine-readable summary line: 'VALIDATE: PASS' or 'VALIDATE: FAIL'.

Invariants:
  1. 00-project-goal.md Definition of Done has no remaining 'TBD'.
  2. A tier line marked Locked is present.
  3. 09-cost-estimate.md Actuals (between the ACTUALS markers) is populated,
     not the dashes-only placeholder.
  4. At least one packet exists under <run_dir>/packets/, OR an explicit
     'no-packets: solo run' note is present.
  5. An artifact manifest is present.
  6. A graph/memory artifact exists (real graphify-out/graph.json, shared-brain,
     or OSVec store at the project root) OR a run file points at one — proof
     the memory/graph layer actually fired at close.

Usage:
  python3 memory/validate_run.py <run_dir>
  python3 memory/validate_run.py --selftest

Standard library only. No network access.
"""
import argparse
import os
import sys

MARK_START = "<!-- ACTUALS:START -->"
MARK_END = "<!-- ACTUALS:END -->"


def _read(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def _dod_no_tbd(goal_text):
    """The Definition of Done block must contain no 'TBD'."""
    if goal_text is None:
        return False
    lines = goal_text.splitlines()
    in_dod = False
    saw_item = False
    for line in lines:
        s = line.strip()
        if s.startswith("## Definition of Done"):
            in_dod = True
            continue
        if in_dod and s.startswith("## "):
            break
        if in_dod and s.startswith("- ["):
            saw_item = True
            if "TBD" in s:
                return False
    return saw_item


def _tier_locked(goal_text):
    if goal_text is None:
        return False
    low = goal_text.lower()
    return "locked" in low and "tier" in low


def _actuals_populated(cost_text):
    if cost_text is None:
        return False
    if MARK_START not in cost_text or MARK_END not in cost_text:
        return False
    block = cost_text.split(MARK_START)[1].split(MARK_END)[0]
    for line in block.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        if s.lower().startswith("| model") or set(s) <= set("|-: "):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        # Measured column is index 2; populated means it has a number / $.
        if len(cells) >= 3 and cells[2] not in ("—", "", "-"):
            return True
    return False


def _has_packets(run_dir):
    pkt = os.path.join(run_dir, "packets")
    if os.path.isdir(pkt):
        for name in os.listdir(pkt):
            if not name.startswith(".") and name != "README.md":
                return True
    # explicit solo-run waiver in any run file
    for name in os.listdir(run_dir) if os.path.isdir(run_dir) else []:
        if name.endswith(".md"):
            t = _read(os.path.join(run_dir, name)) or ""
            if "no-packets: solo run" in t:
                return True
    return False


def _has_manifest(run_dir):
    candidates = ["13-delivery-report.md", "14-artifact-manifest.md"]
    if os.path.isdir(run_dir):
        candidates += [n for n in os.listdir(run_dir) if n.endswith(".md")]
    for name in candidates:
        t = _read(os.path.join(run_dir, name))
        if t and "manifest" in t.lower():
            return True
    return False


# Lowercased pointers that prove the memory/graph layer actually fired at close.
GRAPH_MEM_MARKERS = (
    "graphify-out/graph.json", "shared-brain", "osvec", "turbovec", ".tvim", "memory/store",
)


def _has_graph_or_memory(run_dir):
    """A graph/memory artifact must exist (real file) or be pointed to by a run file.

    Closure runs `build_graph.py` (GraphOS), `osvec_adapter.py`, and
    `brain/brain.py export`; this check refuses to call a run 'done' unless the
    memory/graph layer actually produced something.
    """
    # (a) a real artifact on disk at the project level (runs/<slug>/ -> project root)
    proj = os.path.dirname(os.path.dirname(os.path.abspath(run_dir)))
    candidates = [
        os.path.join(proj, "graphify-out", "graph.json"),
        os.path.join(proj, "brain", "shared-brain.jsonl"),
    ]
    store = os.path.join(proj, "memory", "store")
    if os.path.isdir(store):
        candidates += [os.path.join(store, n) for n in os.listdir(store)]
    for c in candidates:
        if os.path.exists(c):
            return True
    # (b) a run file points at a graph/memory artifact (e.g. the delivery report)
    if os.path.isdir(run_dir):
        for name in os.listdir(run_dir):
            if name.endswith(".md"):
                t = (_read(os.path.join(run_dir, name)) or "").lower()
                if any(m in t for m in GRAPH_MEM_MARKERS):
                    return True
    return False


def validate(run_dir):
    goal_text = _read(os.path.join(run_dir, "00-project-goal.md"))
    cost_text = _read(os.path.join(run_dir, "09-cost-estimate.md"))
    checks = [
        ("DoD has no remaining TBD", _dod_no_tbd(goal_text)),
        ("Tier line present and Locked", _tier_locked(goal_text)),
        ("Actuals populated (not placeholder)", _actuals_populated(cost_text)),
        ("Packets present (or solo-run waiver)", _has_packets(run_dir)),
        ("Artifact manifest present", _has_manifest(run_dir)),
        ("Graph/memory artifact present", _has_graph_or_memory(run_dir)),
    ]
    ok = all(passed for _, passed in checks)
    for label, passed in checks:
        print("  [%s] %s" % ("x" if passed else " ", label))
    print("VALIDATE: PASS" if ok else "VALIDATE: FAIL")
    return ok


def _write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _good_run(d):
    os.makedirs(os.path.join(d, "packets"))
    _write(os.path.join(d, "packets", "3-builder-001.md"), "Packet\n")
    _write(os.path.join(d, "00-project-goal.md"),
           "## Definition of Done\n- [x] Ship it\n\n"
           "## Current Execution Level\nTier: Solo (chosen by USER)\nLocked: yes\n")
    _write(os.path.join(d, "09-cost-estimate.md"),
           "## Actuals\n%s\n| Model | Est $ | Measured $ | Variance |\n"
           "|---|---|---|---|\n| main loop (opus) | — | $0.0175 | — |\n%s\n"
           % (MARK_START, MARK_END))
    _write(os.path.join(d, "13-delivery-report.md"),
           "## Artifact Manifest\n| path | what | where |\n\n"
           "Lessons exported to brain/shared-brain.jsonl; "
           "GraphOS rebuilt at graphify-out/graph.json.\n")


def selftest():
    import tempfile

    base = tempfile.mkdtemp()
    try:
        good = os.path.join(base, "good")
        _good_run(good)
        assert validate(good) is True, "good run should PASS"

        bad = os.path.join(base, "bad")
        _good_run(bad)
        # break the DoD invariant
        _write(os.path.join(bad, "00-project-goal.md"),
               "## Definition of Done\n- [ ] TBD\n\n"
               "## Current Execution Level\nTier: Solo\nLocked: yes\n")
        assert validate(bad) is False, "bad run should FAIL"

        # Otherwise-good run with NO graph/memory artifact and no pointer -> FAIL.
        nomem = os.path.join(base, "nomem")
        _good_run(nomem)
        _write(os.path.join(nomem, "13-delivery-report.md"),
               "## Artifact Manifest\n| path | what | where |\n")
        assert validate(nomem) is False, "run without graph/memory artifact should FAIL"
    finally:
        import shutil
        shutil.rmtree(base)
    print("validate_run selftest: OK")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Validate a run's closure invariants.")
    ap.add_argument("run_dir", nargs="?", help="path to runs/<slug>/")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(selftest())
    if not args.run_dir:
        ap.error("run_dir is required (or use --selftest)")
    ok = validate(args.run_dir)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
