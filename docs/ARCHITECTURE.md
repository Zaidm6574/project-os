# Project OS Architecture

Status: draft for reviewer inspection (2026-08-01).

## Boundary

Project OS is a local, file-backed workflow and verification layer for an AI coding tool. It is not an autonomous-agent runtime. The repository supplies Markdown state, Python command-line tools, generated prompt roles, validation gates, and optional local indexes. The human's AI tool executes the roles.

## Control structure

In the CMU agent-system taxonomy, Project OS sits between a fixed pipeline and a dynamically scheduled DAG:

1. `AGENTS.md` and workflow prompts define a mostly fixed outer loop: read state, choose a tier, plan, execute, verify, close.
2. `blackboard/` is explicit shared state. Decisions, risks, approved plans, packets, evidence, and memory remain inspectable on disk.
3. `scripts/plan_artifact.py` permits a bounded dynamic DAG inside that loop. A JSON plan declares steps and dependencies; validation rejects cycles and missing maker/checker coverage; human approval precedes compilation.
4. Worker roles are prompt templates, not persistent processes. Project OS does not implement search-based controller policies such as MCTS.

This architecture favors replayability and auditability over controller autonomy.

## Tool surface

The core surface is standard-library Python plus Markdown:

- installation and runtime parity: `install.sh`, `scripts/setup_project_os.py`, `scripts/install_full_engine.py`, `scripts/sync_runtime_assets.py`
- state and concurrency: `scripts/bb_lock.py`, blackboard templates, fencing-token leases
- plans and work isolation: `scripts/plan_artifact.py`, `scripts/wt.py`
- verification: `addons/full-engine/memory/validate_run.py`, `score_rubric.py`, `build_verify.py`, `browser_qa.py`, and the unittest suite
- memory: `scripts/harvest.py`, `scripts/brain_append.py`, `memory/mneme_adapter.py`, optional OSVec/full-engine brain tools
- maintenance: `scripts/os_nightly.py`, `scripts/brain_scale.py`, `scripts/evolution.py`

External MCP, graph, vector, browser, and model services are optional capability lanes. They are not prerequisites for the documented zero-dependency suite.

## Compaction strategy

Project OS primarily uses explicit summarization and artifact compaction:

- Long work is checkpointed into numbered Markdown files and run handoffs.
- Worker output is reduced to packets and manifests rather than retained as an entire transcript.
- Memory harvest proposes reusable lessons; approval gates durable promotion.
- Mneme/OSVec indexes are derived, local artifacts and are not the source of truth.
- Closeout receipts keep exact command results and unresolved gates.

It does not currently implement automatic context-window compaction, learned eviction, or controller-selected summarization. Compaction is policy-driven and human-reviewable.

## Trust boundaries

1. Local files are authoritative; derived indexes can be rebuilt.
2. Plan validation proves declared structure, not that verification actually ran.
3. `build_verify.py` executes project-controlled code and is not a sandbox; `--isolated` protects only the source tree.
4. Chat-derived memory does not cross into shared durable memory without approval.
5. Generated runtime assets must match their workflow source through `scripts/sync_runtime_assets.py --check`.
6. Publishing is outside the runtime and requires a human privacy review.

## Current architectural gaps

- Scheduling is documented and locally scriptable, but no first-class scheduler registry exists in the repository.
- Worktree creation exists, but end-to-end scheduler → isolated worker → verifier orchestration remains an operator workflow.
- Plans are replayable data, but execution receipts are not yet a single immutable event log tied to each plan transition.
- Memory has local lexical/vector mechanisms, but tier promotion and decay are not implemented as one coherent policy engine.
- Sub-agent maker/checker roles are enforced in plan shape, not provided as autonomous processes.
