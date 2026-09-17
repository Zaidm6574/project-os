---
name: adopt-project
description: Adopt an existing codebase or docs folder into Project OS as a new run.
---

<!-- GENERATED from prompts/workflows/adopt-project.md by scripts/sync_runtime_assets.py — edit the canonical file, not this one. -->

## Active workspace

Resolve `<run-root>` explicitly from the run argument or the caller's packet before reading or writing. For a named run it is `runs/<slug>/`; do not choose the newest run automatically. For project-level work without a named run, explicitly select `blackboard/`. All numbered notes and `packets/` paths below are relative to that selected root. Pass the same root to every delegated role and follow-up workflow. Shared `blackboard/` notes are read-only context during a named run; promote reviewed cross-run lessons separately. See **Active workspace and shared notes** in `AGENTS.md` for helper arguments and project-level exceptions.

# adopt-project <path> [--slug name]

Bring an **existing project** (a folder with code or docs but no Project OS run scaffold) into the `runs/` lifecycle.

## What to do

1. **Inspect the path.** Confirm it is a real project directory (has README, `package.json`, `pyproject.toml`, or similar) and is **not** already under `runs/`.

2. **Scaffold the run:**

```bash
python3 memory/adopt_project.py <existing_project_path> [--slug name]
```

   - Creates `runs/<slug>/` via the same tier-aware scaffold as the `new-run` workflow (default tier: solo).
   - Writes an inferred `00-project-goal.md` stub with unchecked success criteria from README / package metadata / folder name. Review those criteria; adoption does not complete them or close the run.
   - Regenerates `runs/INDEX.md`.

3. **Point the run at the real code.** The adopted path stays where it is; the run dir holds goal, plan, eval log, and closure artifacts. Document the external path in the goal file or a packet.

4. **Continue the normal loop:** fill DoD → plan → build → the `evaluate` workflow → the `deliver` workflow.

## Notes

- The project-shared `blackboard/` stays read-only; set `<run-root>` to the newly created `runs/<slug>/` and pass it to every following role and workflow.
- Refuses to overwrite existing Project OS run folders.
- If `runs/<slug>/` already exists, the script refuses to overwrite — pick a different `--slug`.
