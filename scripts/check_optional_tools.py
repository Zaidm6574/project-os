#!/usr/bin/env python3
"""Check optional Project OS capabilities without installing external tools."""

from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
from datetime import datetime
from pathlib import Path


GRAPH_COMMANDS = ("graphos", "graphify", "tree-sitter", "code2flow", "pydeps")
VECTOR_COMMANDS = ("osvec", "turbovec")
VECTOR_PACKAGES = ("osvec", "turbovec", "chromadb", "faiss", "lancedb", "sentence_transformers")
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


def preferred_env_command_status(primary: str, legacy: str) -> tuple[str, str] | None:
    primary_status = env_command_status(primary)
    if primary_status:
        return primary_status
    legacy_status = env_command_status(legacy)
    if legacy_status:
        status, evidence = legacy_status
        return status, f"{evidence} Legacy fallback is supported; prefer {primary} for new projects."
    return None


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


def osvec_group_status() -> tuple[str, str]:
    found_commands = [name for name in VECTOR_COMMANDS if has_command(name)]
    if found_commands:
        return "Verified", "Found on PATH: " + ", ".join(found_commands)
    return package_group_status(VECTOR_PACKAGES)


def local_graphos_status(target: Path | None) -> tuple[str, str] | None:
    if target is None:
        return None
    builder = target / "memory" / "build_graph.py"
    if not builder.exists():
        return None
    graph = target / "graphify-out" / "graph.json"
    if graph.exists():
        return "Verified", "Full engine GraphOS builder and graph artifact found: memory/build_graph.py, graphify-out/graph.json."
    return (
        "Verified",
        "Full engine GraphOS builder found: memory/build_graph.py; graph artifact not built yet. "
        "Activate with `python3 memory/build_graph.py --root blackboard` or `python3 memory/build_graph.py --root runs/<slug>`.",
    )


def local_osvec_status(target: Path | None) -> tuple[str, str] | None:
    if target is None:
        return None
    adapter = target / "memory" / "osvec_adapter.py"
    legacy_adapter = target / "memory" / "turbovec_adapter.py"
    if adapter.exists():
        sidecar = target / "memory" / "store" / "project.sidecar.json"
        if sidecar.exists():
            return "Verified", "Full engine OSVec adapter and vector sidecar found: memory/osvec_adapter.py, memory/store/project.sidecar.json."
        return (
            "Verified",
            "Full engine OSVec adapter found: memory/osvec_adapter.py; vector store not populated yet. "
            "Activate with `python3 memory/osvec_adapter.py selftest`, then add approved lessons/preferences.",
        )
    if legacy_adapter.exists():
        return (
            "Verified",
            "Legacy OSVec/TurboVec adapter found: memory/turbovec_adapter.py; prefer memory/osvec_adapter.py for new projects. "
            "Do not treat OSVec as unavailable; run the adapter selftest before claiming vector recall is active.",
        )
    return None


def build_report(target: Path | None = None) -> str:
    target = target.expanduser().resolve() if target else None
    graph_status = (
        preferred_env_command_status("PROJECT_OS_GRAPHOS_CMD", "PROJECT_OS_GRAPH_CMD")
        or local_graphos_status(target)
        or command_group_status(GRAPH_COMMANDS)
    )
    vector_status = (
        preferred_env_command_status("PROJECT_OS_OSVEC_CMD", "PROJECT_OS_VECTOR_CMD")
        or local_osvec_status(target)
        or osvec_group_status()
    )
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
            "GraphOS",
            graph_status,
            "If the local builder exists, run `python3 memory/build_graph.py --root blackboard`; otherwise set PROJECT_OS_GRAPHOS_CMD, run scripts/install_full_engine.py, or connect Graphify.",
        ),
        (
            "OSVec",
            vector_status,
            "If the local adapter exists, run its selftest and add approved memories; otherwise set PROJECT_OS_OSVEC_CMD, run scripts/install_full_engine.py, or connect TurboVec.",
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
            "- Project OS core works without GraphOS or OSVec tools.",
            "- GraphOS and OSVec become active only when a real tool is installed, connected, or the full-engine local scripts are present.",
            "- Do not tell the user GraphOS/OSVec are unavailable when these local scripts exist; say they are available but may need a graph build or vector population step.",
            "- Model routing is configured in the AI tool, not through `PROJECT_OS_GRAPHOS_CMD` or `PROJECT_OS_OSVEC_CMD`.",
            "- If a capability is `Not configured`, the assistant should say that honestly and keep using the markdown blackboard.",
            "- To connect custom tools, set `PROJECT_OS_GRAPHOS_CMD` and/or `PROJECT_OS_OSVEC_CMD` before running this check.",
            "- Legacy `PROJECT_OS_GRAPH_CMD` and `PROJECT_OS_VECTOR_CMD` are still recognized as fallbacks.",
            "- This script does not install anything; if tools are missing, run `python3 scripts/install_full_engine.py --target .` or connect Graphify/TurboVec yourself.",
            "",
        ]
    )
    return "\n".join(lines)


def write_report(target: Path) -> Path:
    target = target.expanduser().resolve()
    preflight = target / "blackboard" / "17-capability-preflight.md"
    preflight.parent.mkdir(parents=True, exist_ok=True)
    report = build_report(target)
    if preflight.exists() and preflight.read_text(encoding="utf-8").strip():
        existing = preflight.read_text(encoding="utf-8").rstrip()
        preflight.write_text(f"{existing}\n\n{report}", encoding="utf-8")
    else:
        preflight.write_text(f"# Capability Preflight\n\n{report}", encoding="utf-8")
    return preflight


def main() -> int:
    parser = argparse.ArgumentParser(description="Check optional Project OS GraphOS, OSVec, and tool capabilities.")
    parser.add_argument("--target", default=".", help="Project folder to inspect. Default: current folder.")
    args = parser.parse_args()

    report_path = write_report(Path(args.target))
    print(f"Wrote optional tool check to {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
