# Memory Harvest

Use this at closeout. This is the self-improvement checkpoint for Project OS.

Do not store raw chats, secrets, private credentials, or sensitive personal details. Promote only short, reviewed summaries.

**Automation:** `python3 scripts/harvest.py scan <run>` extracts the sections below
(tables or bullets both work), dedupes against the shared brain, and stages proposals
for review — nothing is stored until `harvest.py apply`. Rows marked Rejected or
Private-only are never extracted. `harvest.py status` lists finished runs that
haven't been harvested yet (the nightly heartbeat reports this too).

## Run Reflection

```text
Run ID:
Date:
What worked:
What did not work:
What surprised us:
What should be done differently next time:
```

## User Preferences Observed

| Preference | Evidence | Confidence | Approved For Reuse? |
|---|---|---|---|

## Project Patterns

| Pattern | Where It Helped | Reuse Guidance | Approved For Reuse? |
|---|---|---|---|

## Lessons

| Lesson | Evidence | Future Safeguard | Approved For Reuse? |
|---|---|---|---|

## Next-Kickoff Safeguards

These are checks the next Project OS run should consider before planning.

| Safeguard | Trigger | Why It Matters | Destination |
|---|---|---|---|

## Memories To Promote

| Memory ID | Type | Summary | Destination | Status |
|---|---|---|---|---|

Allowed types: user-preference, project-pattern, lesson, research-finding, decision, risk, agent-packet, safeguard.

Status values: Draft, Approved, Rejected, Private-only, Promoted.

## Rejected / Private-Only Memories

| Candidate | Reason | Action |
|---|---|---|

## Next Run Prompt Additions

Short, approved reminders that can be copied into a future kickoff.

```text

```
