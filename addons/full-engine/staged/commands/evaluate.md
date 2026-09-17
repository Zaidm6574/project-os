---
description: Run the evaluator quality-gate loop on an artifact or packet
argument-hint: <path or short description of what to evaluate>
allowed-tools: Bash Read Grep Glob Task Write
---

<!-- GENERATED from prompts/workflows/evaluate.md by scripts/sync_runtime_assets.py — edit the canonical file, not this one. -->

## Active workspace

Resolve `<run-root>` explicitly from the run argument or the caller's packet before reading or writing. For a named run it is `runs/<slug>/`; do not choose the newest run automatically. For project-level work without a named run, explicitly select `blackboard/`. All numbered notes and `packets/` paths below are relative to that selected root. Pass the same root to every delegated role and follow-up workflow. Shared `blackboard/` notes are read-only context during a named run; promote reviewed cross-run lessons separately. See **Active workspace and shared notes** in `AGENTS.md` for helper arguments and project-level exceptions.

Evaluate this artifact/packet against the rubric: **$ARGUMENTS**

Run the `evaluator` role — launch it as a subagent if your runtime has them, otherwise adopt the role yourself in a separate pass, judging the artifact as written rather than as remembered. Create or confirm a task-specific weighted rubric in `<run-root>/12-evaluation-log.md` from the Definition of Done in `<run-root>/00-project-goal.md`; the starter evaluation log does not supply one. Record the criteria and evidence, then use the evaluator scorer when installed. The host follows these review and retry rules; no background retry engine runs them:
- Pass = weighted >= 0.80 and no criterion < 0.50 -> mark Approved.
- Otherwise Reject with precise feedback and an explicit **strategy change** for the next attempt.
- Max 3 iterations, then abort and ask me.

Log the result (date, artifact, iteration, score, verdict, feedback, strategy change) to `<run-root>/12-evaluation-log.md` and tell me the verdict.

## Capability note

This workflow uses `subagents`. If your runtime does not have them, do the work inline yourself — do **not** skip the step and do **not** refuse. Record the substitution in `<run-root>/17-capability-preflight.md`, using the active root selected by this workflow, so the gap is visible instead of silent.
