# Project OS Public Template

[![Tests](https://github.com/Zaidm6574/project-os/actions/workflows/test.yml/badge.svg)](https://github.com/Zaidm6574/project-os/actions/workflows/test.yml)

Turn a messy idea into a guided AI project with planning, research, review, memory, and closeout already structured.

Project OS is a privacy-first workflow template for people who want their AI tool to act less like a random chatbot and more like a careful project operator.

It gives your assistant:

- a clear goal file
- a shared blackboard for decisions, research, risks, and next steps
- solo, mini-swarm, and full-swarm operating patterns
- cost and model-routing guidance
- optional local memory slots
- evaluation and delivery logs
- a self-improvement loop so each serious run leaves behind useful lessons
- a research refresh workflow for checking what changed in the market, tool landscape, or user expectations

This public template is designed to be safe to publish after review. It does not intentionally include personal data, private chat logs, local machine paths, API keys, or private-only branding.

## What It Feels Like

Instead of saying:

```text
build me an app
```

you open a project and say:

```text
/project I want to build a habit tracker for people with ADHD
```

and the assistant is expected to:

1. clarify the idea
2. choose the right workflow depth
3. write down assumptions and risks
4. plan before building
5. keep a readable project brain
6. review the result before calling it done
7. harvest lessons for the next run

The point is not "more agents because agents are cool." The point is better judgment, better structure, and fewer sloppy AI runs.

## What Is Included In This Version

Implemented now:

- `AGENTS.md` and `CLAUDE.md` with the same Project OS workflow for Codex-style tools and Claude.
- Markdown blackboard templates for goals, decisions, risks, cost, model routing, evaluation, delivery, artifacts, memory, research routing, capability preflight, and research refresh.
- A self-improvement loop template for harvesting approved lessons and next-kickoff checks after each serious run.
- `install.sh`, a small friend-friendly installer that runs the setup script from a cloned GitHub repo.
- `--check-tools`, an optional install flag that checks for graph, vector, search, browser, container, and local AI tooling.
- `--full-engine`, an explicit add-on install path for Project OS run scripts, GraphOS, OSVec, cost actuals, validation, and optional Claude Code commands.
- Optional workflow definitions in the full engine: `context-scout` for low-cost blackboard read gates, plus `ui-ux-designer`, `frontend-builder`, and `/ui-review` for web apps, dashboards, visual tools, and responsive/browser QA checks.
- `scripts/setup_project_os.py`, which copies the Project OS files into a target project without overwriting existing files unless `--force` is used.
- `scripts/check_optional_tools.py`, a local-only capability check that writes recommendations into `blackboard/17-capability-preflight.md`.
- `scripts/install_full_engine.py`, a local-only opt-in installer for the full engine add-on.
- `scripts/import_chat_history.py`, a local-only chat export scanner that writes a private review report and redacts common secret patterns.
- `addons/full-engine/`, dormant add-on source that can be activated later without downloading another repo.
- `.gitignore` rules for private memory, raw imports, local memory, vector indexes, GraphOS output, secrets, and environment files.
- Beginner docs for GitHub install, chat import, GitHub publishing, friend review, and a sample project flow.
- `prompts/project-os-kickoff.md` for startup behavior.
- `prompts/research-refresh.md` for current-state refresh passes.
- Unit tests for setup and chat-import safety behavior.

Starter mode versus full engine:

- OSVec is the Project OS vector and memory layer. Starter mode tracks OSVec as optional. The full engine add-on activates `memory/osvec_adapter.py`, powered by TurboVec when installed and a local fallback otherwise.
- GraphOS is the Project OS graph layer. Starter mode tracks GraphOS as optional. The full engine add-on activates `memory/build_graph.py`, with output at `graphify-out/graph.json`.
- The add-on is explicit. It does not run during a normal starter install unless `--full-engine` is passed or `scripts/install_full_engine.py` is run.
- Model routing depends on the AI tool you use. If your tool cannot route sub-agents to different models, record that limitation instead of pretending it happened.
- UI/UX and browser QA depend on the project and host tool. The full engine provides the UI agents and `/ui-review`; the assistant must still run the available build, responsive layout, accessibility, and browser QA checks before claiming approval.
- Swarms are workflow patterns. This template does not include its own autonomous swarm runner.
- Security boundaries in markdown are operating rules. Real sandboxing, egress controls, and container isolation require separate tools.
- Self-improvement is agent-assisted and review-based. This template does not automatically rewrite its own rules or promote memories without human approval.

## Example Project Flow

There are three normal ways to use Project OS:

- `Solo Agent Loop`
  Best for quick documents, focused planning, short audits, and small app tweaks.
- `Mini Swarm`
  Best for serious but contained work where you want planning, research or build, and review separated.
- `Full Swarm`
  Best for big app ideas, business plans, deep research, serious audits, or multi-part builds.

A fuller walk-through lives in [docs/example-project-flow.md](docs/example-project-flow.md).

## Self-Improvement Loop

Project OS improves by remembering small, approved lessons across runs.

At closeout, the assistant should fill `blackboard/19-memory-harvest.md` and `memory/self-improvement-loop.md` with:

- user preferences discovered during the run
- mistakes or friction points to avoid next time
- reusable project patterns
- safeguards for future kickoff checks
- memories that should stay private or be rejected

Those notes can be copied into `blackboard/08-memory-index.md` or OSVec after review. Raw chats, secrets, and sensitive personal details should never be promoted automatically.

## Research Refresh

This template also supports a simple "stay current" pass for active projects.

Use it when you want the assistant to check:

- what is trending now
- what competitors started doing
- what features users now expect
- which tools or models became newly relevant
- whether your project plan is now stale

Run it by giving your assistant a prompt like:

```text
Use prompts/research-refresh.md for this project and update the blackboard.
```

If your assistant supports slash commands or prompt aliases, `/research refresh` should point to this same workflow.

The refresh pass should update:

- `blackboard/02-research.md`
- `blackboard/03-decisions.md`
- `blackboard/04-risks.md`
- `blackboard/16-research-router.md`
- `blackboard/20-research-refresh.md`

This is a repeatable workflow for check-ins. It is not a hidden autonomous updater unless your toolchain adds automation on top.

## Optional Graph And Vector Tool Check

Project OS can check whether a user's machine already has optional graph, vector, search, browser, container, or local AI tools available.

Run:

```bash
./install.sh ../my-new-project --check-tools
```

or inside an existing project:

```bash
python3 scripts/check_optional_tools.py --target .
```

The check writes a plain-English report into `blackboard/17-capability-preflight.md`.

It does not install external tools automatically. Instead, it tells the user what is active, what is missing, and what to connect next. Custom GraphOS/OSVec tools can be exposed with:

```bash
export PROJECT_OS_GRAPHOS_CMD="your-graph-command"
export PROJECT_OS_OSVEC_CMD="your-vector-command"
```

Legacy `PROJECT_OS_GRAPH_CMD` and `PROJECT_OS_VECTOR_CMD` are still recognized as fallbacks.

Important: missing external Graphify/TurboVec commands do not mean Project OS full-engine memory is missing. If `memory/build_graph.py`, `memory/osvec_adapter.py`, or legacy `memory/turbovec_adapter.py` exists, the assistant should report the local memory path as available and run/offer the activation commands:

```bash
python3 memory/build_graph.py --root blackboard
python3 memory/osvec_adapter.py selftest
```

Model routing is different: Project OS records the desired routing plan, but whether each sub-agent can run on a different model depends on the AI app. Configure that in Cursor, Claude, Codex, or whichever tool is running the project; it is not detected by the GraphOS/OSVec environment variables.

## Full Engine Add-On

If a project needs the real local engine, install it explicitly:

```bash
./install.sh ../my-new-project --full-engine --check-tools
```

For Claude Code agents and slash commands too:

```bash
./install.sh ../my-new-project --full-engine --claude-engine --check-tools
```

To also initialize or connect a central brain:

```bash
./install.sh ../my-new-project --full-engine --central-brain ~/.project-os/central-brain --project-id my-new-project --check-tools
```

Or from inside an already-created project:

```bash
python3 scripts/install_full_engine.py --target .
```

The add-on copies local scripts and optional Claude definitions into the target project. If central brain is requested, it creates a local JSONL exchange folder and a `brain/CENTRAL_BRAIN.md` note with push/pull commands. It does not install Python packages, use the network, publish anything, import raw chats, or overwrite existing files unless `--force` is passed.

For UI projects, the Claude definitions include `ui-ux-designer`, `frontend-builder`, and `/ui-review`. Use them to plan the first usable screen, responsive layout, accessibility basics, interaction states, visual direction, and browser QA evidence.

For larger projects, the Claude definitions also include `context-scout`, a low-cost reader agent. Use it on the smallest available model to summarize `Context Used` before more expensive agents plan, build, or evaluate.

To save an approved memory from a chat:

```bash
python3 brain/brain.py save-chat --summary "Approved lesson or preference from this chat." --kind lesson --tag chat
```

Central brain sync is for approved lesson summaries:

```bash
python3 brain/central_brain.py push --path ~/.project-os/central-brain --project . --project-id my-new-project
python3 brain/central_brain.py pull --path ~/.project-os/central-brain --project . --project-id my-new-project
```

## Quick Start

Requirements: Git, Python 3, and an AI coding tool that reads `AGENTS.md` or `CLAUDE.md`.

```bash
git clone https://github.com/Zaidm6574/project-os.git
cd project-os
./install.sh ../my-new-project --check-tools
```

If you are using a fork, replace `Zaidm6574` with the GitHub account that owns the fork.

Then open `../my-new-project` in your AI coding tool and say:

```text
/project I want to build...
```

or:

```text
$project-os I want to build...
```

Later, when the project has been running for a while, you can also say:

```text
Use the research refresh workflow and tell me what changed.
```

## Claude And Codex Compatibility

This template includes both:

- `AGENTS.md` for Codex-style agents
- `CLAUDE.md` for Claude Code / Claude projects

`AGENTS.md` contains the full public Project OS workflow. `CLAUDE.md` points Claude users to that same workflow and repeats the most important Claude-facing rules, so both files should travel together.

## Friend Review

If you are sharing this with friends for critique, send them:

- `README.md`
- `AGENTS.md`
- `CLAUDE.md`
- `docs/for-ai-reviewers.md`
- `docs/friend-review.md`
- `docs/github-publishing.md`
- `docs/install-from-github.md`
- `docs/example-project-flow.md`
- `install.sh`
- `scripts/setup_project_os.py`
- `scripts/install_full_engine.py`
- `scripts/import_chat_history.py`
- `addons/full-engine/README.md`

Ask reviewers to check whether the workflow is understandable, honest about what is implemented, safe with private data, and easy to run in a blank test folder.

If an AI reviewer cannot browse GitHub reliably, paste `docs/for-ai-reviewers.md` first. It gives the short architecture summary and the implemented-versus-optional boundary without requiring the whole README.

## Optional Chat Memory Import

You can optionally scan old AI chats into a private local review report:

```bash
python3 scripts/import_chat_history.py --input /path/to/export --output private-memory/chat-memory.md
```

The importer is local-first and conservative:

- it does not upload chats
- it does not commit raw chat files
- it redacts common secret patterns
- by default, it writes counts and review prompts instead of full source lines
- short redacted excerpts require the explicit `--include-excerpts` flag

Private memory folders are ignored by Git in this template and in projects created by the setup script. Still review `git status --short --ignored` before committing.

## GitHub Setup

1. Create a new empty GitHub repo.
2. Run a local privacy check:

```bash
git status --short --ignored
git log --format=fuller --max-count=5
git remote -v
rg -n --hidden --no-ignore -S "/Users|[A-Za-z]:\\\\|sk-|sk-proj-|ghp_|github_pat_|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{20,}|BEGIN [A-Z ]*PRIVATE KEY|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}|\\.env|graphify-out|private-memory|private-imports" .
```

Expected benign hits include `.gitignore` entries, documentation that mentions privacy checks, tests with fake keys, and redaction regexes in `scripts/import_chat_history.py`. Investigate any real local paths, real keys, raw exports, personal notes, or unwanted Git author/remote metadata before `git add .`.

3. From this folder, run:

```bash
git init
git add .
git commit -m "Initial Project OS template"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/project-os.git
git push -u origin main
```

4. In GitHub, go to repo settings and enable **Template repository** if you want friends to click **Use this template**.

## What This Repo Is Good At

- giving an AI project structure from day one
- making work easier to review later
- helping non-technical users follow what the assistant is doing
- separating "implemented now" from "maybe later"
- creating a repeatable closeout habit instead of ending with scattered files

## What This Repo Does Not Magically Do For You

This repository gives you the workflow, prompts, scripts, blackboard structure, and an opt-in full engine add-on.

It does not automatically provide without explicit add-on/tool setup:

- external TurboVec, Graphify, embedding, or graph database packages
- a true autonomous swarm runtime
- guaranteed model routing across sub-agents
- scheduled research automation
- GitHub publishing automation

It also does not magically make a UI good without review. For frontend work, run the available build/test/browser checks and record responsive layout, accessibility, and browser QA status.

Those depend on the AI tool and local setup you use around the template.

## Privacy Rules

Never commit:

- raw chat exports
- API keys
- private notes
- vector indexes
- local memory databases
- screenshots with personal data
- browser/session data

The `.gitignore` file blocks the common private folders by default.

## Suggested First Demo

If you want to test this quickly, make a blank folder and try one of these:

- `/project Build a personal habit tracker for ADHD with web-first scope`
- `/project Help me research and plan a local service business idea`
- `/project Audit this small app and give me a delivery report`
- `Use the research refresh workflow and tell me what changed in this market`
