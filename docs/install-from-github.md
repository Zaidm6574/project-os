# Install From GitHub

Use this guide when someone wants to try Project OS from your public GitHub repo.

## Option A: Clone And Install Into A Project

This is the simplest friend setup.

```bash
git clone https://github.com/YOUR-USERNAME/project-os-template.git
cd project-os-template
./install.sh ../my-new-project --check-tools
```

Replace `YOUR-USERNAME` with the GitHub account that owns the template repo.

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

The installer merges `.gitignore` privacy rules instead of replacing the whole file.

## Checking Optional Graph And Vector Tools

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
- optional graph tooling
- optional vector memory tooling
- browser/UI QA tooling
- container tooling
- local AI runtime tooling

It does not force-install anything. If graph or vector tools are missing, it recommends what to connect next.

Advanced users can expose their own tools with:

```bash
export PROJECT_OS_GRAPH_CMD="your-graph-command"
export PROJECT_OS_VECTOR_CMD="your-vector-command"
```

Model routing is configured in the AI tool itself. The optional tool check will not prove that sub-agents can use different models; if the tool inherits the parent model, write that limitation into `blackboard/11-model-routing.md`.

## What The Installer Does

It copies:

- `AGENTS.md`
- `CLAUDE.md`
- `prompts/`
- `scripts/`
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
- install a vector database
- install a knowledge graph engine
- create a real autonomous swarm runtime
- configure model routing in your AI tool
- publish anything to GitHub

It installs the workflow structure. Your AI tool provides the actual agent behavior.
