# Memory

This folder holds public memory structure and the zero-dependency memory tools
that ship with Project OS. Nothing here is private: private memory belongs in
`private-memory/`, which is ignored by Git.

Use `self-improvement-loop.md` for reviewed lessons and next-run safeguards that are safe to keep with the project.

## Tools in this folder

These run from the project root and, apart from `osvec_adapter.py`, are
stdlib-only. The installer copies all of them into an installed project; the
optional full-engine add-on adds more alongside them.

- `mneme_adapter.py` — the memory index. `build` embeds the shared brain
  (`shared-brain.jsonl` plus its `shared-brain-archive.jsonl` tier), the
  canonical-goal section of every `runs/*/00-project-goal.md`, and meaningful
  H2 sections from `blackboard/*.md`; `query "..."` searches them. It does
  **not** index the markdown in this folder — the notes
  and rules below are for you to read, not for the index. Uses nomic-embed-text
  via a local Ollama when one is running, and a lexical embedder otherwise; it
  refuses to query an index built with the other embedder. Configure
  `MNEME_EMBEDDER=auto|neural|lexical` (default `auto`); `OSVEC_EMBEDDER` is
  the legacy alias. Ollama uses `MNEME_OLLAMA_URL` (default
  `http://127.0.0.1:11434`), `MNEME_NEURAL_MODEL` (default
  `nomic-embed-text`), `MNEME_OLLAMA_TIMEOUT` (0.1–600 seconds, default 120),
  and `MNEME_OLLAMA_BATCH_SIZE` (1–256, default 64). Each has an `OSVEC_*`
  legacy alias. Invalid values fail with an actionable message instead of
  silently changing retrieval mode.
- `code_graph.py` — a graph of the CURRENT SOURCE CODE (modules, functions,
  classes, tests and the imports/calls/defines edges between them), each node
  fingerprinted with sha256 so staleness is provable. `build` writes
  `code-graph.json`, `check` reports drift, and `orient <module:symbol>` fails
  closed: when the graph is stale or the symbol is absent it says so instead of
  narrating an architecture it cannot support. Use it to orient before
  explaining code you have not read.
- `build_graph.py` — a graph of blackboard/run *relationships* (not code),
  written to `graphify-out/graph.json` with a Mermaid `graph TD` view of the
  same nodes and edges alongside it at `graphify-out/graph.mmd`. Run it with
  `--root blackboard` or `--root runs/<slug>`.
- `osvec_adapter.py` — the full-engine vector store (`memory/store/`). Unlike
  the rest of this folder its data commands need optional numpy, so
  `python3 memory/osvec_adapter.py selftest` is the only thing that proves it
  works; `--help` remains available on a stdlib-only install and a capability
  report saying the file is present has checked nothing but the filename.
- `context_budget.py` — kickoff preflight for context and cost. Reads the newest
  session transcript and returns OK / WATCH / CHECKPOINT / UNKNOWN as its exit
  code; UNKNOWN (no transcript found) is a fail-closed verdict, not a pass.

Use memory categories:

- user-preference
- project-pattern
- lesson
- research-finding
- decision
- risk
- agent-packet
- safeguard

Memory rules:

- Store compact summaries, not raw transcripts.
- Mark whether each memory is Draft, Approved, Rejected, Private-only, or Promoted.
- Do not store secrets, credentials, raw private chats, or sensitive personal details.
- At kickoff, use approved memories to decide what should be done differently this time.
