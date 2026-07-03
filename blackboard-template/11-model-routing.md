# Model Routing

## Active Mode

```text
Mode: Balanced / Cost-aware / Max-effort
Reason:
```

## Sub-Agent Model Routing

| Sub-Agent Work | Default Model Power | Use Strong Model When | Route Down When |
|---|---|---|---|
| Extraction, formatting, checklist updates | Low | Source is messy or user-facing | Mechanical and easy to verify |
| Research scouting | Low to medium | Sources conflict or facts are high-stakes | Only collecting links/summaries |
| Builder/coder | Medium to high | Architecture unclear, tests fail, security involved | Small local change |
| Reviewer/evaluator | High | Final or high-stakes output | Formatting-only review |
| CEO/CFO/strategy | High | Product, cost, or architecture decisions | Simple status update |
| Context read gate | Low | The blackboard is contradictory or safety-critical | Reading and summarizing known project state |

If the platform forces sub-agents to inherit the parent model, record that limitation here.

## Context And Cache Routing

| Situation | Preferred Route | Reason |
|---|---|---|
| Long active session with growing context | Write a handoff packet, then continue from the blackboard in a fresh session | Prevents repeated cache writes of old context from dominating cost. |
| Phase boundary after planning, research, build, or review | Close the phase with a receipt and artifact manifest update | Keeps durable state in files instead of chat history. |
| Heavier agent needs project context | Give it a compact `Context Used` packet from `context-scout` | Avoids every strong agent rereading the full blackboard. |
| Mechanical follow-up work | Use smaller model/context if the platform allows it | Strong models and huge context are often unnecessary for checklist or formatting work. |

## Max-Effort Auto-Continuation

Record this when Max-effort is active, so long sessions have an explicit fresh-thread handoff policy instead of silently accumulating cache-write cost.

```text
Max-effort auto-continuation: Auto / Ask first / Warn only (default: Ask first)
Behavior: On the cache-write watch trigger (cache-write cost above ~50% of AI workflow cost, or cache-write tokens roughly 10x useful new output), write a handoff packet. Auto creates/forks a fresh continuation when thread tools exist; Ask first asks before forking; Warn only leaves continuation manual. If thread tools are unavailable, Auto degrades to writing the packet plus a paste-into-new-chat prompt.
```
