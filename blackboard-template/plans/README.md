# Plans As Data

Wave plans are JSON artifacts here, not prose — inspectable before any agent runs,
replayable after (open-multi-agent `planOnly` / `createPlanArtifact` / `runFromPlan`
pattern, adopted 2026-07-02).

Lifecycle: `planned` → `approved` (human gate) → `running` → `done`

```
run_root="runs/example-run"  # explicitly selected existing run
python3 scripts/plan_artifact.py create --id "$run_root/plans/wave-1.json" --goal "..." --steps-file steps.json
python3 scripts/plan_artifact.py validate "$run_root/plans/wave-1.json"
python3 scripts/plan_artifact.py approve "$run_root/plans/wave-1.json"  # after approval
python3 scripts/plan_artifact.py compile "$run_root/plans/wave-1.json"
python3 scripts/plan_artifact.py complete "$run_root/plans/wave-1.json" --step <step-id>
```

Schema (`plan/v1`): `{schema, id, goal, created, status, steps: [{id, role, task,
model_hint?, isolation?, depends_on[], outputs[], done}]}`

Rules:

- `compile` normally refuses unapproved plans. The deliberate `--force` override bypasses approval only; structural validation still runs. Approval metadata records the caller's assertion, not authenticated human consent.
- **Maker/checker is enforced, not advisory:** `validate` rejects any multi-step plan
  unless every non-checker work step is covered by some checker's `depends_on`, and at
  least one work-dependent checker carries `verification` with non-placeholder
  `method`/`expected`. Any explicit `checks: [step ids]` must match its `depends_on`.
- Steps that mutate a shared repo in parallel set `"isolation": "worktree"` — the
  compiled packet instructs the worker to build in its own git worktree
  (`scripts/wt.py`) and merge back, instead of racing other workers in one checkout.
- Select the active root explicitly. For named runs, use `--id "$run_root/plans/wave-1.json"` at creation and that same JSON path for validate/approve/compile/complete. Packets then land beside `plans/`, under `"$run_root/packets/"`. Bare IDs use project-level `blackboard/plans/` and `blackboard/packets/`; they do not auto-select a run.
- Model hints follow `<run-root>/11-model-routing.md` — cheap models for
  extraction/formatting, strong models for judgment.
