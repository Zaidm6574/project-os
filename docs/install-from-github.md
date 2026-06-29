# Install From GitHub

Use this guide when someone wants to try Project OS from your public GitHub repo.

## Option A: Clone And Install Into A Project

This is the simplest friend setup.

```bash
git clone https://github.com/Zaidm6574/project-os.git
cd project-os
./install.sh ../my-new-project --check-tools
```

If you are using a fork, replace `Zaidm6574` with the GitHub account that owns the fork.

To preview the files first, run:

```bash
./install.sh ../my-new-project --dry-run
```

Dry run prints the files and folders that would be created or overwritten. It does not create the target project or run the optional tool check.

Then open `../my-new-project` in Codex, Claude, or another AI coding tool and say:

```text
/project I want to build...
```

## Option B: Use This Repository As A Template

If the GitHub repo has **Template repository** enabled:

1. Click **Use this template**.
2. Create a new repo from it.
3. Clone the new repo.
4. Run setup inside that repo.

```bash
git clone https://github.com/THEIR-USERNAME/their-project.git
cd their-project
./install.sh .
```

## Updating An Existing Project

To add missing files without replacing existing work:

```bash
./install.sh /path/to/existing-project
```

To overwrite Project OS template files on purpose:

```bash
./install.sh /path/to/existing-project --force
```

To inspect that overwrite before doing it:

```bash
./install.sh /path/to/existing-project --force --dry-run
```

The installer merges `.gitignore` privacy rules instead of replacing the whole file.

## Checking Optional GraphOS And OSVec Tools

Run:

```bash
./install.sh /path/to/project --check-tools
```

The check looks for optional local capabilities and writes the result to:

```text
blackboard/17-capability-preflight.md
```

It checks for:

- fast file search
- optional GraphOS tooling, the native Project OS graph layer
- optional OSVec tooling, the native Project OS vector/memory layer
- browser/UI QA tooling
- container tooling
- local AI runtime tooling

It does not force-install anything. If GraphOS or OSVec tooling is missing, it recommends what to connect next.

Advanced users can expose their own tools with:

```bash
export PROJECT_OS_GRAPHOS_CMD="your-graph-command"
export PROJECT_OS_OSVEC_CMD="your-vector-command"
```

Legacy `PROJECT_OS_GRAPH_CMD` and `PROJECT_OS_VECTOR_CMD` are still recognized as fallbacks.

Model routing is configured in the AI tool itself. The optional tool check will not prove that sub-agents can use different models; if the tool inherits the parent model, write that limitation into `blackboard/11-model-routing.md`.

## Optional Full Engine Add-On

The starter kit works without GraphOS or OSVec active. Starter installs include lightweight local helper scripts at `memory/build_graph.py` and `memory/osvec_adapter.py`; run them only when you want local graph/vector context.

To install the broader full engine scripts:

```bash
./install.sh /path/to/project --full-engine --check-tools
```

For Claude Code agents and slash commands too:

```bash
./install.sh /path/to/project --full-engine --claude-engine --check-tools
```

That Claude add-on includes `ui-ux-designer`, `frontend-builder`, and `/ui-review` for web apps, dashboards, mobile screens, forms, visual tools, and browser QA workflows.

To connect a central brain at the same time:

```bash
./install.sh /path/to/project --full-engine --central-brain ~/.project-os/central-brain --project-id project-name --check-tools
```

This is additive and local-only. It copies `memory/`, `brain/`, and `blackboard/21-agent-roster.md` add-on files into the project, and it does not overwrite existing files unless `--force` is passed.

Use `--dry-run` with `--full-engine` when you want to see those add-on files before copying them.

For UI projects, use `/ui-review` after planning or building to record responsive layout, accessibility, interaction-state, visual-quality, and browser QA findings in a Project OS packet.

From inside a project that already has the starter files:

```bash
python3 scripts/install_full_engine.py --target .
```

Then initialize central brain when desired:

```bash
python3 scripts/install_full_engine.py --target . --central-brain ~/.project-os/central-brain --project-id project-name
```

Then rerun:

```bash
python3 scripts/check_optional_tools.py --target .
```

## What The Installer Does

It copies:

- `AGENTS.md`
- `CLAUDE.md`
- `prompts/`
- `scripts/`
- `addons/`
- `blackboard/`
- `runs/`
- `outputs/`
- `memory/`

It also creates private local folders:

- `private-memory/`
- `private-imports/`

Those folders are ignored by Git.

## What It Does Not Do

The installer does not:

- upload private files
- activate the full engine unless `--full-engine` is passed
- create or connect a central brain unless `--central-brain PATH` is passed
- install external TurboVec, Graphify, embedding, or graph database packages
- create a real autonomous swarm runtime
- configure model routing in your AI tool
- publish anything to GitHub

It installs the workflow structure. Your AI tool provides the actual agent behavior.
