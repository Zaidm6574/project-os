---
name: new-run
description: Start a new, isolated project run cloned from the blackboard template.
capabilities: []
---
# new-run <slug> [--tier solo|mini|full]

## Active workspace

Resolve `<run-root>` explicitly from the run argument or the caller's packet before reading or writing. For a named run it is `runs/<slug>/`; do not choose the newest run automatically. For project-level work without a named run, explicitly select `blackboard/`. All numbered notes and `packets/` paths below are relative to that selected root. Pass the same root to every delegated role and follow-up workflow. Shared `blackboard/` notes are read-only context during a named run; promote reviewed cross-run lessons separately. See **Active workspace and shared notes** in `AGENTS.md` for helper arguments and project-level exceptions.

Create an isolated run and select it as the active root. The installed `blackboard/` contains project-shared context and template source notes; it stays read-only during named-run work.

This workflow requires `memory/new_run.py`. A source clone includes that helper; a plain starter installation stages the add-on sources but does not activate it. In an installed project, first activate the full engine with `python3 scripts/install_full_engine.py --target .` (or use `--full-engine` during initial installation). Host adapter flags also activate the full engine. Do not claim a run was created when the helper is unavailable.

## What to do

1. Run the scaffold script from the project root:

```bash
python3 memory/new_run.py example-run                 # full schema
# Or choose the smaller tier instead, with a distinct unused slug:
python3 memory/new_run.py example-solo --tier solo
```

This creates `runs/<slug>/`, refuses an existing slug, and regenerates `runs/INDEX.md`. Solo selects nine numbered prefixes: **00, 07, 09, 12, 13, 14, 19, 21, 23**. It creates `PACKETS.md` with a solo waiver instead of a `packets/` directory. Prefix selection is not a promise of exactly nine files: a full-engine install can add `21-agent-roster.md` beside `21-evolution-records.md`. When the core scaffold is used without the full-engine goal guard, do not assume the roster exists. Mini/full runs include a packet directory; full selects the whole numbered schema.

2. Bind the selected run explicitly, for example `run_root="runs/example-run"`, and pass it to kickoff, board review, cost check, workers, evaluation, status and delivery. Every current-run note, plan and wave packet belongs under this root. If a solo task grows to need packets, create the run-local directory and document the changed scope/waiver.
3. Fill and approve the goal, Definition of Done and plan. A scaffold is open work, not completed work.
4. At closeout follow `deliver` for evidence, cost attribution, reviewed memory harvest and the closeout receipt.

`runs/INDEX.md` is derived from run goals; do not hand-edit it. The scaffold makes no network calls or changes to host configuration.
