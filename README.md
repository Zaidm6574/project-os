# Project OS

> **Where the live work is:** this repo is the METHOD (doctrine, scripts, templates). Keep live run ledgers private and untracked; create and register new runs with `memory/new_run.py`.

[![Tests](https://github.com/Zaidm6574/project-os/actions/workflows/test.yml/badge.svg)](https://github.com/Zaidm6574/project-os/actions/workflows/test.yml)

A workflow template for AI coding assistants: goals, shared project notes, isolated run notes, and operating rules the assistant reads at the start of each session. Python helpers provide cooperative file locks, structural plan validation, atomic file replacement and known-credential screening for memory exchange. These guards support review; they do not execute the project or certify its results.

The template is Markdown. The tooling underneath it is not: a standard-library core with 900+ tests discoverable without installing packages. Optional paths depend on the interpreter, installed packages and platform; discovery counts include skips. Installation requires Python 3.10+, while the suite also supports older interpreters with explicit skips.

Don't take the README's word for any of that. On a fresh clone:

```bash
git clone https://github.com/Zaidm6574/project-os.git && cd project-os
/usr/bin/python3 -V                              # 3.9.6 — the stock macOS interpreter
/usr/bin/python3 -m unittest discover -s tests   # 900+ tests, zero dependencies
```

Inspect the final test count, failures and skip reasons. Skips can cover optional NumPy, interpreter requirements, filesystem behavior and explicit opt-in integration or stress tests; a skipped test is not executed coverage. For a smaller check:

```bash
/usr/bin/python3 -m unittest tests.test_docs_claims_20260726 \
  tests.test_brain_privacy_gate tests.test_brain_archive_security_20260726 \
  tests.test_bb_lock_hardening 2>&1 | tail -1     # -> OK (skipped=1)
```

The first of those modules checks selected README claims against commands and source. It is not a check of every statement on this page.

Scope, stated plainly so you can stop reading if it isn't what you want: the "agents" here are prompt roles executed by a Claude or Codex session — structured instructions, not autonomous background processes. Nothing in this repo runs on its own, and the plan gate proves a plan *declares* a check, never that the check was executed.

## Check these claims yourself

These checks exercise specific claims on a fresh clone; they do not certify every workflow or optional capability:

| Claim | Check it |
|---|---|
| Run the core suite without third-party dependencies; inspect skips separately | `python3 -m unittest discover -s tests` |
| Cooperative lock ownership checks reject stale tokens; correctness also requires guarded writes by every writer | `python3 -m unittest tests.test_bb_lock_hardening -v` |
| A plan whose verification step is empty or a placeholder (`-`, `n/a`, `tbd`, …) is rejected, with the reason — structural proof the plan *declares* a check, never proof the check ran | the two commands below |
| Memory sync checks approval metadata and known credential shapes; approval flags are caller-supplied, not authenticated human consent | `python3 -m unittest tests.test_brain_privacy -v` |
| The installer fails closed below Python 3.10 and names the interpreter it found | `PATH=/usr/bin:/bin sh install.sh /tmp/demo --dry-run` (on a machine whose only `python3` is older than 3.10, e.g. stock macOS: exits 1, prints `found python3 = 3.9.6 (/usr/bin/python3); ...`) |

![Historical terminal demonstration of tests and plan checks](docs/proof.gif)

The recording is a historical demonstration, not evidence for the current revision or hosted CI status.

The plan gate, live — the checker's `verification.method`/`expected` are placeholders, so the plan never gets created:

```bash
printf '[{"id":"w1","role":"builder","task":"build"},
 {"id":"c1","role":"reviewer","task":"check","depends_on":["w1"],
  "verification":{"method":"-","expected":"-"}}]' > /tmp/sham.json
python3 scripts/plan_artifact.py create --goal "demo" --steps-file /tmp/sham.json
# INVALID:
# - checker verification must be a JSON object with nonempty, non-placeholder method and expected strings
```

Swap those placeholders for a real method and expected result and the same command succeeds. The gate is structural: it enforces that every work step is covered by a checker and that at least one checker declares a nonempty, non-placeholder verification. It cannot tell whether that verification was ever executed — a plausible-sounding but never-run `method` is accepted, and confirming it actually ran stays the human gate's job (see [docs/verifier-ladder.md](docs/verifier-ladder.md)).

## How this is built

Four pieces worth opening, each with the reason it exists and the command that settles it. Run them from a fresh clone; none of them writes to the repo.

**`tests/test_docs_claims_20260726.py` — this README is executed, not proofread.** It extracts the fenced blocks below, runs them, and diffs the output against what the page quotes. It also asserts the documented install tree equals what the installer actually creates, that the interpreter probe order matches `install.sh`'s real loop, and that this README does not claim more than the plan gate can prove.

```bash
/usr/bin/python3 -m unittest tests.test_docs_claims_20260726 2>&1 | tail -1
# OK
```

**`scripts/brain_archive.py` — the path check is not the guard; the descriptor is.** `os.path.islink()` cannot see a hard link, so a path test can never be the only defence. The archive append opens with `O_RDWR|O_CREAT|O_APPEND|O_NOFOLLOW` and then checks `os.fstat(fd).st_nlink > 1` on the descriptor it is holding, refusing before any write. Read access checks an unterminated tail on that same descriptor so a moved record begins on its own line. The rewrite path deliberately uses `abspath()` and not `realpath()` — resolving re-follows a symlink at write time and hands the guard back to an attacker who swapped the target after the check — and it restores the destination's original mode after `rename`, so a deliberately-0600 file is not silently republished 0644. Both tests below include near-miss cases asserting the guards do not fire on ordinary input.

```bash
/usr/bin/python3 -m unittest tests.test_brain_archive_security_20260726 \
  tests.test_evolution_atomic_write_20260727 2>&1 | tail -1
# OK
```

**`scripts/bb_lock.py` — cooperative lock ownership and stale holder detection.** Same-user cross-process locking in stdlib only: a uuid4 fencing token written into the lockfile is the only proof of ownership (`--force` cannot override a tokened lease), leases renew via `os.utime`, stale ones are reaped, and `--wait N` is bounded against `time.monotonic()` rather than wall-clock so a wedged holder cannot make it wait forever.

```bash
/usr/bin/python3 -m unittest tests.test_bb_lock_hardening \
  tests.test_bb_lock_ownership_regression tests.test_bb_lock_wait_bound_20260726 2>&1 | tail -1
# OK (skipped=1)
```

The skip is deliberate: the end-to-end version of the lease-loss test suspends a live holder with `SIGSTOP`, which can freeze it while it owns the guard lock and fail on a healthy machine. It is kept as an opt-in stress test (`BB_LOCK_STRESS=1`) rather than deleted, because a test that fails 2% of the time under load teaches you to ignore it.

**`scripts/sync_runtime_assets.py` — one source of truth for every runtime.** The workflows live once under `prompts/workflows/`; the Claude slash commands, the Codex skills, and the universal index are generated from them. `--check` is the drift gate: hand-edit a generated file to improve only Claude's copy and it fails.

```bash
/usr/bin/python3 scripts/sync_runtime_assets.py --check; echo "exit=$?"
# RUNTIME-PARITY: OK
# exit=0
```

## What it gives your AI tool

- A clear goal file (`00-project-goal.md`) so the assistant knows what done looks like
- A shared blackboard for decisions, research, risks, cost, and next steps
- One explicit active root per run (`runs/<slug>/`), passed through every workflow and role; `blackboard/` remains shared project context. See the active-workspace rule in `AGENTS.md`.
- `AGENTS.md` + `CLAUDE.md` — operating rules for Codex-style tools and Claude Code
- Three workflow tiers — a solo loop, and the multi-role "mini swarm" / "full swarm" patterns from `AGENTS.md` (structured prompt roles for one AI tool, per the first paragraph — not autonomous background processes)
- A self-improvement loop: each serious run fills a memory harvest that can be promoted to the shared brain after review
- Context hygiene guidance for long sessions where prompt-cache cost quietly compounds
- A research refresh workflow for checking what changed since you last looked

## What is actually implemented

The following are working now:

- `AGENTS.md` as the single source of truth for the Project OS workflow, with `CLAUDE.md` as a thin pointer to it plus Claude-specific notes
- Blackboard templates for goals, decisions, risks, cost, model routing, evaluation, delivery, artifacts, memory, research routing, capability preflight, and research refresh
- `install.sh` — copies Project OS files into a target project folder
  - `--dry-run` flag: prints what would be copied without writing anything
  - `--check-tools` flag: checks for optional graph, vector, search, browser, container, and local AI tooling
  - `--full-engine` flag: installs the full engine add-on (run scripts, GraphOS, OSVec, cost actuals, validation)
- `scripts/setup_project_os.py` — the installer backing `install.sh`
- `scripts/check_optional_tools.py` — writes a capability report to `blackboard/17-capability-preflight.md`
- `scripts/install_full_engine.py` — opt-in full engine installer
- `scripts/import_chat_history.py` — scans old AI chat exports locally, redacts secrets, writes a private review report (never uploads)
- Loop tooling (added 2026-07): `bb_lock.py`, `plan_artifact.py`, `promptsmith.py`, `evolution.py`, `wt.py`, `harvest.py`, `brain_scale.py`, `brain_append.py`, `os_nightly.py` — `promptsmith` runs end-to-end without any personal setup via `--brief-file examples/sample-brief.md`
- Local vector memory: `memory/mneme_adapter.py` — neural search via Ollama if available, lexical fallback otherwise
- 900+ tests across 80+ modules covering locking, atomic writes, path containment, secret redaction, plan validation, cost measurement, and documentation drift — zero third-party dependencies

What is not automatic without additional setup: external vector/graph packages, autonomous swarm runtime, scheduled research, GitHub publishing.

## Quick start

Requirements: Git, Python 3.10+ (`python3` for the commands below), and an AI coding tool that reads `AGENTS.md` or `CLAUDE.md`. [ripgrep](https://github.com/BurntSushi/ripgrep) (`rg`) is optional fast file search. The pre-publish scanner uses Python and Git, not `rg`.

The installer probes these names on your PATH, in exactly this order — `python3 python python3.13 python3.12 python3.11 python3.10 python3.14` — and takes the first one that reports 3.10 or newer. Note the ordering: the unversioned names are tried first, and 3.14 is probed last. So a stock macOS python (3.9) is fine as long as a newer interpreter is installed under any of those names. If none qualifies, `install.sh` stops and tells you which one it found instead.

```bash
git clone https://github.com/Zaidm6574/project-os.git
cd project-os
./install.sh ../my-new-project --check-tools
```

Then open `../my-new-project` in your AI tool and describe what you want to build. `CLAUDE.md` and `AGENTS.md` are read automatically, so no special command is needed.

To activate host adapters, add the flag for your tool. Claude receives slash commands (`/project`, `/kickoff`, `/status`, …); Codex receives project skills. The plain install writes neither host adapter directory:

```bash
./install.sh ../my-new-project --full-engine --claude-engine   # Claude
./install.sh ../my-new-project --full-engine --codex-engine    # Codex
```

To install the full engine add-on:

```bash
./install.sh ../my-new-project --full-engine --check-tools
```

## 5-Minute Demo

Before copying anything, preview the install:

```bash
./install.sh ../demo-project --dry-run
```

The first lines look like this:

```text
Project OS setup dry run complete.
- would create /path/to/demo-project
- would write /path/to/demo-project/AGENTS.md
- would write /path/to/demo-project/CLAUDE.md
- would write /path/to/demo-project/.gitignore
- would write /path/to/demo-project/prompts/project-os-kickoff.md
```

(`../demo-project` is resolved to an absolute path, so you will see your own directory there.)

Nothing is written until you run it without the flag.

Before: an empty folder. After: a goal file, a blackboard, prompts, and operating rules — a project any AI coding tool can pick up mid-thought.

## Optional: smarter memory search

Out of the box, `memory/mneme_adapter.py` uses a zero-dependency lexical embedder. Install [Ollama](https://ollama.com) and one pull upgrades it to real semantic search:

```bash
ollama pull nomic-embed-text
python3 memory/mneme_adapter.py build
python3 memory/mneme_adapter.py query "have we solved something like this before?"
```

No Ollama means it stays lexical. Indexes built with one embedder are never queried with the other.

## Optional: daily heartbeat

`scripts/os_nightly.py` checks memory pressure, cleans stale locks, and flags stuck plans into `blackboard/22-automation-log.md`. It runs once when invoked; scheduling requires separate OS configuration. Run it manually or schedule it — the header of `blackboard/22-automation-log.md` itself carries a macOS launchd snippet and the `launchctl bootstrap` line.

## Project structure after install

```
my-new-project/
  AGENTS.md
  CLAUDE.md
  .gitignore
  addons/              # full-engine add-on sources, always copied
  blackboard/
  examples/            # sample-brief.md, used by promptsmith --brief-file
  memory/
  outputs/
  prompts/
  runs/
  scripts/
  private-memory/      # ignored by Git
  private-imports/     # ignored by Git
```

`addons/full-engine/` is copied by every install, including one without `--full-engine`; that flag is what *activates* those helpers, unpacking them into `memory/`, `blackboard/`, and a new `brain/` folder.

## Privacy rules

Never commit: raw chat exports, API keys, private notes, vector indexes, local memory databases, screenshots with personal data, browser/session data, or live run ledgers. The `.gitignore` blocks these common private paths by default.

Run a quick check before pushing:

```bash
git status --short --ignored
python3 scripts/prepublish_check.py --tracked
```

`--tracked` scans stage-0 Git index blobs: the candidate staged bytes, including unchanged tracked files. Use `--tracked --working-tree` separately to inspect local edits for the same selected paths. Plain `PATH` scans a working directory or a supported regular file. The scanner reports selected/scanned counts and match locations without matched values; empty or ambiguous Git selections fail closed. It screens known patterns, not every kind of private information. It does not scan Git history, author metadata or remote configuration; review those locally without copying sensitive values into a transcript. `--list` shows the checked patterns.

This repository's redaction tests intentionally contain synthetic credential-shaped fixtures, so the check is a fail-closed review gate rather than a command expected to exit cleanly. Treat every non-test-source match as a release blocker; review test-source matches before publishing.

## Status

Project OS keeps goals, evidence and unfinished work outside a single conversation. Its checks cover specific failure modes; the host still performs the work and reviewers still judge whether it meets the user's need.

Active. Check the linked CI badge for hosted results on the revision you intend to use. Publication requires review of the actual staged content, history and metadata; a scanner pass alone is not a safety certification.

Sharing it with an AI reviewer? Paste `docs/for-ai-reviewers.md` first — it gives the short architecture summary and the implemented-versus-optional boundary without requiring the whole README.

Field notes from real runs (what broke, what changed as a result) are in [docs/field-notes.md](docs/field-notes.md).

A full example flow is in [docs/example-project-flow.md](docs/example-project-flow.md).

How deep to verify agent work before trusting it — seven rungs from schema gate to human judgment — is in [docs/verifier-ladder.md](docs/verifier-ladder.md). Its per-rung numbers come from a private internal run whose artifacts are not in this repo; the doc says so and marks which rungs rest on policy instead.
