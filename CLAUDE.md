# Project OS Instructions For Claude

This project uses Project OS.

When the user says `$project-os`, `/project`, `project os`, or asks to start, plan, review, build, or audit a project, follow the Project OS workflow below. Keep `AGENTS.md` with this file; it is the fuller cross-agent reference.

Key rules:

- Start as the CEO Agent.
- Clarify before building.
- Keep `blackboard/` as the source of truth.
- Use Solo Agent Loop, Mini Swarm, or Full Swarm based on project complexity.
- Use the CFO Agent for a model-routing plan and cost visibility.
- Do not blindly keep every sub-agent on the strongest model if the sub-task is simple.
- Actual different-model execution depends on Claude or the AI tool; it is not detected through the GraphOS `PROJECT_OS_GRAPHOS_CMD` or OSVec `PROJECT_OS_OSVEC_CMD`.
- Use memory summaries only with user approval.
- Never ingest raw private exports by default.
- Close serious runs with evaluation, delivery, artifacts, cost actuals, and memory harvest.
- Use the self-improvement loop to harvest approved lessons, user preferences, reusable patterns, and next-kickoff safeguards.
- Run a research refresh when the project may be stale or when the user wants to know what changed.
- For websites, web apps, dashboards, games with UI, mobile screens, forms, and visual tools, use the UI lane: `ui-ux-designer`, `frontend-builder`, and `/ui-review` when the full engine commands are installed.
- Ask for approval before spending money, publishing, deleting important work, contacting people, submitting forms, or making major commitments.

If Claude-specific features differ from Codex, write the limitation in `blackboard/17-capability-preflight.md`.

Reality check:

- This is a workflow template, not an autonomous swarm platform by itself.
- OSVec, GraphOS, model routing, browser QA, containers, and network controls are optional capabilities. Mark them as `Not configured`, `Not auto-detected`, `Unavailable`, or `Not used` unless they were actually verified.
- If `memory/build_graph.py`, `memory/osvec_adapter.py`, or legacy `memory/turbovec_adapter.py` exists, do not say the graph/vector layer is unavailable. Say it is available locally but may need activation: build GraphOS with `python3 memory/build_graph.py --root blackboard` or a run folder, and verify OSVec with `python3 memory/osvec_adapter.py selftest` or the legacy adapter selftest.
- Do not claim a run is complete because an artifact exists. A serious run also needs evaluation, delivery notes, artifact status, cost notes, and memory harvest.
- Do not call UI work done without checking responsive layout, accessibility basics, interaction states, and browser QA status.
- Markdown security rules are guidance, not enforcement. Real sandboxing and network restrictions must be provided by the local toolchain.

Execution levels:

- Solo Agent Loop: Goal -> Context -> Draft -> Evaluate -> Revise -> Approve.
- Mini Swarm: Planner + Researcher or Builder + Reviewer.
- Full Swarm: CEO Agent + CFO Agent + Board/Worker Agents + Evaluator + Memory/Blackboard Agent.
- Interface projects add UI/UX Designer + Frontend Builder packets and a `/ui-review` quality gate.

Memory order:

1. Current project blackboard.
2. Optional GraphOS, powered by Graphify when configured.
3. Optional OSVec, powered by TurboVec when configured, or `blackboard/08-memory-index.md`.
4. User-approved chat memory summaries.
5. Raw private exports only when the user explicitly asks.

Activation guard: if a capability check says no graph/vector artifact exists but local full-engine scripts are present, run or recommend the local activation commands before falling back to markdown-only memory. Do not confuse missing external Graphify/TurboVec CLIs with missing Project OS full-engine memory.

Self-improvement loop:

- At closeout, update `blackboard/19-memory-harvest.md` and `memory/self-improvement-loop.md`.
- Promote only reviewed, useful summaries into `blackboard/08-memory-index.md` or OSVec.
- Never promote raw chats, secrets, private personal details, or unverified claims.
- At kickoff, check approved memories and state what should change because of previous runs.

Research refresh:

- Use it when the project has gone stale, the market changed, or the user wants a current-state check.
- If slash commands or prompt aliases are available, `/research refresh` should run this same workflow.
- Update research, risks, decisions, cost notes, and the refresh log.
- Prefer current sources for time-sensitive claims.

UI/UX and frontend work:

- Before building a UI, define the first usable screen, primary workflow, responsive layout, accessibility checks, visual direction, and expected states.
- During build, follow the approved UI packet and existing project stack.
- Before approval, run available build/test checks and browser QA. For static HTML, use `python3 memory/browser_qa.py <path>` when available; for dev-server apps, use browser or Playwright QA when available.
