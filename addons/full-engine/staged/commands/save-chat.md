---
description: Save an approved chat summary, lesson, preference, or decision into the local Project OS shared brain.
argument-hint: <approved summary to remember>
---

<!-- GENERATED from prompts/workflows/save-chat.md by scripts/sync_runtime_assets.py — edit the canonical file, not this one. -->

## Active workspace

Resolve `<run-root>` explicitly from the run argument or the caller's packet before reading or writing. For a named run it is `runs/<slug>/`; do not choose the newest run automatically. For project-level work without a named run, explicitly select `blackboard/`. All numbered notes and `packets/` paths below are relative to that selected root. Pass the same root to every delegated role and follow-up workflow. Shared `blackboard/` notes are read-only context during a named run; promote reviewed cross-run lessons separately. See **Active workspace and shared notes** in `AGENTS.md` for helper arguments and project-level exceptions.

# save-chat <approved summary>

Save a compact, approved memory from the current chat into `brain/shared-brain.jsonl`.

## What to do

1. Convert the chat into a short approved summary, lesson, preference, or decision. Do not save raw chat unless the user explicitly asks for raw storage.
2. Refuse to store secrets, API keys, passwords, credentials, or unnecessary sensitive personal data.
3. Run:

```bash
python3 brain/brain.py save-chat --summary "$ARGUMENTS" --kind lesson --tag chat
```

4. If the user says the memory is a preference or decision, use `--kind preference` or `--kind decision`.
5. If central brain is connected and the saved item should be reusable across projects, run the approved central sync command from `brain/CENTRAL_BRAIN.md`.

## Notes

- The safe default is summary memory.
- Raw chat storage requires an explicit `--mode raw` command and still refuses secret-looking text.
- The selected run notes remain the source of truth; the project brain is shared compact recall. This command treats the supplied summary as approved; it does not authenticate human consent. Confirm that the requested memory scope is authorized before saving or central sync.
