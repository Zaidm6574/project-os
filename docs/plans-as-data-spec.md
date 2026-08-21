# Plans as Data — replayable artifact specification

Status: specification of the implemented baseline and the next contract. No new implementation in this lane because Lane A is blocked.

## Goal

Make the decision recorded in `blackboard/07-approved-plan.md` reproducible as a machine-inspectable artifact without allowing prose, file presence, or an old approval to trigger execution.

## Semantics

### `planOnly`

Creating or validating a plan may write `blackboard/plans/<id>.json`, but it must not emit worker packets, create worktrees, or run commands. Initial state is `planned`.

### `createPlanArtifact`

A deterministic compiler converts approved plan content into `plan/v1` JSON:

```json
{
  "schema": "plan/v1",
  "id": "safe-slug",
  "goal": "bounded outcome",
  "created": "ISO-8601",
  "status": "planned",
  "source": {
    "path": "blackboard/07-approved-plan.md",
    "sha256": "digest of source bytes"
  },
  "steps": [
    {
      "id": "build",
      "role": "builder",
      "task": "specific task",
      "depends_on": [],
      "outputs": ["relative/path"],
      "isolation": "worktree",
      "done": false
    },
    {
      "id": "verify",
      "role": "checker",
      "task": "independently verify build",
      "depends_on": ["build"],
      "verification": {"method": "exact command", "expected": "falsifiable result"},
      "done": false
    }
  ]
}
```

Approval records a digest of goal, steps, and instruction provenance. Editing those fields invalidates approval.

### `runFromPlan`

`compile <id>` validates the exact stored artifact, requires state `approved` or `running`, confirms the approval digest, topologically sorts steps, and writes deterministic packets under `blackboard/packets/`. It does not itself execute arbitrary shell commands. A host runner may consume packets, but must write a result record before a step can become `done`.

## State machine

```text
planned --human approve--> approved --compile--> running --all checked--> done
   |                            |
   +--edit content--------------+--> planned (approval invalidated)
```

There is no implicit `approved` state and no backwards transition from `done` without creating a new plan revision.

## Required invariants

1. IDs are safe single path components; generated files remain inside plan/packet roots.
2. Dependency graph is acyclic and every dependency exists.
3. Every non-checker step in a multi-step plan is covered by a checker dependency.
4. At least one work-dependent checker declares non-placeholder `method` and `expected`.
5. Approval binds the source/plan digest; content mutation invalidates it.
6. Compile is idempotent or refuses collisions; it never silently overwrites an existing packet.
7. Worktree isolation is required for parallel steps that can mutate the same repository.
8. Completion requires a result record with command, exit status, timestamp, and evidence path; a boolean alone is insufficient in the next schema.
9. All writes are atomic and lock-protected.
10. Relative output paths are containment-checked and cannot be symlink escapes.

## Falsifiable acceptance checks

- Create a valid plan: no packet files appear and status remains `planned`.
- Compile a planned plan: exit nonzero with “not approved”; no packets appear.
- Approve, edit one task byte, compile: exit nonzero for digest mismatch.
- Supply a cycle: validate exits nonzero and names the cycle.
- Supply a work step without checker coverage: validate exits nonzero.
- Use `-`, `n/a`, `todo`, or `tbd` as verification: validate exits nonzero.
- Use `../../escape` as plan or step ID: exit nonzero; no file outside roots exists.
- Compile the same approved artifact twice: byte-identical packets or a clean collision refusal; no partial set.
- Mark a build step done without a verifier result record: completion exits nonzero.
- Kill the writer before replace: previous plan and packets remain byte-identical.

## Compatibility

Current `scripts/plan_artifact.py` already implements most planOnly/create/validate/approve/compile behavior and content-digest migration safeguards. The principal future change is schema `plan/v2` with explicit source digest and append-only execution result records. Migration must never silently re-approve content.
