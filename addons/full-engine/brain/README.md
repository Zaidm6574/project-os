# Shared Brain

This folder is the local Project OS shared-brain exchange.

`brain/brain.py` appends reviewed lessons to the selected shared-brain store and can print or export that JSONL for another AI tool. The default store is `brain/shared-brain.jsonl` inside the project. A full installation also honors the canonical resolver: an absolute `PROJECT_OS_SHARED_BRAIN` override takes precedence over a valid, explicitly ignored `brain/shared-brain-binding.jsonl` binding. The host or operator must establish the intended approved storage scope. These options select that one durable store; `export --from`, `import --into`, and `save-chat --summary-file` remain restricted to files inside the project. Symlink and hardlink durable-store targets are refused by the canonical resolver. Exchange paths also refuse existing hardlinked files, because an inside-project name can share the same underlying file with an outside-project name.

Each command revalidates the selected store. If the selected store changes or path validation fails since the module was loaded, the command refuses; restart it with the intended configuration. This check and cooperative locking are not a filesystem sandbox against adversarial concurrent processes. A portable `brain.py` copy without the core scripts retains project-local behavior.

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

If a JSONL line cannot be parsed (a crash-truncated tail, a hand edit), that record does not sync. `push`, `pull`, `sync`, and `status` still process every other line, then print the file and the line numbers they had to skip and exit `1`, so a lost lesson is never reported as a clean run.

For exact keyword search over this file (instead of semantic recall), use the read-only FTS mirror: `python3 memory/brain_fts_mirror.py rebuild` then `query "<terms>"` — see the full-engine README.
