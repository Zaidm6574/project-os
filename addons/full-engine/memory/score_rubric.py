#!/usr/bin/env python3
"""Deterministic rubric scorer for the Project OS evaluator.

Reads JSON on stdin:
  {"criteria":[{"name":..,"score":0-1,"weight":..}, ...],
   "artifact_type":"executable|doc",
   "passk":"3/3"        (required for executable, N/A for doc),
   "redteam":"...",     (required when verdict would be Reject),
   "strategy_change":"..."}

Gate: weighted total >= 0.80 AND no single criterion < 0.50 -> Pass, else Reject.
The UNROUNDED weighted total is authoritative.

Refusal rules (printed as the verdict, nonzero exit):
  - Reject + (redteam or strategy_change missing) -> exit 2.
  - artifact_type==executable + passk missing      -> exit 3.

No network, stdlib only.
"""
import json
import sys

PASS_TOTAL = 0.80
MIN_CRITERION = 0.50
WEIGHT_TOL = 1e-6


def score(payload):
    """Return (verdict, total, row_or_msg, exit_code)."""
    criteria = payload.get("criteria") or []
    if not criteria:
        return ("Reject", 0.0, "no criteria supplied", 2)

    weights = sum(float(c["weight"]) for c in criteria)
    assert abs(weights - 1.0) <= WEIGHT_TOL, (
        "weights must sum to ~1.0, got %r" % weights
    )

    # UNROUNDED weighted total.
    total = sum(float(c["score"]) * float(c["weight"]) for c in criteria)
    min_score = min(float(c["score"]) for c in criteria)

    passes = (total >= PASS_TOTAL) and (min_score >= MIN_CRITERION)
    verdict = "Pass" if passes else "Reject"

    artifact_type = payload.get("artifact_type", "doc")
    passk = payload.get("passk")
    redteam = (payload.get("redteam") or "").strip()
    strategy_change = (payload.get("strategy_change") or "").strip()

    # Refusal: executable artifacts must carry a k/pass-count.
    if artifact_type == "executable" and not passk:
        return (
            verdict,
            total,
            "executable artifact requires k/pass-count",
            3,
        )

    # Refusal: a Reject must carry the red-team triple + a strategy change.
    if verdict == "Reject" and (not redteam or not strategy_change):
        return (
            verdict,
            total,
            "REJECT requires red-team triple + strategy change",
            2,
        )

    passk_cell = passk if artifact_type == "executable" else "N/A"
    row = "| %s | %s | %s | %s | %s |" % (
        verdict,
        total,
        passk_cell,
        redteam or "—",
        strategy_change or "—",
    )
    return (verdict, total, row, 0)


def selftest():
    # Pass case (doc; passk N/A).
    v, total, row, code = score({
        "criteria": [
            {"name": "a", "score": 0.9, "weight": 0.5},
            {"name": "b", "score": 0.9, "weight": 0.5},
        ],
        "artifact_type": "doc",
    })
    assert v == "Pass", v
    assert abs(total - 0.9) < 1e-9, total
    assert code == 0, code

    # Reject missing red-team -> exit 2.
    v, total, msg, code = score({
        "criteria": [
            {"name": "a", "score": 0.2, "weight": 0.5},
            {"name": "b", "score": 0.2, "weight": 0.5},
        ],
        "artifact_type": "doc",
    })
    assert v == "Reject", v
    assert code == 2, code
    assert "red-team" in msg.lower(), msg

    # Executable missing passk -> exit 3.
    v, total, msg, code = score({
        "criteria": [
            {"name": "a", "score": 0.9, "weight": 0.5},
            {"name": "b", "score": 0.9, "weight": 0.5},
        ],
        "artifact_type": "executable",
    })
    assert code == 3, code
    assert "pass" in msg.lower(), msg

    # Executable WITH passk passes.
    v, total, row, code = score({
        "criteria": [
            {"name": "a", "score": 0.9, "weight": 0.5},
            {"name": "b", "score": 0.9, "weight": 0.5},
        ],
        "artifact_type": "executable",
        "passk": "3/3",
    })
    assert v == "Pass" and code == 0, (v, code)
    assert "3/3" in row, row

    # Reject WITH red-team + strategy change emits a row, exit 0.
    v, total, row, code = score({
        "criteria": [
            {"name": "a", "score": 0.3, "weight": 0.5},
            {"name": "b", "score": 0.3, "weight": 0.5},
        ],
        "artifact_type": "doc",
        "redteam": "Claim X fails if Y. Test: Z. Kill: <0.5.",
        "strategy_change": "Switch approach to W.",
    })
    assert v == "Reject" and code == 0, (v, code)

    print("score_rubric selftest: OK")
    return 0


def main():
    if "--selftest" in sys.argv[1:]:
        sys.exit(selftest())

    raw = sys.stdin.read()
    payload = json.loads(raw)
    verdict, total, row, code = score(payload)

    print("Verdict: %s" % verdict)
    print("Weighted total (unrounded): %r" % total)
    if code != 0:
        print("REFUSAL: %s" % row)
    else:
        print("Log row:")
        print(row)
    sys.exit(code)


if __name__ == "__main__":
    main()
