# Chat Import Guide

The chat importer is optional.

It is meant to help a new user review old chats locally and decide what, if anything, should become a short Project OS memory summary.

## What It Does

- Reads local `.json`, `.txt`, and `.md` exports.
- Redacts several common API key, token, email, and private-key patterns.
- Counts likely preferences, project ideas, repeated tools, and recurring blockers.
- Writes a private markdown review report to `private-memory/chat-memory.md`.
- Avoids copying full source lines by default.

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
