---
name: adopt-project
description: Adopt an existing codebase or docs folder into Project OS as a new run.
---

# /adopt-project <path> [--slug name]

Bring an **existing project** (a folder with code or docs but no Project OS run scaffold) into the `runs/` lifecycle.

## What to do

1. **Inspect the path.** Confirm it is a real project directory (has README, `package.json`, `pyproject.toml`, or similar) and is **not** already under `runs/`.

2. **Scaffold the run:**

```bash
python3 memory/adopt_project.py <existing_project_path> [--slug name]
```

   - Creates `runs/<slug>/` via the same tier-aware scaffold as `/new-run` (default tier: solo).
   - Writes an inferred `00-project-goal.md` stub from README / package metadata / folder name.
   - Regenerates `runs/INDEX.md`.

3. **Point the run at the real code.** The adopted path stays where it is; the run dir holds goal, plan, eval log, and closure artifacts. Document the external path in the goal file or a packet.

4. **Continue the normal loop:** fill DoD → plan → build → `/evaluate` → `/deliver`.

## Notes

- `blackboard/` stays read-only; all run writes go under `runs/<slug>/`.
- Refuses to overwrite existing Project OS run folders.
- If `runs/<slug>/` already exists, the script refuses to overwrite — pick a different `--slug`.
