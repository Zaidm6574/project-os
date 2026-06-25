---
description: Show the current Project OS state from the blackboard
allowed-tools: Read Grep Glob
---

Give me a concise status read of the current project from the blackboard. Do not change anything.

Read and summarize:
- `blackboard/00-project-goal.md` — the canonical goal, current tier, cost mode, phase.
- `blackboard/07-approved-plan.md` — active wave, next actions, pending human checkpoints.
- `blackboard/06-open-questions.md` — any **blocking** questions.
- `blackboard/21-agent-roster.md` — goal-drift check + last wave.
- `blackboard/12-evaluation-log.md` — last evaluation verdict.

Then: in 3-5 sentences, where the project stands and the single best next step. Flag any goal drift.
