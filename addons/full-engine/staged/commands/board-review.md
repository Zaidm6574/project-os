---
description: Run the board-of-directors review (5 director viewpoints + CFO cost packet)
argument-hint: [optional focus, e.g. "focus on privacy risk"]
allowed-tools: Read Write Grep Glob Task WebSearch
---

Run a board review for the current project. Optional focus: **$ARGUMENTS**

1. Launch the `board` subagent (Task tool). It produces five director packets — Strategy, Product, Technical, Risk/Privacy, User Advocate — into `blackboard/packets/`, adds concrete risks to `blackboard/04-risks.md`, and surfaces blocking unknowns into `06-open-questions.md`.
2. Launch the `project-os-cfo` subagent for the cost/model-routing packet.
3. As CEO, synthesize a **Board Summary**: the 2-3 weakest assumptions, what must be true to succeed, and a go / refine / stop recommendation. Put the synthesis in `blackboard/07-approved-plan.md` only after I approve it.
