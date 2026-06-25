# Project OS Public Template

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

This public template is designed to be safe to publish after review. It does not intentionally include personal data, private chat logs, local machine paths, API keys, or private tool branding.

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
- `scripts/setup_project_os.py`, which copies the Project OS files into a target project without overwriting existing files unless `--force` is used.
- `scripts/check_optional_tools.py`, a local-only capability check that writes recommendations into `blackboard/17-capability-preflight.md`.
- `scripts/import_chat_history.py`, a local-only chat export scanner that writes a private review report and redacts common secret patterns.
- `.gitignore` rules for private memory, raw imports, local memory, vector indexes, knowledge graph output, secrets, and environment files.
- Beginner docs for GitHub install, chat import, GitHub publishing, friend review, and a sample project flow.
- `prompts/project-os-kickoff.md` for startup behavior.
- `prompts/research-refresh.md` for current-state refresh passes.
- Unit tests for setup and chat-import safety behavior.

Optional future/tooling slots:

- Vector Memory is a slot for a local vector store, not a bundled database.
- Knowledge Graph is a slot for a graph builder, not a bundled graph engine.
- Model routing depends on the AI tool you use. If your tool cannot route sub-agents to different models, record that limitation instead of pretending it happened.
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

Those notes can be copied into `blackboard/08-memory-index.md` or an optional local Vector Memory after review. Raw chats, secrets, and sensitive personal details should never be promoted automatically.

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

It does not install external tools automatically. Instead, it tells the user what is active, what is missing, and what to connect next. Custom graph/vector tools can be exposed with:

```bash
export PROJECT_OS_GRAPH_CMD="your-graph-command"
export PROJECT_OS_VECTOR_CMD="your-vector-command"
```

Model routing is different: Project OS records the desired routing plan, but whether each sub-agent can run on a different model depends on the AI app. Configure that in Cursor, Claude, Codex, or whichever tool is running the project; it is not detected by the graph/vector environment variables.

## Quick Start

Requirements: Git, Python 3, and an AI coding tool that reads `AGENTS.md` or `CLAUDE.md`.

```bash
git clone https://github.com/YOUR-USERNAME/project-os-template.git
cd project-os-template
./install.sh ../my-new-project --check-tools
```

Replace `YOUR-USERNAME` with the GitHub account that owns the template repo.

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
- `docs/friend-review.md`
- `docs/github-publishing.md`
- `docs/install-from-github.md`
- `docs/example-project-flow.md`
- `install.sh`
- `scripts/setup_project_os.py`
- `scripts/import_chat_history.py`

Ask reviewers to check whether the workflow is understandable, honest about what is implemented, safe with private data, and easy to run in a blank test folder.

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
rg -n --hidden --no-ignore -S "/Users|sk-|private key|\\.env" .
```

Expected benign hits include `.gitignore` entries, documentation that mentions privacy checks, tests with fake keys, and redaction regexes in `scripts/import_chat_history.py`. Investigate any real local paths, real keys, raw exports, or personal notes before `git add .`.

3. From this folder, run:

```bash
git init
git add .
git commit -m "Initial Project OS template"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/project-os-template.git
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

This repository gives you the workflow, prompts, scripts, and blackboard structure.

It does not automatically provide:

- a live vector database
- a live knowledge graph engine
- a true autonomous swarm runtime
- guaranteed model routing across sub-agents
- scheduled research automation
- GitHub publishing automation

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
