#!/usr/bin/env python3
"""Create a private Project OS memory report from local chat exports.

This script is intentionally conservative. It does not upload data, does not
store raw transcripts in the default output, and redacts common secret patterns.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


SECRET_PATTERNS = [
    (re.compile(r"sk-[A-Za-z0-9_-]{20,}"), "[REDACTED_OPENAI_KEY]"),
    (re.compile(r"sk-proj-[A-Za-z0-9_-]{20,}"), "[REDACTED_OPENAI_KEY]"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED_AWS_KEY]"),
    (re.compile(r"ASIA[0-9A-Z]{16}"), "[REDACTED_AWS_KEY]"),
    (re.compile(r"AIza[0-9A-Za-z_-]{20,}"), "[REDACTED_GOOGLE_KEY]"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[REDACTED_PRIVATE_KEY]"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[REDACTED_EMAIL]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED_SSN_LIKE_VALUE]"),
    (re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b"), "[REDACTED_PHONE_LIKE_VALUE]"),
]

TOOL_KEYWORDS = [
    "codex",
    "claude",
    "chatgpt",
    "openai",
    "cursor",
    "github",
    "python",
    "javascript",
    "typescript",
    "react",
    "next.js",
    "supabase",
    "postgres",
    "vercel",
    "figma",
    "notion",
    "obsidian",
]


def redact(text: str) -> str:
    for pattern, replacement in SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def iter_strings(value: Any) -> list[str]:
    strings: list[str] = []
    if isinstance(value, str):
        strings.append(value)
    elif isinstance(value, list):
        for item in value:
            strings.extend(iter_strings(item))
    elif isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in {"content", "text", "message", "prompt", "response", "title", "summary"}:
                strings.extend(iter_strings(item))
            elif isinstance(item, (dict, list)):
                strings.extend(iter_strings(item))
    return strings


def read_export(path: Path) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(f"Input path does not exist: {path}")
    files = [path] if path.is_file() else sorted(
        p for p in path.rglob("*") if p.suffix.lower() in {".json", ".txt", ".md"}
    )
    chunks: list[str] = []
    for file in files:
        try:
            raw = file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if file.suffix.lower() == ".json":
            try:
                chunks.extend(iter_strings(json.loads(raw)))
            except json.JSONDecodeError:
                chunks.append(raw)
        else:
            chunks.append(raw)
    return chunks


def clean_lines(chunks: list[str]) -> list[str]:
    lines: list[str] = []
    for chunk in chunks:
        chunk = redact(chunk)
        for line in chunk.splitlines():
            line = re.sub(r"\s+", " ", line).strip()
            if 40 <= len(line) <= 500:
                lines.append(line)
    return lines


def select_lines(lines: list[str], patterns: list[str], limit: int) -> list[str]:
    selected: list[str] = []
    regexes = [re.compile(pattern, re.I) for pattern in patterns]
    for line in lines:
        if any(regex.search(line) for regex in regexes):
            selected.append(line)
        if len(selected) >= limit:
            break
    return selected


def count_tools(lines: list[str]) -> Counter[str]:
    counts: Counter[str] = Counter()
    lowered = "\n".join(lines).lower()
    for tool in TOOL_KEYWORDS:
        count = lowered.count(tool)
        if count:
            counts[tool] = count
    return counts


def clipped_hint(line: str, max_words: int = 10) -> str:
    words = line.split()
    hint = " ".join(words[:max_words])
    return hint + ("..." if len(words) > max_words else "")


def write_summary(lines: list[str], output: Path, max_items: int, include_excerpts: bool) -> None:
    preferences = select_lines(
        lines,
        [r"\bi want\b", r"\bi like\b", r"\bi prefer\b", r"\bi need\b", r"\bmy goal\b", r"\bi don't want\b"],
        max_items,
    )
    project_ideas = select_lines(
        lines,
        [r"\bbuild\b", r"\bproject\b", r"\bapp\b", r"\bwebsite\b", r"\bbusiness\b", r"\bagent\b", r"\bworkflow\b"],
        max_items,
    )
    blockers = select_lines(
        lines,
        [r"\bstuck\b", r"\bconfus", r"\bproblem\b", r"\bissue\b", r"\bbug\b", r"\bhard\b", r"\boverwhelm"],
        max_items,
    )
    tools = count_tools(lines).most_common(20)

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as f:
        f.write("# Private Chat Memory Summary\n\n")
        f.write("Generated locally from user-provided exports. Review before using. Keep this file private.\n\n")
        f.write("This default report avoids copying full source lines. Use it as a review queue, not as verified memory.\n\n")
        f.write("## Likely Preferences\n\n")
        f.write(f"- Candidate preference lines found: {len(preferences)}\n")
        if include_excerpts:
            for item in preferences:
                f.write(f"  - Excerpt: {clipped_hint(item)}\n")
        f.write("\n## Project Ideas And Themes\n\n")
        f.write(f"- Candidate project/theme lines found: {len(project_ideas)}\n")
        if include_excerpts:
            for item in project_ideas:
                f.write(f"  - Excerpt: {clipped_hint(item)}\n")
        f.write("\n## Repeated Tools Mentioned\n\n")
        for tool, count in tools:
            f.write(f"- {tool}: {count}\n")
        f.write("\n## Recurring Blockers\n\n")
        f.write(f"- Candidate blocker lines found: {len(blockers)}\n")
        if include_excerpts:
            for item in blockers:
                f.write(f"  - Excerpt: {clipped_hint(item)}\n")
        f.write("\n## Recommended Project OS Memory Entries\n\n")
        f.write("- user-preference: Open the original private export locally and write a short approved summary in your own words.\n")
        f.write("- project-pattern: Convert repeated project ideas into reusable patterns only after manual review.\n")
        f.write("- lesson: Convert recurring blockers into lessons or safeguards only after manual review.\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a private local review report from chat exports.")
    parser.add_argument("--input", required=True, help="Chat export file or folder. Supports .json, .txt, and .md.")
    parser.add_argument("--output", default="private-memory/chat-memory.md", help="Output markdown path.")
    parser.add_argument("--max-items", type=int, default=30, help="Maximum lines per section.")
    parser.add_argument(
        "--include-excerpts",
        action="store_true",
        help="Include short redacted excerpts from matching lines. Leave off for the safest default.",
    )
    args = parser.parse_args()

    input_path = Path(args.input).expanduser()
    output_path = Path(args.output).expanduser()
    try:
        chunks = read_export(input_path)
    except FileNotFoundError as exc:
        parser.error(str(exc))
    lines = clean_lines(chunks)
    write_summary(lines, output_path, args.max_items, args.include_excerpts)
    print(f"Wrote private memory summary: {output_path}")
    print("Review the original exports manually before copying any summary into the project blackboard.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
