---
name: evaluate
description: Run the evaluator quality-gate loop on an artifact or packet
argument-hint: <path or short description of what to evaluate>
capabilities: [subagents]
claude-tools: Read Grep Glob Task Write
---
Evaluate this artifact/packet against the rubric: **{{ARGUMENTS}}**

Run the `evaluator` role — launch it as a subagent if your runtime has them, otherwise adopt the role yourself in a separate pass, judging the artifact as written rather than as remembered. It scores against the weighted rubric in `blackboard/12-evaluation-log.md` (seeded from the Definition of Done in `00-project-goal.md`):
- Pass = weighted >= 0.80 and no criterion < 0.50 -> mark Approved.
- Otherwise Reject with precise feedback and an explicit **strategy change** for the next attempt.
- Max 3 iterations, then abort and ask me.

Log the result (date, artifact, iteration, score, verdict, feedback, strategy change) to `blackboard/12-evaluation-log.md` and tell me the verdict.
