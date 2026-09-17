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
import io
import json
import os
import re
import secrets
import stat
import sys
from collections import Counter
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:  # runtime authority stays brain.py; this documents the shared API.
    from secret_patterns import redaction_pairs


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

# Alphabetic values are still passwords. Exempt only colon-labelled prose
# about a design token or "the secret", with a sentence continuing afterward.
# An equals sign or a credential-specific label never receives this exemption.
_KEYWORD_PROSE_MATCH = re.compile(
    r"(?:secret|token):\s+"
    r"[A-Za-z]{1,15}\Z", re.I)
_PROSE_CONTEXT = re.compile(r"\b(?:the (?=secret:)|design (?=token:))", re.I)

# Personal data the shared credential list does not carry. No vendor
# credential shape may be defined here, because that is what drifts.
PII_PATTERNS = [
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[REDACTED_EMAIL]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED_SSN_LIKE_VALUE]"),
    (re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b"), "[REDACTED_PHONE_LIKE_VALUE]"),
]


class RedactionUnavailable(RuntimeError):
    """The authoritative credential list could not be loaded."""


def _load_canonical_scanner():
    """Load this installation's shared scanner, never a sys.path substitute."""
    canonical_path = ROOT / "scripts" / "secret_patterns.py"
    if canonical_path.is_symlink() or not canonical_path.is_file():
        raise RedactionUnavailable("canonical scripts/secret_patterns.py is unavailable")
    try:
        canonical_spec = importlib.util.spec_from_file_location(
            "project_os_canonical_secret_patterns", str(canonical_path))
        canonical = importlib.util.module_from_spec(canonical_spec)
        # Use the installation's current source even if timestamp/size-valid
        # bytecode exists. A privacy gate must not use a stale cached scanner.
        exec(compile(canonical_path.read_bytes(), str(canonical_path), "exec"),
             canonical.__dict__)
        canonical_patterns = canonical.SECRET_PATTERNS
        specs = canonical.SECRET_PATTERN_SPECS
        if (not isinstance(canonical_patterns, (list, tuple)) or not canonical_patterns
                or not isinstance(specs, (list, tuple)) or not specs):
            raise ValueError("canonical exports must be nonempty sequences")
        expected = []
        for item in specs:
            if (not isinstance(item, (list, tuple)) or len(item) != 2
                    or not all(isinstance(value, str) and value for value in item)):
                raise ValueError("invalid canonical pattern specification")
            expected.append(re.compile(item[0]))
        if (any(not isinstance(p, re.Pattern) or not isinstance(p.pattern, str)
                for p in canonical_patterns)
                or [(p.pattern, p.flags) for p in canonical_patterns]
                != [(p.pattern, p.flags) for p in expected]):
            raise ValueError("compiled patterns do not match canonical specifications")
    except (Exception, SystemExit):
        # Exception text can contain source or credentials. Keep refusals inert.
        raise RedactionUnavailable("canonical secret scanner could not be loaded") from None
    return canonical


def load_credential_patterns() -> list:
    """Return brain.py's patterns only when they cover the canonical scanner.

    Both modules are located in this installation. The exhaustive marker
    alone is insufficient: portable brain copies retain a compatibility list.
    Raises RedactionUnavailable on failure; the caller refuses.
    """
    canonical = _load_canonical_scanner()
    required = {(p.pattern, p.flags) for p in canonical.SECRET_PATTERNS}
    path = next((p for p in BRAIN_MODULE_CANDIDATES if p.is_file()), None)
    if path is None:
        raise RedactionUnavailable(
            "the brain module is not present at "
            + " or ".join(str(p) for p in BRAIN_MODULE_CANDIDATES)
        )
    previous_scanner = sys.modules.get("secret_patterns")
    try:
        if path.is_symlink():
            raise ValueError("brain module must be local")
        spec = importlib.util.spec_from_file_location("project_os_brain_patterns", str(path))
        module = importlib.util.module_from_spec(spec)
        # The brain's imports must see the validated installation-local source,
        # not an earlier sys.modules entry or stale scanner bytecode.
        sys.modules["secret_patterns"] = canonical
        exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
    except (Exception, SystemExit):
        raise RedactionUnavailable("the brain module could not be loaded") from None
    finally:
        if previous_scanner is None:
            sys.modules.pop("secret_patterns", None)
        else:
            sys.modules["secret_patterns"] = previous_scanner
    if not getattr(module, "SECRET_SCAN_EXHAUSTIVE", False):
        raise RedactionUnavailable(
            f"{path} does not advertise SECRET_SCAN_EXHAUSTIVE, so it may screen "
            "fewer shapes than the shared-brain gate"
        )
    patterns = getattr(module, "SECRET_PATTERNS", None)
    if (not isinstance(patterns, (list, tuple)) or not patterns
            or not all(isinstance(p, re.Pattern) and isinstance(p.pattern, str)
                       for p in patterns)):
        raise RedactionUnavailable(f"{path} exposes no usable SECRET_PATTERNS")
    if not required.issubset({(p.pattern, p.flags) for p in patterns}):
        raise RedactionUnavailable(f"{path} does not provide canonical scanner coverage")
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
        # A short alphabetic value alone is never evidence of harmless prose.
        context = _PROSE_CONTEXT.search(text, max(0, match.start() - 7), match.start() + 7)
        if (_KEYWORD_PROSE_MATCH.fullmatch(match.group(0)) and context
                and context.end() == match.start()
                and re.match(r"[ \t]+[A-Za-z]+\b", text[match.end():])):
            continue
        start, end = match.span()
        # A quoted passphrase can contain spaces; consume through its closing
        # quote (or line end if truncated), not just the first matched word.
        quoted = re.search(r"[:=]\s*([\"'])", match.group(0))
        if quoted:
            value_start = start + quoted.end()
            cursor = value_start
            escaped = False
            while cursor < len(text) and text[cursor] not in "\r\n":
                char = text[cursor]
                cursor += 1
                if char == quoted.group(1) and not escaped:
                    break
                # An odd run of backslashes escapes a quote; an even run
                # leaves it as the boundary. Truncated values end at EOL/EOS.
                escaped = char == "\\" and not escaped
            end = max(end, cursor)
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


def redact(text: str, patterns: list = None, counts: Counter = None) -> str:
    """Redact every match of `patterns`, tallying replacements into `counts`.

    The tally is what makes a redaction visible: the importer prints it and
    writes it into the report, so a transcript that carried credentials can
    never be summarized silently.
    """
    if patterns is None:
        # Preserve the labeled API for library callers, with the same local
        # canonical-module requirement as the CLI.
        patterns = redaction_patterns(_load_canonical_scanner().redaction_pairs())
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


def clean_lines(chunks: list[str], patterns: list = None,
                counts: Counter = None) -> list[str]:
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


def _private_report_parent(output: Path) -> int:
    """Open each parent without following links; create missing dirs privately.

    Keep the directory descriptor for all subsequent publication operations,
    so a parent-path substitution cannot redirect a write to another tree.
    Platforms without no-follow directory operations must refuse safely.
    """
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise OSError("private report publication requires no-follow directory operations")
    parent = Path(os.path.abspath(output)).parent
    # Traversal needs search permission, not permission to list every ancestor.
    access = getattr(os, "O_SEARCH", getattr(os, "O_PATH", os.O_RDONLY))
    flags = access | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(parent.anchor, flags)
    try:
        for part in parent.parts[1:]:
            try:
                child = os.open(part, flags, dir_fd=fd)
            except FileNotFoundError:
                os.mkdir(part, mode=0o700, dir_fd=fd)
                child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _report_stat(fd: int, name: str):
    try:
        current = os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(current.st_mode) or current.st_nlink != 1:
        raise OSError("report paths must be regular files with no links")
    return current


def _same_report(left, right) -> bool:
    if left is None or right is None:
        return left is right
    return (left.st_dev, left.st_ino, left.st_size, left.st_mtime_ns, left.st_ctime_ns) == (
        right.st_dev, right.st_ino, right.st_size, right.st_mtime_ns, right.st_ctime_ns)


def _publish_private_report(output: Path, data: bytes) -> None:
    """Stage private files, retain the old report, then atomically publish.

    Never overwrite an existing backup. Refuse links, special files and an
    output changed while preparing the replacement. Every staging file starts
    at 0600, independent of umask; tighter original owner modes are retained.
    """
    fd = _private_report_parent(output)
    staged = []

    def stage(content, mode):
        name = ".chat-report-" + secrets.token_hex(16)
        file_fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                          0o600, dir_fd=fd)
        staged.append(name)
        with os.fdopen(file_fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
        return name

    try:
        name = output.name
        backup = name + ".bak"
        original = _report_stat(fd, name)
        previous = None
        if original is not None:
            # lstat also sees dangling symlinks. Any existing backup belongs
            # to the user and is a refusal, not permission to clobber it.
            try:
                os.stat(backup, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise OSError("report backup already exists; choose a new output path")
            old_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
            with os.fdopen(old_fd, "rb") as stream:
                if not _same_report(original, os.fstat(stream.fileno())):
                    raise OSError("report changed before backup")
                previous = stream.read()
                if not _same_report(original, os.fstat(stream.fileno())):
                    raise OSError("report changed during backup")
        mode = stat.S_IMODE(original.st_mode) & 0o600 if original else 0o600
        report_stage = stage(data, mode)
        backup_stage = stage(previous, mode) if previous is not None else None
        if not _same_report(original, _report_stat(fd, name)):
            raise OSError("report changed before publication")
        if backup_stage is not None:
            # link is atomic and exclusive: a racing backup cannot be replaced.
            os.link(backup_stage, backup, src_dir_fd=fd, dst_dir_fd=fd,
                    follow_symlinks=False)
            os.replace(report_stage, name, src_dir_fd=fd, dst_dir_fd=fd)
        else:
            os.link(report_stage, name, src_dir_fd=fd, dst_dir_fd=fd,
                    follow_symlinks=False)
    finally:
        try:
            for name in staged:
                try:
                    os.unlink(name, dir_fd=fd)
                except FileNotFoundError:
                    pass
        finally:
            os.close(fd)


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

    with io.StringIO() as f:
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
        _publish_private_report(output, f.getvalue().encode("utf-8"))


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
            "for credentials. Restore scripts/secret_patterns.py and the "
            "matching brain module, then retry.",
            file=sys.stderr,
        )
        return 2
    try:
        chunks = read_export(input_path)
    except FileNotFoundError as exc:
        parser.error(str(exc))
    redaction_counts: Counter = Counter()
    lines = clean_lines(chunks, patterns, redaction_counts)
    try:
        write_summary(lines, output_path, args.max_items, args.include_excerpts, redaction_counts)
    except OSError as exc:
        print(f"REFUSED: could not safely publish private report: {exc}", file=sys.stderr)
        return 2
    print(f"Wrote private memory summary: {output_path}")
    print(f"Redacted {sum(redaction_counts.values())} secret-like value(s) before writing.")
    print("Review the original exports manually before copying any summary into the project blackboard.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
