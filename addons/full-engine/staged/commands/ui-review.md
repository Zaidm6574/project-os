---
description: Review or plan a Project OS user interface with UI/UX, responsive layout, accessibility, and browser QA checks
argument-hint: <path, artifact, or UI goal>
allowed-tools: Read Write Edit Grep Glob Task Bash
---

Run a Project OS UI review for: **$ARGUMENTS**

Use this command for websites, web apps, dashboards, mobile screens, browser games, forms, visual tools, and any UI artifact.

By default, review and write findings. Only apply fixes when the user's request explicitly asks you to revise or build the UI.

## What to do

1. Read the goal, approved plan, risks, latest packets, and the target artifact or app files.
2. If the interface has not been planned yet, launch `ui-ux-designer` and ask it to write a UI/UX packet.
3. If the user asked for implementation and the plan is already approved, launch `frontend-builder` with a tight brief and a packet path.
4. Run or request evaluator review before marking the UI packet or frontend packet `Status: Approved`.
5. For static HTML, run:

```bash
python3 memory/browser_qa.py <path>
```

6. For dev-server apps, run the project's build/test command first, then use browser or Playwright QA when available. Check mobile and desktop viewports.
7. Log missing tools honestly. Do not claim browser QA ran if it did not.

## Review checklist

- The first screen is the usable experience, unless a landing page was explicitly requested.
- Information hierarchy is clear and matches the user's real workflow.
- Navigation and primary actions are obvious.
- Loading, empty, error, success, disabled, hover/focus, and selected states are covered.
- Responsive layout works on mobile and desktop without overlapping text or controls.
- Accessibility basics are covered: labels, keyboard path, focus visibility, contrast, touch targets, reduced motion when relevant.
- Visual direction fits the domain. Operational tools should be dense and scannable; consumer or creative work can be more expressive.
- Assets render, local links resolve, and screenshots or browser observations back up visual claims when possible.

## Output

Append or create a packet at `blackboard/packets/<wave>-ui-review-<nnn>.md`:

```text
Packet ID:
Agent: UI Review
Task:
Evidence:
Conclusion:
Confidence:
Findings:
Responsive layout:
Accessibility:
Interaction states:
Visual direction:
Browser QA:
Required fixes:
Recommended Next Step:
Status: Draft / Rejected / Approved
```

Approve only when the UI meets the Definition of Done and the verification evidence is real.
