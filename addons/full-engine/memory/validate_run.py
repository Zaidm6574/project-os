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
import decimal
import hashlib
import json
import os
import re
import stat
import sys

MARK_START = "<!-- ACTUALS:START -->"
MARK_END = "<!-- ACTUALS:END -->"
OSVEC_SIDECAR_SCHEMA = "osvec-sidecar/v2"
OSVEC_MANIFEST_SCHEMA = "osvec-manifest/v1"
OSVEC_VALID_TYPES = {
    "user-preference", "project-pattern", "research-finding",
    "decision", "risk", "agent-packet", "lesson",
}


def _read(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def _unfenced_lines(text):
    """Yield Markdown lines outside backtick/tilde fenced code blocks."""
    fence_char = None
    fence_len = 0
    for line in text.splitlines():
        stripped = line.lstrip()
        if fence_char is not None:
            if re.match(
                r"^%s{%d,}\s*$" % (re.escape(fence_char), fence_len),
                stripped,
            ):
                fence_char = None
                fence_len = 0
            continue
        match = re.match(r"^(`{3,}|~{3,})", stripped)
        if match:
            marker = match.group(1)
            fence_char = marker[0]
            fence_len = len(marker)
            continue
        yield line


def _dod_no_tbd(goal_text):
    """The Definition of Done must contain checkboxes and all must be checked."""
    if goal_text is None:
        return False
    in_dod = False
    saw_item = False
    for line in _unfenced_lines(goal_text):
        s = line.strip()
        if re.fullmatch(
            r"##[ \t]+definition of done(?:[ \t]+#+)?",
            s,
            flags=re.IGNORECASE,
        ):
            in_dod = True
            continue
        if in_dod and re.match(r"^#{1,2}[ \t]+", s):
            break
        match = re.match(r"^[-*+]\s+\[([^]]*)\]\s*(.*)$", s) if in_dod else None
        if match:
            saw_item = True
            item = match.group(2)
            if (
                match.group(1).casefold() != "x"
                or _placeholder_prefixed(item)
                or re.search(r"\b(?:tbd|todo)\b", item, flags=re.IGNORECASE)
            ):
                return False
    return saw_item


def _tier_locked(goal_text):
    if goal_text is None:
        return False
    tiers = []
    locks = []
    for line in _unfenced_lines(goal_text):
        stripped = line.strip()
        tier = re.fullmatch(
            r"(?:[-*+]\s*)?(?:\*\*)?\s*(?:chosen\s+)?tier\s*"
            r"(?:\*\*)?\s*:\s*(.+?)\s*",
            stripped,
            flags=re.IGNORECASE,
        )
        lock = re.fullmatch(
            r"(?:[-*+]\s*)?(?:\*\*)?\s*locked\s*(?:\*\*)?\s*"
            r":\s*(.+?)\s*",
            stripped,
            flags=re.IGNORECASE,
        )
        if tier:
            tiers.append(tier.group(1).casefold())
        elif lock:
            locks.append(lock.group(1).casefold())
    return (
        len(tiers) == 1
        and bool(re.fullmatch(
            r"(?:solo(?: agent loop)?|mini(?: swarm)?|full(?: swarm)?)(?:\s+\([^()\r\n]+\))?",
            tiers[0],
        ))
        and len(locks) == 1
        and locks[0] in ("yes", "true")
    )


def _actuals_populated(cost_text):
    if cost_text is None:
        return False
    evidence_text = "\n".join(_unfenced_lines(cost_text))
    if evidence_text.count(MARK_START) != 1 or evidence_text.count(MARK_END) != 1:
        return False
    start = evidence_text.find(MARK_START)
    end = evidence_text.find(MARK_END)
    if start >= end:
        return False
    block = evidence_text[start + len(MARK_START):end]
    for line in block.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        if s.lower().startswith("| model") or set(s) <= set("|-: "):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) >= 3 and _finite_nonnegative_amount(cells[2]):
            return True
    return False


def _finite_nonnegative_amount(value):
    text = value.strip()
    if text.startswith("**") and text.endswith("**") and len(text) >= 4:
        text = text[2:-2].strip()
    if text.startswith("$"):
        text = text[1:].strip()
    if not re.fullmatch(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", text):
        return False
    try:
        amount = decimal.Decimal(text.replace(",", ""))
    except decimal.InvalidOperation:
        return False
    return amount.is_finite() and amount >= 0


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
    for line in _unfenced_lines(goal_text):
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
    resolved_run = os.path.realpath(run_dir)
    if os.path.lexists(pkt):
        if (
            os.path.islink(pkt)
            or not os.path.isdir(pkt)
            or os.path.realpath(pkt) != os.path.join(resolved_run, "packets")
        ):
            return False
        try:
            with os.scandir(pkt) as entries:
                for entry in entries:
                    if (
                        entry.name.startswith(".")
                        or entry.name == "README.md"
                        or not entry.is_file(follow_symlinks=False)
                    ):
                        continue
                    if not require_approved:
                        return True
                    if _packet_is_approved(_read(entry.path) or ""):
                        return True
        except OSError:
            return False
    if require_approved:
        # The waiver is the obvious way around the gate, so it must not open
        # for the one tier the gate exists for: a Full Swarm run is by
        # definition not "a single-agent loop with no subagents".
        return False
    # explicit solo-run waiver in any run file
    try:
        entries = os.scandir(run_dir)
    except OSError:
        return False
    with entries:
        for entry in entries:
            if entry.name.endswith(".md") and entry.is_file(follow_symlinks=False):
                t = _read(entry.path) or ""
                if any("no-packets: solo run" in line for line in _unfenced_lines(t)):
                    return True
    return False


def _has_manifest(run_dir):
    for name in ("14-artifact-manifest.md", "13-delivery-report.md"):
        t = _read(os.path.join(run_dir, name))
        if not t:
            continue
        in_manifest = name == "14-artifact-manifest.md"
        manifest_level = None
        in_current = False
        for line in _unfenced_lines(t):
            stripped = line.strip()
            heading = re.match(r"^(#{1,6})\s+(.+)$", stripped)
            if not in_manifest:
                if heading and heading.group(2).strip().casefold() == "artifact manifest":
                    in_manifest = True
                    manifest_level = len(heading.group(1))
                continue
            if (
                manifest_level is not None
                and heading
                and len(heading.group(1)) <= manifest_level
                and heading.group(2).strip().casefold() != "artifact manifest"
            ):
                break
            if heading and heading.group(2).strip().casefold() == "current artifacts":
                in_current = True
                continue
            if in_current and heading:
                in_current = False
            if in_current:
                bullet = re.match(r"^[-*+]\s+(.+)$", stripped)
                if bullet and _substantive_manifest_value(bullet.group(1)):
                    return True
            bullet = re.match(r"^[-*+]\s+(.+)$", stripped)
            if (bullet and _substantive_manifest_value(bullet.group(1))
                    and not _manifest_denial(bullet.group(1))):
                return True
            if not stripped.startswith("|") or set(stripped) <= set("|-: "):
                continue
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if len(cells) >= 3 and cells[2].casefold() == "current":
                if _substantive_manifest_value(cells[0], reject_header=True):
                    return True
    return False


def _substantive_manifest_value(value, reject_header=False):
    text = value.strip()
    checkbox = re.match(r"^\[([^]]*)\]\s*(.*)$", text)
    if checkbox:
        if checkbox.group(1).casefold() != "x":
            return False
        text = checkbox.group(2)
    normalized = text.strip().strip("`*_ ")
    if reject_header and normalized.casefold() in {
        "artifact", "artifacts", "file", "name", "output", "path"
    }:
        return False
    return not _placeholder_prefixed(normalized)


def _manifest_denial(value):
    text = value.casefold()
    return bool(re.search(
        r"\b(?:no|none|not|never|without|missing|absent|omitted|skipped)\b"
        r"[^.\n]{0,40}\bmanifest\b"
        r"|\bmanifest\b[^.\n]{0,40}\b(?:was not|were not|isn't|is not|"
        r"wasn't|not produced|not written|omitted|skipped|deferred|pending|tbd)\b",
        text,
    ))


def _placeholder_prefixed(value):
    text = value.strip().strip("`*_ ").casefold()
    if not text:
        return True
    if re.match(r"^\(?(?:tbd|todo|n\s*/\s*a|none|pending)\)?(?:$|[\s:;,.!?—-])", text):
        return True
    return bool(re.match(r"^[-—_.?]+(?:$|\s+)", text))


def _loop_closeout_complete(text):
    """Require a substantive, explicitly CLOSED lifecycle receipt."""
    if text is None:
        return False
    sections = {}
    section_counts = {}
    current = None
    preamble = []
    for line in _unfenced_lines(text):
        heading = re.match(r"^##\s+(.+?)\s*$", line.strip())
        if heading:
            current = heading.group(1).strip().casefold()
            section_counts[current] = section_counts.get(current, 0) + 1
            sections.setdefault(current, [])
        elif current is None:
            preamble.append(line)
        else:
            sections[current].append(line)

    metadata = {"loop id": [], "date": [], "owner": [], "agent": []}
    for line in preamble:
        match = re.match(
            r"^(Loop ID|Date|Owner|Agent):\s*(.*?)\s*$",
            line.strip(), flags=re.IGNORECASE,
        )
        if match:
            metadata[match.group(1).casefold()].append(
                match.group(2).strip().strip("`*_ ")
            )
    if any(
        len(values) != 1 or _placeholder_prefixed(values[0])
        or re.search(r"\b(?:tbd|todo|yyyy|__+)\b", values[0], flags=re.IGNORECASE)
        for values in metadata.values()
    ):
        return False

    required = (
        "objective", "scope proof", "source packet", "verified evidence",
        "completed", "open work", "blocker / dependency", "next action",
        "disposition", "close condition", "memory harvest", "external effects",
        "final artifact links", "updated",
    )
    if any(name not in sections or section_counts.get(name) != 1 for name in required):
        return False
    for name in required:
        body = "\n".join(sections[name]).strip()
        if not body or re.search(r"\b(?:tbd|todo)\b", body, flags=re.IGNORECASE):
            return False
    artifact_lines = [
        line.strip() for line in sections["final artifact links"] if line.strip()
    ]
    if not artifact_lines or any(
        _placeholder_prefixed(re.sub(r"^[-*+]\s+", "", line))
        for line in artifact_lines
    ):
        return False
    dispositions = [
        line.strip().strip("`*_ ").upper()
        for line in sections["disposition"] if line.strip()
    ]
    return dispositions == ["CLOSED"]


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
        ("Loop closeout receipt complete", _loop_closeout_complete(
            _read(os.path.join(run_dir, "23-loop-closeout.md"))
        )),
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
    _write(os.path.join(d, "14-artifact-manifest.md"),
           "# Artifact Manifest\n\n"
           "| Artifact | Type | Status | Owner | Verification | Notes |\n"
           "|---|---|---|---|---|---|\n"
           "| outputs/selftest.txt | Text | Current | selftest | read | verified |\n")
    _write(os.path.join(d, "23-loop-closeout.md"),
           "# Project OS Loop Closeout\n\nLoop ID: `L-selftest`\n"
           "Date: 2026-07-29\nOwner: selftest\nAgent: validate-run\n\n"
           "## Objective\nVerify closure.\n\n## Scope Proof\nScratch scope.\n\n"
           "## Source Packet\nSelftest fixture.\n\n## Verified Evidence\n- Selftest.\n\n"
           "## Completed\n- Validation.\n\n## Open Work\n- None.\n\n"
           "## Blocker / Dependency\nNone.\n\n## Next Action\nRetain receipt.\n\n"
           "## Disposition\nCLOSED\n\n## Close Condition\nChecks passed.\n\n"
           "## Memory Harvest\nReviewed; no promotion.\n\n"
           "## External Effects\nNone.\n\n"
           "## Final Artifact Links\n- outputs/selftest.txt\n\n"
           "## Updated\n2026-07-29 by selftest.\n")
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
