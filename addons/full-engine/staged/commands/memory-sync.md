---
description: Update memory — refresh the GraphOS graph and store durable lessons in OSVec
argument-hint: [optional: a lesson/preference to remember]
allowed-tools: Read Write Edit Grep Glob Task Bash
---

Sync project memory. Optional item to remember: **$ARGUMENTS**

Launch the `memory-librarian` subagent. It should:
1. Run `python3 scripts/check_optional_tools.py --target .` when the script exists. If the report finds local full-engine files, do not call GraphOS/OSVec unavailable just because external Graphify/TurboVec commands are missing.
2. (Re)build the GraphOS graph when `memory/build_graph.py` exists: `python3 memory/build_graph.py --root blackboard` or `python3 memory/build_graph.py --root runs/<slug>` -> `graphify-out/graph.json` + Mermaid. Note status in `08-memory-index.md`.
3. Promote durable items (preferences, reusable patterns, lessons from failures, key decisions) into OSVec via `memory/osvec_adapter.py` when present. If only legacy `memory/turbovec_adapter.py` exists, run its selftest and record the legacy status. Record status in `10-osvec-index.md`. Never store secrets/keys.
4. If I gave a specific lesson above, store it with a stable id and a link back to its source blackboard file/packet.

Report what was indexed and anything stale or contradictory worth reconciling.
