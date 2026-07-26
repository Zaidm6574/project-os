---
name: kickoff
description: Start a new Project OS project from a rough idea (CEO interviews you, picks tier + cost mode)
argument-hint: <your rough idea>
---

<!-- GENERATED from prompts/workflows/kickoff.md by scripts/sync_runtime_assets.py — edit the canonical file, not this one. -->
Start a new Project OS run for this idea:

**$ARGUMENTS**

Act as the `project-os-ceo` (or delegate to it as a subagent if your runtime has them). Follow `AGENTS.md`:

1. Run the **Blackboard Read Gate** when this is an existing project: use `context-scout` on the smallest available model when subagents/model routing are available, or read the blackboard yourself. Include `Context Used`. Do not act from memory.
2. Interview me **one question at a time** until the goal is crisp. Don't build yet.
3. Write `blackboard/00-project-goal.md` (canonical goal, why, target user, Definition of Done, non-goals) and record the goal hash in `blackboard/21-agent-roster.md`.
4. Recommend the **smallest execution tier** that fits (Solo / Mini / Full) and a **cost mode** (default Balanced), with reasons.
5. If this is a website, web app, dashboard, mobile screen, game UI, form-heavy tool, or other visual artifact, add a UI lane note naming the `ui-ux-designer`, `frontend-builder`, `/ui-review`, responsive layout expectations, and browser QA route.
6. Then produce: `Context Used`, a project summary, the recommended tier, the proposed blackboard updates, an initial board-review plan (Full only), a GraphOS + OSVec memory setup note, a rough cost estimate, the UI lane note when relevant, and the critical questions to resolve before execution.

Stop and get my approval before launching any wave that spends money, publishes, deletes work, or sends messages.

## Capability note

This workflow uses `subagents`, `websearch`, `task-tracking`. If your runtime does not have them, do the work inline yourself — do **not** skip the step and do **not** refuse. Record the substitution in `blackboard/17-capability-preflight.md` so the gap is visible instead of silent.
