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

# Status label for "the file is on disk, and that is ALL we checked". Distinct
# from "Verified", which this script only earns by finding an executable on
# PATH or an importable package. Deliberately reuses the label vocabulary
# already documented in blackboard/17-capability-preflight.md instead of
# inventing an eighth status word.
PRESENT_UNVERIFIED = "Claimed but unverified"


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
    """Status derived from the builder script being on disk.

    Like local_osvec_status, this is a file-existence check and nothing more:
    it never runs memory/build_graph.py, never confirms the file is a usable
    helper, and cannot know whether the graph artifact is valid. Reporting that
    as "Verified" told users a capability was proven when only a filename had
    been seen (audit 2026-07-26); "Verified" is reserved for an executable on
    PATH or an importable package. So every branch here reports
    PRESENT_UNVERIFIED and names the command that would actually verify it.
    """
    if target is None:
        return None
    builder = target / "memory" / "build_graph.py"
    if not builder.exists():
        return None
    graph = target / "graphify-out" / "graph.json"
    if graph.exists():
        return (
            PRESENT_UNVERIFIED,
            "Local GraphOS helper and graph artifact found: memory/build_graph.py, graphify-out/graph.json. "
            "Files only — confirm with `python3 memory/build_graph.py --root blackboard` before claiming graph recall works.",
        )
    return (
        PRESENT_UNVERIFIED,
        "Local GraphOS helper found: memory/build_graph.py; graph artifact not built yet. "
        "Activate with `python3 memory/build_graph.py --root blackboard` or `python3 memory/build_graph.py --root runs/<slug>`.",
    )


def local_osvec_status(target: Path | None) -> tuple[str, str] | None:
    """Status derived from adapter files being on disk.

    This is a file-existence check and nothing more: it never imports the
    adapter, never runs its selftest, and cannot know whether numpy is
    installed or the vector store is usable. Reporting that as "Verified" told
    users a capability was proven when only a filename had been seen
    (audit 2026-07-26), so every branch here reports PRESENT_UNVERIFIED and
    names the command that would actually verify it.
    """
    if target is None:
        return None
    adapter = target / "memory" / "osvec_adapter.py"
    legacy_adapter = target / "memory" / "turbovec_adapter.py"
    if adapter.exists():
        sidecar = target / "memory" / "store" / "project.sidecar.json"
        if sidecar.exists():
            return (
                PRESENT_UNVERIFIED,
                "Local OSVec helper and vector sidecar found: memory/osvec_adapter.py, memory/store/project.sidecar.json. "
                "Files only — confirm with `python3 memory/osvec_adapter.py selftest` before claiming vector recall works.",
            )
        return (
            PRESENT_UNVERIFIED,
            "Local OSVec helper found: memory/osvec_adapter.py; vector store not populated yet. "
            "Activate with `python3 memory/osvec_adapter.py selftest`, then add approved lessons/preferences.",
        )
    if legacy_adapter.exists():
        return (
            PRESENT_UNVERIFIED,
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
            "- GraphOS and OSVec become active only when a real tool is installed, connected, or the local helper scripts are present and run.",
            "- Do not tell the user GraphOS/OSVec are unavailable when these local scripts exist; say they are available but may need a graph build or vector population step.",
            f"- `{PRESENT_UNVERIFIED}` means the capability was only asserted — an env var is set, or a helper file is on disk — and nothing was executed to prove it. Run the command in the Recommendation column before reporting that capability as working.",
            "- Model routing is configured in the AI tool, not through `PROJECT_OS_GRAPHOS_CMD` or `PROJECT_OS_OSVEC_CMD`.",
            "- If a capability is `Not configured`, the assistant should say that honestly and keep using the markdown blackboard.",
            "- To connect custom tools, set `PROJECT_OS_GRAPHOS_CMD` and/or `PROJECT_OS_OSVEC_CMD` before running this check.",
            "- Legacy `PROJECT_OS_GRAPH_CMD` and `PROJECT_OS_VECTOR_CMD` are still recognized as fallbacks.",
            "- This script does not install anything; if broader engine tools are missing, run `python3 scripts/install_full_engine.py --target .` or connect Graphify/TurboVec yourself.",
            "",
        ]
    )
    return "\n".join(lines)


REPORT_MARKER = "## Automated Optional Tool Check"

# Explicit machine-owned delimiters. Everything between them belongs to this
# script and is replaced on every run; everything outside them is hand-written
# and must survive untouched. Older reports were written with the bare
# REPORT_MARKER heading only, so write_report() migrates those once (see
# _legacy_block) and from then on relies on these delimiters.
BEGIN_MARKER = "<!-- project-os:optional-tool-check:begin -->"
END_MARKER = "<!-- project-os:optional-tool-check:end -->"

# Lines that only a generated block carries, in the exact order build_report()
# emits them. Requiring the full shape - heading, then `- Date:`, then
# `- Scope: local machine PATH` - is what tells a real legacy report apart from
# hand-written prose that happens to reuse the heading or quote a bullet.
_DATE_FINGERPRINT = "- Date:"
_SCOPE_FINGERPRINT = "- Scope: local machine PATH"
_SUMMARY_HEADING = "### Plain-English Summary"
_FINGERPRINT_WINDOW = 8


def _fence_delimiter(line: str) -> tuple[str, int] | None:
    """Return ``(char, run_length)`` when ``line`` opens or closes a code fence."""
    stripped = line.lstrip(" ")
    if len(line) - len(stripped) > 3:
        return None
    for char in ("`", "~"):
        if stripped.startswith(char * 3):
            return char, len(stripped) - len(stripped.lstrip(char))
    return None


def _fence_mask(lines: list) -> tuple[list, bool]:
    """Mark every line markdown would render as fenced code.

    Returns ``(mask, unclosed)``. An unclosed fence legitimately runs to end of
    file, so it can hide a large tail of the document - including our own block.
    ``unclosed`` lets callers know the mask cannot be trusted.
    """
    mask = [False] * len(lines)
    open_fence = None
    for index, line in enumerate(lines):
        fence = _fence_delimiter(line)
        if open_fence is None:
            if fence is not None:
                open_fence = fence
                mask[index] = True
            continue
        mask[index] = True
        if fence is not None:
            char, run = fence
            info = line.strip().lstrip(char).strip()
            if char == open_fence[0] and run >= open_fence[1] and not info:
                open_fence = None
    return mask, open_fence is not None


def _delimited_blocks(lines: list, mask: list) -> list:
    """Return ``(start, end_exclusive)`` for every complete BEGIN..END block."""
    blocks = []
    start = None
    for index, line in enumerate(lines):
        if mask[index]:
            continue
        stripped = line.strip()
        if stripped == BEGIN_MARKER:
            start = index
        elif stripped == END_MARKER and start is not None:
            blocks.append((start, index + 1))
            start = None
    return blocks


def _orphan_begin(lines: list, mask: list) -> int | None:
    """Index of a BEGIN marker that has no matching END marker after it."""
    orphan = None
    for index, line in enumerate(lines):
        if mask[index]:
            continue
        stripped = line.strip()
        if stripped == BEGIN_MARKER:
            orphan = index
        elif stripped == END_MARKER:
            orphan = None
    return orphan


def _looks_generated(lines: list, index: int) -> bool:
    """True when the heading at ``index`` is followed by generated report content."""
    head = []
    for line in lines[index + 1 : index + 1 + _FINGERPRINT_WINDOW]:
        stripped = line.strip()
        if not stripped:
            continue
        head.append(stripped)
        if len(head) == 2:
            break
    return (
        len(head) == 2
        and head[0].startswith(_DATE_FINGERPRINT)
        and head[1].startswith(_SCOPE_FINGERPRINT)
    )


def _legacy_block_end(lines: list, mask: list, start: int) -> int:
    """Exclusive end of an un-delimited legacy block opened at ``start``.

    Walks the exact shape build_report() has always emitted - metadata bullets,
    a blank, the capability table, a blank, the summary heading, a blank, the
    summary bullets - and stops at the first line that breaks it. Bounding by
    shape rather than by "the next `## ` heading" is what lets hand-written
    `###` subsections under an old report survive the one-time migration. When
    the shape is broken early the block simply ends early, which leaves a few
    stale generated lines behind: noisy, but never destructive.
    """
    limit = len(lines)
    index = start + 1
    end = start + 1

    def content(position):
        """Stripped line, or None once generated content cannot continue."""
        if position >= limit or mask[position]:
            return None
        stripped = lines[position].strip()
        if stripped == BEGIN_MARKER or stripped.startswith("## "):
            return None
        return stripped

    def take(prefix):
        """Consume consecutive lines starting with ``prefix``."""
        nonlocal index, end
        while True:
            stripped = content(index)
            if stripped is None or not stripped.startswith(prefix):
                return
            index += 1
            end = index

    def skip_blank():
        nonlocal index
        if content(index) == "":
            index += 1

    if content(index) == END_MARKER:
        return index + 1

    skip_blank()
    take("- ")  # - Date: / - Scope:
    skip_blank()
    take("|")  # capability table
    skip_blank()
    if content(index) == _SUMMARY_HEADING:
        index += 1
        end = index
        skip_blank()
        take("- ")  # summary bullets
    if content(index) == END_MARKER:
        return index + 1
    return end


def _legacy_block(lines: list, mask: list) -> tuple[int, int] | None:
    for index, line in enumerate(lines):
        if mask[index] or line.strip() != REPORT_MARKER:
            continue
        if _looks_generated(lines, index):
            return index, _legacy_block_end(lines, mask, index)
    return None


def _locate_block(lines: list) -> tuple[int, int, list] | None:
    """Locate the machine-owned block.

    Returns ``(start, end_exclusive, duplicates)`` or ``None`` when the file has
    no machine block yet. ``duplicates`` are stale extra delimited blocks that
    must be dropped so an old report is never presented as current.
    """
    mask, unclosed = _fence_mask(lines)
    blocks = _delimited_blocks(lines, mask)
    if blocks:
        first = blocks[0]
        return first[0], first[1], blocks[1:]
    orphan = _orphan_begin(lines, mask)
    if orphan is not None:
        # A BEGIN with no END is a hand-mangled file. Replace only the marker
        # line: consuming to the next heading or to EOF would delete prose a
        # human wrote below it.
        return orphan, orphan + 1, []
    legacy = _legacy_block(lines, mask)
    if legacy is not None:
        return legacy[0], legacy[1], []
    if unclosed:
        # The mask is unreliable, so our own block may be hidden inside the open
        # fence. Retry fence-blind on the delimiters only - never on the legacy
        # heading, so a *balanced* fenced example is still never touched. Without
        # this, every run would append yet another block.
        blind = [False] * len(lines)
        blocks = _delimited_blocks(lines, blind)
        if blocks:
            last = blocks[-1]
            return last[0], last[1], []
        orphan = _orphan_begin(lines, blind)
        if orphan is not None:
            return orphan, orphan + 1, []
    return None


def render_block(report: str) -> str:
    """Wrap a generated report in the machine-owned delimiters."""
    return f"{BEGIN_MARKER}\n{report.strip()}\n{END_MARKER}"


def apply_report(existing: str, report: str) -> str:
    """Return ``existing`` with the machine-owned block replaced by ``report``."""
    block_lines = render_block(report).split("\n")
    if not existing.strip():
        return "# Capability Preflight\n\n" + "\n".join(block_lines) + "\n"

    lines = existing.rstrip("\n").split("\n")
    location = _locate_block(lines)
    if location is None:
        return "\n".join(lines).rstrip() + "\n\n" + "\n".join(block_lines) + "\n"

    start, end, duplicates = location
    merged = list(lines)
    # Duplicates always sit after the primary block, so removing them from the
    # tail first keeps the primary indices valid.
    for dup_start, dup_end in reversed(duplicates):
        del merged[dup_start:dup_end]
        while 0 < dup_start < len(merged) and not merged[dup_start - 1].strip() and not merged[dup_start].strip():
            del merged[dup_start]
    merged[start:end] = block_lines
    return "\n".join(merged).rstrip("\n") + "\n"


def write_report(target: Path) -> Path:
    target = target.expanduser().resolve()
    preflight = target / "blackboard" / "17-capability-preflight.md"
    preflight.parent.mkdir(parents=True, exist_ok=True)
    report = build_report(target)
    existing = preflight.read_text(encoding="utf-8") if preflight.exists() else ""
    preflight.write_text(apply_report(existing, report), encoding="utf-8")
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
