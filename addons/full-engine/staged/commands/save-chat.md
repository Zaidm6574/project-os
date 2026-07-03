---
name: save-chat
description: Save an approved chat summary, lesson, preference, or decision into the local Project OS shared brain.
argument-hint: <approved summary to remember>
---

# /save-chat <approved summary>

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
- The project blackboard remains the source of truth; the brain is for compact recall.
