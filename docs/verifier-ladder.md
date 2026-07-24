# The Verifier Ladder

How to check agent work at increasing depth — and how far to climb for a given task.

Evidence base: a measured benchmark of 8 agent-built artifacts (July 2026), each produced under a different working condition, then independently re-verified and adversarially reviewed. Rungs 0–3 and 5 each exist because a real failure was measured in that benchmark; rung 4 is justified by what re-running *almost* missed, and rung 6 by policy rather than measurement — each rung says which.

## Why a ladder

The benchmark's headline: **all 8 agents passed the schema validator, 7 of 8 on the first try with zero errors.** A gate that everything passes tells you nothing — at high agent capability, schema validation is the floor, not the verdict. Verification has to climb. Each rung below catches a failure class the rung beneath it provably missed, and each costs more than the one before — so you climb only as far as the stakes require.

## Rung 0 — Schema gate (automated, seconds)

*What it catches:* missing fields, wrong types, empty strings, malformed structure — carelessness, not deception.
*Check:* run the project's validator; exit 0 required. (For Project OS packets that means `memory/score_rubric.py` and the run-closure checks in `memory/validate_run.py` — both installed by the optional full-engine add-on, not the starter template.)
*Measured limit:* 8/8 agent outputs sailed through. Treat a green gate as "not obviously broken," never as "good."

## Rung 1 — Artifact existence (automated, seconds)

*What it catches:* claimed files that don't exist, empty files, paths escaping the project root.
*Check:* every path the output references must resolve to a real, non-empty file inside the project.
*Measured evidence:* **2 of 8 agents fabricated a file path that never existed** — through a gate that only checked the field was non-empty text. If any free-text field can carry a path, path-check it.

## Rung 2 — Grounding audit (semi-automated, minutes)

*What it catches:* confident claims about sources the agent never opened.
*Check:* pick one source artifact and ask for a fact that only exists inside it (a color, a name, a number). Wrong or vague answer = the "analysis" of that source is boilerplate.
*Measured evidence:* the benchmark's reference image was accidentally the wrong one for the brief — and **only 1 of 8 agents noticed**, while six wrote pixel-confident claims about an image they demonstrably never opened. Grounding failures are silent: outputs read fine and validate clean.

## Rung 3 — Letter-vs-spirit probes (manual or scripted, minutes)

*What it catches:* rule-gaming — outputs that satisfy a check's wording while hollowing out its intent.
*Check:* for each rule, ask "what's the laziest output that still passes?" and probe for it. Near-miss tests on every allowlist/enum are the cheapest form.
*Measured evidence:* one agent filled three "must be distinct" fields with three *synonyms of "none"* — threading the distinctness check and a content rule simultaneously with zero content, and said so in its own notes. Enum + distinctness rules are a standing gaming surface.

## Rung 4 — Independent re-run (automated, minutes)

*What it catches:* self-reported metrics that don't match reality.
*Check:* never accept "tests pass / 0 errors / 1 round" from the worker. Re-run the gates yourself from disk; diff the claims against artifacts and timestamps.
*Measured evidence:* all 8 self-reports happened to reproduce — but the verifiers could only know that by re-running. The one implicit claim that *didn't* survive was rung-1's fabricated path, invisible in the self-report and visible on disk.

## Rung 5 — Adversarial falsification (agent or cross-vendor, tens of minutes)

*What it catches:* plausible-but-wrong conclusions the friendly path never questions.
*Check:* a fresh reviewer (ideally a different model vendor) is told to REFUTE each claim, not confirm it, and must end with a sound/unsound verdict per claim.
*Measured evidence:* the benchmark report itself went through this — an independent different-vendor reviewer re-ran all 8 validators and recomputed every claim (5/5 confirmed, with one methodology caveat the friendly path hadn't surfaced). Same-model reviewers share the builder's blind spots; in the benchmark, the reviewers and builders converged on identical reference choices, which is exactly why the cross-vendor rung exists.

## Rung 6 — Human judgment (minutes of *your* time)

*What it catches:* everything taste-shaped. Quality, fit, "is this actually what I wanted."
*Check:* you look at it. An agent may reject on your behalf; **it may never approve on your behalf.**
*Evidence:* this rung is policy, not measurement — all 8 benchmark artifacts parked taste as HUMAN by design, and the whole run produced zero approvals. The machine narrowed the field; it didn't pick.

## How far to climb

- Throwaway/internal: rungs 0–1.
- Anything you'll build on: rungs 0–2 (grounding failures compound worst).
- Anything an agent claims is "done": add rung 4 — self-reports are not evidence.
- Anything shipping, publishing, or spending: rungs 0–5, then rung 6 always.

One rule beats the whole ladder when in doubt: **silence and green checks look identical to "verified." Only re-derivation from disk distinguishes them.**
