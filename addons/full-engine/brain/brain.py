#!/usr/bin/env python3
"""
Project OS - tool-to-tool shared-brain bridge.

The mission of Project OS is a portable shared brain across AI tools. The OSVec
side-car stores durable lessons inside one project, and this bridge provides a
small local exchange file that other tools can read or append to.

It is deliberately small and safe:
  * zero network calls,
  * stdlib only,
  * uses the canonical explicitly selected durable store,
  * keeps exchange source/output files inside this project copy.

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
import stat
import sys
import tempfile
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))


def _core_scripts_dir():
    """Locate the owning Project OS scripts directory from an add-on copy."""
    current = HERE
    while True:
        scripts_dir = os.path.join(current, "scripts")
        module = os.path.join(scripts_dir, "brain_paths.py")
        if os.path.isfile(module) and not os.path.islink(module):
            return scripts_dir
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


CORE_SCRIPTS = _core_scripts_dir()
if CORE_SCRIPTS and CORE_SCRIPTS not in sys.path:
    sys.path.insert(0, CORE_SCRIPTS)
if CORE_SCRIPTS:
    import brain_paths
    ROOT = str(brain_paths.find_project_root(HERE))
    BRAIN_FILE = str(brain_paths.resolve_shared_brain(ROOT))
else:
    brain_paths = None
    ROOT = os.path.dirname(HERE)
    BRAIN_FILE = os.path.join(HERE, "shared-brain.jsonl")


def _load_from_scripts(module_name):
    """Load a core shared module when this is a full Project OS layout."""
    current = HERE
    while True:
        scripts = os.path.join(current, "scripts")
        candidate = os.path.join(scripts, module_name + ".py")
        if os.path.isfile(candidate) and not os.path.islink(candidate):
            if scripts not in sys.path:
                sys.path.insert(0, scripts)
            return __import__(module_name)
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


# Full installations share one denylist.  A script-less portable copy cannot
# silently treat records as clean, so it retains a deliberately broad
# compatibility screen until its scripts/ directory is restored.
_secret_patterns = _load_from_scripts("secret_patterns")
if _secret_patterns is not None:
    SECRET_PATTERNS = list(_secret_patterns.SECRET_PATTERNS)
else:
    SECRET_PATTERNS = [
        re.compile(r"(?i)(?:api[_-]?key|secret|password|passwd|token|bearer)\s*[:=]\s*\S{6,}"),
        re.compile(r"(?<![A-Za-z0-9_])[A-Za-z]{2,20}[-_][A-Za-z0-9._-]{16,}"),
        re.compile(r"\b(?:AKIA|ASIA|AC|SK)[A-Za-z0-9]{16,}\b"),
        re.compile(r"(?:eyJ[A-Za-z0-9_-]{10,}\.){2}"),
        re.compile(r"(?:https?://[^\s:@/]+:[^\s@/]+@|https://hooks\.[^\s/]+/[^\s]{20,})", re.I),
        re.compile(r"(?:AccountKey\s*=\s*|-----BEGIN [A-Z ]*PRIVATE KEY-----)", re.I),
        re.compile(r"SG\.[A-Za-z0-9_-]{20,}"),
    ]
bb_lock = _load_from_scripts("bb_lock")

# A value that is nothing but a redaction marker carries no secret by
# construction, so exempting it cannot hide one: the match must consume the
# WHOLE value, and a redaction marker with appended credential text is still
# scanned.
_PLACEHOLDER_VALUE = re.compile(
    r"^[\W_]*(?:redacted|removed|omitted|scrubbed|todo|tbd|changeme|"
    r"placeholder|example|sample|none|null|nil|unset|empty|n/?a|x+|\*+|\.+)"
    r"[\W_]*$", re.I)


def _create_private(path) -> bool:
    """Create `path` as an empty 0600 file, or leave an existing one untouched.

    The shared brain holds lesson text harvested from every project, and every
    record is credential-scanned before it is allowed in, so it must not be
    born at the umask default (0644 on a stock umask 022 account).
    scripts/brain_archive.py already creates this same data with
    os.open(..., O_CREAT|O_EXCL, 0o600) and gives the reasoning in
    _mode_or_private(); this is that pattern, kept byte-identical in every
    module that can bring a brain file into existence -- scripts/brain_append.py,
    scripts/install_full_engine.py, addons/full-engine/brain/brain.py and
    addons/full-engine/brain/central_brain.py. Path.touch(), Path.write_text()
    and open(path, "a") all create at 0666 & ~umask instead, and this repo has
    already shipped a guard applied to one of two installers, so a sync test
    pins these copies together.

    O_EXCL is what makes it safe to call unconditionally: it fails with EEXIST
    when the path already exists -- including when the path is a symlink, even
    a dangling one -- so an operator who deliberately chose 0640 keeps 0640,
    and nothing is ever created through a pre-planted name. Returns True only
    when this call is the one that created the file.
    """
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    os.close(fd)
    return True


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
    """Refuse an outside path or a file sharing its inode with another path."""
    root = os.path.realpath(os.path.abspath(ROOT))
    full = os.path.realpath(os.path.abspath(path))
    if os.path.commonpath([full, root]) != root:
        sys.exit(f"refuse: path '{path}' is outside the project ({ROOT})")
    try:
        info = os.stat(full)
    except FileNotFoundError:
        pass  # An exchange output may legitimately be a new file.
    except OSError as exc:
        sys.exit(f"refuse: cannot inspect project exchange path: {exc}")
    else:
        if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
            sys.exit("refuse: project exchange path must not be hardlinked")
    return full


def _safe_brain_path() -> str:
    """Validate the selected durable store again at the command boundary.

    The canonical resolver may select one external store via an explicit
    environment override or an ignored binding. That selection does not apply
    to exchange input/output files, which still use _safe_path. Re-resolving
    refuses a changed store selection or invalid filesystem components since
    import; it is not a sandbox against an adversarial concurrent process.
    """
    if brain_paths is None:
        return _safe_path(BRAIN_FILE)
    try:
        selected = str(brain_paths.resolve_shared_brain(ROOT))
    except brain_paths.BrainPathError as exc:
        sys.exit(f"refuse: {exc}")
    if os.path.abspath(BRAIN_FILE) != selected:
        sys.exit("refuse: shared-brain selection changed; restart the command")
    return selected


class BrainFormatError(ValueError):
    """The durable brain contains a row that is not a JSON object."""


def _read_jsonl(path, *, strict=False):
    """Read a JSONL file into a list of parsed objects.

    ``strict`` decides what a corrupt line MEANS. The shared brain file may
    legitimately carry a crash-truncated tail (see _heal_truncated_tail), so
    the readers of BRAIN_FILE (_existing_ids, cmd_import) stay lenient and skip
    an unparseable line. A user-supplied ``export --from`` source is different:
    silently dropping one of its lines and then printing "export: N new
    lesson(s) appended" reports SUCCESS for an export that LOST a lesson -- the
    repo's data-loss-looks-like-success signature (audit 2026-07-25/27). That
    caller passes ``strict=True`` so a corrupt line is SURFACED and aborts with
    a non-zero exit instead of vanishing.

    Reads are decoded as UTF-8 -- the encoding every writer here uses -- and
    non-UTF-8 bytes become a clean, surfaced refusal rather than a raw
    UnicodeDecodeError traceback.
    """
    out = []
    if not os.path.exists(path):
        return out
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
            for lineno, raw in enumerate(lines, 1):
                line = raw.strip()
                if not line:
                    continue
                # 2026-07-25 audit: this used to call json.loads with no
                # try/except, so one corrupt line anywhere in the file (a crash
                # mid-write, a hand edit) raised and took the whole read down.
                # central_brain.py's read_jsonl already skips bad lines instead.
                # 2026-07-27: skipping is right for the brain file's healed tail
                # but WRONG for an export source, where a dropped line is a lost
                # lesson reported as success -- strict callers surface it.
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    # An interrupted append leaves one unterminated final
                    # fragment. It is safe to leave that fragment isolated and
                    # append a new newline-delimited record; any complete bad
                    # row is durable corruption and must stop the write.
                    truncated_tail = (
                        lineno == len(lines) and not raw.endswith("\n")
                    )
                    if strict and truncated_tail:
                        continue
                    if strict:
                        raise BrainFormatError(
                            f"{path} line {lineno}: invalid JSON ({exc.msg})"
                        ) from None
                    continue
                if not isinstance(record, dict):
                    if strict:
                        raise BrainFormatError(
                            f"{path} line {lineno}: expected a JSON object, "
                            f"got {type(record).__name__}"
                        )
                    continue
                out.append(record)
    except UnicodeDecodeError as exc:
        sys.exit(
            f"refuse: '{path}' is not valid UTF-8 (byte {exc.start}: "
            f"{exc.reason}); expected a UTF-8 JSON/JSONL file"
        )
    return out


def _existing_ids(path):
    return {r.get("id") for r in _read_jsonl(path, strict=True)}


def _append_new(path, records, agent):
    """Gate, deduplicate, and append while a single lock covers the snapshot."""
    gate_records(records, where=agent)

    def append_locked():
        existing = _existing_ids(path)
        _create_private(path)
        added = 0
        with open(path, "a", encoding="utf-8") as handle:
            _heal_truncated_tail(handle, path)
            for record in records:
                rid = record.get("id")
                if not rid or rid in existing:
                    continue
                handle.write(json.dumps(record, sort_keys=True) + "\n")
                existing.add(rid)
                added += 1
            handle.flush()
            os.fsync(handle.fileno())
        return added

    if bb_lock is None:
        if CORE_SCRIPTS is not None:
            raise RuntimeError("shared-brain locking support is required")
        # A standalone portable copy has no shared installation or sync runtime.
        # Its legacy single-process local-file mode remains supported.
        return append_locked()
    token = bb_lock.acquire(path, agent=agent, wait=10)
    if not token:
        raise RuntimeError("could not lock shared-brain.jsonl")
    try:
        with bb_lock.fenced(path, token):
            return append_locked()
    finally:
        if not bb_lock.release(path, agent=agent, token=token):
            raise RuntimeError("shared-brain append completed, but lock release failed")


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
                    # value defeats per-string scanning -- a token prefix key
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
            "refuse: record looks like it contains a "
            "secret. Redact it and retry; the shared brain syncs to the central "
            "brain and must never carry credentials."
        )
    return record


def gate_records(records, *, where: str):
    """Gate a batch, reporting the index of the first offender."""
    for i, record in enumerate(records):
        field = record_secret_path(record)
        if field:
            # Keys, IDs and paths are untrusted payload data. Use only the
            # batch position; a refusal must not copy a secret into logs.
            sys.exit(
                f"refuse: record #{i} looks like it "
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
    # A current sidecar is one member of OSVec's committed three-file store.
    # Exporting it without its manifest would publish records from a partial or
    # interrupted write as trusted durable lessons.
    if blob.get("schema") == getattr(tv, "SIDECAR_SCHEMA", None):
        manifest = getattr(tv, "MANIFEST_PATH", None)
        if not manifest or not os.path.isfile(manifest):
            raise SystemExit(
                "refuse: osvec sidecar has no matching manifest; repair or "
                "rebuild the OSVec store before export"
            )
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


def _adapter_search_paths():
    """Installed adapter first, source add-on adapter second."""
    candidates = (
        os.path.join(ROOT, "memory"),
        os.path.join(ROOT, "addons", "full-engine", "memory"),
    )
    return tuple(dict.fromkeys(os.path.abspath(path) for path in candidates))


def _lessons_from_file(path):
    full = _safe_path(path)
    # A typo'd --from path used to traceback rather than refuse: a missing
    # `.json` hit FileNotFoundError inside open() (above the list guard below),
    # while a missing `.jsonl` silently read nothing and printed success for an
    # export that never happened. Refuse both, cleanly and non-zero (2026-07-27).
    if not os.path.exists(full):
        sys.exit(f"refuse: --from file '{path}' does not exist")
    # Accept a case-variant extension: FOO.JSONL is still JSONL. The old
    # case-sensitive endswith(".jsonl") routed it to the json.load() branch,
    # which tracebacked with a JSONDecodeError on the first newline between
    # records (2026-07-27).
    if full.lower().endswith(".jsonl"):
        recs = _read_jsonl(full, strict=True)
    else:
        # A non-.jsonl --from used to json.load() with no guard, so a
        # newline-delimited file mislabelled `.json`, a non-UTF-8 file, or any
        # invalid JSON raised a raw traceback. Refuse each cleanly (2026-07-27).
        try:
            with open(full, encoding="utf-8") as f:
                blob = json.load(f)
        except UnicodeDecodeError as exc:
            sys.exit(f"refuse: '{path}' is not valid UTF-8 (byte {exc.start}: "
                     f"{exc.reason}); expected a UTF-8 JSON file")
        except json.JSONDecodeError as exc:
            sys.exit(f"refuse: '{path}' is not valid JSON ({exc.msg} at line "
                     f"{exc.lineno} column {exc.colno}); if it is newline-"
                     "delimited JSON, name it with a .jsonl extension")
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
        if brain_paths is not None:
            try:
                typ = brain_paths.record_type(r)
            except brain_paths.BrainRecordError as exc:
                raise BrainFormatError(f"{full}: {exc}") from None
        else:
            typ = r.get("type") or r.get("kind") or r.get("memory_type")
        if typ == "lesson":
            out.append({
                "id": r.get("id") or r.get("memory_id"),
                "ts": r.get("ts") or r.get("created_at", "") or time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "source": r.get("source", "project-os"),
                "type": "lesson",
                "text": r.get("text", ""),
                "tags": r.get("tags", []) or [],
            })
    return out


def _refuse_idless(lessons, origin):
    """Refuse an export whose lessons cannot be identified. Nothing is written.

    export deduplicates by id, so a lesson with no id (or an id that is an
    empty string) could never be written at all -- it was skipped by the very
    same `continue` that skips an already-present lesson, and the command then
    printed "export: N new lesson(s) appended" and exited 0. A lesson vanished
    and every surface said success (audit 2026-07-27).

    Refusing, rather than appending it id-less, matches the non-object refusal
    in _lessons_from_file() directly above: a malformed record is the user's
    data, and this file is the one place that can still tell them. The check
    runs BEFORE the append handle is opened, so a source with one bad record
    does not half-commit its good ones -- the same "parse everything before
    writing anything" rule scripts/harvest.py::cmd_apply learned on 2026-07-25.
    """
    missing = [i for i, l in enumerate(lessons)
               if not str(l.get("id") or "").strip()]
    if not missing:
        return
    shown = ", ".join("#%d" % i for i in missing[:10])
    more = " +%d more" % (len(missing) - 10) if len(missing) > 10 else ""
    first = str(lessons[missing[0]].get("text", ""))[:60]
    sys.exit(
        f"refuse: {len(missing)} of {len(lessons)} lesson record(s) from "
        f"'{origin}' have no 'id' (0-based lesson {shown}{more}; first begins "
        f'"{first}"). export deduplicates by id, so an id-less lesson would be '
        "dropped in silence. Give each lesson an \"id\" (or \"memory_id\") and "
        "re-run -- nothing was written."
    )


def cmd_export(args):
    brain_file = _safe_brain_path()
    if args.from_file:
        lessons = _lessons_from_file(args.from_file)
    else:
        lessons = _lessons_from_adapter()
        if lessons is None:
            sys.exit("refuse: osvec_adapter not importable; pass --from FILE")
    # THE gate. export used to append with no secret scan at all, so a record
    # save-chat refuses could be smuggled in via `export --from` (audit 07-25).
    gate_records(lessons, where="export")
    _refuse_idless(lessons, args.from_file or "osvec_adapter")
    added = _append_new(brain_file, lessons, "brain-export")
    note = ""
    print(f"export: {added} new lesson(s) appended to "
          f"{os.path.relpath(brain_file, ROOT)}{note}")
    return 0


def cmd_import(args):
    lessons = _read_jsonl(_safe_brain_path())
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
    brain_file = _safe_brain_path()
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

    added = _append_new(brain_file, [record], "brain-save-chat")
    if not added:
        print(f"save-chat: kept existing {rid}")
        return 0
    print(f"save-chat: appended {rid} to {os.path.relpath(brain_file, ROOT)}")
    return 0


def _selftest():
    global ROOT, BRAIN_FILE
    original_root, original_brain_file = ROOT, BRAIN_FILE
    original_lock_dir = bb_lock.LOCK_DIR if bb_lock is not None else None
    with tempfile.TemporaryDirectory(prefix="project-os-brain-selftest-") as temp_root:
        temp_brain_dir = os.path.join(temp_root, "brain")
        os.makedirs(temp_brain_dir)
        temp_source = os.path.join(temp_brain_dir, "source.jsonl")
        ROOT, BRAIN_FILE = temp_root, os.path.join(temp_brain_dir, "shared-brain.jsonl")
        if bb_lock is not None:
            bb_lock.LOCK_DIR = os.path.join(temp_root, "locks")
        original_override = os.environ.pop("PROJECT_OS_SHARED_BRAIN", None)
        try:
            # Keep synthetic IDs free of token-like separators: the durable
            # gate intentionally treats opaque `prefix-<long-token>` values as
            # possible credentials.
            syn = {"id": "selftestexport" + uuid.uuid4().hex,
                   "ts": _now(), "source": "codex", "type": "lesson",
                   "text": "round-trip self-test lesson", "tags": ["selftest"]}
            with open(temp_source, "w", encoding="utf-8") as source_file:
                source_file.write(json.dumps(syn) + "\n")
            cmd_export(argparse.Namespace(from_file=temp_source))
            cmd_save_chat(
                argparse.Namespace(
                    summary="Save chat memories as approved summaries, not raw logs.",
                    summary_file=None,
                    id="selftestchat" + uuid.uuid4().hex,
                    kind="lesson",
                    tag=["selftest", "chat"],
                    source=None,
                    mode="summary",
                    approved=True,
                )
            )
            roundtripped = {r["id"] for r in _read_jsonl(BRAIN_FILE)}
            assert syn["id"] in roundtripped, "export did not persist synthetic lesson"
            cmd_import(argparse.Namespace(into=None))
            print("selftest: OK")
            return 0
        finally:
            if original_override is not None:
                os.environ["PROJECT_OS_SHARED_BRAIN"] = original_override
            else:
                os.environ.pop("PROJECT_OS_SHARED_BRAIN", None)
            ROOT, BRAIN_FILE = original_root, original_brain_file
            if bb_lock is not None:
                bb_lock.LOCK_DIR = original_lock_dir


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
    ps.add_argument("--approved", action="store_true",
                    help="record explicit approval for summary-mode central sync")
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
    try:
        main()
    except BrainFormatError as exc:
        print(f"refuse: {exc}", file=sys.stderr)
        sys.exit(2)
