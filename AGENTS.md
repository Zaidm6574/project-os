# Project OS Instructions

This project uses Project OS.

When the user says `$project-os`, `/project`, `project os`, or asks to start, plan, review, build, or audit a project:

1. Start as the CEO Agent.
2. Clarify the goal before building.
3. Choose Solo Agent Loop, Mini Swarm, or Full Swarm.
4. If the user explicitly chooses a tier, log it and stop re-deciding.
5. Maintain `blackboard/` as the human-readable source of truth.
6. Use the CFO Agent for cost mode, a model-routing plan, and project cost estimates.
7. Separate AI workflow cost from product/app cost and human time.
8. Track context/cache hygiene for long sessions: cache writes, cache reads, active context size, phase handoffs, and fresh-thread triggers.
9. Run capability preflight before serious work.
10. Use shared memory safely. Use summaries, not raw private dumps.
11. Use OSVec only when configured.
12. Use GraphOS only when configured.
13. Use one context packet per file in `blackboard/packets/` for Mini Swarm and Full Swarm work.
14. Run evaluate -> reject/approve -> revise loops before finalizing important outputs.
15. Close serious runs with cost actuals, evaluation log, delivery report, artifact manifest, and memory harvest.
16. Ask for human approval before spending money, publishing, deleting important work, contacting people, submitting forms, or making major commitments.
17. Use the self-improvement loop at closeout: harvest approved lessons, user preferences, reusable project patterns, and next-kickoff safeguards.
18. When a project may have gone stale, run a research refresh and log what changed before pushing ahead with the old plan.
19. For websites, web apps, dashboards, games with UI, mobile screens, forms, and visual tools, add a UI/UX lane before or alongside build work: use `ui-ux-designer`, `frontend-builder`, and `/ui-review` when the full engine is installed; otherwise write equivalent packets manually.

## Reality Check

Project OS is a workflow template. Do not overclaim capabilities.

- Say `implemented` only for files, scripts, tests, and behaviors that exist and were verified.
- Say `optional` for OSVec, GraphOS, actual different-model execution, browser QA, external research tools, and swarm runners unless those tools are actually configured in the current project.
- If a platform cannot route sub-agents to different models, record that in `blackboard/11-model-routing.md`.
- If GraphOS, OSVec, browser QA, containers, or network controls were not actually used, record `Not used`, `Unavailable`, or `Not configured`; do not imply they ran.
- If `memory/build_graph.py`, `memory/osvec_adapter.py`, or legacy `memory/turbovec_adapter.py` exists, do not say the graph/vector layer is unavailable. Say it is available locally but may need activation: build GraphOS with `python3 memory/build_graph.py --root blackboard` or a run folder, and verify OSVec with `python3 memory/osvec_adapter.py selftest` or the legacy adapter selftest.
- Markdown rules are not security enforcement. Treat sandboxing, egress filtering, and container isolation as separate capabilities that must be verified before relying on them.
- Keep current artifacts separate from draft, test, superseded, or broken artifacts.
- UI quality is a real deliverable. For frontend work, record responsive layout, accessibility, interaction states, visual direction, and browser QA status instead of treating UI polish as optional.

## Blackboard Read Gate

Do not act from memory on serious Project OS work. Before planning, building, reviewing, delivering, or approving, read the current blackboard files that govern the task and report a short `Context Used` summary.

For Mini Swarm and Full Swarm runs, use `context-scout` on the smallest available model when subagents are available. Its job is to read the blackboard cheaply and hand the heavier agents a compact context packet. If the host cannot run subagents or route smaller models, the main agent must do the read gate itself and record that limitation in `blackboard/11-model-routing.md`.

Minimum read set for most work:

- `blackboard/00-project-goal.md`
- `blackboard/03-decisions.md`
- `blackboard/04-risks.md`
- `blackboard/06-open-questions.md`
- `blackboard/07-approved-plan.md`
- relevant packets in `blackboard/packets/`

Do not overwrite early decisions or risks. Decision and risk logs are append-only: add a new dated entry and mark older entries `Superseded` instead of deleting or rewriting them.

## Context Cache Hygiene

Long AI sessions can become expensive because the active conversation, tool outputs, instructions, and code context may be written or rewritten into provider prompt caches. Cheap cache reads are useful, but repeated cache writes in a growing session can dominate the bill.

For serious runs:

- Prefer blackboard files, packets, receipts, and artifact paths over replaying the whole chat.
- Use a low-cost `context-scout` read gate when available, then pass compact context packets to heavier agents.
- Add a context/cache budget to loop specs: active phase, context sources, max iterations, phase handoff trigger, cache-write watch trigger, and fresh-thread trigger.
- When Max-effort is selected, ask the user whether auto-continuation should be `Auto`, `Ask first`, or `Warn only/Disabled`; record the answer before serious execution.
- At phase boundaries, write a handoff packet or receipt and continue from that packet in a fresh session when the current chat is mostly old context.
- In `09-cost-estimate.md`, track uncached input, output, cached reads, cached writes, and cost when usage data exposes them.
- For local Codex logs under `~/.codex/sessions`, use `payload.info.last_token_usage` for rollups, or the final `payload.info.total_token_usage` per session file as a cross-check. Never sum every `total_token_usage` row because it is cumulative. Treat `cached_input_tokens` as cached reads, not cache writes; these logs do not expose `cache_creation_input_tokens`.
- If cache writes exceed half of AI workflow cost, or cache-write tokens are roughly 10x larger than useful new work, pause at the next safe point, summarize state, and restart from the blackboard.
- If auto-continuation is `Auto` and the host supports thread creation/forking, create the fresh continuation from the handoff packet without asking again. If thread creation is unavailable, write the packet and give the user the continuation prompt. If the setting is `Ask first`, ask before creating/forking. If it is `Warn only/Disabled`, warn and keep continuation manual.
- Never paste raw request logs or full transcripts into the blackboard. Record totals, time window, attribution filter, source, confidence, and privacy notes.

## Friend Review Mode

When the user asks for critique, publishing readiness, or friend review, act as an auditor first.

Check:

- Is the setup understandable to a non-expert?
- Are implemented features separated from optional future tooling?
- Are private-memory and raw-import paths ignored by Git?
- Are there local paths, personal names, private project names, raw chats, secrets, or vendor-specific private branding?
- Does a blank test install create the expected files?
- Are delivery reports honest about what was verified and what was not?

## Execution Levels

### Solo Agent Loop

Use for simple tasks.

Goal -> Context -> Draft -> Evaluate -> Revise -> Approve

### Mini Swarm

Use for serious but contained work.

Default roles:

- Planner
- Researcher or Builder
- Reviewer

### Full Swarm

Use for large projects, apps, businesses, audits, research, or complex personal systems.

Default roles:

- CEO Agent
- CFO Agent
- Board Agents
- UI/UX Designer and Frontend Builder when the project has a user interface
- Worker Agents
- Evaluator Agent
- Memory and Blackboard Agent

Prefer flat stages over giant nested swarms unless the project is genuinely large.

## Research Refresh

Use a research refresh when:

- the project has been sitting for a while
- the market moved
- new AI tools or models became relevant
- competitors shipped meaningful updates
- the user asks what is popular now
- you suspect the plan is becoming stale

The refresh is a focused update pass, not a full restart.

If the assistant supports slash commands or prompt aliases, `/research refresh` should run this same refresh workflow.

Check:

- what changed
- what still holds
- what users now expect
- what features or workflows are newly standard
- whether the plan, stack, cost, or routing should change

Log the result in:

- `blackboard/02-research.md`
- `blackboard/03-decisions.md`
- `blackboard/04-risks.md`
- `blackboard/09-cost-estimate.md`
- `blackboard/16-research-router.md`
- `blackboard/20-research-refresh.md`

If web or market facts may have changed, use current research instead of stale memory.

## UI/UX And Frontend Work

When a run includes a user interface, plan the interface before approving implementation.

Use the UI lane to define:

- primary user workflow and first usable screen
- information hierarchy, navigation, controls, and expected states
- responsive layout for mobile and desktop
- accessibility basics: labels, keyboard path, focus states, contrast, touch targets, and reduced motion when relevant
- visual direction that fits the domain rather than generic decoration
- browser QA checks, screenshots, or manual viewport checks needed before approval

If the full engine is installed, use `ui-ux-designer` for the design packet, `frontend-builder` for implementation, and `/ui-review` for the UI quality gate. For web artifacts, run `python3 memory/browser_qa.py <path>` when available and log whether browser QA passed, failed, or was unavailable.

## Model Routing Plan

Do not blindly put every sub-agent on the strongest model.

- Use strong/frontier models for strategy, architecture, hard debugging, security, final review, ambiguous reasoning, and user-facing synthesis.
- Use smaller/faster/local models for extraction, formatting, file inventory, checklist updates, and simple summaries when the platform allows it.
- If sub-agents must inherit the parent model because the platform does not expose actual different-model execution, record that limitation in `blackboard/11-model-routing.md`.
- In Max-effort mode, default toward stronger agents, but still avoid obviously wasteful max-model use on trivial work.
- Use smaller context windows or fresh continuations for mechanical phases when the prior chat history is no longer needed.

## Memory

Memory is optional and local-first.

Use this order:

1. Current project blackboard.
2. Optional GraphOS, powered by Graphify when configured.
3. Optional OSVec, powered by TurboVec when configured, or `blackboard/08-memory-index.md`.
4. User-approved chat memory summaries.
5. Raw private exports only when the user explicitly asks.

Never store secrets, passwords, API keys, private credentials, or unnecessary sensitive personal data.

Activation guard: if a capability check says no graph/vector artifact exists but local helper scripts are present, run or recommend the local activation commands before falling back to markdown-only memory. Do not confuse missing external Graphify/TurboVec CLIs with missing Project OS local memory helpers.

## Self-Improvement Loop

Project OS should improve from run to run, but only through reviewed memory. Do not silently rewrite instructions or promote private data.

At closeout, update:

- `blackboard/19-memory-harvest.md`
- `memory/self-improvement-loop.md`
- `blackboard/08-memory-index.md` when a memory is approved for reuse

Capture:

- user-preference: how the user likes work planned, verified, explained, or delivered
- project-pattern: reusable workflows, checklists, structures, or design choices
- lesson: mistakes, blockers, stale assumptions, or quality failures to avoid
- safeguard: a future kickoff or closeout check that would have prevented a problem
- rejected-memory: something intentionally not stored because it is private, unverified, irrelevant, or sensitive

Before the next serious run, read the relevant approved entries and ask: `What should we do differently this time because of previous runs?`
