---
name: status
description: Show the current Project OS state from the blackboard
---

<!-- GENERATED from prompts/workflows/status.md by scripts/sync_runtime_assets.py — edit the canonical file, not this one. -->
Give me a concise status read of the current project from the blackboard. Do not change anything.

## Blackboard Read Gate

Do not act from memory. Read the files below and include a `Context Used` line that names them. If the folder has a lot of packets, use `context-scout` on the smallest available model to summarize the relevant ones.

Read and summarize:
- `blackboard/00-project-goal.md` — the canonical goal, current tier, cost mode, phase.
- `blackboard/07-approved-plan.md` — active wave, next actions, pending human checkpoints.
- `blackboard/06-open-questions.md` — any **blocking** questions.
- `blackboard/21-agent-roster.md` — goal-drift check + last wave.
- `blackboard/12-evaluation-log.md` — last evaluation verdict.

Then: in 3-5 sentences, where the project stands and the single best next step. Flag any goal drift.

## Capability note

This workflow uses `subagents`. If your runtime does not have them, do the work inline yourself — do **not** skip the step and do **not** refuse. Record the substitution in `blackboard/17-capability-preflight.md` so the gap is visible instead of silent.
