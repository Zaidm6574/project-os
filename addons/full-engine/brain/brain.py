#!/usr/bin/env python3
"""
Project OS - tool-to-tool shared-brain bridge.

The mission of Project OS is a portable shared brain across AI tools. The OSVec
side-car stores durable lessons inside one project, and this bridge provides a
small local exchange file that other tools can read or append to.

It is deliberately small and safe:
  * zero network calls,
  * stdlib only,
  * refuses to import/export files outside this project copy.

It is the executable counterpart to the doctrine sibling; see brain/README.md.

Subcommands
-----------
  export   read durable lessons (from the OSVec side-car via osvec_adapter
           if importable, else from a --from JSONL/JSON file) and append any
           not-already-present lessons to brain/shared-brain.jsonl (dedup by id).
  save-chat  save an approved chat summary, preference, decision, or lesson
           directly into brain/shared-brain.jsonl. Summary mode is the default;
           raw mode must be explicit and still refuses secret-looking text.
  import   read brain/shared-brain.jsonl and print the lessons, or with --into
           write them to a file another AI tool can ingest.
  --selftest  round-trip one synthetic lesson through export then import; exit 0.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BRAIN_FILE = os.path.join(HERE, "shared-brain.jsonl")
SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)(api[_-]?key|secret|password|passwd|token)\s*[:=]\s*\S{6,}"),
]


def _safe_path(path: str) -> str:
    """Resolve a path and refuse anything outside the project copy."""
    full = os.path.abspath(path)
    if os.path.commonpath([full, ROOT]) != ROOT:
        sys.exit(f"refuse: path '{path}' is outside the project ({ROOT})")
    return full


def _read_jsonl(path):
    out = []
    if not os.path.exists(path):
        return out
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _existing_ids(path):
    return {r.get("id") for r in _read_jsonl(path)}


def _looks_like_secret(text: str) -> bool:
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _stable_chat_id(kind: str, text: str, source: str, mode: str) -> str:
    digest = hashlib.sha256(f"{kind}\0{source}\0{mode}\0{text}".encode("utf-8")).hexdigest()[:12]
    return f"chat-{digest}"


def _tags(values):
    tags = []
    for value in values or []:
        for tag in str(value).split(","):
            clean = tag.strip()
            if clean and clean not in tags:
                tags.append(clean)
    return tags


def _chat_text(args):
    if args.summary_file:
        with open(_safe_path(args.summary_file), encoding="utf-8") as f:
            text = f.read()
    else:
        text = args.summary
    text = text.strip()
    if not text:
        sys.exit("refuse: save-chat needs a non-empty summary")
    if _looks_like_secret(text):
        sys.exit("refuse: chat text looks like it contains a secret; save a redacted summary instead")
    return text


def _lessons_from_adapter():
    """Read durable lessons from the OSVec side-car via osvec_adapter."""
    sys.path.insert(0, os.path.join(ROOT, "memory"))
    try:
        import osvec_adapter as tv  # type: ignore
    except Exception:
        return None
    sidecar = getattr(tv, "SIDECAR_PATH", None)
    if not sidecar or not os.path.exists(sidecar):
        return []
    with open(sidecar) as f:
        blob = json.load(f)
    out = []
    for rec in blob.get("records", {}).values():
        if rec.get("memory_type") == "lesson":
            out.append({
                "id": rec.get("memory_id"),
                "ts": rec.get("created_at", "") or time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "source": "project-os",
                "type": "lesson",
                "text": rec.get("text", ""),
                "tags": rec.get("tags", []) or [],
            })
    return out


def _lessons_from_file(path):
    full = _safe_path(path)
    if full.endswith(".jsonl"):
        recs = _read_jsonl(full)
    else:
        with open(full) as f:
            blob = json.load(f)
        recs = blob.get("records", blob) if isinstance(blob, dict) else blob
        if isinstance(recs, dict):
            recs = list(recs.values())
    out = []
    for r in recs:
        if r.get("type") == "lesson" or r.get("memory_type") == "lesson":
            out.append({
                "id": r.get("id") or r.get("memory_id"),
                "ts": r.get("ts") or r.get("created_at", "") or time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "source": r.get("source", "project-os"),
                "type": "lesson",
                "text": r.get("text", ""),
                "tags": r.get("tags", []) or [],
            })
    return out


def cmd_export(args):
    _safe_path(BRAIN_FILE)
    if args.from_file:
        lessons = _lessons_from_file(args.from_file)
    else:
        lessons = _lessons_from_adapter()
        if lessons is None:
            sys.exit("refuse: osvec_adapter not importable; pass --from FILE")
    have = _existing_ids(BRAIN_FILE)
    added = 0
    with open(BRAIN_FILE, "a") as f:
        for l in lessons:
            if not l.get("id") or l["id"] in have:
                continue
            f.write(json.dumps(l) + "\n")
            have.add(l["id"])
            added += 1
    print(f"export: {added} new lesson(s) appended to {os.path.relpath(BRAIN_FILE, ROOT)}")
    return 0


def cmd_import(args):
    lessons = _read_jsonl(BRAIN_FILE)
    if args.into:
        full = _safe_path(args.into)
        with open(full, "w") as f:
            for l in lessons:
                f.write(json.dumps(l) + "\n")
        print(f"import: wrote {len(lessons)} lesson(s) to {os.path.relpath(full, ROOT)}")
    else:
        for l in lessons:
            print(json.dumps(l))
    return 0


def cmd_save_chat(args):
    _safe_path(BRAIN_FILE)
    text = _chat_text(args)
    source = args.source or ("chat-raw" if args.mode == "raw" else "chat-summary")
    tags = _tags(args.tag)
    mode_tag = "raw-chat" if args.mode == "raw" else "chat-summary"
    if mode_tag not in tags:
        tags.append(mode_tag)
    rid = args.id or _stable_chat_id(args.kind, text, source, args.mode)
    record = {
        "id": rid,
        "ts": _now(),
        "source": source,
        "type": args.kind,
        "text": text,
        "tags": tags,
        "summary_only": args.mode == "summary",
        "raw_chat": args.mode == "raw",
        "approved": True,
    }

    have = _existing_ids(BRAIN_FILE)
    if rid in have:
        print(f"save-chat: kept existing {rid}")
        return 0
    with open(BRAIN_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")
    print(f"save-chat: appended {rid} to {os.path.relpath(BRAIN_FILE, ROOT)}")
    return 0


def _selftest():
    syn = {"id": "selftest-%d" % int(time.time()),
           "ts": _now(), "source": "codex",
           "type": "lesson", "text": "round-trip self-test lesson", "tags": ["selftest"]}
    tmp = os.path.join(HERE, ".selftest-from.jsonl")
    with open(tmp, "w") as f:
        f.write(json.dumps(syn) + "\n")
    try:
        cmd_export(argparse.Namespace(from_file=tmp))
        cmd_save_chat(
            argparse.Namespace(
                summary="Save chat memories as approved summaries, not raw logs.",
                summary_file=None,
                id="selftest-chat-save",
                kind="lesson",
                tag=["selftest", "chat"],
                source=None,
                mode="summary",
            )
        )
        roundtripped = {r["id"] for r in _read_jsonl(BRAIN_FILE)}
        assert syn["id"] in roundtripped, "export did not persist synthetic lesson"
        assert "selftest-chat-save" in roundtripped, "save-chat did not persist synthetic chat summary"
        cmd_import(argparse.Namespace(into=None))
        print("selftest: OK")
        return 0
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def main():
    p = argparse.ArgumentParser(description="tool-to-tool shared-brain bridge")
    p.add_argument("--selftest", action="store_true", help="round-trip a synthetic lesson and exit")
    sub = p.add_subparsers(dest="cmd")
    pe = sub.add_parser("export", help="append durable lessons to the shared brain")
    pe.add_argument("--from", dest="from_file", default=None, help="JSONL/JSON file to read lessons from")
    ps = sub.add_parser("save-chat", help="save an approved chat summary to the shared brain")
    text = ps.add_mutually_exclusive_group(required=True)
    text.add_argument("--summary", default=None, help="approved summary, lesson, preference, or decision to save")
    text.add_argument("--summary-file", default=None, help="project-local file containing the approved summary")
    ps.add_argument("--id", default=None, help="stable id for this memory; generated from text when omitted")
    ps.add_argument(
        "--kind",
        choices=["lesson", "preference", "decision", "project-pattern", "research-finding", "agent-packet"],
        default="lesson",
        help="memory type to write",
    )
    ps.add_argument("--tag", action="append", default=[], help="tag to add; may be repeated or comma-separated")
    ps.add_argument("--source", default=None, help="memory source label; defaults to chat-summary or chat-raw")
    ps.add_argument(
        "--mode",
        choices=["summary", "raw"],
        default="summary",
        help="summary is the safe default; raw must be explicit and still refuses secret-looking text",
    )
    pi = sub.add_parser("import", help="read the shared brain; print or write with --into")
    pi.add_argument("--into", default=None, help="write lessons to this file for another AI tool")
    args = p.parse_args()
    if args.selftest:
        sys.exit(_selftest())
    if args.cmd == "export":
        sys.exit(cmd_export(args))
    if args.cmd == "save-chat":
        sys.exit(cmd_save_chat(args))
    if args.cmd == "import":
        sys.exit(cmd_import(args))
    p.print_help()
    sys.exit(0)


if __name__ == "__main__":
    main()
