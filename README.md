# Project OS

[![Tests](https://github.com/Zaidm6574/project-os/actions/workflows/test.yml/badge.svg)](https://github.com/Zaidm6574/project-os/actions/workflows/test.yml)

A template for running projects with AI assistants — with planning, shared state, decision logs, and memory built in from the start.

Instead of a blank folder and a chatbot, you get a working structure: a goal file, a blackboard for decisions and research, operating rules for your AI tool, and a closeout habit that saves lessons for the next run.

Built by someone with ADHD who needed project state to live outside his head, not in a chat thread that disappears.

## What it gives your AI tool

- A clear goal file (`00-project-goal.md`) so the assistant knows what done looks like
- A shared blackboard for decisions, research, risks, cost, and next steps
- `AGENTS.md` + `CLAUDE.md` — operating rules for Codex-style tools and Claude Code
- Solo, mini-swarm, and full-swarm workflow patterns
- A self-improvement loop: each serious run fills a memory harvest that can be promoted to the shared brain after review
- Context hygiene guidance for long sessions where prompt-cache cost quietly compounds
- A research refresh workflow for checking what changed since you last looked

## What is actually implemented

The following are working now:

- `AGENTS.md` and `CLAUDE.md` with the full Project OS workflow
- Blackboard templates for goals, decisions, risks, cost, model routing, evaluation, delivery, artifacts, memory, research routing, capability preflight, and research refresh
- `install.sh` — copies Project OS files into a target project folder
  - `--dry-run` flag: prints what would be copied without writing anything
  - `--check-tools` flag: checks for optional graph, vector, search, browser, container, and local AI tooling
  - `--full-engine` flag: installs the full engine add-on (run scripts, GraphOS, OSVec, cost actuals, validation)
- `scripts/setup_project_os.py` — the installer backing `install.sh`
- `scripts/check_optional_tools.py` — writes a capability report to `blackboard/17-capability-preflight.md`
- `scripts/install_full_engine.py` — opt-in full engine installer
- `scripts/import_chat_history.py` — scans old AI chat exports locally, redacts secrets, writes a private review report (never uploads)
- Loop tooling (added 2026-07): `bb_lock.py`, `plan_artifact.py`, `promptsmith.py`, `evolution.py`, `wt.py`, `harvest.py`, `brain_scale.py`, `brain_append.py`, `os_nightly.py`
- Local vector memory: `memory/mneme_adapter.py` — neural search via Ollama if available, lexical fallback otherwise
- Unit tests for setup and chat-import safety behavior

What is not automatic without additional setup: external vector/graph packages, autonomous swarm runtime, scheduled research, GitHub publishing.

## Quick start

Requirements: Git, Python 3, and an AI coding tool that reads `AGENTS.md` or `CLAUDE.md`.

```bash
git clone https://github.com/Zaidm6574/project-os.git
cd project-os
./install.sh ../my-new-project --check-tools
```

Then open `../my-new-project` in your AI tool and say:

```
/project I want to build...
```

To preview without writing anything:

```bash
./install.sh ../demo-project --dry-run
```

To install the full engine add-on:

```bash
./install.sh ../my-new-project --full-engine --check-tools
```

## Optional: smarter memory search

Out of the box, `memory/mneme_adapter.py` uses a zero-dependency lexical embedder. Install [Ollama](https://ollama.com) and one pull upgrades it to real semantic search:

```bash
ollama pull nomic-embed-text
python3 memory/mneme_adapter.py build
python3 memory/mneme_adapter.py query "have we solved something like this before?"
```

No Ollama means it stays lexical. Indexes built with one embedder are never queried with the other.

## Optional: daily heartbeat

`scripts/os_nightly.py` checks memory pressure, cleans stale locks, and flags stuck plans into `blackboard/22-automation-log.md`. Run it manually or schedule it — the file header includes a macOS launchd snippet.

## Project structure after install

```
my-new-project/
  AGENTS.md
  CLAUDE.md
  .gitignore
  blackboard/
  memory/
  outputs/
  prompts/
  runs/
  scripts/
  private-memory/      # ignored by Git
  private-imports/     # ignored by Git
```

## Privacy rules

Never commit: raw chat exports, API keys, private notes, vector indexes, local memory databases, screenshots with personal data, browser/session data. The `.gitignore` blocks the common private folders by default.

Run a quick check before pushing:

```bash
git status --short --ignored
rg -n --hidden --no-ignore -S "/Users|sk-|ghp_|github_pat_|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{20,}|BEGIN [A-Z ]*PRIVATE KEY" .
```

## Status

Active. Loop tooling added July 2026. CI passing. Template is safe to publish after the privacy check above.

Field notes from real runs (what broke, what changed as a result) are in [docs/field-notes.md](docs/field-notes.md).

A full example flow is in [docs/example-project-flow.md](docs/example-project-flow.md).
