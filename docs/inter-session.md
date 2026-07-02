# Inter-Session Messaging — verified findings + adoption plan

Status: **optional add-on** — install with `claude plugin install inter-session` (after adding its marketplace). Per-session join is `/inter-session:inter-session` in each Claude session.
Date: 2026-07-02. Source: https://github.com/yilunzhang/claude-code-inter-session (MIT, ~19★).

## What it actually is (read from the repo, not the hype)

A third concurrency axis for Claude Code, distinct from subagents and agent teams:
it connects **already-running, long-lived sessions** peer-to-peer on the same machine.
The trigger heuristic: *"if you are copy-pasting between open sessions."*

- **Transport: localhost WebSocket bus** — server binds `127.0.0.1` only, default port
  `9473`. (Note: our radar swarm *refuted this 0-3*; direct repo read shows the
  refutation was a false kill. Recorded as a lesson — adversarial verify can kill
  true claims when voters can't reach the primary source.)
- **Auth:** bearer token at `~/.claude/data/inter-session/token`, mode 0600.
- **Delivery:** receiving side uses Claude Code's `Monitor` tool — no active polling;
  messages arrive as prompts governed by a reaction policy in the skill
  (destructive ops need explicit affirmative wording; ambiguity triggers `question:`).
- **Overflow:** payloads over the 400-char notification cap are logged in full to
  `~/.claude/data/inter-session/messages.log`.
- **Limits:** 16 MB frames, 10 MB direct messages, 256 KB broadcasts,
  60 broadcasts/min/session. Server auto-exits after 10 idle minutes.
- **Requirements:** Python >= 3.10, Claude Code >= 2.1.105, macOS/Linux/WSL2,
  same Unix user.

## Security caveat (from the repo's own security section)

Any process running as your user can read the token. This offers **no protection
against untrusted local code** — it is a convenience bus, not a security boundary.
Consistent with our Reality Check rule: markdown/token rules are not security
enforcement.

## Install (user action — run these yourself)

```
/plugin marketplace add https://github.com/yilunzhang/claude-code-inter-session
/plugin install inter-session
```

Then `/inter-session:inter-session` in each session that should join the bus.

## How it fits Project OS

| Concern | Today | With inter-session |
|---|---|---|
| Claude session ↔ Claude session | copy-paste / shared files | direct peer messages |
| Coordination latency | poll-when-remembered | ms-level via Monitor |
| Shared brain (`shared-brain.jsonl`) | mailbox AND memory | memory only (its real job) |

**Codex bridge: BUILT and verified 2026-07-02** — `scripts/codex_bus.py` joins the bus
as a real agent-role peer (imports the plugin's own `bin/shared.py`, so protocol drift
tracks the plugin). Verified end-to-end with the live server: `setup` (venv+websockets),
`serve` (standalone server start, no Claude needed), `list`, `send --to/--all`,
`listen` (msgs → stdout JSONL). Codex runs `python3 scripts/codex_bus.py listen --name codex`.
`shared-brain.jsonl` remains the durable memory layer; the bus is ephemeral signaling.

## Decision

Adopt for Claude↔Claude peer messaging once installed; keep locked JSONL for
Claude↔Codex. Logged in `blackboard/03-decisions.md`.
