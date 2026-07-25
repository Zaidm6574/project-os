#!/usr/bin/env python3
"""Keep every runtime's workflow assets generated from one canonical source.

AGENTS.md unified DOCTRINE across runtimes. This unifies CAPABILITY. The 11
Project OS workflows live once, runtime-neutral, in `prompts/workflows/`. Thin
host adapters are GENERATED from them:

    prompts/workflows/<name>.md   canonical (edit this)
      -> addons/full-engine/staged/commands/<name>.md   Claude slash command
      -> prompts/workflows/INDEX.md                     universal discovery

Any runtime that cannot use Claude slash commands (Codex, Cursor, Antigravity)
reads INDEX.md, opens the canonical file, and follows it directly. Nothing is
Claude-only except the generated adapter's frontmatter.

Usage:
  python3 scripts/sync_runtime_assets.py            # regenerate adapters
  python3 scripts/sync_runtime_assets.py --check    # fail if anything drifted
  python3 scripts/sync_runtime_assets.py --adopt    # one-time import of
                                                    # existing Claude commands

Standard library only.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

GENERATED_MARKER = (
    "<!-- GENERATED from prompts/workflows/{name}.md by "
    "scripts/sync_runtime_assets.py — edit the canonical file, not this one. -->"
)

# Capability tokens a workflow may require. Neutral names, not host APIs.
KNOWN_CAPABILITIES = ("subagents", "websearch", "task-tracking", "browser")


@dataclass
class Workflow:
    name: str
    description: str = ""
    argument_hint: str = ""
    capabilities: list = field(default_factory=list)
    degradation: str = ""
    claude_tools: str = ""
    body: str = ""


# --------------------------------------------------------------------------
# Minimal frontmatter handling (stdlib only — no PyYAML dependency).
# --------------------------------------------------------------------------

def split_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    raw, body = text[4:end], text[end + 5:]
    meta: dict = {}
    key = None
    for line in raw.split("\n"):
        if not line.strip():
            continue
        m = re.match(r"^([A-Za-z_-]+):\s*(.*)$", line)
        if m:
            key, value = m.group(1), m.group(2).strip()
            if value.startswith("[") and value.endswith("]"):
                inner = value[1:-1].strip()
                meta[key] = [v.strip() for v in inner.split(",") if v.strip()]
            elif value in (">-", ">", "|"):
                meta[key] = ""
            else:
                meta[key] = value
        elif key is not None and isinstance(meta.get(key), str):
            # folded continuation line
            meta[key] = (meta[key] + " " + line.strip()).strip()
    return meta, body


def render_frontmatter(pairs: list) -> str:
    out = ["---"]
    for key, value in pairs:
        if value in (None, "", []):
            continue
        if isinstance(value, list):
            out.append(f"{key}: [{', '.join(value)}]")
        else:
            out.append(f"{key}: {value}")
    out.append("---")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------
# Load / render
# --------------------------------------------------------------------------

def load_workflows(root: Path) -> list:
    canon = Path(root) / "prompts" / "workflows"
    workflows = []
    for path in sorted(canon.glob("*.md")):
        if path.stem == "INDEX":
            continue
        meta, body = split_frontmatter(path.read_text(encoding="utf-8"))
        workflows.append(Workflow(
            name=meta.get("name", path.stem),
            description=meta.get("description", ""),
            argument_hint=meta.get("argument-hint", ""),
            capabilities=meta.get("capabilities", []) or [],
            degradation=meta.get("degradation", ""),
            claude_tools=meta.get("claude-tools", ""),
            body=body,
        ))
    return workflows


def capability_note(wf: Workflow) -> str:
    """What a runtime should do when it lacks a capability this workflow wants.

    POLICY — this is the one genuine judgment call in the file, and it decides
    how a Codex/Cursor session behaves when it hits `subagents`:

      (a) proceed silently inline    — frictionless, but the gap stays invisible
      (b) proceed and log the gap    — matches the existing Capability
                                       Preflight doctrine in AGENTS.md; costs
                                       one blackboard write per run
      (c) refuse to run              — fail-closed, but makes the weaker runtime
                                       strictly worse, which is the opposite of
                                       parity

    (b) is implemented because AGENTS.md already tells agents to record
    limitations in blackboard/17-capability-preflight.md, so this makes the
    existing rule executable rather than inventing a new one.
    """
    if not wf.capabilities:
        return ""
    caps = ", ".join(f"`{c}`" for c in wf.capabilities)
    note = (
        f"\n## Capability note\n\n"
        f"This workflow uses {caps}. If your runtime does not have them, do the "
        f"work inline yourself — do **not** skip the step and do **not** refuse. "
        f"Record the substitution in `blackboard/17-capability-preflight.md` so "
        f"the gap is visible instead of silent.\n"
    )
    if wf.degradation:
        note += f"\n{wf.degradation}\n"
    return note


def render_claude_command(wf: Workflow) -> str:
    """Claude slash-command adapter: host frontmatter + $ARGUMENTS token."""
    pairs = [("description", wf.description)]
    if wf.argument_hint:
        pairs.append(("argument-hint", wf.argument_hint))
    if wf.claude_tools:
        pairs.append(("allowed-tools", wf.claude_tools))
    body = wf.body.replace("{{ARGUMENTS}}", "$ARGUMENTS")
    marker = GENERATED_MARKER.format(name=wf.name)
    return render_frontmatter(pairs) + "\n" + marker + "\n" + body.rstrip("\n") + \
        "\n" + capability_note(wf)


def render_index(workflows: list) -> str:
    lines = [
        "# Project OS workflows — universal index",
        "",
        GENERATED_MARKER.format(name="*").replace(
            "prompts/workflows/*.md", "prompts/workflows/"),
        "",
        "Every Project OS workflow lives here in runtime-neutral form.",
        "",
        "- **Claude** exposes these as slash commands (`/kickoff`, `/status`, ...)",
        "  via `addons/full-engine/staged/commands/`, which is generated from this",
        "  directory.",
        "- **Every other runtime** (Codex, Cursor, Antigravity, plain CLI) invokes a",
        "  workflow by opening `prompts/workflows/<name>.md` and following it. There",
        "  is no Claude-only step; the canonical file is the whole instruction.",
        "",
        "| Workflow | Takes | What it does |",
        "| --- | --- | --- |",
    ]
    for wf in workflows:
        hint = wf.argument_hint or "—"
        desc = wf.description.replace("|", "\\|")
        lines.append(f"| `{wf.name}` | {hint} | {desc} |")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Sync / check
# --------------------------------------------------------------------------

def _targets(root: Path, workflows: list) -> list:
    commands = Path(root) / "addons" / "full-engine" / "staged" / "commands"
    out = [(commands / f"{wf.name}.md", render_claude_command(wf)) for wf in workflows]
    out.append((Path(root) / "prompts" / "workflows" / "INDEX.md",
                render_index(workflows)))
    return out


def sync(root: Path) -> list:
    workflows = load_workflows(root)
    written = []
    for path, content in _targets(Path(root), workflows):
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.is_file() or path.read_text(encoding="utf-8") != content:
            path.write_text(content, encoding="utf-8")
            written.append(str(path.relative_to(Path(root))))
    return written


def check(root: Path) -> list:
    """Return a list of drift problems. Empty list == every adapter matches."""
    root = Path(root)
    workflows = load_workflows(root)
    problems = []
    for path, content in _targets(root, workflows):
        rel = str(path.relative_to(root))
        if not path.is_file():
            problems.append(f"missing generated asset: {rel}")
        elif path.read_text(encoding="utf-8") != content:
            problems.append(f"hand-edited or stale: {rel}")
    return problems


# --------------------------------------------------------------------------
# One-time adoption of the existing Claude commands
# --------------------------------------------------------------------------

_SUBAGENTS = ("context-scout", "ui-ux-designer", "frontend-builder", "evaluator",
              "researcher", "project-os-ceo", "project-os-cfo", "memory-librarian",
              "board", "builder")


def _infer_capabilities(tools: str, body: str) -> list:
    caps = []
    low = body.lower()
    # Word-boundary match, NOT substring: plain `in` reports the `board` role
    # for every file that merely says "blackboard", which over-declares the
    # capability and makes weaker runtimes log gaps they never actually hit.
    named_role = any(re.search(r"\b" + re.escape(a) + r"\b", body)
                     for a in _SUBAGENTS)
    if "task tool" in low or named_role or "Task" in tools:
        caps.append("subagents")
    if "WebSearch" in tools:
        caps.append("websearch")
    if "TodoWrite" in tools:
        caps.append("task-tracking")
    return caps


def adopt(root: Path) -> list:
    """Import existing staged Claude commands into canonical neutral form."""
    root = Path(root)
    commands = root / "addons" / "full-engine" / "staged" / "commands"
    canon = root / "prompts" / "workflows"
    canon.mkdir(parents=True, exist_ok=True)
    created = []
    for path in sorted(commands.glob("*.md")):
        target = canon / path.name
        if target.is_file():
            continue
        meta, body = split_frontmatter(path.read_text(encoding="utf-8"))
        tools = meta.get("allowed-tools", "")
        body = body.replace("$ARGUMENTS", "{{ARGUMENTS}}")
        # After the doc unification, doctrine lives in AGENTS.md for every
        # runtime; CLAUDE.md is a thin pointer and means nothing to Codex.
        body = body.replace("`CLAUDE.md`", "`AGENTS.md`").replace(
            "CLAUDE.md", "AGENTS.md")
        pairs = [
            ("name", path.stem),
            ("description", meta.get("description", "")),
            ("argument-hint", meta.get("argument-hint", "")),
            ("capabilities", _infer_capabilities(tools, body)),
            ("claude-tools", tools),
        ]
        target.write_text(
            render_frontmatter(pairs) + body.lstrip("\n"), encoding="utf-8")
        created.append(str(target.relative_to(root)))
    return created


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument("--check", action="store_true",
                    help="exit non-zero if any generated asset drifted")
    ap.add_argument("--adopt", action="store_true",
                    help="one-time import of staged Claude commands")
    args = ap.parse_args()
    root = Path(args.root)

    if args.adopt:
        for rel in adopt(root):
            print(f"adopted {rel}")
        return 0
    if args.check:
        problems = check(root)
        for p in problems:
            print(f"DRIFT: {p}")
        print("RUNTIME-PARITY: %s" % ("OK" if not problems else "DRIFT"))
        return 1 if problems else 0
    for rel in sync(root):
        print(f"wrote {rel}")
    print("RUNTIME-PARITY: synced")
    return 0


if __name__ == "__main__":
    sys.exit(main())
