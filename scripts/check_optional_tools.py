#!/usr/bin/env python3
"""Check optional Project OS capabilities without installing external tools."""

from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
from datetime import datetime
from pathlib import Path


GRAPH_COMMANDS = ("tree-sitter", "code2flow", "pydeps")
VECTOR_PACKAGES = ("chromadb", "faiss", "lancedb", "sentence_transformers")
BROWSER_COMMANDS = ("node", "npm", "npx")
CONTAINER_COMMANDS = ("docker", "podman")


def has_command(name: str) -> bool:
    return shutil.which(name) is not None


def has_package(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def env_command_status(env_name: str) -> tuple[str, str] | None:
    value = os.environ.get(env_name, "").strip()
    if not value:
        return None
    command = value.split()[0]
    if has_command(command):
        return "Verified", f"{env_name} is set and its command is available on PATH."
    return "Claimed but unverified", f"{env_name} is set, but the command was not found on PATH."


def command_group_status(names: tuple[str, ...], configured_status: str = "Verified") -> tuple[str, str]:
    found = [name for name in names if has_command(name)]
    if found:
        return configured_status, "Found on PATH: " + ", ".join(found)
    return "Not configured", "Not found on PATH: " + ", ".join(names)


def package_group_status(names: tuple[str, ...]) -> tuple[str, str]:
    found = [name for name in names if has_package(name)]
    if found:
        return "Verified", "Python package detected: " + ", ".join(found)
    return "Not configured", "No common Python packages detected: " + ", ".join(names)


def build_report() -> str:
    graph_status = env_command_status("PROJECT_OS_GRAPH_CMD") or command_group_status(GRAPH_COMMANDS)
    vector_status = env_command_status("PROJECT_OS_VECTOR_CMD") or package_group_status(VECTOR_PACKAGES)
    search_status = command_group_status(("rg",))
    browser_status = command_group_status(BROWSER_COMMANDS)
    container_status = command_group_status(CONTAINER_COMMANDS)
    local_ai_status = command_group_status(("ollama",), configured_status="Optional")

    rows = [
        (
            "Model routing",
            (
                "Not auto-detected",
                "Configure this in your AI tool; Project OS cannot verify per-agent model routing from PATH.",
            ),
            "Record the platform limitation in blackboard/11-model-routing.md.",
        ),
        (
            "File search",
            search_status,
            "Install ripgrep if missing. Project OS still works without it, but searches are slower.",
        ),
        (
            "Knowledge Graph",
            graph_status,
            "Set PROJECT_OS_GRAPH_CMD or install/connect a graph builder if you want relationship maps.",
        ),
        (
            "Vector Memory",
            vector_status,
            "Set PROJECT_OS_VECTOR_CMD or install/connect a vector store if you want semantic recall.",
        ),
        (
            "Browser/UI QA",
            browser_status,
            "Install Node.js tooling if the project needs browser automation or UI checks.",
        ),
        (
            "Container isolation",
            container_status,
            "Install Docker or Podman only if the project needs isolated execution.",
        ),
        (
            "Local AI runtime",
            local_ai_status,
            "Optional. Useful for local experiments, but not required for Project OS.",
        ),
    ]

    lines = [
        "## Automated Optional Tool Check",
        "",
        f"- Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "- Scope: local machine PATH, selected environment variables, and installed Python packages",
        "",
        "| Capability | Status | Evidence | Recommendation |",
        "|---|---|---|---|",
    ]
    for capability, (status, evidence), recommendation in rows:
        lines.append(f"| {capability} | {status} | {evidence} | {recommendation} |")

    lines.extend(
        [
            "",
            "### Plain-English Summary",
            "",
            "- Project OS core works without graph or vector tools.",
            "- Knowledge Graph and Vector Memory become active only when a real tool is installed or connected.",
            "- Model routing is configured in the AI tool, not through `PROJECT_OS_GRAPH_CMD` or `PROJECT_OS_VECTOR_CMD`.",
            "- If a capability is `Not configured`, the assistant should say that honestly and keep using the markdown blackboard.",
            "- To connect custom tools, set `PROJECT_OS_GRAPH_CMD` and/or `PROJECT_OS_VECTOR_CMD` before running this check.",
            "",
        ]
    )
    return "\n".join(lines)


def write_report(target: Path) -> Path:
    target = target.expanduser().resolve()
    preflight = target / "blackboard" / "17-capability-preflight.md"
    preflight.parent.mkdir(parents=True, exist_ok=True)
    report = build_report()
    if preflight.exists() and preflight.read_text(encoding="utf-8").strip():
        existing = preflight.read_text(encoding="utf-8").rstrip()
        preflight.write_text(f"{existing}\n\n{report}", encoding="utf-8")
    else:
        preflight.write_text(f"# Capability Preflight\n\n{report}", encoding="utf-8")
    return preflight


def main() -> int:
    parser = argparse.ArgumentParser(description="Check optional Project OS graph, vector, and tool capabilities.")
    parser.add_argument("--target", default=".", help="Project folder to inspect. Default: current folder.")
    args = parser.parse_args()

    report_path = write_report(Path(args.target))
    print(f"Wrote optional tool check to {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
