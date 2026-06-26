# For AI Reviewers

Use this page when an AI reviewer cannot reliably browse the GitHub repo. Paste this first, then paste specific files only if the reviewer asks.

Repository: https://github.com/Zaidm6574/project-os

## What Project OS Is

Project OS is a privacy-first AI project workflow template. It gives Codex, Claude, Cursor, and similar tools a shared project structure: goal files, a blackboard, staged agent roles, cost/model-routing notes, memory rules, evaluation logs, delivery reports, artifact manifests, and an opt-in full engine add-on.

The goal is not to make a magic autonomous platform. The goal is to make AI project work more reviewable, safer with private data, and easier to resume.

## Starter Vs Full Engine

Starter mode is what users get from a normal install:

- `AGENTS.md` and `CLAUDE.md` workflow instructions
- numbered `blackboard/` files for goals, plans, decisions, risks, memory, cost, preflight, evaluation, and delivery
- `install.sh` and setup scripts
- optional tool checks
- privacy-first `.gitignore` rules
- docs and prompts for project kickoff, research refresh, friend review, and chat import

Full engine mode is explicit opt-in with `--full-engine`:

- local run helpers such as `new_run.py`, `validate_run.py`, `score_rubric.py`, `goal_guard.py`, `cost_actuals.py`, and `browser_qa.py`
- GraphOS builder script outputting `graphify-out/graph.json`
- OSVec adapter using TurboVec when available and a local fallback otherwise
- local brain and central-brain scripts for approved summary memories
- optional Claude Code agents and commands, including `context-scout`, `ui-ux-designer`, `frontend-builder`, and `/ui-review`

## Implemented Now

- Safe starter installation into blank or existing projects.
- Dry-run installation previews for starter and full-engine files.
- Optional capability preflight that checks tools without installing anything.
- Safe chat export scanning with secret redaction and private output defaults.
- Blackboard Read Gate guidance so agents read project files before acting from memory.
- Append-only decision and risk templates.
- UI workflow guidance and `/ui-review` approval rules requiring real QA evidence.
- GitHub Actions unit test workflow.
- Unit tests covering setup, optional tools, full-engine install smoke paths, central brain behavior, chat memory safety, UI guidance, and public onboarding docs.

## Optional Or External

These are not magically provided by cloning the repo:

- external Graphify, TurboVec, embedding, or graph database packages
- a true autonomous swarm runtime
- guaranteed model routing across sub-agents
- scheduled research automation
- GitHub publishing automation
- real UI quality without build, browser, responsive-layout, and accessibility checks

Project OS can structure those workflows, but the user's AI tool and local setup decide what is actually available.

## Privacy Model

Never commit raw chat exports, API keys, private notes, vector indexes, local memory databases, screenshots with personal data, browser/session data, or `.env` files. The template blocks common private folders by default, but users still need to review `git status` and privacy-scan output before publishing.

Memory should be summary-first and approval-first. Save compact lessons, preferences, and project patterns. Do not bulk-import private chat logs into public docs or central memory.

## What To Review

1. Are the starter and full-engine boundaries honest?
2. Does any wording overpromise GraphOS, OSVec, model routing, autonomous swarms, browser QA, or security sandboxing?
3. Are privacy rules clear enough for a beginner?
4. Do the Quick Start commands work?
5. Do the tests pass with `python3 -m unittest discover -s tests -v`?
6. Does `/ui-review` require real evidence before approval?
7. Is the Blackboard Read Gate clear enough to reduce context-window drift?

## GitHub About Settings

Suggested description:

```text
Privacy-first AI project workflow - blackboard, staged agents, optional full engine
```

Suggested topics:

```text
ai-agents, cursor, claude, workflow, project-management
```

Enable GitHub's **Template repository** setting if you want people to click **Use this template**.
