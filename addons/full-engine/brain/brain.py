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
# Secret shapes. The 2026-07-25 audit found the original `sk-[A-Za-z0-9]{16,}`
# could not cross a hyphen or underscore, so every modern key format passed:
# sk-ant-…, sk-proj-…, sk_live_… (Stripe), xoxb-… (Slack), figd_…, SG.… ,
# Twilio AC…, JWTs, and postgres:// DSNs with inline credentials.
SECRET_PATTERNS = [
    re.compile(r"(?<![A-Za-z0-9_])sk-[A-Za-z0-9_\-]{16,}"),            # OpenAI/Anthropic incl. sk-ant-, sk-proj-
    re.compile(r"(?<![A-Za-z0-9_])sk_(live|test)_[A-Za-z0-9]{16,}"),   # Stripe
    re.compile(r"(?<![A-Za-z0-9_])rk_(live|test)_[A-Za-z0-9]{16,}"),   # Stripe restricted
    re.compile(r"AKIA[0-9A-Z]{16}"),                  # AWS access key id
    re.compile(r"ASIA[0-9A-Z]{16}"),                  # AWS session key
    # gh[pours]_ is ONE family: personal (p), OAuth (o), server-to-server (s),
    # user-to-server (u) and refresh (r). Only the first three were listed, so
    # a GitHub App's ghu_/ghr_ tokens walked through the gate -- and through
    # import_chat_history, which certifies its output as redacted (adversary
    # 2026-07-26). Enumerating a vendor's prefixes one at a time is how this
    # list has failed before; match the character class, not the examples.
    re.compile(r"(?<![A-Za-z0-9_])gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"(?<![A-Za-z0-9_])AIza[0-9A-Za-z_\-]{20,}"),           # Google
    re.compile(r"(?<![A-Za-z0-9_])ya29\.[A-Za-z0-9_\-]{20,}"),         # Google OAuth
    re.compile(r"(?<![A-Za-z0-9_])xox[baprs]-[A-Za-z0-9\-]{10,}"),     # Slack
    re.compile(r"(?<![A-Za-z0-9_])figd_[A-Za-z0-9_\-]{20,}"),          # Figma PAT
    re.compile(r"SG\.[A-Za-z0-9_\-]{20,}"),           # SendGrid
    re.compile(r"\bAC[0-9a-fA-F]{32}\b"),             # Twilio account SID
    re.compile(r"\bSK[0-9a-fA-F]{32}\b"),             # Twilio API key
    re.compile(r"(?<![A-Za-z0-9_])glpat-[A-Za-z0-9_\-]{16,}"),         # GitLab
    re.compile(r"(?<![A-Za-z0-9_])dop_v1_[A-Za-z0-9]{32,}"),           # DigitalOcean
    re.compile(r"(?<![A-Za-z0-9_])npm_[A-Za-z0-9]{30,}"),
    # Added 2026-07-25: an adversarial pass found these pass the gate when
    # pasted BARE, i.e. dropped into a lesson's prose with no `KEY=` label for
    # the keyword catch-all to anchor on -- which is exactly how an accidental
    # paste looks.
    re.compile(r"(?<![A-Za-z0-9_])hf_[A-Za-z0-9]{30,}"),               # HuggingFace
    re.compile(r"(?<![A-Za-z0-9_])ntn_[A-Za-z0-9]{40,}"),              # Notion
    re.compile(r"(?<![A-Za-z0-9_])lin_api_[A-Za-z0-9]{30,}"),          # Linear
    re.compile(r"(?<![A-Za-z0-9_])vercel_[A-Za-z0-9]{20,}"),           # Vercel
    re.compile(r"(?i)https://[0-9a-f]{32}@[\w.\-]+/\d+"),              # Sentry DSN
    re.compile(r"(?i)AccountKey\s*=\s*[A-Za-z0-9+/]{40,}={0,2}"),      # Azure storage
    re.compile(r"https://hooks\.slack\.com/services/T[A-Za-z0-9/]{20,}"),
    re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\."),  # JWT
    re.compile(r"(?i)\b(postgres(ql)?|mysql|mongodb(\+srv)?|redis|amqp)://[^\s:@/]+:[^\s@/]+@"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)(api[_-]?key|secret|password|passwd|token|bearer)\s*[:=]\s*\S{6,}"),
]

# A value that is nothing but a redaction marker carries no secret by
# construction, so exempting it cannot hide one: the match must consume the
# WHOLE value, and "REDACTEDsk-ant-api03..." is therefore still scanned.
_PLACEHOLDER_VALUE = re.compile(
    r"^[\W_]*(?:redacted|removed|omitted|scrubbed|todo|tbd|changeme|"
    r"placeholder|example|sample|none|null|nil|unset|empty|n/?a|x+|\*+|\.+)"
    r"[\W_]*$", re.I)


def _could_be_credential(value):
    """Could this VALUE be the secret a keyword pattern is hunting for?

    Only used to gate the "key=value" spelling of the split-credential scan
    (see _iter_scannable_items). A live credential is one opaque token: prose
    ("rotate quarterly per runbook") contains whitespace, and a redaction
    marker ("REDACTED", "***") carries no secret. Both were being REFUSED,
    which blocks legitimate saves. The bare key+value concatenation is still
    yielded unconditionally, so prefix reassembly is unaffected by this gate.
    """
    return bool(value.strip()) and not (
        re.search(r"\s", value) or _PLACEHOLDER_VALUE.match(value))


# Historical allowlist of the fields a human was expected to paste text into.
# KEPT FOR REFERENCE ONLY -- record_secret_hit() no longer consults it. The
# 2026-07-26 audit found the allowlist WAS the hole: a credential in any field
# outside these eight (a nested dict, `metadata`, `author`, `url`, a custom key)
# was written through unscanned, as was a non-dict payload (a bare JSON string
# or list). The gate is now exhaustive by construction instead: it walks the
# WHOLE payload. Do not reintroduce a field allowlist here.
SCANNED_FIELDS = ("text", "summary", "note", "content", "tags", "source", "id", "title")

# Depth cap for the exhaustive walk. Nothing any writer produces nests this
# deep, so a payload that exceeds it is REFUSED rather than truncated --
# truncating would hand back the bypass (bury the key 200 levels down).
MAX_SCAN_DEPTH = 64

# Marker other writers (scripts/brain_append.py) assert on, so importing an
# old or shadowing `brain` module that only scans an allowlist fails closed
# instead of silently screening eight fields.
SECRET_SCAN_EXHAUSTIVE = True


def _safe_path(path: str) -> str:
    """Resolve a path and refuse anything outside the project copy."""
    root = os.path.realpath(os.path.abspath(ROOT))
    full = os.path.realpath(os.path.abspath(path))
    if os.path.commonpath([full, root]) != root:
        sys.exit(f"refuse: path '{path}' is outside the project ({ROOT})")
    return full


def _read_jsonl(path):
    out = []
    if not os.path.exists(path):
        return out
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            # 2026-07-25 audit: this used to call json.loads with no
            # try/except, so one corrupt line anywhere in the file (a crash
            # mid-write, a hand edit) raised and took the whole read down.
            # central_brain.py's read_jsonl already skips bad lines instead.
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _existing_ids(path):
    return {r.get("id") for r in _read_jsonl(path)}


def _heal_truncated_tail(handle, path):
    """Never weld a new record onto a crash-truncated last line.

    If a previous write died between the buffered write and the flush
    (SIGKILL, full disk, an interrupted `bb_lock append`), the last line is a
    partial JSON fragment with no newline. Appending to it produces ONE
    unparseable line, destroying both the truncated record AND the one being
    saved now -- and every reader skips unparseable lines silently
    (_read_jsonl, central_brain.read_jsonl, mneme_adapter._gather), so the
    loss is invisible forever while the command prints success and exits 0.
    Start a new line instead: the damaged fragment stays damaged, but it
    stays ALONE.

    scripts/brain_append.py got this heal in d71aeca; the three sibling
    writers to the same file did not, which is the repo's signature
    "fixed one of N call sites" pattern (adversarial verify 2026-07-26).
    save-chat is the worst case -- unlike central-brain push/pull there is no
    upstream copy to re-sync from, so the lesson is gone for good.
    """
    if handle.tell():
        with open(path, "rb") as probe:
            probe.seek(-1, os.SEEK_END)
            if probe.read(1) != b"\n":
                handle.write("\n")


def _looks_like_secret(text: str) -> bool:
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)


class _Unscannable(Exception):
    """A payload holds a value the secret scan cannot read -- refuse it.

    Fail closed: an unreadable value is treated exactly like a detected secret,
    because "we could not look" must never be spelled "it is clean".
    """

    def __init__(self, path: str) -> None:
        super().__init__(path)
        self.path = path


# Scalars that cannot carry a credential shape, so they need no scan. bool is an
# int subclass, so it is covered; str/bytes are handled before this check.
_SAFE_SCALARS = (int, float, complex, type(None))

_PAYLOAD_ROOT = "<payload>"


def _child_path(path: str, key: str) -> str:
    return path + "." + key if path else key


def _iter_scannable_items(value, root: str = ""):
    """Yield ``(path, text)`` for EVERY string reachable inside ``value``.

    Exhaustive by construction rather than by an allowlist of field names
    (audit 2026-07-26). Covered: strings at any depth, dict KEYS as well as
    values, list/tuple/set/frozenset elements, bytes (decoded), and a non-dict
    top-level payload such as a bare JSON string or list. Anything whose type
    cannot be read raises _Unscannable so the caller refuses it.

    Iterative (explicit stack) so a hostile nesting depth cannot raise
    RecursionError inside the privacy gate, and `seen` keeps a self-referential
    or heavily shared structure from looping or blowing up exponentially --
    every container is still walked once, so nothing goes unscanned.
    """
    stack = [(root, value, 0)]
    seen = set()
    while stack:
        path, node, depth = stack.pop()
        if depth > MAX_SCAN_DEPTH:
            raise _Unscannable(path or _PAYLOAD_ROOT)
        if isinstance(node, str):
            yield path or _PAYLOAD_ROOT, node
        elif isinstance(node, (bytes, bytearray)):
            yield path or _PAYLOAD_ROOT, node.decode("utf-8", "replace")
        elif isinstance(node, _SAFE_SCALARS):
            continue
        elif isinstance(node, dict):
            if id(node) in seen:
                continue
            seen.add(id(node))
            for key, sub in node.items():
                if isinstance(key, str):
                    kpath = _child_path(path, key)
                    yield kpath, key
                    # 2026-07-26 audit: a credential SPLIT across a key and its
                    # value defeats per-string scanning -- key "sk-ant-api03"
                    # plus the remaining 40 chars as the value each match no
                    # pattern alone, so the pair walked straight in. Scan the
                    # joined pair too: bare (prefix tokens reassemble across
                    # the boundary) and with "=" (the keyword catch-all and
                    # AccountKey patterns require their separator, and
                    # {"api_key": "..."} is the JSON spelling of "api_key=...").
                    # Extra yields only ADD ways to refuse; nothing that was
                    # scanned before is skipped because of them.
                    #
                    # The "=" spelling is GATED on the value looking like a
                    # credential, because the keyword catch-all only needs six
                    # non-space characters after the separator: ungated, it
                    # refused {"auth_token": "rotate quarterly per runbook"}
                    # and {"client_secret": "REDACTED"} -- ordinary notes a
                    # user is entitled to save (judge round 2026-07-26). A
                    # value with whitespace or no entropy cannot BE the secret
                    # the pattern is looking for.
                    if isinstance(sub, str):
                        joined = sub
                    elif isinstance(sub, (bytes, bytearray)):
                        joined = sub.decode("utf-8", "replace")
                    else:
                        joined = None
                    if joined is not None:
                        yield kpath, key + joined
                        if _could_be_credential(joined):
                            yield kpath, key + "=" + joined
                elif isinstance(key, _SAFE_SCALARS):
                    kpath = _child_path(path, repr(key))
                else:
                    raise _Unscannable(_child_path(path, "<key>"))
                stack.append((kpath, sub, depth + 1))
        elif isinstance(node, (list, tuple, set, frozenset)):
            if id(node) in seen:
                continue
            seen.add(id(node))
            for i, sub in enumerate(node):
                stack.append(("%s[%d]" % (path or _PAYLOAD_ROOT, i), sub, depth + 1))
        else:
            raise _Unscannable(path or _PAYLOAD_ROOT)


def _iter_scannable(value):
    """Yield every string reachable inside a value (kept for callers/back-compat)."""
    for _path, chunk in _iter_scannable_items(value):
        yield chunk


def record_secret_hit(record) -> "str | None":
    """Return the offending top-level field name when a payload holds a secret.

    The 2026-07-25 version scanned a fixed SCANNED_FIELDS allowlist and
    early-returned None for a non-dict payload, so a credential in any other
    key -- or in a bare JSON string -- was written through unscanned. It now
    scans the whole payload (see _iter_scannable_items) and fails closed on a
    value it cannot read.

    Returns the TOP-LEVEL name so long-standing callers keep getting a field
    name; use record_secret_path() for the full dotted path to the offender.
    """
    path = record_secret_path(record)
    if path is None:
        return None
    head = re.split(r"[.\[]", path, maxsplit=1)[0]
    return head or path


def record_secret_path(record) -> "str | None":
    """Like record_secret_hit(), but returns the full path to the offender.

    e.g. "metadata.author.note" or "tags[2]" -- what a human needs in order to
    know WHICH value to redact, which a bare field name cannot express once the
    scan goes all the way down.
    """
    try:
        for path, chunk in _iter_scannable_items(record):
            if _looks_like_secret(chunk):
                return path
    except _Unscannable as exc:
        return exc.path
    return None


def gate_record(record: dict, *, where: str) -> dict:
    """THE privacy gate. Every brain write path must call this.

    The 2026-07-25 audit found `save-chat` scanned for secrets while `export`,
    `import --into`, and scripts/brain_append.py did not -- so the refusal was
    trivially bypassed by using a different verb. One gate, all writers.
    """
    field = record_secret_path(record)
    if field:
        sys.exit(
            f"refuse: {where} record field '{field}' looks like it contains a "
            "secret. Redact it and retry; the shared brain syncs to the central "
            "brain and must never carry credentials."
        )
    return record


def gate_records(records, *, where: str):
    """Gate a batch, reporting the index of the first offender."""
    for i, record in enumerate(records):
        field = record_secret_path(record)
        if field:
            # a non-dict record has no .get; the gate must still report it
            # rather than dying with an AttributeError inside the refusal path.
            rid = record.get("id", f"#{i}") if isinstance(record, dict) else f"#{i}"
            sys.exit(
                f"refuse: {where} record {rid} field '{field}' looks like it "
                "contains a secret. Redact it and retry."
            )
    return records


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
    if not isinstance(recs, list):
        # a bare JSON scalar (`42`, `"note"`) is iterable-or-not by accident;
        # say so instead of a TypeError traceback (2026-07-26).
        sys.exit(f"refuse: '{path}' must hold a list of lesson objects or a "
                 f"mapping with a 'records' key, got {type(recs).__name__}")
    out = []
    for i, r in enumerate(recs):
        # 2026-07-26: a record that is valid JSON but not an object (a bare
        # string, number or list) has no .get(), so `export --from` died with a
        # raw AttributeError traceback. Refuse by index rather than skipping:
        # a malformed line is the user's data, and silently dropping it would
        # report success for an export that lost a lesson.
        if not isinstance(r, dict):
            sys.exit(f"refuse: record #{i} in '{path}' is a "
                     f"{type(r).__name__}, not a JSON object; every lesson "
                     "must be a JSON object like "
                     '{"id": ..., "type": "lesson", "text": ...}')
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
    # THE gate. export used to append with no secret scan at all, so a record
    # save-chat refuses could be smuggled in via `export --from` (audit 07-25).
    gate_records(lessons, where="export")
    have = _existing_ids(BRAIN_FILE)
    added = 0
    with open(BRAIN_FILE, "a") as f:
        _heal_truncated_tail(f, BRAIN_FILE)
        for l in lessons:
            if not l.get("id") or l["id"] in have:
                continue
            f.write(json.dumps(l) + "\n")
            have.add(l["id"])
            added += 1
    print(f"export: {added} new lesson(s) appended to {os.path.relpath(BRAIN_FILE, ROOT)}")
    return 0


def cmd_import(args):
    # same containment gate as export/save-chat: refuse a brain file that
    # resolves outside the project (independent review finding, 2026-07-17)
    lessons = _read_jsonl(_safe_path(BRAIN_FILE))
    if args.into:
        # `--into` EXPORTS brain contents to another file, so it is a write path
        # and must clear the same gate. Printing to stdout below is not a write.
        gate_records(lessons, where="import --into")
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
        "approved": args.mode == "summary",
    }

    # THE gate. save-chat had its own text-only check, so a secret pasted into
    # --tag or --source reached the brain untouched even after export/import
    # were gated (cross-check finding, 2026-07-25). Gate the assembled RECORD,
    # not just the chat text.
    gate_record(record, where="save-chat")

    have = _existing_ids(BRAIN_FILE)
    if rid in have:
        print(f"save-chat: kept existing {rid}")
        return 0
    with open(BRAIN_FILE, "a", encoding="utf-8") as f:
        _heal_truncated_tail(f, BRAIN_FILE)
        f.write(json.dumps(record, sort_keys=True) + "\n")
    print(f"save-chat: appended {rid} to {os.path.relpath(BRAIN_FILE, ROOT)}")
    return 0


def _selftest():
    global BRAIN_FILE
    syn = {"id": "selftest-%d" % int(time.time()),
           "ts": _now(), "source": "codex",
           "type": "lesson", "text": "round-trip self-test lesson", "tags": ["selftest"]}
    tmp = os.path.join(HERE, ".selftest-from.jsonl")
    with open(tmp, "w") as f:
        f.write(json.dumps(syn) + "\n")
    # 2026-07-25 audit: this used to run cmd_export/cmd_save_chat/cmd_import
    # against the module-level BRAIN_FILE with no override, so --selftest
    # appended synthetic "selftest-*" records into the real, live
    # shared-brain.jsonl every time it ran. Swap BRAIN_FILE to a scratch
    # file inside HERE (must stay inside ROOT for _safe_path) for the
    # duration of the test, same isolation central_brain.py's selftest
    # gets via tempfile.TemporaryDirectory.
    real_brain_file = BRAIN_FILE
    BRAIN_FILE = os.path.join(HERE, ".selftest-brain.jsonl")
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
        BRAIN_FILE = real_brain_file
        if os.path.exists(tmp):
            os.remove(tmp)
        scratch = os.path.join(HERE, ".selftest-brain.jsonl")
        if os.path.exists(scratch):
            os.remove(scratch)


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
