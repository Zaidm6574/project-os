# Project OS

[![Tests](https://github.com/Zaidm6574/project-os/actions/workflows/test.yml/badge.svg)](https://github.com/Zaidm6574/project-os/actions/workflows/test.yml)

The system I use to ship real software with AI assistants — most recently a live booking application for a working auto-detailing business. It gives an AI-assisted project persistent goals, decisions, verification gates, and memory, instead of leaving everything inside one disappearing chat.

Built by someone with ADHD who needed project state to live outside his head. The first version of that detailing site looked finished but couldn't take a booking — it failed silently, for real people. Project OS is what I built so that never happens again: plans are rejected unless they declare how the result will be checked, concurrent agents can't silently erase each other's work (fencing-token file locks), and chat-derived memory never syncs without explicit approval (privacy fail-closed).

Over 120 tests cover installation, concurrency, privacy boundaries, plan verification, and memory tooling. An adversarial model-judge review found real flaws (a sync gate that silently dropped every lesson, a validator/compiler mismatch); each became a regression test before the fix landed.

## What it gives your AI tool

- A clear goal file (`00-project-goal.md`) so the assistant knows what done looks like
- A shared blackboard for decisions, research, risks, cost, and next steps
- `AGENTS.md` + `CLAUDE.md` — operating rules for Codex-style tools and Claude Code
- Solo, mini-swarm, and full-swarm workflow patterns (structured prompt roles for one AI tool — not autonomous background processes)
- A self-improvement loop: each serious run fills a memory harvest that can be promoted to the shared brain after review
- Context hygiene guidance for long sessions where prompt-cache cost quietly compounds
- A research refresh workflow for checking what changed since you last looked

## What is actually implemented

The following are working now:

- `AGENTS.md` and `CLAUDE.md` with the same core Project OS workflow
- Blackboard templates for goals, decisions, risks, cost, model routing, evaluation, delivery, artifacts, memory, research routing, capability preflight, and research refresh
- `install.sh` — copies Project OS files into a target project folder
  - `--dry-run` flag: prints what would be copied without writing anything
  - `--check-tools` flag: checks for optional graph, vector, search, browser, container, and local AI tooling
  - `--full-engine` flag: installs the full engine add-on (run scripts, GraphOS, OSVec, cost actuals, validation)
- `scripts/setup_project_os.py` — the installer backing `install.sh`
- `scripts/check_optional_tools.py` — writes a capability report to `blackboard/17-capability-preflight.md`
- `scripts/install_full_engine.py` — opt-in full engine installer
- `scripts/import_chat_history.py` — scans old AI chat exports locally, redacts secrets, writes a private review report (never uploads)
- Loop tooling (added 2026-07): `bb_lock.py`, `plan_artifact.py`, `promptsmith.py`, `evolution.py`, `wt.py`, `harvest.py`, `brain_scale.py`, `brain_append.py`, `os_nightly.py` — `promptsmith` runs end-to-end without any personal setup via `--brief-file examples/sample-brief.md`
- Local vector memory: `memory/mneme_adapter.py` — neural search via Ollama if available, lexical fallback otherwise
- Unit tests for setup and chat-import safety behavior

What is not automatic without additional setup: external vector/graph packages, autonomous swarm runtime, scheduled research, GitHub publishing.

## Quick start

Requirements: Git, Python 3.10+, and an AI coding tool that reads `AGENTS.md` or `CLAUDE.md`.

The installer finds Python by probing `python3`, `python`, and the versioned names `python3.13` down to `python3.10` on your PATH — so a stock macOS `python3` (3.9) is fine as long as a newer interpreter is installed under any of those names. If none qualifies, `install.sh` stops and tells you which Python it found instead.

```bash
git clone https://github.com/Zaidm6574/project-os.git
cd project-os
./install.sh ../my-new-project --check-tools
```

Then open `../my-new-project` in your AI tool and say:

```
/project I want to build...
```

To install the full engine add-on:

```bash
./install.sh ../my-new-project --full-engine --check-tools
```

## 5-Minute Demo

Before copying anything, preview the install:

```bash
./install.sh ../demo-project --dry-run
```

The first lines look like this:

```text
Project OS setup dry run complete.
- would create /tmp/project-os-demo
- would write /tmp/project-os-demo/AGENTS.md
- would write /tmp/project-os-demo/CLAUDE.md
- would write /tmp/project-os-demo/.gitignore
- would write /tmp/project-os-demo/prompts/project-os-kickoff.md
```

Nothing is written until you run it without the flag.

Before: an empty folder. After: a goal file, a blackboard, prompts, and operating rules — a project any AI coding tool can pick up mid-thought.

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

Sharing it with an AI reviewer? Paste `docs/for-ai-reviewers.md` first — it gives the short architecture summary and the implemented-versus-optional boundary without requiring the whole README.

Field notes from real runs (what broke, what changed as a result) are in [docs/field-notes.md](docs/field-notes.md).

A full example flow is in [docs/example-project-flow.md](docs/example-project-flow.md).
