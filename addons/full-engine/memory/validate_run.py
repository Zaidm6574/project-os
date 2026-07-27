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
     'no-packets: solo run' note is present. For a Full Swarm run that packet
     must be marked 'Status: Approved' -- the wave gate the CEO agent doc and
     new_run.py's solo waiver both promise.
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
import re
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
    """True only when a real `Locked:` field says so.

    This used to be `"locked" in low and "tier" in low`, which passes on any
    prose containing both words anywhere in the file -- "the door is locked"
    plus "beta tier" satisfied a run-closure gate. Read the field the roster
    actually defines (`Locked: yes`) instead of scanning for vocabulary
    (audit 2026-07-25).
    """
    if goal_text is None:
        return False
    # Collect EVERY Locked: field; do not return on the first. A goal doc that
    # records rejected options ("### Option B (rejected) / Locked: yes") ABOVE
    # the real "## Current Execution Level / Locked: no" made first-match-wins
    # report a locked tier that was not locked (adversarial verify 2026-07-25).
    values = []
    for line in goal_text.splitlines():
        m = re.match(r"\s*(?:[-*]\s*)?\**\s*Locked\s*\**\s*:\s*(.+?)\s*$", line, re.I)
        if m:
            values.append(m.group(1).strip().strip("*`").lower())
    if not values:
        return False
    affirmative = {"yes", "y", "true", "locked", "1"}
    # Fail closed: one non-affirmative value anywhere means the tier is not
    # cleanly locked, whatever another section of the same document claims.
    return all(v in affirmative for v in values)


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


TIER_FIELD = re.compile(r"\s*(?:[-*]\s*)?\**\s*Tier\s*\**\s*:\s*(.+?)\s*$", re.I)
STATUS_FIELD = re.compile(
    r"^\s*(?:[-*]\s*)?\**\s*Status\s*\**\s*:\s*(.+?)\s*$", re.I | re.M)


def _is_full_swarm(goal_text):
    """True when a `Tier:` field in the goal doc names Full Swarm.

    Fail closed the way `_tier_locked` does: the Approved-packet branch is the
    STRICTER one, so any Tier line naming Full Swarm selects it. A goal doc
    that still advertises Full Swarm in a rejected-option block is ambiguous,
    and refusing to close an ambiguous run is the safe direction.
    """
    if not goal_text:
        return False
    for line in goal_text.splitlines():
        m = TIER_FIELD.match(line)
        if m and re.search(r"\bfull\s*swarm\b", m.group(1), re.I):
            return True
    return False


def _packet_is_approved(text):
    """A packet is Approved only when its own `Status:` field says exactly so.

    The packet schema (blackboard-template/packets/README.md) ends every packet
    with `Status: Draft / Rejected / Approved`. Read that FIELD -- do not scan
    for the word. Substring matching is what broke `_tier_locked` ("locked"
    anywhere) and `_has_manifest` ("manifest" anywhere) before; here it would
    let `Recommended Next Step: get this approved` clear an evaluator gate.

    Every Status field in the packet must say approved, so a packet that an
    evaluator later downgraded ("## Re-review / Status: Rejected") does not
    keep its earlier approval on a first-match-wins read.
    """
    values = [m.group(1).strip().strip("*`").strip().lower()
              for m in STATUS_FIELD.finditer(text or "")]
    return bool(values) and all(v == "approved" for v in values)


def _has_packets(run_dir, goal_text=None):
    """Packets exist -- and for Full Swarm, at least one is Approved.

    `addons/full-engine/staged/agents/project-os-ceo.md` and the solo waiver
    text in `new_run.py` both state the same hard gate: a Full Swarm wave does
    not advance until >= 1 packet is marked `Status: Approved`, and "only
    Draft/Rejected packets means the wave is not done". Nothing enforced it --
    this check accepted ANY file under `packets/`, so a Full Swarm run whose
    only packet was explicitly `Status: Rejected` closed with VALIDATE: PASS
    (audit 2026-07-27). Other tiers keep the previous behaviour; doctrine
    scopes the Approved requirement to Full Swarm.
    """
    if goal_text is None:
        goal_text = _read(os.path.join(run_dir, "00-project-goal.md"))
    require_approved = _is_full_swarm(goal_text)
    pkt = os.path.join(run_dir, "packets")
    if os.path.isdir(pkt):
        for name in os.listdir(pkt):
            if name.startswith(".") or name == "README.md":
                continue
            if not require_approved:
                return True
            if _packet_is_approved(_read(os.path.join(pkt, name)) or ""):
                return True
    if require_approved:
        # The waiver is the obvious way around the gate, so it must not open
        # for the one tier the gate exists for: a Full Swarm run is by
        # definition not "a single-agent loop with no subagents".
        return False
    # explicit solo-run waiver in any run file
    for name in os.listdir(run_dir) if os.path.isdir(run_dir) else []:
        if name.endswith(".md"):
            t = _read(os.path.join(run_dir, name)) or ""
            if "no-packets: solo run" in t:
                return True
    return False


BULLET_ROW = re.compile(r"^\s*[-*]\s+\S")
TABLE_SEPARATOR = re.compile(r"^\s*\|[\s:|-]*\|?\s*$")


def _manifest_entry_count(body):
    """Count real manifest entries: bullets, and table rows past the header.

    A single regex was not enough. `| path | what |` followed by the alignment
    separator `| :--- | ---: |` and NO data rows still matched, because the
    HEADER row itself looks like a row -- an empty manifest passed the gate
    (adversarial verify 2026-07-25). Tables need explicit header/separator
    handling, so count instead of pattern-match.
    """
    entries = 0
    seen_table_header = False
    for line in body.splitlines():
        s = line.strip()
        if BULLET_ROW.match(s):
            entries += 1
            continue
        if not s.startswith("|"):
            continue
        if TABLE_SEPARATOR.match(s):
            continue
        if not seen_table_header:
            seen_table_header = True  # first non-separator table row is the header
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if any(c and c not in ("—", "-", "n/a", "tbd") for c in cells):
            entries += 1
    return entries


NO_MANIFEST = re.compile(
    r"\b(no|none|not|never|without|missing|absent|omitted|skipped|n/?a)\b"
    r"[^.\n]{0,40}\bmanifest\b"
    r"|\bmanifest\b[^.\n]{0,40}\b(was not|were not|isn't|is not|wasn't|not produced|"
    r"not written|omitted|skipped|deferred|pending|tbd|n/?a)\b",
    re.I,
)


def _has_manifest(run_dir):
    """A manifest must have entries -- not merely the word 'manifest'.

    This used to return True if any .md in the run contained the substring
    "manifest", so a delivery report reading "No artifact manifest was produced"
    CLOSED the run it should have blocked. Require the manifest file itself and
    at least one list/table row, and refuse when the text explicitly denies one
    (audit 2026-07-25).
    """
    for name in ("14-artifact-manifest.md", "13-delivery-report.md"):
        t = _read(os.path.join(run_dir, name))
        if not t or "manifest" not in t.lower():
            continue
        if NO_MANIFEST.search(t):
            continue
        body = t.lower().split("manifest", 1)[1]
        if _manifest_entry_count(body) > 0:
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
    packet_label = (
        "Approved packet present (Full Swarm wave gate)"
        if _is_full_swarm(goal_text)
        else "Packets present (or solo-run waiver)"
    )
    checks = [
        ("DoD has no remaining TBD", _dod_no_tbd(goal_text)),
        ("Tier line present and Locked", _tier_locked(goal_text)),
        ("Actuals populated (not placeholder)", _actuals_populated(cost_text)),
        (packet_label, _has_packets(run_dir, goal_text)),
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
    # The manifest needs a real ROW. This fixture used to be a bare table
    # header (`| path | what | where |` and nothing under it), which the old
    # substring check happily accepted -- the module's own example of a "good
    # run" modelled an EMPTY manifest as closable (audit 2026-07-25).
    _write(os.path.join(d, "13-delivery-report.md"),
           "## Artifact Manifest\n| path | what | where |\n|---|---|---|\n"
           "| site/index.html | landing page | runs/selftest/ |\n\n"
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

        # Full Swarm closes only on an APPROVED packet. Same otherwise-good run,
        # one field different, so the gate is proven to be the thing deciding.
        swarm = os.path.join(base, "swarm-project", "runs", "swarm")
        _good_run(swarm)
        _write(os.path.join(swarm, "00-project-goal.md"),
               "## Definition of Done\n- [x] Ship it\n\n"
               "## Current Execution Level\nTier: Full Swarm\nLocked: yes\n")
        _write(os.path.join(swarm, "packets", "3-builder-001.md"),
               "Packet ID: 3-builder-001\nStatus: Rejected\n")
        assert validate(swarm) is False, \
            "Full Swarm run with only a Rejected packet should FAIL"
        _write(os.path.join(swarm, "packets", "3-builder-001.md"),
               "Packet ID: 3-builder-001\nStatus: Approved\n")
        assert validate(swarm) is True, \
            "Full Swarm run with an Approved packet should PASS"

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
