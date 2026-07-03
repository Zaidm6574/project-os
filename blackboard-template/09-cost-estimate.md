# Cost Estimate

Track cost for visibility. Do not make cost the main constraint unless the user chooses Cost-aware mode.

## Cost Mode

```text
Mode: Balanced / Cost-aware / Max-effort
Reason:
Chosen by:
```

## Split

| Cost Area | Estimate | Actuals | Confidence | Notes |
|---|---|---|---|---|
| AI workflow cost |  |  |  | Model/API usage, sub-agents, research, generation, review loops, uncached input, output, cached reads, and cached writes. |
| Product/app cost |  |  |  | Hosting, database, storage, domains, subscriptions. |
| Human time cost |  |  |  | Review, verification, support, maintenance. |

## Context Cache Budget

Use this section for long sessions or expensive loops.

```text
Active phase:
Context sources to carry:
Context sources to drop:
Handoff packet path:
Fresh-session trigger:
Cache-write watch trigger:
Max-effort auto-continuation:
```

Recommended default trigger: if cached-write cost is more than half of AI workflow cost, or cache-write tokens are roughly 10x larger than useful new work, pause at the next safe boundary, write a handoff packet or receipt, and continue from the blackboard in a fresh session.

## Request Log Actuals

Use provider request logs, API logs, usage dashboards, request IDs, token columns, and per-call cost columns when available.

| Date | Time Window | Attribution Filter | Model | Input | Output | Cached Read | Cached Write | Cost | Source | Confidence |
|---|---|---|---|---|---|---|---|---|---|---|

### Codex local session logs

Use this only for local activity rollups, not guaranteed account-wide billing.

```text
Source: ~/.codex/sessions
Preferred local activity estimate: sum payload.info.last_token_usage
Lower cross-check: final payload.info.total_token_usage per session file
Do not use: sum of every payload.info.total_token_usage row, because it is cumulative
Cached input meaning: cached reads / subset of input
Cache write status: not exposed unless provider logs include cache_creation_input_tokens
```

Do not paste raw logs, prompts, private responses, request bodies, API keys, or full transcripts here. Record only totals and enough attribution to trust the number.
