# Project OS Architecture

Status: draft for reviewer inspection (2026-08-01).

## Boundary

Project OS is a local, file-backed workflow and verification layer for an AI coding tool. It is not an autonomous-agent runtime. The repository supplies Markdown state, Python command-line tools, generated prompt roles, validation gates, and optional local indexes. The human's AI tool executes the roles.

## Control structure

In the CMU agent-system taxonomy, Project OS sits between a fixed pipeline and a dynamically scheduled DAG:

1. `AGENTS.md` and workflow prompts define a mostly fixed outer loop: read state, choose a tier, plan, execute, verify, close.
2. `blackboard/` holds shared project state; named runs use an explicit `runs/<slug>/` active root for their decisions, risks, plans, packets and evidence. The root is passed to every workflow/role, not inferred from the newest directory.
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
4. Memory exchange checks approval metadata and known secret patterns. A CLI flag or record field is a caller assertion, not authenticated human consent; the host must establish the authorized storage scope.
5. Generated runtime assets must match their workflow source through `scripts/sync_runtime_assets.py --check`. The check also reports unexpected command/skill entrypoints after a workflow deletion or rename. Sync refuses until those files are reviewed and moved or removed manually; it does not delete user-authored adapters.
6. Publishing is outside the runtime and requires a human privacy review.

## Current architectural gaps

Plan creation and compilation hold the same per-plan lease and publication fence from the initial read through the final save. This serializes cooperating writers and keeps compiled packets tied to the plan revision being marked running. It does not prevent arbitrary direct file edits, make a whole packet set transactional, or bind a later completion assertion to an execution revision.

On POSIX, lease-loss cancellation targets a command's ordinary process group, including workers that remain after its immediate child exits. The CLI also performs bounded cleanup for SIGINT/SIGTERM before releasing its lease, then restores the caller's signal behavior. This is process cleanup, not containment of deliberately detached processes. A cancellation error remains a failed command, not proof of successful cleanup.

Run-index rebuilds hold a shared lease and publication fence from enumeration through atomic replacement of `runs/INDEX.md`. This prevents an older cooperating rebuild from overwriting a newer completed rebuild. The catalogue remains a derived view; run files remain authoritative. Concurrent hostile path replacement is refused at publication, but can leave a lease for stale reaping because lease keys use resolved paths.

The default `brain_append.py` operation rebuilds its derived index even when retrying an existing record ID, so retry can finish a refresh that previously failed. `--no-reindex` explicitly skips that refresh. Successful indexing is not a measurement of retrieval quality or a guarantee that another process cannot subsequently change the source.

- Scheduling is documented and locally scriptable, but no first-class scheduler registry exists in the repository.
- Worktree creation exists, but end-to-end scheduler → isolated worker → verifier orchestration remains an operator workflow.
- Plans are replayable data, but execution receipts are not yet a single immutable event log tied to each plan transition.
- Memory has local lexical/vector mechanisms, but tier promotion and decay are not implemented as one coherent policy engine.
- Sub-agent maker/checker roles are enforced in plan shape, not provided as autonomous processes.
