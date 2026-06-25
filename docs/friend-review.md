# Friend Review Guide

Use this when sharing Project OS with a friend for critique.

The goal is not to prove the template is impressive. The goal is to find anything confusing, unsafe, overclaimed, or annoying before more people use it.

## What To Review

1. Can you understand what Project OS does from `README.md` without already knowing Codex or Claude?
2. Do `AGENTS.md` and `CLAUDE.md` describe the same workflow?
3. Does the template clearly separate implemented files/scripts from optional future tooling?
4. Does anything sound like it promises a real vector database, knowledge graph, model router, autonomous swarm runner, or security sandbox that is not actually bundled?
5. Does `./install.sh` create a usable starter project in a blank folder?
6. Does `./install.sh --check-tools` write an honest graph/vector capability report?
7. Does `.gitignore` block private memory, raw imports, local memory, vector indexes, knowledge graph output, secrets, and env files?
8. Does the chat importer feel safe and appropriately limited?
9. Are the delivery report and artifact manifest clear enough to tell current work from drafts or experiments?

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

Expected result: the demo project should contain `AGENTS.md`, `CLAUDE.md`, `.gitignore`, `prompts/`, `scripts/`, `blackboard/`, `runs/`, `outputs/`, `memory/`, `private-memory/`, and `private-imports/`. Private folders should be ignored by Git. The capability preflight should contain an automated optional tool check.

## Privacy Scan

From the template folder:

```bash
rg -n --hidden --no-ignore -S "/Users|sk-|private key|\.env|YOUR_PRIVATE_NAME|YOUR_PRIVATE_TOOL" .
```

Expected benign hits may include documentation examples, `.gitignore` privacy rules, tests with fake keys, and redaction patterns. A real local path, real key, raw chat, private project name, private tool name, or personal note should block publishing until fixed.

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
