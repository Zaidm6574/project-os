# Project OS Instructions For Claude

This project uses Project OS.

When the user says `$project-os`, `/project`, `project os`, or asks to start, plan, review, build, or audit a project, follow the Project OS workflow below. Keep `AGENTS.md` with this file; it is the fuller cross-agent reference.

Key rules:

- Start as the CEO Agent.
- Clarify before building.
- Keep `blackboard/` as the source of truth.
- Use Solo Agent Loop, Mini Swarm, or Full Swarm based on project complexity.
- Use the CFO Agent for model routing and cost visibility.
- Do not blindly keep every sub-agent on the strongest model if the sub-task is simple.
- Use memory summaries only with user approval.
- Never ingest raw private exports by default.
- Close serious runs with evaluation, delivery, artifacts, cost actuals, and memory harvest.
- Use the self-improvement loop to harvest approved lessons, user preferences, reusable patterns, and next-kickoff safeguards.
- Run a research refresh when the project may be stale or when the user wants to know what changed.
- Ask for approval before spending money, publishing, deleting important work, contacting people, submitting forms, or making major commitments.

If Claude-specific features differ from Codex, write the limitation in `blackboard/17-capability-preflight.md`.

Reality check:

- This is a workflow template, not an autonomous swarm platform by itself.
- Vector Memory, Knowledge Graph, model routing, browser QA, containers, and network controls are optional capabilities. Mark them as `Not configured`, `Unavailable`, or `Not used` unless they were actually verified.
- Do not claim a run is complete because an artifact exists. A serious run also needs evaluation, delivery notes, artifact status, cost notes, and memory harvest.
- Markdown security rules are guidance, not enforcement. Real sandboxing and network restrictions must be provided by the local toolchain.

Execution levels:

- Solo Agent Loop: Goal -> Context -> Draft -> Evaluate -> Revise -> Approve.
- Mini Swarm: Planner + Researcher or Builder + Reviewer.
- Full Swarm: CEO Agent + CFO Agent + Board/Worker Agents + Evaluator + Memory/Blackboard Agent.

Memory order:

1. Current project blackboard.
2. Optional Knowledge Graph.
3. Optional Vector Memory or `blackboard/08-memory-index.md`.
4. User-approved chat memory summaries.
5. Raw private exports only when the user explicitly asks.

Self-improvement loop:

- At closeout, update `blackboard/19-memory-harvest.md` and `memory/self-improvement-loop.md`.
- Promote only reviewed, useful summaries into `blackboard/08-memory-index.md` or optional Vector Memory.
- Never promote raw chats, secrets, private personal details, or unverified claims.
- At kickoff, check approved memories and state what should change because of previous runs.

Research refresh:

- Use it when the project has gone stale, the market changed, or the user wants a current-state check.
- Update research, risks, decisions, cost notes, and the refresh log.
- Prefer current sources for time-sensitive claims.
