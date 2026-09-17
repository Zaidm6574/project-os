# Chat Import Guide

The chat importer is optional.

It is meant to help a new user review old chats locally and decide what, if anything, should become a short Project OS memory summary.

## What It Does

- Reads local `.json`, `.txt`, and `.md` exports.
- Redacts several common API key, token, email, and private-key patterns.
- Counts likely preferences, project ideas, repeated tools, and recurring blockers.
- Writes a private markdown review report to `private-memory/chat-memory.md`.
- Avoids copying full source lines by default.

The importer requires the canonical `scripts/secret_patterns.py` scanner. A partial installation without that module is refused rather than using the portable brain's weaker compatibility patterns. Matching credential assignments are redacted even when their values contain only letters; natural-language prose exceptions are deliberately narrow.

New reports and automatic backups are created with owner-only permissions (`0600` or stricter). Replacing a report preserves or tightens its existing mode. An existing `.bak` is never overwritten; select a new output path or review and move the backup before another replacement. Review excerpts before sharing: pattern matching still cannot remove every kind of sensitive information.

Output publication requires no-follow directory operations and refuses symbolic links in the output or any parent directory. On macOS, aliases such as `/tmp` and `/var` are therefore refused; use their physical directory paths (for example `/private/tmp`). Unsupported platforms refuse instead of writing with weaker path protection.

## What It Does Not Do

- It does not upload chats.
- It does not use an AI model.
- It does not create a perfect psychological profile.
- It does not remove every kind of sensitive personal information.
- It does not turn old chats into verified facts or polished memories.
- It does not automatically trust the old chats as facts.
- It does not commit raw exports.

## Safe Workflow

1. Put raw exports in `private-imports/`.
2. Run the importer.
3. Review `private-memory/chat-memory.md`.
4. Open the original export locally only when needed.
5. Copy only short, approved summaries written in your own words into the blackboard.
6. Delete raw exports if you no longer need them.

## Command

```bash
python3 scripts/import_chat_history.py --input private-imports --output private-memory/chat-memory.md
```

Short redacted excerpts are available, but they are less private:

```bash
python3 scripts/import_chat_history.py --input private-imports --output private-memory/chat-memory.md --include-excerpts
```
