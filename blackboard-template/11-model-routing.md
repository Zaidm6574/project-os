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

If the platform forces sub-agents to inherit the parent model, record that limitation here.

