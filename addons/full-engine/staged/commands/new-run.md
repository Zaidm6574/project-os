---
name: new-run
description: Start a new, isolated project run cloned from the blackboard template.
---

# /new-run <slug> [--tier solo|mini|full]

Create an isolated working directory for a new run and switch all wave outputs into it. `blackboard/` is a **read-only template** — never write to it during a run.

## What to do

1. Run the scaffold script:

```bash
python3 memory/new_run.py <slug>            # full schema
python3 memory/new_run.py <slug> --tier solo   # 00/07/12 only
```

   This copies the numbered blackboard schema into `runs/<slug>/` (including `runs/<slug>/packets/`), refuses if the slug already exists, and regenerates `runs/INDEX.md`.

2. For the rest of this run, write **every** wave output — goal, decisions, packets, plans, evaluations — into `runs/<slug>/` (and `runs/<slug>/packets/`), not into `blackboard/`.

3. At run close, follow `/deliver` to fill the delivery report, record cost actuals, export lessons to the shared brain, and validate the run.

## Notes

- `runs/INDEX.md` is derived by `new_run.py` from `runs/*/00-project-goal.md`; do not hand-edit it.
- Tier pruning: `solo` instantiates only `00/07/12`; `full` instantiates the whole schema.
- The scaffold makes **zero** network calls and never touches the Codex project.
