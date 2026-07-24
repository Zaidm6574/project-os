# Project OS Instructions For Claude

This project uses Project OS.

**`AGENTS.md` is the single source of truth for Project OS doctrine across every runtime (Claude, Codex, Cursor).** This file is only a pointer plus Claude-specific notes. When the user says `$project-os`, `/project`, `project os`, or asks to start, plan, review, build, or audit a project, read `AGENTS.md` and follow it — the workflow, execution levels (Solo Agent Loop / Mini Swarm / Full Swarm), blackboard read gate, loop tooling, context & cache economy, memory order, research refresh, UI lane, model routing, and self-improvement loop are all defined there. Doctrine changes land in `AGENTS.md`, never only here.

## Non-Negotiable Safety Rules

Mirrored verbatim from `AGENTS.md` because this file is auto-loaded — if you edit these rules, update both blocks in the same commit.

- **Never `git push` to origin without explicit user approval in the same conversation turn.** Ask first, always — even mid-run, even at closeout.
- **Never include personal/local tooling in template commits.** If a file is hardcoded to personal or local paths, engines, or private data, it belongs in `.gitignore`, not in a public push. When in doubt, ask before committing to the public repo.
- **An artifact existing is not a run being complete.** A serious run also needs evaluation, delivery notes, artifact status, cost notes, and memory harvest before it may be called done.
- Actual different-model execution depends on the host AI tool; it is not detected through the GraphOS `PROJECT_OS_GRAPHOS_CMD` or OSVec `PROJECT_OS_OSVEC_CMD` environment variables.

## Claude-Specific Notes

Everything below exists only in Claude sessions; Codex has its own equivalents documented in `AGENTS.md`.

- **Skills.** When the full engine is installed, kickoff/status/evaluate/deliver/ui-review run as slash commands (`/kickoff`, `/status`, `/evaluate`, `/deliver`, `/ui-review`, `/project`). Prefer them over improvising the workflow from prose.
- **Subagents.** Use `context-scout` on the smallest available model for the blackboard read gate before heavier agents act. `ui-ux-designer`, `frontend-builder`, `builder`, `researcher`, `evaluator`, and `board` are available as agent types.
- **Brain MCP.** In Claude sessions you may call `mcp__brain__brief` directly and pass `--brief-file` to `scripts/promptsmith.py` instead of letting the script fetch the brief.
- **Auto-continuation.** When Max-effort is selected, ask the auto-continuation preference (`Auto`, `Ask first`, or `Warn only/Disabled`) and record it — full rules in the Context Cache Hygiene section of `AGENTS.md`.
- **Capability parity.** If a Claude-specific feature differs from Codex, record the limitation in `blackboard/17-capability-preflight.md` before serious work.

## Friend Review Mode

When the user asks for critique, publishing readiness, or friend review, audit before recommending publication (full checklist in `AGENTS.md`):

- Confirm a blank test install creates the documented starter files and that optional tooling remains clearly labeled.
- Scan tracked source and Git metadata for local paths, personal names, private project names, raw chats, secrets, credentials, and unwanted private artifacts.
- Check delivery reports and artifact manifests distinguish current outputs from drafts, tests, superseded files, and known gaps.
- Treat any real private-data hit, unsafe default, broken install, or unsupported readiness claim as a publishing blocker.
