# Research Refresh Prompt

## Active workspace

Resolve `<run-root>` explicitly from the run argument or the caller's packet before reading or writing. For a named run it is `runs/<slug>/`; do not choose the newest run automatically. For project-level work without a named run, explicitly select `blackboard/`. All numbered notes and `packets/` paths below are relative to that selected root. Pass the same root to every delegated role and follow-up workflow. Shared `blackboard/` notes are read-only context during a named run; promote reviewed cross-run lessons separately. See **Active workspace and shared notes** in `AGENTS.md` for helper arguments and project-level exceptions.

Use this prompt when an existing project needs a current-state update. It can be invoked directly as `/research`, "research refresh", or "suggest Project OS updates."

You are running a Project OS research refresh.

Your job is to check whether the project's assumptions, features, tooling choices, or priorities are now stale.

Do not rebuild the whole project from scratch. First compare the current project state against what has changed. Suggest updates, but do not auto-change major decisions unless the user explicitly approves them or asked you to apply the updates.

## Goals

You should identify:

1. what changed in the market, ecosystem, or tool landscape
2. what users now expect that may be missing
3. what competitors or comparable projects are doing
4. what new risks, opportunities, or simplifications appeared
5. whether the plan, scope, feature set, cost expectation, or model/tool choices should change

## Source Order

Use the best available sources in this order:

1. current blackboard and project artifacts
2. GraphOS, the native Project OS graph layer, when configured
3. OSVec, the native Project OS vector/memory layer, approved memory summaries, or the markdown memory index
4. local project files
5. official docs and primary sources
6. current web research
7. social or video sources when useful for trend signals

If a source is unavailable, say so clearly instead of pretending it was checked.

## Output Format

Produce:

1. Refresh summary
2. What changed
3. What did not change
4. New user expectations or trending features
5. Tooling or model updates that matter
6. Recommended plan changes
7. Recommended risk updates
8. Recommended cost updates
9. Confidence and evidence notes
10. Blackboard files to update
11. Project OS workflow updates to consider, if the refresh found a better operating pattern

## Blackboard Updates

Update or recommend updates for:

- `<run-root>/02-research.md`
- `<run-root>/03-decisions.md`
- `<run-root>/04-risks.md`
- `<run-root>/09-cost-estimate.md`
- `<run-root>/16-research-router.md`
- `<run-root>/20-research-refresh.md`

For major scope, roadmap, architecture, privacy, publishing, spending, or workflow changes, write a clear recommendation and ask for human approval before applying it. Smaller factual updates, source notes, and stale-research annotations can be applied directly when they do not change the approved direction.

## Guardrails

- Keep it beginner-friendly and plain English.
- Separate verified evidence from inference.
- Do not force a plan change if the original plan is still good.
- Do not overreact to hype. Explain why something matters before recommending it.
- If the platform supports model routing, recommend which work should use stronger vs lighter models.
- Be honest when GraphOS, OSVec, web access, or social/video research tools are unavailable.
- Do not install tools, upload private files, inspect raw private exports, or publish anything during a refresh unless the user explicitly approves that action.
