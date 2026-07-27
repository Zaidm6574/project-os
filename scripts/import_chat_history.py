#!/usr/bin/env python3
"""Create a private Project OS memory report from local chat exports.

This script is intentionally conservative. It does not upload data, does not
store raw transcripts in the default output, and redacts secrets using the
project's ONE authoritative credential list.

Credential shapes are NOT defined here. They are loaded at run time from
addons/full-engine/brain/brain.py's SECRET_PATTERNS -- the same list
scripts/brain_append.py gates the shared brain with. The local fork this file
used to carry had drifted badly: it missed live Slack, SendGrid, Figma, Stripe,
Twilio and GitLab keys and the generic `api_key=...` catch-all, so a README
that promised "redacts secrets" wrote them straight into the report
(audit finding, 2026-07-26). If that list cannot be loaded the importer
REFUSES and writes nothing rather than falling back to a weaker one.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]

# Where brain.py lives: the repo / bootstrapped layout, and the
# install_full_engine layout (addons/full-engine/brain -> <project>/brain).
BRAIN_MODULE_CANDIDATES = (
    ROOT / "addons" / "full-engine" / "brain" / "brain.py",
    ROOT / "brain" / "brain.py",
)

CREDENTIAL_REPLACEMENT = "[REDACTED_CREDENTIAL]"

# A shape the shared list matches only the HEADER of. It must run BEFORE the
# credential patterns: once brain.py has replaced `-----BEGIN ... KEY-----`
# there is no anchor left for the whole-block pattern, and the key BODY would
# survive in the report. Detection is still brain.py's; this only widens the
# span.
#
# The lazy body stops at the FIRST of: the END footer (a complete block), a
# blank line, or end-of-text. That last two cases are what redact a TRUNCATED
# block -- a BEGIN header with NO END footer. Before, only whole BEGIN...END
# blocks matched here; brain.py's header-only `-----BEGIN ... KEY-----` pattern
# then redacted just that one line and the base64 body survived verbatim in the
# report (audit 2026-07-27). Stopping at a blank line / EOS is fail-closed: it
# may over-redact text glued to the key with no blank-line break after it, but
# it never LEAKS the body.
SPAN_WIDENER_PATTERNS = [
    (re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
        r"[\s\S]*?"
        r"(?:-----END [A-Z ]*PRIVATE KEY-----|(?=\r?\n[ \t]*\r?\n)|\Z)",
        re.S), "[REDACTED_PRIVATE_KEY]"),
]

# The shared list's generic keyword catch-all -- (api_key|secret|password|
# passwd|token|bearer) + separator + 6+ non-space chars -- has no vendor shape
# to anchor on, so ordinary prose like "the secret: happiness comes from
# within" trips it and the redactor eats a real sentence. A live credential
# VALUE is one opaque, high-entropy token; a prose value is a natural-language
# word. Mirror the brain-side gate that stopped {"auth_token": "rotate
# quarterly per runbook"} from being REFUSED (2026-07-26): skip a match that is
# a keyword + separator + a single plain alphabetic word. Only the generic
# catch-all can ever produce that exact shape -- every vendor pattern's match
# carries digits, symbols, or a non-keyword prefix -- so this can never
# un-redact a vendor key (audit 2026-07-27). The 15-char cap keeps a long
# all-letter passphrase (unusual, but possible) on the redacted side.
_KEYWORD_PROSE_MATCH = re.compile(
    r"(?:api[_-]?key|secret|password|passwd|token|bearer)\s*[:=]\s*"
    r"[A-Za-z]{1,15}\Z", re.I)

# Personal data the shared credential list does not carry. No vendor
# credential shape may be defined here, because that is what drifts.
PII_PATTERNS = [
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[REDACTED_EMAIL]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED_SSN_LIKE_VALUE]"),
    (re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b"), "[REDACTED_PHONE_LIKE_VALUE]"),
]


class RedactionUnavailable(RuntimeError):
    """The authoritative credential list could not be loaded."""


def load_credential_patterns() -> list:
    """Return brain.py's SECRET_PATTERNS as (regex, replacement) pairs.

    Loaded by file path (the install_full_engine.load_central_brain_module
    idiom) so an unrelated `brain` module on sys.path cannot shadow it, and
    gated on SECRET_SCAN_EXHAUSTIVE exactly like scripts/brain_append.py so an
    older brain.py cannot silently downgrade this to a narrower list.

    Raises RedactionUnavailable on every failure shape; the caller refuses.
    """
    path = next((p for p in BRAIN_MODULE_CANDIDATES if p.is_file()), None)
    if path is None:
        raise RedactionUnavailable(
            "the brain module is not present at "
            + " or ".join(str(p) for p in BRAIN_MODULE_CANDIDATES)
        )
    spec = importlib.util.spec_from_file_location("project_os_brain_patterns", str(path))
    if spec is None or spec.loader is None:
        raise RedactionUnavailable(f"{path} could not be loaded")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - any import failure must fail closed
        raise RedactionUnavailable(f"{path} failed to import ({exc})")
    if not getattr(module, "SECRET_SCAN_EXHAUSTIVE", False):
        raise RedactionUnavailable(
            f"{path} does not advertise SECRET_SCAN_EXHAUSTIVE, so it may screen "
            "fewer shapes than the shared-brain gate"
        )
    patterns = getattr(module, "SECRET_PATTERNS", None)
    if (not isinstance(patterns, (list, tuple)) or not patterns
            or not all(hasattr(p, "finditer") for p in patterns)):
        raise RedactionUnavailable(f"{path} exposes no usable SECRET_PATTERNS")
    return [(p, CREDENTIAL_REPLACEMENT) for p in patterns]


def redaction_patterns(credential_patterns: list) -> list:
    """Order the passes: widen spans, then screen credentials, then strip PII."""
    return SPAN_WIDENER_PATTERNS + list(credential_patterns) + PII_PATTERNS


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


def _sub_whole_tokens(text: str, pattern, replacement: str):
    """Replace each match, widened to the whitespace-delimited token(s) it sits in.

    Detection stays exactly brain.py's; only the replacement SPAN is widened,
    and only outward to whitespace. Several shared patterns match a prefix of a
    longer credential -- SendGrid's `SG.<id>.<signature>` pattern stops at the
    second dot, JWT stops before the signature -- so a plain `sub` leaves half
    the key sitting in the report. Widening is generic (no vendor shape is
    re-implemented here) and cannot reach past a space, so surrounding prose is
    untouched. Returns (text, replacement_count).
    """
    spans = []
    hits = 0
    for match in pattern.finditer(text):
        # Ordinary prose that merely reads "keyword: word" is not a credential;
        # skip it so a real sentence survives the report (audit 2026-07-27).
        if _KEYWORD_PROSE_MATCH.match(match.group(0)):
            continue
        start, end = match.span()
        # Both scans are bounded by the previous span's end, which is always a
        # whitespace index (or end of text). Without that bound a long
        # whitespace-free blob -- a minified JSON export line -- rescans the
        # same token once per match and turns this into O(n^2): 96 KB took 80s.
        floor = spans[-1][1] if spans else 0
        while start > floor and not text[start - 1].isspace():
            start -= 1
        if spans and spans[-1][1] >= end:
            end = spans[-1][1]  # same token; already scanned to its boundary
        else:
            while end < len(text) and not text[end].isspace():
                end += 1
        hits += 1
        # Merge rather than skip: widening can push a span over the start of
        # the NEXT match, and dropping that match would leave its tail (the
        # body of a second PEM block glued to the first) in the report.
        if spans and start <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], end)
        else:
            spans.append([start, end])
    if not hits:
        return text, 0
    out = []
    pos = 0
    for start, end in spans:
        out.append(text[pos:start])
        out.append(replacement)
        pos = end
    out.append(text[pos:])
    return "".join(out), hits


def redact(text: str, patterns: list, counts: Counter = None) -> str:
    """Redact every match of `patterns`, tallying replacements into `counts`.

    The tally is what makes a redaction visible: the importer prints it and
    writes it into the report, so a transcript that carried credentials can
    never be summarized silently.
    """
    for pattern, replacement in patterns:
        text, hits = _sub_whole_tokens(text, pattern, replacement)
        if hits and counts is not None:
            counts[replacement] += hits
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


def clean_lines(chunks: list[str], patterns: list, counts: Counter = None) -> list[str]:
    lines: list[str] = []
    for chunk in chunks:
        chunk = redact(chunk, patterns, counts)
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


def write_summary(
    lines: list[str],
    output: Path,
    max_items: int,
    include_excerpts: bool,
    redaction_counts: Counter = None,
) -> None:
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
    # 2026-07-25: back up any pre-existing report before truncating it. A prior
    # run's output (or a hand-curated file placed at the same path) would
    # otherwise be silently destroyed by the unconditional open("w") below.
    if output.exists():
        backup = output.with_name(output.name + ".bak")
        backup.write_bytes(output.read_bytes())
    with output.open("w", encoding="utf-8") as f:
        f.write("# Private Chat Memory Summary\n\n")
        f.write("Generated locally from user-provided exports. Review before using. Keep this file private.\n\n")
        f.write("This default report avoids copying full source lines. Use it as a review queue, not as verified memory.\n\n")
        # A redaction that nobody can see is indistinguishable from a leak.
        # Always state the tally, including the zero case.
        counts = redaction_counts or Counter()
        f.write("## Redactions Applied\n\n")
        f.write(f"- Secret-like values redacted before writing: {sum(counts.values())}\n")
        for label, count in sorted(counts.items()):
            f.write(f"- {label}: {count}\n")
        f.write("\n## Likely Preferences\n\n")
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
    # Load the shared credential list BEFORE reading or writing anything: with
    # no authoritative patterns there is no safe report to write, so refuse
    # instead of degrading to a weaker local list.
    try:
        patterns = redaction_patterns(load_credential_patterns())
    except RedactionUnavailable as exc:
        print(
            f"REFUSED: {exc}; refusing to write a report that was never screened "
            "for credentials. Restore addons/full-engine/brain/brain.py (the "
            "authoritative secret list) and retry.",
            file=sys.stderr,
        )
        return 2
    try:
        chunks = read_export(input_path)
    except FileNotFoundError as exc:
        parser.error(str(exc))
    redaction_counts: Counter = Counter()
    lines = clean_lines(chunks, patterns, redaction_counts)
    write_summary(lines, output_path, args.max_items, args.include_excerpts, redaction_counts)
    print(f"Wrote private memory summary: {output_path}")
    print(f"Redacted {sum(redaction_counts.values())} secret-like value(s) before writing.")
    print("Review the original exports manually before copying any summary into the project blackboard.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
