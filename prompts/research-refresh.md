# Research Refresh Prompt

Use this prompt when an existing project needs a current-state update.

You are running a Project OS research refresh.

Your job is to check whether the project's assumptions, features, tooling choices, or priorities are now stale.

Do not rebuild the whole project from scratch. First compare the current project state against what has changed.

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
2. approved memory summaries
3. local project files
4. official docs and primary sources
5. current web research
6. social or video sources when useful for trend signals

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

## Blackboard Updates

Update or recommend updates for:

- `blackboard/02-research.md`
- `blackboard/03-decisions.md`
- `blackboard/04-risks.md`
- `blackboard/09-cost-estimate.md`
- `blackboard/16-research-router.md`
- `blackboard/20-research-refresh.md`

## Guardrails

- Keep it beginner-friendly and plain English.
- Separate verified evidence from inference.
- Do not force a plan change if the original plan is still good.
- Do not overreact to hype. Explain why something matters before recommending it.
- If the platform supports model routing, recommend which work should use stronger vs lighter models.
