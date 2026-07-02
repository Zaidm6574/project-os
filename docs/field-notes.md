# Field Notes — lessons this template is built on

Generic, hard-won lessons from real multi-agent runs. Each one changed a script or
a rule in this template. No personal data; dates kept so future readers can judge
staleness.

## Verification

- **Adversarial verification can kill TRUE claims.** A 3-voter refutation panel
  unanimously "refuted" a claim that turned out to be correct — the voters simply
  never reached the primary source. Treat unanimous refutations of *checkable
  implementation details* as "unverified", not "false"; read the primary source
  before acting. (2026-07)
- **Never act on unverified audit findings.** In one 36-finding audit, 3 findings
  were confidently wrong — including one that would have "fixed" correct
  documentation into a lie. Re-verify against the source before editing anything
  an audit told you to edit. (2026-07)
- **A vision model cannot judge motion from one still.** Gate animation/motion work
  with a filmstrip of frames plus code-grounded checks, never a single screenshot.

## Multi-agent hygiene

- **Two agents completing steps concurrently WILL lose writes** unless every
  read-modify-write goes through a lock. This bit the plan tool within hours of
  shipping it; the fix (lock around load→mutate→save) is now mandatory. (2026-07)
- **Anchor tooling to the main checkout.** A worktree helper run from *inside* a
  worktree merged a branch into itself and reported success while removing
  nothing. Resolve the primary checkout first; never trust `cwd`. (2026-07)
- **Check every checkout before trusting one.** Sessions have edited a worktree
  while another session read the stale main checkout and called it truth. `git
  worktree list` (or `wt.py list`) is a pre-flight, not an afterthought.
- **Concurrent sessions rename things under you.** Mid-sprint, a second agent
  renamed core modules — including references inside scripts written an hour
  earlier — and logged nothing. After any pause, re-grep what you referenced; log
  renames as decisions so the other sessions find out. (2026-07)

## Memory

- **Format drift silently destroys harvesting.** Three heading dialects for the
  same closeout file meant a parser matched one and silently dropped the rest —
  twice in one day. Parsers for human-written markdown must accept every dialect
  in the wild and report zero-extraction loudly, never silently. (2026-07)
- **An append without a reindex is a silent memory hole.** At one point only 13 of
  138 stored lessons were actually searchable because appends outpaced index
  rebuilds. Couple the write and the reindex in one tool. (2026-07)
- **Flat markdown memory has a ceiling** (~100 sources / ~200 pages before
  retrieval degrades). Put a gauge on it and cut over to vector retrieval before
  degradation, not after. Lexical hash embeddings score ~0.25 with word-overlap
  noise where local neural embeddings score 0.55+ on-target — the upgrade is
  free, offline, and takes seconds to rebuild. (2026-07)
- **Refuse mixed-embedder queries.** Cosine similarity between vectors from
  different embedders is meaningless; a query against a mismatched index must be
  an error, not a low score.

## Cost shape

- **Research swarms are the budget lever; builds are cheap.** Two swarm-heavy days
  cost ~three orders of magnitude more than an afternoon of inline building that
  shipped ten tools. Route bulk agent work to cheap models, reserve large fan-outs
  for research/audit questions that genuinely need many independent readers, and
  price a swarm before launching it. (2026-07)

## Honesty rules

- **Never print "removed"/"done" when something survived.** Verify the
  post-condition before reporting success; a tool that lies once poisons every
  log that quotes it.
- **Classify fixes as visible vs invisible** and never present unchanged UI as
  progress.
- **Markdown rules are not security enforcement.** Operating agreements in
  markdown govern cooperative agents; they do nothing against untrusted code.
  Same for local-machine bearer tokens readable by any same-user process.
