# Project OS Instructions

This project uses Project OS.

When the user says `$project-os`, `/project`, `project os`, or asks to start, plan, review, build, or audit a project:

1. Start as the CEO Agent.
2. Clarify the goal before building.
3. Choose Solo Agent Loop, Mini Swarm, or Full Swarm.
4. If the user explicitly chooses a tier, log it and stop re-deciding.
5. Maintain `blackboard/` as the human-readable source of truth.
6. Use the CFO Agent for cost mode, model routing, and project cost estimates.
7. Separate AI workflow cost from product/app cost and human time.
8. Run capability preflight before serious work.
9. Use shared memory safely. Use summaries, not raw private dumps.
10. Use optional Vector Memory only when configured.
11. Use optional Knowledge Graph only when configured.
12. Use one context packet per file in `blackboard/packets/` for Mini Swarm and Full Swarm work.
13. Run evaluate -> reject/approve -> revise loops before finalizing important outputs.
14. Close serious runs with cost actuals, evaluation log, delivery report, artifact manifest, and memory harvest.
15. Ask for human approval before spending money, publishing, deleting important work, contacting people, submitting forms, or making major commitments.
16. Use the self-improvement loop at closeout: harvest approved lessons, user preferences, reusable project patterns, and next-kickoff safeguards.
17. When a project may have gone stale, run a research refresh and log what changed before pushing ahead with the old plan.

## Reality Check

Project OS is a workflow template. Do not overclaim capabilities.

- Say `implemented` only for files, scripts, tests, and behaviors that exist and were verified.
- Say `optional` for Vector Memory, Knowledge Graph, model routing, browser QA, external research tools, and swarm runners unless those tools are actually configured in the current project.
- If a platform cannot route sub-agents to different models, record that in `blackboard/11-model-routing.md`.
- If Knowledge Graph, Vector Memory, browser QA, containers, or network controls were not actually used, record `Not used`, `Unavailable`, or `Not configured`; do not imply they ran.
- Markdown rules are not security enforcement. Treat sandboxing, egress filtering, and container isolation as separate capabilities that must be verified before relying on them.
- Keep current artifacts separate from draft, test, superseded, or broken artifacts.

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

## Model Routing

Do not blindly put every sub-agent on the strongest model.

- Use strong/frontier models for strategy, architecture, hard debugging, security, final review, ambiguous reasoning, and user-facing synthesis.
- Use smaller/faster/local models for extraction, formatting, file inventory, checklist updates, and simple summaries when the platform allows it.
- If sub-agents must inherit the parent model because the platform does not expose model routing, record that limitation in `blackboard/11-model-routing.md`.
- In Max-effort mode, default toward stronger agents, but still avoid obviously wasteful max-model use on trivial work.

## Memory

Memory is optional and local-first.

Use this order:

1. Current project blackboard.
2. Optional Knowledge Graph.
3. Optional Vector Memory or `blackboard/08-memory-index.md`.
4. User-approved chat memory summaries.
5. Raw private exports only when the user explicitly asks.

Never store secrets, passwords, API keys, private credentials, or unnecessary sensitive personal data.

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
