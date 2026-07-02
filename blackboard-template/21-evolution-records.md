# Evolution Records

The evaluate/approve loop keeps evolution records: every variant of an important
artifact (or of the harness that produces it) is logged with its evaluator score,
and the next variant **always evolves from the best-scoring variant, not the
latest** (arXiv 2604.21003 pattern, adopted 2026-07-02).

## Protocol

1. Worker builds variant → Evaluator scores it against the rubric.
2. Record it: `python3 scripts/evolution.py record --run <run> --variant vN --parent <id> --change "<what changed>" --score 0.NN --verdict approve|reject|revise`
3. Before proposing the next variant, the Evolution agent runs
   `python3 scripts/evolution.py next --run <run>` and mutates exactly ONE thing
   (worker prompt, rubric, tools, or orchestration) applied to the best variant.
4. Records live at `runs/<run>/evolution.json`; a human-readable
   `runs/<run>/evolution.md` table regenerates on every write.

## Active runs

| Run | Records file | Best variant | Notes |
|---|---|---|---|
