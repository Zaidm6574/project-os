---
name: project
description: Route a rough idea into the right Project OS workflow (the general entry point)
argument-hint: <what you want to build, review, or audit>
capabilities: [subagents, websearch, task-tracking]
claude-tools: Bash Read Write Edit Grep Glob Task TodoWrite WebSearch
---

## Active workspace

Resolve `<run-root>` explicitly from the run argument or the caller's packet before reading or writing. For a named run it is `runs/<slug>/`; do not choose the newest run automatically. For project-level work without a named run, explicitly select `blackboard/`. All numbered notes and `packets/` paths below are relative to that selected root. Pass the same root to every delegated role and follow-up workflow. Shared `blackboard/` notes are read-only context during a named run; promote reviewed cross-run lessons separately. See **Active workspace and shared notes** in `AGENTS.md` for helper arguments and project-level exceptions.

Handle this Project OS request:

**{{ARGUMENTS}}**

This is the general entry point. Your first job is to route it, not to build.

1. Read `AGENTS.md` — it is the single source of truth for Project OS doctrine across every runtime. Do not act from memory.
2. Run the **Blackboard Read Gate** if `<run-root>/` already has content: read the current goal, decisions, risks, open questions and approved plan before doing anything else. Use `context-scout` on the smallest available model when subagents and model routing are available, or do the read yourself and record the substitution. Report a compact `Context Used` summary.
3. Route to the workflow that actually fits, and say which one you picked and why:

   | The request is… | Go to |
   |---|---|
   | a new idea, nothing started yet | `kickoff` — the CEO interview, one question at a time |
   | "where are we?" | `status` |
   | work that exists and needs judging | `evaluate` |
   | finished work that needs packaging | `deliver` |
   | a website, app, dashboard or other visual artifact | the UI lane: `ui-ux-designer` → `frontend-builder` → `ui-review` |
   | an existing codebase to bring under Project OS | `adopt-project` |
   | a rough idea that needs pressure-testing first | `board-review` |

   Run the chosen workflow the way your runtime offers it — a slash command, a skill, or by reading the matching file in `prompts/workflows/` and following it inline. Do **not** skip the step and do **not** refuse.
4. Recommend the **smallest execution tier** that fits (Solo / Mini / Full), with your reasoning. Default to the smaller one when it is a close call.
5. State what you are about to do and get approval before launching any wave.

Stop and ask before spending money, publishing, deleting work, contacting anyone, or making any other commitment that is hard to reverse.
