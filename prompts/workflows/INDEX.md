# Project OS workflows — universal index

<!-- GENERATED from prompts/workflows/ by scripts/sync_runtime_assets.py — edit the canonical file, not this one. -->

Every Project OS workflow lives here in runtime-neutral form.

- **Claude** exposes these as slash commands (`/kickoff`, `/status`, ...)
  via `addons/full-engine/staged/commands/`, which is generated from this
  directory.
- **Every other runtime** (Codex, Cursor, Antigravity, plain CLI) invokes a
  workflow by opening `prompts/workflows/<name>.md` and following it. There
  is no Claude-only step; the canonical file is the whole instruction.

| Workflow | Takes | What it does |
| --- | --- | --- |
| `adopt-project` | — | Adopt an existing codebase or docs folder into Project OS as a new run. |
| `board-review` | ['optional focus', 'e.g. "focus on privacy risk"'] | Run the board-of-directors review (5 director viewpoints + CFO cost packet) |
| `cost-check` | ['optional: balanced | cost-aware | max-effort'] | Estimate cost and set model routing (CFO) |
| `deliver` | — | Close out a run — delivery report, memory/graph wiring, cost actuals, lesson export, and a mechanical validation gate. |
| `evaluate` | <path or short description of what to evaluate> | Run the evaluator quality-gate loop on an artifact or packet |
| `kickoff` | <your rough idea> | Start a new Project OS project from a rough idea (CEO interviews you, picks tier + cost mode) |
| `memory-sync` | ['optional: a lesson/preference to remember'] | Update memory — refresh the GraphOS graph and store durable lessons in OSVec |
| `new-run` | — | Start a new, isolated project run cloned from the blackboard template. |
| `save-chat` | <approved summary to remember> | Save an approved chat summary, lesson, preference, or decision into the local Project OS shared brain. |
| `status` | — | Show the current Project OS state from the blackboard |
| `ui-review` | <path, artifact, or UI goal> | Review or plan a Project OS user interface with UI/UX, responsive layout, accessibility, and browser QA checks |
