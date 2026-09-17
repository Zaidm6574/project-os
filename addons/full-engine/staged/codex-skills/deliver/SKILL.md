---
name: deliver
description: Close out a run with evidence, delivery notes, attributed costs, reviewed memory, and structural validation.
---

<!-- GENERATED from prompts/workflows/deliver.md by scripts/sync_runtime_assets.py — edit the canonical file, not this one. -->
# deliver <run_dir>

## Active workspace

Resolve `<run-root>` explicitly from the run argument or the caller's packet before reading or writing. For a named run it is `runs/<slug>/`; do not choose the newest run automatically. For project-level work without a named run, explicitly select `blackboard/`. All numbered notes and `packets/` paths below are relative to that selected root. Pass the same root to every delegated role and follow-up workflow. Shared `blackboard/` notes are read-only context during a named run; promote reviewed cross-run lessons separately. See **Active workspace and shared notes** in `AGENTS.md` for helper arguments and project-level exceptions.

Use the explicitly supplied run directory, for example `run_root="runs/example-run"` and `run_slug="example-run"`. Keep commands at the project root. Full-engine helpers below are conditional on installation and dependencies; do not claim an unavailable tool ran.

## Read and verify the work

Read the run goal, approved plan, decisions, risks, artifact manifest, evaluation log and latest packets. Include `Context Used`. Before closing, attempt the user's primary task on the real artifact, check the Definition of Done and record evaluator evidence in `<run-root>/12-evaluation-log.md`. A file or a structural validator PASS does not prove the task succeeded.

## Closeout sequence

1. **Document the result.** Fill `<run-root>/13-delivery-report.md` and `14-artifact-manifest.md` with current artifact paths, direct verification evidence, limitations, cost source and memory disposition. Do not add pointers to stores that were not used merely to satisfy validation.

2. **Review and persist the current lesson before exporting.** Fill `<run-root>/19-memory-harvest.md`, distinguishing approved lessons, rejected candidates and next-kickoff safeguards. For an installed OSVec + brain workflow, persist the approved lesson first:

```bash
python3 memory/osvec_adapter.py add --type lesson --source project-os \
  --id closeout-lesson --run-slug "$run_slug" --tags "run,$run_slug" \
  --text "<reviewed durable lesson from this run>"
python3 brain/brain.py export
```

Verify the new record's ID and approved text at the **resolved configured brain destination**, and record that ID/path in the harvest note. Do not assume `brain/shared-brain.jsonl`: a project binding or explicit environment override can select another store. Inspect the configured destination before export; cross-project or external storage needs authorization for that scope. Export reads already persisted lesson records and can include older lessons: an export count alone is not evidence the current lesson arrived. OSVec's default representation is lexical, not neural semantic search. Do not create a dummy lesson just to pass a gate.

For a reviewed Markdown harvest instead, use `python3 scripts/harvest.py scan "$run_slug"`, inspect the emitted proposal file and destination, then run `python3 scripts/harvest.py apply <reviewed-proposals.jsonl>` only for approved content. This is a separate project-level memory promotion; proposals use the helper's shared packet staging path. Check the resulting lesson IDs at the resolved store and record them. Do not execute both routes blindly. If there is no reusable lesson or storage is unavailable, record that disposition honestly; a required gate that remains unsatisfied stays open.

3. **Rebuild the graph if used.** `python3 memory/build_graph.py --root "$run_root"` updates the project-level `graphify-out/` derived view from this run. Record the source root and result. A graph existing from another run is not evidence of current-run memory activity.

4. **Record attributed cost actuals.** Choose the actual runtime and identify input files, project/run scope, time window, pricing date and confidence before collection. The destination does not select the source.

For Claude with a transcript known to belong to this run:

```bash
python3 memory/cost_actuals.py --transcript /path/to/this-run.jsonl \
  --write --target "$run_root/09-cost-estimate.md"
```

Do not omit `--transcript`: automatic newest-file selection can select another project. Record which associated subagent files were included and any pricing gaps; these are log-derived costs, not a provider invoice.

For Codex local activity with an explicitly selected directory containing only the intended run's session logs:

```bash
python3 memory/cost_actuals.py --codex-sessions --sessions-dir /path/to/run-only-sessions
```

This mode reads every discovered JSONL below that directory, prints token activity, and does **not** implement project/time filtering, event deduplication, dollar pricing or the `--write`/`--target` report writer. Review completeness diagnostics and record attributable totals manually in `<run-root>/09-cost-estimate.md`. Do not silently use the entire global sessions directory as run cost. If run-only inputs cannot be identified, say `Unmeasured` with the reason; do not turn unavailable dollars into zero. Never copy raw logs into run notes.

5. **Check UI deliverables when applicable.** `python3 memory/browser_qa.py <artifact-path>` checks selected local HTML `href`/`src` references only. Use actual browser/manual checks for layout, accessibility and interactions; record the evidence or the missing capability.

6. **Fill `<run-root>/23-loop-closeout.md` explicitly.** Complete objective, scope proof, source packet, verified evidence, completed/open work, dependencies, next action, close condition, memory harvest, external effects and final artifact links. Choose exactly one disposition. Use `CLOSED` only when the agreed close condition and actual user-task check are satisfied; otherwise use the accurate open/blocked/paused disposition.

7. **Run structural validation when installed.**

```bash
python3 memory/validate_run.py "$run_root"
```

`VALIDATE: PASS` checks declared closeout structure, including goal/tier fields, cost notes, packets or solo waiver, manifest, the closeout receipt, and a qualifying graph/memory artifact. It does not execute evaluator evidence, authenticate approval, verify every artifact, require all three memory systems or bind an old memory artifact to this run's lesson. Fix structural failures without inventing work or evidence. Passing this gate is necessary for the full-engine closeout workflow, but is not sufficient to call the user task complete.

8. **Deliver a truthful receipt.** Report completed work, actual verification, current artifact links, cost uncertainty, memory disposition and open ownership. If full-engine validation is unavailable, label it unavailable and report manual checks without claiming `VALIDATE: PASS`.

## Capability note

This workflow uses `subagents`. If your runtime does not have them, do the work inline yourself — do **not** skip the step and do **not** refuse. Record the substitution in `<run-root>/17-capability-preflight.md`, using the active root selected by this workflow, so the gap is visible instead of silent.
