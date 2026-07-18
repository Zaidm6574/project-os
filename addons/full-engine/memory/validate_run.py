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
  6. A non-empty, machine-readable graph/memory artifact exists at the project
     root — proof the memory/graph layer actually fired at close.

Usage:
  python3 memory/validate_run.py <run_dir>
  python3 memory/validate_run.py --selftest

Standard library only. No network access.
"""
import argparse
import json
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


def _nonempty_string(value):
    return isinstance(value, str) and bool(value.strip())


def _valid_jsonl_record(record):
    return (
        isinstance(record, dict)
        and all(_nonempty_string(record.get(field)) for field in ("id", "type", "text"))
    )


def _valid_osvec_record(record):
    return (
        isinstance(record, dict)
        and all(
            _nonempty_string(record.get(field))
            for field in ("memory_id", "text", "memory_type")
        )
        and isinstance(record.get("u64_id"), int)
        and not isinstance(record.get("u64_id"), bool)
    )


def _has_graph_or_memory(run_dir):
    """A graph/memory artifact must contain real machine-readable evidence.

    Closure runs `build_graph.py` (GraphOS), `osvec_adapter.py`, and
    `brain/brain.py export`; this check refuses to call a run 'done' unless the
    memory/graph layer actually produced something.
    """
    # (a) a real artifact on disk at the project level (runs/<slug>/ -> project root)
    proj = os.path.dirname(os.path.dirname(os.path.abspath(run_dir)))
    graph_path = os.path.join(proj, "graphify-out", "graph.json")
    brain_path = os.path.join(proj, "brain", "shared-brain.jsonl")
    if os.path.isfile(graph_path):
        try:
            with open(graph_path, "r", encoding="utf-8") as fh:
                graph = json.load(fh)
            nodes = graph.get("nodes") if isinstance(graph, dict) else None
            if (
                isinstance(nodes, list)
                and nodes
                and all(
                    isinstance(node, dict) and _nonempty_string(node.get("id"))
                    for node in nodes
                )
            ):
                return True
        except (OSError, ValueError):
            pass
    if os.path.isfile(brain_path):
        try:
            saw_record = False
            with open(brain_path, "r", encoding="utf-8") as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    record = json.loads(line)
                    if not _valid_jsonl_record(record):
                        break
                    saw_record = True
                else:
                    if saw_record:
                        return True
        except (OSError, ValueError):
            pass

    # OSVec's JSON sidecar is the durable source of record metadata. The
    # adapter can rebuild its .tvim or .tvim.npz index from this data, so the
    # binary index alone is not sufficient evidence of populated memory.
    store = os.path.join(proj, "memory", "store")
    if os.path.isdir(store):
        for name in os.listdir(store):
            if not name.endswith(".sidecar.json"):
                continue
            try:
                with open(os.path.join(store, name), "r", encoding="utf-8") as fh:
                    sidecar = json.load(fh)
            except (OSError, ValueError):
                continue
            records = sidecar.get("records") if isinstance(sidecar, dict) else None
            if not isinstance(records, dict):
                continue
            if records and all(_valid_osvec_record(record) for record in records.values()):
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


def _good_run(d, with_memory=True):
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
    if with_memory:
        project = os.path.dirname(os.path.dirname(os.path.abspath(d)))
        brain = os.path.join(project, "brain")
        os.makedirs(brain, exist_ok=True)
        _write(
            os.path.join(brain, "shared-brain.jsonl"),
            '{"id":"selftest-lesson","type":"lesson","text":"Verified selftest lesson"}\n',
        )


def selftest():
    import tempfile

    base = tempfile.mkdtemp()
    try:
        good = os.path.join(base, "good-project", "runs", "good")
        _good_run(good)
        assert validate(good) is True, "good run should PASS"

        bad = os.path.join(base, "bad-project", "runs", "bad")
        _good_run(bad)
        # break the DoD invariant
        _write(os.path.join(bad, "00-project-goal.md"),
               "## Definition of Done\n- [ ] TBD\n\n"
               "## Current Execution Level\nTier: Solo\nLocked: yes\n")
        assert validate(bad) is False, "bad run should FAIL"

        # Otherwise-good run with NO graph/memory artifact and no pointer -> FAIL.
        nomem = os.path.join(base, "nomem-project", "runs", "nomem")
        _good_run(nomem, with_memory=False)
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
