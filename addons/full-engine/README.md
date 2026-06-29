# Project OS Full Engine Add-On

This add-on turns the starter template into the fuller Project OS runtime.

It is opt-in. The normal `install.sh` starter path does not install this engine automatically, because a public template should not silently add commands, memory stores, or Claude-specific files to someone else's project.

## What It Adds

- `memory/new_run.py` for isolated `runs/<slug>/` scaffolds.
- `memory/validate_run.py` for mechanical closeout gates.
- `memory/score_rubric.py` for deterministic evaluator scoring.
- `memory/cost_actuals.py` and `memory/cost_rollup.py` for measured cost visibility, including cached reads, cached writes, and cache-write pressure.
- `memory/build_graph.py` for GraphOS output at `graphify-out/graph.json`.
- `memory/osvec_adapter.py` for OSVec local memory, powered by TurboVec when installed and a numpy fallback otherwise.
- `brain/brain.py` for a local shared-brain JSONL exchange, including `save-chat` for approved chat summaries.
- `brain/central_brain.py` for optional cross-project central brain sync.
- Optional `.claude/agents` and `.claude/commands` definitions for Claude Code users, including `context-scout` for low-cost blackboard read gates and `ui-ux-designer`, `frontend-builder`, and `/ui-review` for interface projects.
- `blackboard/21-agent-roster.md` so goal locking and goal drift checks have a public-template-safe home.

## Install

From the template checkout:

```bash
python3 scripts/install_full_engine.py --target /path/to/project
```

To also install the Claude Code agents and slash commands:

```bash
python3 scripts/install_full_engine.py --target /path/to/project --claude
```

The installer is local-only. It does not use the network, install packages, publish anything, or overwrite existing files unless `--force` is passed.

To preview the add-on files first:

```bash
python3 scripts/install_full_engine.py --target /path/to/project --dry-run
```

## Save Chat To Brain

After the full engine is installed:

```bash
python3 brain/brain.py save-chat --summary "Approved lesson or preference from this chat." --kind lesson --tag chat
```

This writes to `brain/shared-brain.jsonl`. Summary mode is the default. Raw chat storage requires explicit `--mode raw` and still refuses secret-looking text.

## Central Brain

After the full engine is installed, a project can opt into a central brain:

```bash
python3 scripts/install_full_engine.py --target . --central-brain ~/.project-os/central-brain --project-id my-project
```

Then sync approved lessons:

```bash
python3 brain/central_brain.py push --path ~/.project-os/central-brain --project . --project-id my-project
python3 brain/central_brain.py pull --path ~/.project-os/central-brain --project . --project-id my-project
```

Central brain is a local JSONL exchange for approved lesson summaries. It is not a raw chat import, not a private data dump, and not a replacement for each project's blackboard.

## UI/UX Layer

When the Claude Code agents and slash commands are installed, interface projects get a dedicated UI lane:

- `ui-ux-designer` writes the UI plan: workflow, responsive layout, accessibility, visual direction, interaction states, and browser QA checklist.
- `frontend-builder` implements the approved UI using the existing project stack and records build/test/browser evidence.
- `/ui-review` reviews a UI artifact or plan and writes a packet with responsive layout, accessibility, and browser QA findings.

This layer is local and optional. It does not download design tools or overwrite a project's existing frontend setup.

## Low-Cost Read Gate

Use `context-scout` before expensive or high-reasoning agents when the host supports subagents. It runs on the smallest available model and returns a compact `Context Used` packet from the blackboard, so heavier agents do not waste context or cost rereading the whole project.

## Context/Cache Hygiene

For long sessions, treat the blackboard as durable memory and the chat as temporary working space. At phase boundaries, write a receipt or handoff packet and continue from that packet when the active chat has become mostly historical context.

When the host exposes usage logs, run:

```bash
python3 memory/cost_actuals.py --transcript /path/to/session.jsonl --write
```

The report separates uncached input, output, cached reads, cached writes, and measured dollars. It also has `--codex-sessions` for local Codex activity rollups from `~/.codex/sessions`; that mode sums `last_token_usage`, warns against summing cumulative `total_token_usage`, and labels `cached_input_tokens` as cached reads rather than cache writes. If cached writes dominate the cost in provider logs, checkpoint the run before doing more work.

## Why This Is Separate

The starter kit is for safe public cloning. The full engine adds stronger local machinery. Keeping it separate avoids surprising users and protects existing projects from accidental integration churn.
