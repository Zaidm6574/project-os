---
name: deliver
description: Close out a run — delivery report, memory/graph wiring, cost actuals, lesson export, and a mechanical validation gate.
---

# /deliver <run_dir>

Run this at the end of a run to make closure **real**. Closure is not "I think we're done" — it is **memory actually firing** (brain + OSVec + GraphOS) and then a mechanical gate passing. Do not declare a run done until `memory/validate_run.py` prints `VALIDATE: PASS`.

## What to do (in order)

1. **Fill the delivery report.** Complete `runs/<slug>/13-delivery-report.md`:
   the Artifact Manifest (path | what it is | where), the estimate-vs-actual cost
   pointer, and the lessons-exported pointer (point it at `brain/shared-brain.jsonl`
   and `graphify-out/graph.json` so the graph/memory check can see them).

2. **Export lessons to the shared brain.** Run:

```bash
python3 brain/brain.py export
```

   so durable lessons reach `brain/shared-brain.jsonl` for the tool-to-tool bridge.

3. **Store the run's lesson in OSVec** (semantic recall for "have we seen this before?"). Run:

```bash
python3 memory/osvec_adapter.py add --type lesson --source project-os \
  --run-slug <slug> --tags "run,<slug>" \
  --text "<one durable lesson from this run>"
```

4. **Rebuild the GraphOS graph** for this run so relationships are current:

```bash
python3 memory/build_graph.py --root runs/<slug>
```

5. **Record cost actuals.** Run:

```bash
python3 memory/cost_actuals.py --write --target runs/<slug>/09-cost-estimate.md
```

   This parses the session JSONL and patches the Actuals markers with measured $ per model vs estimate. The orchestrator (main-loop Opus) cost is summed into its own row, kept separate from the subagent rows.

6. **Browser QA (optional, for web artifacts).** If the run produced `index.html` or other HTML deliverables, run:

```bash
python3 memory/browser_qa.py runs/<slug>/
# or: python3 memory/browser_qa.py path/to/index.html
```

   Checks local `href`/`src` references resolve. Prints `QA: PASS` / `QA: FAIL`. Fix broken links before validating.

7. **Validate the run.** Run:

```bash
python3 memory/validate_run.py runs/<slug>
```

   It checks the closure invariants (DoD has no TBD, tier Locked, Actuals populated, packets present or a solo-run waiver, manifest present, **and a graph/memory artifact present**) and prints `VALIDATE: PASS` / `VALIDATE: FAIL`.

8. **Only declare the run done on `VALIDATE: PASS`.** If it prints `VALIDATE: FAIL`, fix the flagged item and re-run step 7.

## Notes

- Run the steps **in order**: export → OSVec → GraphOS → cost actuals → validate. Memory must fire (steps 2–4) before the gate (step 6), because the gate now checks that a graph/memory artifact exists.
- Referenced tools (`brain/brain.py`, `memory/osvec_adapter.py`, `memory/build_graph.py`, `memory/cost_actuals.py`, `memory/validate_run.py`) are owned elsewhere — this command only orchestrates them.
- All writes target `runs/<slug>/` (plus the project-level `brain/`, `graphify-out/`, and `memory/store/` that the memory tools own); `blackboard/` stays a read-only template.
