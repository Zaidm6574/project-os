# Shared Brain

This folder is the local Project OS shared-brain exchange.

`brain/brain.py` appends reviewed lessons to `brain/shared-brain.jsonl` and can print or export that JSONL for another AI tool. It is local-only and refuses to write outside the project copy.

The shared brain is not raw chat memory. Store compact, approved lessons only.

To save a chat memory directly:

```bash
python3 brain/brain.py save-chat --summary "Approved lesson or preference from this chat." --kind lesson --tag chat
```

Use `--kind preference`, `--kind decision`, `--kind project-pattern`, or `--kind research-finding` when that better describes the memory. Raw chat storage requires explicit `--mode raw` and still refuses secret-looking text.

`brain/central_brain.py` can sync approved lessons with an opt-in central brain folder:

```bash
python3 brain/central_brain.py init --path ~/.project-os/central-brain
python3 brain/central_brain.py push --path ~/.project-os/central-brain --project . --project-id my-project
python3 brain/central_brain.py pull --path ~/.project-os/central-brain --project . --project-id my-project
```
