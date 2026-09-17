---
description: Run the board-of-directors review (5 director viewpoints + CFO cost packet)
argument-hint: [optional focus, e.g. "focus on privacy risk"]
allowed-tools: Read Write Grep Glob Task WebSearch Bash
---

<!-- GENERATED from prompts/workflows/board-review.md by scripts/sync_runtime_assets.py — edit the canonical file, not this one. -->

## Active workspace

Resolve `<run-root>` explicitly from the run argument or the caller's packet before reading or writing. For a named run it is `runs/<slug>/`; do not choose the newest run automatically. For project-level work without a named run, explicitly select `blackboard/`. All numbered notes and `packets/` paths below are relative to that selected root. Pass the same root to every delegated role and follow-up workflow. Shared `blackboard/` notes are read-only context during a named run; promote reviewed cross-run lessons separately. See **Active workspace and shared notes** in `AGENTS.md` for helper arguments and project-level exceptions.

Run a board review for the current project. Optional focus: **$ARGUMENTS**

1. Run the `board` role — launch it as a subagent if your runtime has them, otherwise adopt each director's viewpoint yourself in sequence. It produces five director packets — Strategy, Product, Technical, Risk/Privacy, User Advocate — into `<run-root>/packets/`, adds concrete risks to `<run-root>/04-risks.md`, and surfaces blocking unknowns into `06-open-questions.md`.
2. Run the `project-os-cfo` role (subagent if available, otherwise inline) for the cost/model-routing packet.
3. As CEO, synthesize a **Board Summary**: the 2-3 weakest assumptions, what must be true to succeed, and a go / refine / stop recommendation. Put the synthesis in `<run-root>/07-approved-plan.md` only after I approve it.

## Capability note

This workflow uses `subagents`, `websearch`. If your runtime does not have them, do the work inline yourself — do **not** skip the step and do **not** refuse. Record the substitution in `<run-root>/17-capability-preflight.md`, using the active root selected by this workflow, so the gap is visible instead of silent.
