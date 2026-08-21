# Tiered Memory for Project OS

Status: SPEC ONLY. Implementation is a separate run.

## Constraints

- local-first; no cloud dependency
- Markdown/JSONL sources of truth
- standard-library Python baseline
- explicit provenance and approval
- derived indexes are rebuildable
- secret gate applies to every persisted field

## Four tiers

| Tier | Purpose | Default representation | Admission | Eviction/decay |
|---|---|---|---|---|
| Working | Current run context and unresolved state | numbered run/blackboard Markdown, packets | created by active workflow | expires at closeout after unresolved items are promoted or explicitly dropped |
| Episodic | What happened in a specific run | append-only JSONL receipt + closeout/handoff | closed run with provenance and exact evidence | relevance decays by age and access; source artifact is retained according to project policy |
| Semantic | Stable project facts, decisions, preferences | approved JSONL/Markdown records + local lexical/vector index | explicit promotion from evidence-backed episode; secrets refused | confidence/retrieval weight decays when stale or contradicted; record is superseded, not silently deleted |
| Procedural | Reusable workflows and safeguards | reviewed workflow Markdown/skill source + tests | repeated successful pattern or approved rule | no time decay by default; demote when tests fail, tools disappear, or owner rejects it |

## Record envelope

```json
{
  "schema": "memory/v2",
  "id": "stable-id",
  "tier": "episodic|semantic|procedural",
  "text": "compact approved content",
  "source": "relative/path#anchor",
  "source_sha256": "...",
  "created_at": "ISO-8601",
  "last_accessed_at": "ISO-8601",
  "access_count": 0,
  "confidence": 0.0,
  "sensitivity": "public|project-private|private-only",
  "status": "active|superseded|rejected",
  "supersedes": [],
  "tags": []
}
```

Working memory remains Markdown and need not use this envelope until promotion.

## Decay

Decay changes retrieval weight, never historical bytes:

```text
age_days = max(0, now - last_accessed_at)
base = confidence * (1 + log1p(access_count))
weight = base * exp(-lambda[tier] * age_days)
```

Suggested policy:

- episodic: half-life 30 days
- semantic: half-life 180 days, reset only when corroborated
- procedural: no age decay; health is determined by executable checks

A stale semantic record remains discoverable by exact lookup but ranks below recent corroborated facts. Contradictions create a new record and mark the old record `superseded`; they do not rewrite provenance.

## Promotion workflow

1. `harvest scan` reads a closed run and creates a proposal without marking it promoted.
2. Secret/PII policy scans keys and values recursively.
3. Human or project owner approves, rejects, or marks private-only.
4. Approved record is appended atomically under a fencing lock.
5. Derived Mneme/OSVec indexes rebuild from the durable log.
6. Marker is written only after durable append and successful index disposition.

## Retrieval

Use reciprocal-rank fusion over available local retrievers:

- lexical BM25-like score (stdlib approximation acceptable)
- optional vector score from OSVec/TurboVec-compatible local adapter
- provenance/relationship score from explicit links

Missing optional backends must reduce recall honestly, not fail the local lexical path. Every result exposes tier, source, age, sensitivity, and score components.

## OSVec/TurboVec mapping

- Sidecar stores the durable envelope and stable IDs.
- Vector index stores embeddings keyed by stable unsigned IDs.
- Manifest binds sidecar/index schema, count, and digests.
- Atomic multi-file commit and rollback preserve the last complete store.
- Search refuses sidecar/index mismatch rather than silently rebuilding from partial state.

## Acceptance checks for a future implementation

- A new run item exists only in Working until explicit promotion.
- Rejected/private-only bullets never enter a shared index.
- Secret-shaped data in text, IDs, tags, timestamps, or unknown nested metadata is refused before persistence.
- Two concurrent promotions preserve both records; same-ID divergent writes produce a conflict.
- A corrupted vector index does not erase or rewrite the durable JSONL source.
- An episodic record ranks lower after its half-life unless accessed/corroborated.
- A procedural record with a failing health test is demoted or flagged despite recent access.
- Exact lookup can still retrieve a decayed/superseded record with its status and provenance.
- A lexical-only machine completes build/query without third-party packages.
- Rebuilding derived indexes is deterministic for fixed input and configuration.
