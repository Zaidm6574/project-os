#!/usr/bin/env python3
"""OSVec — local vector memory for Project OS ("have we seen this before?").

Two embedders behind one interface (cutover 2026-07-02, per 18-project-pipeline milestone):
  neural  — nomic-embed-text via local Ollama HTTP API (still zero pip deps, $0, offline)
  lexical — deterministic word + char-trigram hashing (the v1 fallback, no model needed)

Selection: OSVEC_EMBEDDER=auto|neural|lexical (default auto: neural if Ollama answers,
else lexical). The index records which embedder built it; query() always embeds the
query the same way, and refuses to mix embedders rather than return garbage cosines.

Usage:
  python3 memory/mneme_adapter.py build
  python3 memory/mneme_adapter.py query "your text here" [k]
  python3 memory/mneme_adapter.py stats
"""
import os, re, json, glob, sys, math, hashlib, tempfile, fcntl
import urllib.request
from contextlib import contextmanager

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # project-os/
HOME = os.path.expanduser("~")
INDEX = os.environ.get("MNEME_INDEX", os.path.join(ROOT, "memory", "mneme_index.json"))
# A full project resolves to its project-local brain.  Standalone copies of this
# adapter (the documented lexical fallback) have no scripts/ directory, so keep
# the legacy environment-derived location available rather than failing at
# import time.  Reads still fail loudly if that selected source is corrupt.
SCRIPTS = os.path.join(ROOT, "scripts")
if os.path.isfile(os.path.join(SCRIPTS, "brain_paths.py")):
    if SCRIPTS not in sys.path:
        sys.path.insert(0, SCRIPTS)
    import brain_paths
    SHARED_BRAIN = str(brain_paths.resolve_shared_brain(ROOT))
else:
    SHARED_BRAIN = os.environ.get(
        "PROJECT_OS_SHARED_BRAIN",
        os.path.join(HOME, ".project-os", "central-brain", "shared-brain.jsonl"))
DIM = 256
OLLAMA_URL = os.environ.get("MNEME_OLLAMA_URL") or os.environ.get("OSVEC_OLLAMA_URL", "http://127.0.0.1:11434")
NEURAL_MODEL = os.environ.get("MNEME_NEURAL_MODEL") or os.environ.get("OSVEC_NEURAL_MODEL", "nomic-embed-text")
EMBEDDER_PREF = os.environ.get("MNEME_EMBEDDER") or os.environ.get("OSVEC_EMBEDDER", "auto")  # auto | neural | lexical

# The MNEME_* names are canonical; OSVEC_* aliases preserve compatibility with
# the older adapter. Keep network work bounded so a dead local Ollama cannot
# hold a build forever, and so an accidental huge batch does not exhaust RAM.
OLLAMA_TIMEOUT_DEFAULT = 120.0
OLLAMA_TIMEOUT_MIN = 0.1
OLLAMA_TIMEOUT_MAX = 600.0
OLLAMA_BATCH_SIZE_DEFAULT = 64
OLLAMA_BATCH_SIZE_MIN = 1
OLLAMA_BATCH_SIZE_MAX = 256
VALID_EMBEDDER_PREFS = frozenset(("auto", "neural", "lexical"))


def _configured_preference():
    value = str(EMBEDDER_PREF or "").strip().lower()
    if value not in VALID_EMBEDDER_PREFS:
        raise ValueError(
            "MNEME_EMBEDDER/OSVEC_EMBEDDER must be one of auto, neural, lexical; "
            "got %r" % value
        )
    return value


def _bounded_float(primary, alias, default, minimum, maximum):
    raw = os.environ.get(primary) or os.environ.get(alias)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ValueError("%s/%s must be a number" % (primary, alias)) from None
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(
            "%s/%s must be between %s and %s seconds"
            % (primary, alias, minimum, maximum)
        )
    return value


def _bounded_int(primary, alias, default, minimum, maximum):
    raw = os.environ.get(primary) or os.environ.get(alias)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError("%s/%s must be an integer" % (primary, alias)) from None
    if not minimum <= value <= maximum:
        raise ValueError(
            "%s/%s must be between %s and %s"
            % (primary, alias, minimum, maximum)
        )
    return value


def ollama_timeout():
    return _bounded_float(
        "MNEME_OLLAMA_TIMEOUT", "OSVEC_OLLAMA_TIMEOUT",
        OLLAMA_TIMEOUT_DEFAULT, OLLAMA_TIMEOUT_MIN, OLLAMA_TIMEOUT_MAX)


def ollama_batch_size():
    return _bounded_int(
        "MNEME_OLLAMA_BATCH_SIZE", "OSVEC_OLLAMA_BATCH_SIZE",
        OLLAMA_BATCH_SIZE_DEFAULT, OLLAMA_BATCH_SIZE_MIN,
        OLLAMA_BATCH_SIZE_MAX)


def _tokens(text):
    text = (text or "").lower()
    words = re.findall(r"[a-z0-9]+", text)
    grams = []
    for w in words:
        p = f" {w} "
        grams += [p[i:i + 3] for i in range(len(p) - 2)]
    return words + grams


def embed(text):
    vec = [0.0] * DIM
    for tok in _tokens(text):
        h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
        idx = h % DIM
        sign = 1.0 if (h >> 8) & 1 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _l2(vec):
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def embed_neural_batch(texts):
    """Embed via local Ollama /api/embed (batched). Raises on any failure —
    callers decide whether to fall back to lexical."""
    out = []
    batch_size = ollama_batch_size()
    timeout = ollama_timeout()
    for i in range(0, len(texts), batch_size):
        chunk = texts[i:i + batch_size]
        req = urllib.request.Request(
            OLLAMA_URL + "/api/embed",
            data=json.dumps({"model": NEURAL_MODEL, "input": chunk}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.load(r)
        embs = resp.get("embeddings")
        if not embs or len(embs) != len(chunk):
            raise RuntimeError(f"bad /api/embed response for {NEURAL_MODEL}")
        out += [_l2(e) for e in embs]
    return out


def neural_available():
    try:
        embed_neural_batch(["ping"])
        return True
    except ValueError:
        # Configuration errors are operator errors, not an unavailable server;
        # auto mode must not silently downgrade a misspelled setting.
        raise
    except Exception:
        return False


def pick_embedder():
    preference = _configured_preference()
    if preference == "lexical":
        return "lexical-hash-v1"
    name = "neural-" + NEURAL_MODEL
    if preference == "neural":
        if not neural_available():
            sys.exit(f"[mneme] MNEME_EMBEDDER=neural but Ollama/{NEURAL_MODEL} "
                     f"unavailable at {OLLAMA_URL} (try: ollama pull {NEURAL_MODEL})")
        return name
    return name if neural_available() else "lexical-hash-v1"  # auto


def cosine(a, b):
    return sum(x * y for x, y in zip(a, b))


def read(p):
    """Read a source file. Absent is fine; DAMAGED OR UNREADABLE IS NOT.

    This was `except Exception: return ""`, which conflated "there is no such
    file" with "the brain file is corrupt / not readable" (audit 2026-07-27).
    A single non-UTF-8 byte anywhere in shared-brain.jsonl — brain_append,
    brain_archive and harvest all write with ensure_ascii=False, so a torn
    write can leave a partial multi-byte character — made _gather() see an
    EMPTY document. `build` then published a 0-vector index over a good one,
    printed "built ... 0 vectors" and exited 0 with no stderr, and
    brain_append.py / brain_scale.py both reported that total loss of
    semantic recall as success. chmod 000 on the same file did likewise.

    Fail loud on a damaged brain file, like every sibling reader already does:
    addons/full-engine/memory/brain_fts_mirror.py raises on these same bytes
    (test_brain_fts_mirror.py::test_malformed_jsonl_fails_loudly_not_silently)
    and brain.py's export aborts non-zero on a corrupt source line. Both
    callers below guard with os.path.isfile()/glob, so only a genuine race
    reaches the absent branch — and brain_append.py:163-167 already has the
    "appended, but reindex FAILED" branch for the raise.
    """
    try:
        with open(p, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def _brain_text(o):
    """Entries store their content under different keys depending on writer."""
    for k in ("text", "lesson", "content", "note", "summary"):
        v = o.get(k)
        if isinstance(v, str) and v.strip():
            return v
    return ""


def _archive_path(brain):
    """Archive sibling of the brain file; only the final path component changes."""
    directory, name = os.path.split(brain)
    if name.endswith(".jsonl"):
        name = name[:-len(".jsonl")] + "-archive.jsonl"
    else:
        name = name + "-archive.jsonl"
    return os.path.join(directory, name)


_PLACEHOLDER_TOKENS = frozenset({"tbd", "n/a", "na", "-", "--", "---", "..", "..."})


def _is_placeholder(text):
    text = re.sub(r"<!--.*?-->", " ", text or "", flags=re.S)
    tokens = []
    for line in text.splitlines():
        line = line.strip()
        if re.match(r"#{1,6}\s", line):
            continue
        line = re.sub(r"^(?:[-*+]|\d+[.)])\s+", "", line).replace("|", " ")
        tokens.extend(line.split())
    return not any(token.lower() not in _PLACEHOLDER_TOKENS
                   and not re.fullmatch(r"[-–—_.:]+", token)
                   for token in tokens)


def _heading_slug(heading):
    return re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-") or "section"


def _gather_blackboard(items):
    for path in sorted(glob.glob(os.path.join(ROOT, "blackboard", "*.md"))):
        heading, body, seen = None, [], set()

        def add_section():
            if heading is None:
                return
            text = "\n".join(body)
            if _is_placeholder(text):
                return
            base = _heading_slug(heading)
            slug, number = base, 1
            while slug in seen:
                number += 1
                slug = f"{base}-{number}"
            seen.add(slug)
            items.append((f"blackboard/{os.path.basename(path)}#{slug}",
                          "blackboard", (heading + "\n" + text.strip())[:600]))

        for line in read(path).splitlines():
            if line.startswith("## "):
                add_section()
                heading, body = line[3:].strip(), []
            elif heading is not None:
                body.append(line)
        add_section()


def _gather():
    """Collect (id, source, text) tuples from lessons + run goals.

    Sources are exactly the four in _scanned_sources(): the shared brain, its
    archive tier, each run's 00-project-goal.md, and meaningful H2 sections
    from blackboard markdown. Nothing in memory/. Keep both this list and
    memory/README.md's mneme bullet honest;
    tests/test_docs_graph_mermaid_20260727.py pins the README against
    _scanned_sources() at runtime.

    The archive file is indexed too: archiving an entry moves it out of the
    active brain (smaller, sharper flat file) without losing semantic recall.
    """
    items = []
    skipped = []
    # shared-brain lessons: active file + archive tier
    for path, source in ((SHARED_BRAIN, "lesson"),
                         (_archive_path(SHARED_BRAIN), "lesson-archived")):
        if os.path.isfile(path):
            for line_no, line in enumerate(read(path).splitlines(), start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except Exception as exc:
                    skipped.append((path, line_no, exc))
                    continue
                if not isinstance(o, dict):
                    # `123` and `["a"]` are valid JSON but have no .get(); this
                    # used to escape the try above and abort build() with a raw
                    # AttributeError traceback (audit 2026-07-27).
                    skipped.append((path, line_no, "not a JSON object"))
                    continue
                items.append((o.get("id", f"lesson-{len(items)}"), source, _brain_text(o)))
    # Dropping a brain line stays non-fatal — a torn tail must not brick every
    # future reindex, and brain_append.py deliberately leaves such a fragment
    # ALONE — but it must never be SILENT. brain_append.py:132-146 names this
    # exact skip as one of the reasons such a loss is "invisible forever".
    for pth, line_no, why in skipped[:10]:
        print(f"[mneme] WARNING: skipped unparseable line {line_no} of {pth}: {why}",
              file=sys.stderr)
    if len(skipped) > 10:
        print(f"[mneme] WARNING: ... and {len(skipped) - 10} more unparseable line(s)",
              file=sys.stderr)
    if skipped:
        print(f"[mneme] WARNING: skipped {len(skipped)} unreadable brain line(s); "
              "they are NOT in the index; "
              f"repair the source and rebuild", file=sys.stderr)
    # run goals
    for goal in glob.glob(os.path.join(ROOT, "runs", "*", "00-project-goal.md")):
        slug = os.path.basename(os.path.dirname(goal))
        txt = read(goal)
        m = re.search(r"## Canonical Goal.*?\n(.+?)(?:\n##|\Z)", txt, re.S)
        body = (m.group(1) if m else txt)[:600]
        items.append((f"{slug}:goal", "goal", body))
    _gather_blackboard(items)
    return items


def _scanned_sources():
    """Every path _gather() reads, for reporting. Keep in sync with _gather()."""
    return [SHARED_BRAIN,
            _archive_path(SHARED_BRAIN),
            os.path.join(ROOT, "runs", "*", "00-project-goal.md"),
            os.path.join(ROOT, "blackboard", "*.md")]


def _empty_index_note():
    """Why an empty index is empty, naming where we looked.

    2026-07-27: three independent cold-clone reviewers ran the README's
    "Optional: smarter memory search" block and got `built ... 0 vectors`
    then `[]`, both exit 0, no stderr. That is a success-shaped report of
    "nothing happened": indistinguishable from a broken install, a wrong
    working directory, or a brain bound somewhere the reader did not expect.

    Deliberately NOT an error. A fresh install genuinely has no lessons yet,
    and an empty corpus SHOULD produce an empty index -- failing here would
    break the documented first run for every new user. The defect was the
    silence, so the remedy is to name the scanned paths and the next action,
    and keep the exit code at 0.
    """
    lines = ["[mneme] WARNING: built zero vectors -- the index is EMPTY (this is not an error).",
             "[mneme] scanned:"]
    for p in _scanned_sources():
        exists = "" if ("*" in p or os.path.exists(p)) else "  (does not exist)"
        lines.append("[mneme]   %s%s" % (p, exists))
    lines.append("[mneme] add an approved lesson with "
                 "`python3 scripts/brain_append.py`, or point "
                 "PROJECT_OS_SHARED_BRAIN at an existing brain, then rebuild.")
    return "\n".join(lines)


USAGE = """mneme_adapter - local semantic recall over the shared brain and run goals.

Usage:
  python3 memory/mneme_adapter.py build            rebuild the index from all sources
  python3 memory/mneme_adapter.py query "<text>" [k]   top-k matches as JSON on stdout
  python3 memory/mneme_adapter.py stats            index size, dimension, embedder

Environment:
  MNEME_INDEX                 index path (default: memory/mneme_index.json)
  PROJECT_OS_SHARED_BRAIN     brain path to index (absolute; project-local by default)
  MNEME_EMBEDDER              auto|neural|lexical (default: auto)
  OSVEC_EMBEDDER               legacy alias for MNEME_EMBEDDER
  MNEME_OLLAMA_URL             Ollama base URL (default: http://127.0.0.1:11434)
  OSVEC_OLLAMA_URL             legacy alias for MNEME_OLLAMA_URL
  MNEME_NEURAL_MODEL           Ollama model (default: nomic-embed-text)
  OSVEC_NEURAL_MODEL            legacy alias for MNEME_NEURAL_MODEL
  MNEME_OLLAMA_TIMEOUT         request timeout in seconds, 0.1..600 (default: 120)
  OSVEC_OLLAMA_TIMEOUT          legacy alias for MNEME_OLLAMA_TIMEOUT
  MNEME_OLLAMA_BATCH_SIZE      texts per request, 1..256 (default: 64)
  OSVEC_OLLAMA_BATCH_SIZE       legacy alias for MNEME_OLLAMA_BATCH_SIZE

An empty corpus produces an empty index and exits 0 -- that is correct on a
fresh install, and `build` says so explicitly rather than reporting silence."""


@contextmanager
def _build_lock():
    directory = os.path.dirname(INDEX) or "."
    os.makedirs(directory, exist_ok=True)
    with open(INDEX + ".build.lock", "a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def build():
    with _build_lock():
        return _build_locked()


def _build_locked():
    embedder = pick_embedder()
    gathered = [(i, s, t) for i, s, t in _gather() if (t or "").strip()]
    if embedder.startswith("neural-"):
        vecs = embed_neural_batch([t for _, _, t in gathered])
    else:
        vecs = [embed(t) for _, _, t in gathered]
    if len(vecs) != len(gathered):
        raise ValueError(
            "invalid embedding count: received %d vectors for %d source entries"
            % (len(vecs), len(gathered))
        )
    entries = [{"id": _id, "source": source, "text": text[:240], "vec": v}
               for (_id, source, text), v in zip(gathered, vecs)]
    dim = len(vecs[0]) if vecs else DIM
    index = {"dim": dim, "embedder": embedder, "count": len(entries), "entries": entries}
    _validate_index(index)
    _publish(index)
    return index


def _publish(index):
    """Swap the finished index in with a single atomic rename.

    `open(INDEX, "w")` truncated the live index BEFORE the new JSON was
    flushed, so a build that died partway (full disk, SIGKILL) — or a reader
    arriving mid-write — saw a torn or empty file where a good index had been.
    Same sibling-temp + os.replace() shape as osvec_adapter.save() and
    brain_archive.py: a reader sees either the previous complete index or the
    new one, never a half-written one.

    Resolve symlinks first, like cost_actuals._atomic_write_preserving_mode:
    os.replace() does not follow a link, so renaming onto the link PATH would
    delete the link and drop a regular file in its place (and fail with EXDEV
    if the link crosses a filesystem). mkstemp's 0600 is left as-is: the index
    holds text[:240] of the CROSS-PROJECT brain (see
    test_open_findings_opus_20260726.py), so owner-only is the right default.
    """
    real = os.path.realpath(INDEX)
    directory = os.path.dirname(real) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".mneme_index.", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(index, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, real)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _validate_index(index):
    if not isinstance(index, dict):
        raise ValueError("invalid Mneme index: expected JSON object")
    entries = index.get("entries")
    count = index.get("count")
    dim = index.get("dim")
    embedder = index.get("embedder")
    if not isinstance(entries, list):
        raise ValueError("invalid Mneme index: entries must be a list")
    if not isinstance(count, int) or isinstance(count, bool) or count != len(entries):
        raise ValueError("invalid Mneme index: count does not match entries")
    if not isinstance(dim, int) or isinstance(dim, bool) or dim <= 0:
        raise ValueError("invalid Mneme index: dim must be a positive integer")
    if not isinstance(embedder, str) or not embedder:
        raise ValueError("invalid Mneme index: embedder must be a nonempty string")
    if embedder == "lexical-hash-v1" and dim != DIM:
        raise ValueError("invalid Mneme lexical index dimension")
    if embedder != "lexical-hash-v1" and (not embedder.startswith("neural-") or embedder == "neural-"):
        raise ValueError("invalid Mneme index: unsupported embedder")
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("vec"), list):
            raise ValueError("invalid Mneme index: entries must carry vectors")
        vector = entry["vec"]
        if len(vector) != dim:
            raise ValueError("invalid Mneme index: vector dimension does not match index")
        if not all(isinstance(value, (int, float)) and not isinstance(value, bool)
                   and math.isfinite(value) for value in vector):
            raise ValueError("invalid Mneme index: vector contains a non-finite value")
    return index


def load():
    try:
        with open(INDEX, encoding="utf-8") as f:
            return _validate_index(json.load(f))
    except FileNotFoundError:
        return None


def query(text, k=5):
    idx = load() or build()
    emb = idx.get("embedder", "lexical-hash-v1")
    if emb.startswith("neural-"):
        try:
            q = embed_neural_batch([text])[0]
        except Exception as e:
            # mixing embedders would return garbage cosines — refuse instead
            sys.exit(f"[mneme] index was built with {emb} but Ollama is unreachable ({e}).\n"
                     f"Start Ollama, or rebuild lexical: OSVEC_EMBEDDER=lexical "
                     f"python3 memory/mneme_adapter.py build")
    else:
        q = embed(text)
    scored = [(cosine(q, e["vec"]), e) for e in idx["entries"]]
    scored.sort(key=lambda x: -x[0])
    return [{"score": round(s, 3), "id": e["id"], "source": e["source"], "text": e["text"]}
            for s, e in scored[:k]]


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    # `--help` fell through every branch below and exited 0 having printed
    # nothing, so the one command a stranger tries first looked like a no-op
    # (cold-clone reviewer, 2026-07-27). An unknown subcommand did the same,
    # which made a typo indistinguishable from a successful run.
    if cmd in ("-h", "--help", "help"):
        print(USAGE)
        sys.exit(0)
    if cmd not in ("build", "stats", "query"):
        print("[mneme] unknown subcommand %r\n" % cmd, file=sys.stderr)
        print(USAGE, file=sys.stderr)
        sys.exit(2)
    if cmd == "build":
        try:
            i = build()
        except (OSError, UnicodeDecodeError, ValueError) as e:
            # Non-zero and legible, not a raw traceback: brain_append.py:167
            # surfaces only the FIRST 300 stderr chars, and a traceback puts
            # the actual cause LAST, so the operator would see frames instead
            # of the reason. Narrow on purpose — a bug in this file must still
            # crash loudly rather than be reported as a source problem.
            sys.exit(f"[mneme] REFUSED: build configuration or source is invalid — {e}\n"
                     f"[mneme] {INDEX} was left untouched; repair the setting/source, then rebuild")
        print(f"[mneme] built {INDEX} — {i['count']} vectors (dim {i['dim']}, {i['embedder']})")
        if not i["count"]:
            print(_empty_index_note(), file=sys.stderr)
    elif cmd == "stats":
        try:
            i = load()
        except ValueError as e:
            sys.exit(f"[mneme] REFUSED: {e}")
        print(json.dumps({"count": i["count"], "dim": i["dim"], "embedder": i["embedder"]} if i else {"count": 0}, indent=2))
    elif cmd == "query":
        text = sys.argv[2] if len(sys.argv) > 2 else ""
        k = int(sys.argv[3]) if len(sys.argv) > 3 else 5
        try:
            hits = query(text, k)
        except ValueError as e:
            sys.exit(f"[mneme] REFUSED: {e}")
        # JSON stays on STDOUT and the explanation goes to STDERR: README shows
        # this as a JSON-producing command, so prose on stdout would break any
        # caller piping it into jq.
        print(json.dumps(hits, indent=2))
        if not hits and not (load() or {}).get("count"):
            print(_empty_index_note(), file=sys.stderr)
