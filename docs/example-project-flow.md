# Example Project Flow

This is the simplest way to think about Project OS:

## 1. You start with an idea

Example:

```text
/project I want to build a simple study app that helps me stay focused.
```

## 2. The assistant should clarify before building

It should ask questions, define the scope, and decide whether this is:

- a Solo Agent Loop
- a Mini Swarm
- a Full Swarm

If the idea is a website, web app, dashboard, mobile screen, game UI, form, or visual tool, it should also add a UI lane: plan the first usable screen, responsive layout, accessibility checks, visual direction, and browser QA route before approving the build.

## 3. The blackboard becomes the shared project brain

The assistant should write down:

- the goal
- user preferences
- research notes
- decisions
- risks
- the approved plan

That way, the project is not trapped inside one messy chat.

## 4. Work happens in loops

For important work, the pattern is:

```text
Attempt -> Evaluate -> Revise -> Approve
```

That helps the assistant catch weak plans, weak research, and weak outputs before pretending the work is done.

For interface work, the full engine can use `ui-ux-designer`, `frontend-builder`, and `/ui-review` so the loop checks responsive layout, accessibility, interaction states, and browser QA evidence instead of only checking whether code exists.

## 5. Serious runs get a closeout

Before calling a serious run complete, the assistant should leave behind:

- a cost note
- an evaluation log
- a delivery report
- an artifact manifest
- a memory harvest

For long AI sessions, closeout should also say whether the run needs a fresh continuation. The goal is to keep the project state in files, not trapped in a giant chat that keeps being rewritten into cache.

## 6. Later, you can run `/research` or a research refresh

If the market changed, competitors shipped new features, or AI tooling moved forward, you can ask for a refresh pass.

That pass should answer:

- What changed since the last plan?
- What is newly expected by users?
- What tools or approaches are now better?
- Should the project scope, roadmap, or stack change?
- Are there Project OS workflow updates worth suggesting?

The refresh result should be logged in `blackboard/20-research-refresh.md`. It can recommend updates, but major scope, roadmap, architecture, privacy, publishing, spending, or workflow changes still need human approval before they become the new plan.

## A Good Beginner Mental Model

Project OS is not one giant swarm that does everything at once.

It works best in levels:

- Solo Agent Loop for simple work
- Mini Swarm for serious but contained work
- Full Swarm for genuinely large or high-stakes work

That keeps the system understandable and avoids overkill.
