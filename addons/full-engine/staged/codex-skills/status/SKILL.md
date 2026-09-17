---
name: status
description: Show the current Project OS state from the blackboard
---

<!-- GENERATED from prompts/workflows/status.md by scripts/sync_runtime_assets.py — edit the canonical file, not this one. -->

## Active workspace

Resolve `<run-root>` explicitly from the run argument or the caller's packet before reading or writing. For a named run it is `runs/<slug>/`; do not choose the newest run automatically. For project-level work without a named run, explicitly select `blackboard/`. All numbered notes and `packets/` paths below are relative to that selected root. Pass the same root to every delegated role and follow-up workflow. Shared `blackboard/` notes are read-only context during a named run; promote reviewed cross-run lessons separately. See **Active workspace and shared notes** in `AGENTS.md` for helper arguments and project-level exceptions.

Give me a concise status read of the selected active root. Do not change anything. For this read-only workflow, report any capability substitution in the response; a later authorized write pass can record it in the run notes.

## Blackboard Read Gate

Do not act from memory. Read the files below and include a `Context Used` line that names them. If the folder has a lot of packets, use `context-scout` on the smallest available model to summarize the relevant ones.

Read and summarize:
- `<run-root>/00-project-goal.md` — the canonical goal, current tier, cost mode, phase.
- `<run-root>/07-approved-plan.md` — active wave, next actions, pending human checkpoints.
- `<run-root>/06-open-questions.md` — any **blocking** questions.
- `<run-root>/21-agent-roster.md` — goal-drift check + last wave, when the full-engine roster exists; otherwise state that no roster check is available.
- `<run-root>/12-evaluation-log.md` — last evaluation verdict.

Then: in 3-5 sentences, where the project stands and the single best next step. Flag any goal drift.

## Capability note

This workflow uses `subagents`. If your runtime does not have them, do the work inline yourself — do **not** skip the step and do **not** refuse. Record the substitution in `<run-root>/17-capability-preflight.md`, using the active root selected by this workflow, so the gap is visible instead of silent.
