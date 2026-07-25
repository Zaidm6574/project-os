# Project OS Instructions

> **Naming:** "Project OS" is a descriptor, not a brand. Internal module names: vector memory **Mneme** (formerly TurboVec/OSVec — `memory/mneme_adapter.py`), knowledge graph **Arachne** (formerly Graphify — output `graphify-out/graph.json`). Historical run logs keep old names. Private working notes live in `blackboard/` (gitignored).

> **Single source of truth.** This file is the canonical Project OS doctrine for EVERY runtime — Codex reads it natively; Claude reads `CLAUDE.md`, which is a thin pointer back here plus Claude-only notes. Doctrine changes land in THIS file, never only in `CLAUDE.md`. The one deliberate exception: the Non-Negotiable Safety Rules below are mirrored verbatim in `CLAUDE.md` because that file is auto-loaded — if you edit those rules, update both blocks in the same commit.

This project uses Project OS.

## Non-Negotiable Safety Rules

- **Never `git push` to origin without explicit user approval in the same conversation turn.** Ask first, always — even mid-run, even at closeout.
- **Never include personal/local tooling in template commits.** If a file is hardcoded to personal or local paths, engines, or private data, it belongs in `.gitignore`, not in a public push. When in doubt, ask before committing to the public repo.
- **An artifact existing is not a run being complete.** A serious run also needs evaluation, delivery notes, artifact status, cost notes, and memory harvest before it may be called done.
- Actual different-model execution depends on the host AI tool; it is not detected through the GraphOS `PROJECT_OS_GRAPHOS_CMD` or OSVec `PROJECT_OS_OSVEC_CMD` environment variables.

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
- If `memory/build_graph.py`, `memory/mneme_adapter.py`, `memory/osvec_adapter.py`, or legacy `memory/turbovec_adapter.py` exists, do not say the graph/vector layer is unavailable. Say it is available locally but may need activation: build the graph with `python3 memory/build_graph.py --root blackboard` or a run folder, and verify vector memory with `python3 memory/mneme_adapter.py build` (core) or `python3 memory/osvec_adapter.py selftest` (full engine).
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
- Are private-memory and raw-import paths ignored by Git? Does `.gitignore` protect private memory, imports, vector stores, graph output, environment files, and secrets?
- Scan tracked source AND Git metadata: are there local paths, personal names, private project names, raw chats, secrets, credentials, or vendor-specific private branding?
- Does a blank test install create the expected files?
- Are delivery reports honest about what was verified and what was not? Do artifact manifests distinguish current outputs from drafts, tests, superseded files, and known gaps?
- Does the README claim only implemented behavior? Do not present optional or unverified capabilities as active.

Treat any real private-data hit, unsafe default, broken install, or unsupported readiness claim as a publishing blocker.

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

## Loop Tooling

These scripts exist and are smoke-tested. Use them; do not reimplement ad hoc.

- **Locking.** Any write to a shared blackboard file or `~/.project-os/central-brain/shared-brain.jsonl` from a swarm or a second session goes through `scripts/bb_lock.py` (`acquire`/`release`, or `append` for JSONL lines, `run` to hold a lock around a command). Locks stale-reap after 60s.
- **Promptsmith.** UI/creative worker prompts are compiled, not hand-rolled: `scripts/promptsmith.py --task "..."` fetches a brain brief and emits BOTH the worker prompt and the evaluator rubric from the same brief into `blackboard/packets/`. In Claude sessions you may call `mcp__brain__brief` yourself and pass `--brief-file`. DON'T violations are auto-fail. If the brain is unreachable the packets say BRAIN-UNAVAILABLE — never invent taste.
- **Evolution records.** In evaluate → reject/approve → revise loops, record every scored variant with `scripts/evolution.py record`, and evolve the next variant from the BEST-scoring one (`evolution.py next`), never merely the latest. On rejection, also write a lesson line to `memory/self-improvement-loop.md`.
- **Plans as data.** Mini/Full Swarm plans are JSON artifacts in `blackboard/plans/` via `scripts/plan_artifact.py` (create → validate → **approve = human gate** → compile to worker packets → complete). `compile` refuses unapproved plans (`--force` overrides approval only; validation always runs).
- **Brain scale.** Run `scripts/brain_scale.py` at kickoff of serious runs. With a neural index live the active-entries ceiling is a soft 400; relieve pressure with `scripts/brain_archive.py` (moves entries to the archive tier — they STAY semantically searchable via Mneme) rather than deleting. Stale `interest` entries (>60d) are flagged as archive candidates automatically.
- **Taste gate.** Promptsmith + rubric make the Evaluator a taste *pre-gate*; the human gate stays final for aesthetics. Motion/animation is verified by filmstrip, never a single frame.
- **Shared-brain writes.** Append lessons via `scripts/brain_append.py` (validates JSON, locks, auto-rebuilds the Mneme index). A raw append leaves the index stale.
- **Checker enforcement.** `plan_artifact.py validate` REJECTS multi-step plans unless (a) every non-checker work step is covered by some checker's `depends_on` — no work ships unreviewed, (b) >=1 work-dependent checker carries a `verification` object whose `method` and `expected` are nonempty, non-placeholder strings ("-", "n/a", "todo", "tbd", "..." and similar are rejected; `evidence` is optional and not validated), and (c) any explicit `checks: [step ids]` binding names known work steps the checker also depends_on. Checker roles match broadly (check/verif/review/critic/evaluat/test/audit/red-team). This is structural enforcement — it proves the plan *contains* a real check, not that the check *ran*; runtime evidence stays the human gate's job. The same validation re-runs at `approve` and `compile` (even with `--force`). Design plans maker/checker from the start.
- **Nightly heartbeat.** Schedule `scripts/os_nightly.py` daily via launchd/cron: brain-scale gauge, stale-lock reap, stale Draft packets, stuck plans -> newest-first entries in `blackboard/22-automation-log.md`. Read that file at kickoff instead of re-deriving drift.
- **Neural retrieval.** `memory/mneme_adapter.py` embeds with nomic-embed-text via local Ollama (auto-falls back to lexical if Ollama is down; refuses mixed-embedder queries). Rebuild: `python3 memory/mneme_adapter.py build`. **No Ollama on this machine?** Lexical mode matches words, not meaning — compensate by querying 2–3 phrasings, and for high-stakes recall read `memory/self-improvement-loop.md` and `blackboard/08-memory-index.md` directly and judge relevance yourself instead of trusting one keyword query.
- **Cockpit.** Command-center has a Loops view (`/api/loops-state`): gauge, plan lifecycle, evolution records, live locks. If a loop artifact doesn't show there, it isn't real.
- **Kickoff sequence.** Start supervised runs with the deterministic sequence: gauge -> recall -> promptsmith -> plan-gate -> evolve. Wrap it in a Claude Code skill (e.g. `/project`) rather than improvising kickoff from prose.
- **Worktree isolation.** Parallel builder steps that mutate the same repo each get a worktree: `scripts/wt.py create/list/merge/remove` (lives in `~/.project-os/worktrees/`, branch `wt/<name>`; merge/remove refuse dirty or unmerged state without `--force`). Plan steps opt in with `"isolation": "worktree"`. Run `wt.py list` before trusting ANY checkout — the stale-tree trap is real.
- **Memory harvest.** Run closeouts flow to the brain via `scripts/harvest.py`: `status` (nightly reports unharvested done runs) → `scan <run>` (extracts 19-memory-harvest.md bullets, dedupes vs shared brain, stages JSONL proposals in packets/) → human/agent review → `apply` (brain_append + one reindex + `.harvested` marker). Nothing enters the brain without the apply step.
- **Inter-session bus (optional).** Claude↔Claude peer messaging via the `inter-session` plugin (join per session with `/inter-session:inter-session`). Codex joins as a real peer via `scripts/codex_bus.py` (`setup`/`serve`/`list`/`send`/`listen`). The bus is ephemeral signaling; `shared-brain.jsonl` stays the durable layer. Token offers no protection against untrusted local code.

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

If the full engine is installed, use `ui-ux-designer` for the design packet, `frontend-builder` for implementation, and `/ui-review` for the UI quality gate. For static HTML artifacts, run `python3 memory/browser_qa.py <path>` when available; for dev-server apps, use browser or Playwright QA when available. Always log whether browser QA passed, failed, or was unavailable.

## Code Orientation

Orient before explaining code. Before summarizing how something works, establish the entry point, the relevant path through the system, upstream callers, downstream effects, the tests that pin the behavior, and what remains unknown. Label each load-bearing claim by how you established it: read directly from source, derived from a structural relationship, or inferred and still needing verification. A fluent explanation that leaves the developer unable to name the entry point or predict what a change breaks has failed, regardless of how correct it sounds.

Never let a generated structural map replace reading the code. A map orients you toward the right files; the source and its tests are the authority. If any structural evidence is stale or missing, say so and continue from direct source inspection rather than narrating plausible architecture.

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

## Workflows (every runtime)

The Project OS workflows — `kickoff`, `status`, `evaluate`, `deliver`, `ui-review`, `new-run`, `adopt-project`, `board-review`, `cost-check`, `memory-sync`, `save-chat` — live in runtime-neutral form in `prompts/workflows/`. That directory is the single source; nothing about a workflow is Claude-only.

- **To run one in any runtime:** open `prompts/workflows/<name>.md` and follow it. The canonical file is the complete instruction — there is no host-specific step to translate. `prompts/workflows/INDEX.md` lists all of them with their arguments.
- **Claude** additionally exposes each as a slash command (`/kickoff`, `/status`, …). Those live in `addons/full-engine/staged/commands/` and are **generated** from `prompts/workflows/` — never hand-edit them.
- **After changing a workflow**, run `python3 scripts/sync_runtime_assets.py` to regenerate the adapters. `--check` fails if any generated asset drifted from its source; the test suite enforces this, so a hand-edit that only improves Claude's copy is caught rather than silently leaving other runtimes behind.

Workflows declare the capabilities they want (`subagents`, `websearch`, `task-tracking`, `browser`). **A runtime that lacks a capability does the work inline itself — it does not skip the step and does not refuse** — and records the substitution in `blackboard/17-capability-preflight.md`. A subagent is a role plus a fresh context; a runtime without them can still adopt the role, it just loses the context isolation.

## Capability Preflight (Claude/Codex parity)

If Claude-specific features differ from Codex, record the limitation in `blackboard/17-capability-preflight.md` before serious work. The workflow layer above is the mechanism: capability gaps are declared per workflow and degrade to inline work, so the difference is logged rather than felt as one runtime silently being weaker.

## Cursor Cloud specific instructions

This repository is a Python 3 CLI/template tool ("Project OS"), not a long-running service. There is no server to start, no build step, and no watcher/dev server. Everything runs as one-shot Python/shell commands.

- Runtime: Python 3 standard library only for the core. The only optional dependency is `numpy`, used solely by the OSVec memory layer (`addons/full-engine/memory/osvec_adapter.py`). `turbovec` is optional; when absent the adapter falls back to a numpy brute-force index. The startup update script installs `numpy`; do not rely on `addons/full-engine/memory/requirements.txt` for install because it also lists optional `turbovec`.
- Tests: `python3 -m unittest discover -s tests -v` (same command CI runs, see `.github/workflows/test.yml`). No network required.
- Lint: no linter/formatter is configured in this repo (no ruff/flake8/black/mypy config). There is no lint command to run.
- Running the app: it is an installer that scaffolds a Project OS workspace into a target directory. Do NOT run it against the repo root; use a scratch target, e.g. `./install.sh /tmp/hello-project --full-engine --claude-engine --check-tools`.
- Quick verification of the full-engine helpers (run from inside a bootstrapped target dir): `python3 memory/osvec_adapter.py selftest`, `python3 memory/score_rubric.py --selftest`, `python3 memory/build_graph.py --root blackboard`, `python3 brain/brain.py save-chat --summary "..." --kind lesson`. `python3 memory/new_run.py <name> --tier solo` intentionally refuses to overwrite an existing run dir (this is expected behavior, not an error).

## Context & cache economy (added 2026-07-05)

Live billing analysis (2026-07-05) confirmed the audit's #1 cost finding: the **orchestrator's context — not the subagents — dominates spend.** Four rules, enforced by `memory/context_budget.py`:

1. **Kickoff preflight:** run `python3 memory/context_budget.py`; record its output line in the run's `09-cost-estimate.md`. A fat harness (>25 enabled plugins or >200K baseline) gets fixed **before** wave 1 — the CFO cannot route costs it never measured.
2. **Wave-boundary fresh-session rule:** when the check says CHECKPOINT (>200K live context), close the wave (packets + decisions to the blackboard), then continue in a **fresh session** that re-reads only the blackboard. Never let one orchestrator context run for hours — auto-compact summaries lose decisions AND re-write the whole cache prefix at 12.5x read price.
3. **Fewer, bigger subagents:** each spawn cache-writes ~36K of agent prompt. 3 agents x 10 items beats 30 agents x 1.
4. **Overnight wakes:** accept one cold cache write per wake. Never keep-warm ping at <5-minute intervals — six warm pings cost more than one cold write.
