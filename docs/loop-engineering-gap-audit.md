# Loop-engineering gap audit

Reference model: Cherny loop pattern carried as ledger-settled fact: schedule → triage → state → worktree → implementer → verifier → human gate.

Scoring: 0 = missing, 1 = documented/manual, 2 = implemented local primitive, 3 = integrated and enforced end to end.

| Component | Score | Canon evidence | Gap |
|---|---:|---|---|
| Automations / Scheduling | 1 | `scripts/os_nightly.py`; launchd instructions in `blackboard-template/22-automation-log.md` | A script and OS scheduling recipe exist, but no project-owned schedule registry, triage queue, or enforced dispatch lifecycle. |
| Worktrees | 2 | `scripts/wt.py`; plan `isolation: worktree`; path/namespace tests | Safe local creation/merge primitives exist, but no integrated scheduler assigns and rejoins every mutating step automatically. |
| Skills | 2 | `prompts/workflows/`; generated Codex skills under `addons/full-engine/staged/codex-skills/`; `scripts/sync_runtime_assets.py --check` | Strong source-of-truth and parity gate; execution still belongs to the host AI tool. |
| Plugins / MCP | 1 | `blackboard-template/17-capability-preflight.md`; `scripts/check_optional_tools.py`; docs distinguish optional external tools | Capability discovery is implemented, but Project OS does not own plugin lifecycle, trust, credentials, or availability. |
| Sub-agents maker/checker | 2 | `scripts/plan_artifact.py` rejects multi-step plans without checker coverage and declared verification; staged builder/evaluator roles | Structural enforcement is real, but Project OS does not spawn autonomous agents or prove the declared check ran. |
| Memory / State | 2 | numbered `blackboard/`; `scripts/harvest.py`; `scripts/brain_append.py`; `memory/mneme_adapter.py`; privacy tests | File state and approval-gated promotion exist. A unified tier/decay engine and immutable transition log are missing. |

## Overall

Total: **10/18**.

Project OS is strongest at inspectable state, structural plan gates, generated workflow parity, and local concurrency primitives. Its largest loop gap is orchestration: scheduling and triage do not automatically bind state, worktree allocation, implementation, verification receipts, and a final human gate into one enforced transaction.

## Recommended next increment

Do not add a cloud controller. Add a local append-only run ledger that records plan transition, assigned worktree, packet digest, verifier command/result, and human decision. A replay command should refuse any transition lacking its predecessor and should never execute a plan merely because it exists.
