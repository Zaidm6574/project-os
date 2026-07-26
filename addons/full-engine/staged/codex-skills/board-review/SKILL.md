---
name: board-review
description: Run the board-of-directors review (5 director viewpoints + CFO cost packet)
argument-hint: [optional focus, e.g. "focus on privacy risk"]
---

<!-- GENERATED from prompts/workflows/board-review.md by scripts/sync_runtime_assets.py — edit the canonical file, not this one. -->
Run a board review for the current project. Optional focus: **$ARGUMENTS**

1. Run the `board` role — launch it as a subagent if your runtime has them, otherwise adopt each director's viewpoint yourself in sequence. It produces five director packets — Strategy, Product, Technical, Risk/Privacy, User Advocate — into `blackboard/packets/`, adds concrete risks to `blackboard/04-risks.md`, and surfaces blocking unknowns into `06-open-questions.md`.
2. Run the `project-os-cfo` role (subagent if available, otherwise inline) for the cost/model-routing packet.
3. As CEO, synthesize a **Board Summary**: the 2-3 weakest assumptions, what must be true to succeed, and a go / refine / stop recommendation. Put the synthesis in `blackboard/07-approved-plan.md` only after I approve it.

## Capability note

This workflow uses `subagents`, `websearch`. If your runtime does not have them, do the work inline yourself — do **not** skip the step and do **not** refuse. Record the substitution in `blackboard/17-capability-preflight.md` so the gap is visible instead of silent.
