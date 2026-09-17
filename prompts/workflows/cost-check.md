---
name: cost-check
description: Estimate cost and set model routing (CFO)
argument-hint: [optional: balanced | cost-aware | max-effort]
capabilities: [subagents, websearch]
claude-tools: Bash Read Write Edit Grep Glob Task WebSearch
---

## Active workspace

Resolve `<run-root>` explicitly from the run argument or the caller's packet before reading or writing. For a named run it is `runs/<slug>/`; do not choose the newest run automatically. For project-level work without a named run, explicitly select `blackboard/`. All numbered notes and `packets/` paths below are relative to that selected root. Pass the same root to every delegated role and follow-up workflow. Shared `blackboard/` notes are read-only context during a named run; promote reviewed cross-run lessons separately. See **Active workspace and shared notes** in `AGENTS.md` for helper arguments and project-level exceptions.

Run a CFO cost pass. Requested cost mode (if any): **{{ARGUMENTS}}**

Run the `project-os-cfo` role — launch it as a subagent if your runtime has them, otherwise adopt the role yourself. It should:
1. Inventory the models/tools I actually have (update the table in `<run-root>/11-model-routing.md`).
2. Set/confirm the active cost mode and map it to subagent models.
3. Fill in `<run-root>/09-cost-estimate.md` using the arithmetic formula (expected calls x avg tokens x price), plus tools, hosting, human time, and a risk buffer.
4. Add or refresh the context/cache budget: context sources to carry/drop, handoff packet path, fresh-session trigger, and cache-write watch trigger.
5. If usage logs expose cache fields, separate uncached input, output, cached reads, cached writes, and cost. Treat cache writes as a first-class AI workflow cost.
6. For Codex local logs, use `python3 memory/cost_actuals.py --codex-sessions --sessions-dir /path/to/run-only-sessions` only after verifying that every selected file belongs to this run, or apply the same rule by hand: sum `payload.info.last_token_usage`, never every cumulative `total_token_usage` row. Treat `cached_input_tokens` as cached reads, not cache writes. This mode prints token activity only; it has no project/time filtering, event deduplication, dollar pricing or Markdown writer. Record attributed totals manually and mark unavailable dollars unmeasured. For Claude, always pass `--transcript` plus the run report `--target`; see `deliver` for the runtime-specific commands.
7. Recommend where to save without hurting quality, when to flip the Max-effort toggle, and when to checkpoint into a fresh session.

Report the estimate and routing in plain English and tell me what (if anything) needs my decision.
