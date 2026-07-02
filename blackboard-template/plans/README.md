# Plans As Data

Wave plans are JSON artifacts here, not prose — inspectable before any agent runs,
replayable after (open-multi-agent `planOnly` / `createPlanArtifact` / `runFromPlan`
pattern, adopted 2026-07-02).

Lifecycle: `planned` → `approved` (human gate) → `running` → `done`

```
python3 scripts/plan_artifact.py create --goal "..." --steps-file steps.json
python3 scripts/plan_artifact.py validate <id>      # schema + dep cycles
python3 scripts/plan_artifact.py approve  <id>      # human gate — required
python3 scripts/plan_artifact.py compile  <id>      # emits worker packets, topo order
python3 scripts/plan_artifact.py complete <id> --step <step-id>
```

Schema (`plan/v1`): `{schema, id, goal, created, status, steps: [{id, role, task,
model_hint?, isolation?, depends_on[], outputs[], done}]}`

Rules:

- `compile` refuses unapproved plans (that is the point of planOnly).
- **Maker/checker is enforced, not advisory:** any multi-step plan must include at
  least one step whose role is a checker/verifier/reviewer/evaluator, and that step
  must `depends_on` the work it checks. `validate` rejects plans without one.
- Steps that mutate a shared repo in parallel set `"isolation": "worktree"` — the
  compiled packet instructs the worker to build in its own git worktree
  (`scripts/wt.py`) and merge back, instead of racing other workers in one checkout.
- Compiled packets land in `blackboard/packets/` using the standard packet format.
- Model hints follow `blackboard/11-model-routing.md` — cheap models for
  extraction/formatting, strong models for judgment.
