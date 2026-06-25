# Friend Review Guide

Use this when sharing Project OS with a friend for critique.

The goal is not to prove the template is impressive. The goal is to find anything confusing, unsafe, overclaimed, or annoying before more people use it.

## What To Review

1. Can you understand what Project OS does from `README.md` without already knowing Codex or Claude?
2. Do `AGENTS.md` and `CLAUDE.md` describe the same workflow?
3. Does the template clearly separate starter files, the opt-in full engine add-on, and external tooling?
4. Does anything sound like it promises OSVec, GraphOS, a real vector database, a graph engine, model router, autonomous swarm runner, or security sandbox that is not actually bundled, installed, or configured?
5. Does `./install.sh` create a usable starter project in a blank folder?
6. Does `./install.sh --check-tools` write an honest capability report, including that model routing is configured in the AI tool rather than auto-detected through GraphOS/OSVec environment variables?
7. Does `.gitignore` block private memory, raw imports, local memory, OSVec/vector indexes, GraphOS/graph output, secrets, and env files?
8. Does the chat importer feel safe and appropriately limited?
9. Are `/research` and research-refresh docs clear that Project OS can suggest updates without silently changing major decisions?
10. Are the delivery report and artifact manifest clear enough to tell current work from drafts or experiments?

## Quick Test

From the template folder:

```bash
python3 -m unittest discover -s tests -v
tmpdir=$(mktemp -d)
./install.sh "$tmpdir/demo-project" --check-tools
find "$tmpdir/demo-project" -maxdepth 2 -type f | sort
git -C "$tmpdir/demo-project" init
git -C "$tmpdir/demo-project" status --short --ignored
```

Expected result: the demo project should contain `AGENTS.md`, `CLAUDE.md`, `.gitignore`, `prompts/`, `scripts/`, `addons/`, `blackboard/`, `runs/`, `outputs/`, `memory/`, `private-memory/`, and `private-imports/`. Private folders should be ignored by Git. The capability preflight should contain an automated optional tool check.

To review the full engine path too:

```bash
./install.sh "$tmpdir/full-engine-demo" --full-engine --claude-engine --check-tools
test -f "$tmpdir/full-engine-demo/memory/osvec_adapter.py"
test -f "$tmpdir/full-engine-demo/brain/brain.py"
test -f "$tmpdir/full-engine-demo/.claude/commands/new-run.md"
```

## Privacy Scan

From the template folder:

```bash
git status --short --ignored
git log --format=fuller --max-count=5
git remote -v
rg -n --hidden --no-ignore -S "/Users|[A-Za-z]:\\\\|sk-|sk-proj-|ghp_|github_pat_|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{20,}|BEGIN [A-Z ]*PRIVATE KEY|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}|\\.env|graphify-out|private-memory|private-imports|YOUR_PRIVATE_NAME|YOUR_PRIVATE_TOOL" .
```

Expected benign hits may include documentation examples, `.gitignore` privacy rules, tests with fake keys, and redaction patterns. A real local path, real key, raw chat, private project name, private tool name, personal note, or unwanted Git author/remote metadata should block publishing until fixed.

## Useful Feedback Format

```text
Pass/fail:
High-risk issues:
Medium-risk issues:
Low-risk polish:
Confusing wording:
Overclaims:
Files/lines to fix:
Would you use this in a blank project? Why or why not?
```

## Reviewer Notes

Be skeptical of polished language. A claim should be backed by a file, script, test, command output, or clearly marked as optional future tooling.
