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
import os, re, json, glob, sys, math, hashlib, tempfile
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # project-os/
HOME = os.path.expanduser("~")
INDEX = os.environ.get("MNEME_INDEX", os.path.join(ROOT, "memory", "mneme_index.json"))
SHARED_BRAIN = os.environ.get(
    "PROJECT_OS_SHARED_BRAIN",
    os.path.join(HOME, ".project-os", "central-brain", "shared-brain.jsonl"))
DIM = 256
OLLAMA_URL = os.environ.get("MNEME_OLLAMA_URL") or os.environ.get("OSVEC_OLLAMA_URL", "http://127.0.0.1:11434")
NEURAL_MODEL = os.environ.get("MNEME_NEURAL_MODEL") or os.environ.get("OSVEC_NEURAL_MODEL", "nomic-embed-text")
EMBEDDER_PREF = os.environ.get("MNEME_EMBEDDER") or os.environ.get("OSVEC_EMBEDDER", "auto")  # auto | neural | lexical


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
    for i in range(0, len(texts), 64):
        chunk = texts[i:i + 64]
        req = urllib.request.Request(
            OLLAMA_URL + "/api/embed",
            data=json.dumps({"model": NEURAL_MODEL, "input": chunk}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
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
    except Exception:
        return False


def pick_embedder():
    if EMBEDDER_PREF == "lexical":
        return "lexical-hash-v1"
    name = "neural-" + NEURAL_MODEL
    if EMBEDDER_PREF == "neural":
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


def _gather():
    """Collect (id, source, text) tuples from lessons + run goals/decisions.

    The archive file is indexed too: archiving an entry moves it out of the
    active brain (smaller, sharper flat file) without losing semantic recall.
    """
    items = []
    skipped = []
    # shared-brain lessons: active file + archive tier
    for path, source in ((SHARED_BRAIN, "lesson"),
                         (SHARED_BRAIN.replace(".jsonl", "-archive.jsonl"), "lesson-archived")):
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
        print(f"[mneme] WARNING: {len(skipped)} brain line(s) are NOT in the index; "
              f"repair the source and rebuild", file=sys.stderr)
    # run goals
    for goal in glob.glob(os.path.join(ROOT, "runs", "*", "00-project-goal.md")):
        slug = os.path.basename(os.path.dirname(goal))
        txt = read(goal)
        m = re.search(r"## Canonical Goal.*?\n(.+?)(?:\n##|\Z)", txt, re.S)
        body = (m.group(1) if m else txt)[:600]
        items.append((f"{slug}:goal", "goal", body))
    return items


def _scanned_sources():
    """Every path _gather() reads, for reporting. Keep in sync with _gather()."""
    return [SHARED_BRAIN,
            SHARED_BRAIN.replace(".jsonl", "-archive.jsonl"),
            os.path.join(ROOT, "runs", "*", "00-project-goal.md")]


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
    lines = ["[mneme] nothing to index -- the index is EMPTY (this is not an error).",
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
  PROJECT_OS_SHARED_BRAIN     brain path to index
  OSVEC_EMBEDDER=lexical      force the dependency-free embedder instead of Ollama

An empty corpus produces an empty index and exits 0 -- that is correct on a
fresh install, and `build` says so explicitly rather than reporting silence."""


def build():
    embedder = pick_embedder()
    gathered = [(i, s, t) for i, s, t in _gather() if (t or "").strip()]
    if embedder.startswith("neural-"):
        vecs = embed_neural_batch([t for _, _, t in gathered])
    else:
        vecs = [embed(t) for _, _, t in gathered]
    entries = [{"id": _id, "source": source, "text": text[:240], "vec": v}
               for (_id, source, text), v in zip(gathered, vecs)]
    dim = len(vecs[0]) if vecs else DIM
    index = {"dim": dim, "embedder": embedder, "count": len(entries), "entries": entries}
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


def load():
    try:
        with open(INDEX, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
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
        except (OSError, UnicodeDecodeError) as e:
            # Non-zero and legible, not a raw traceback: brain_append.py:167
            # surfaces only the FIRST 300 stderr chars, and a traceback puts
            # the actual cause LAST, so the operator would see frames instead
            # of the reason. Narrow on purpose — a bug in this file must still
            # crash loudly rather than be reported as a source problem.
            sys.exit(f"[mneme] REFUSED: cannot read a brain source — {e}\n"
                     f"[mneme] {INDEX} was left untouched; repair the source, then rebuild")
        print(f"[mneme] built {INDEX} — {i['count']} vectors (dim {i['dim']}, {i['embedder']})")
        if not i["count"]:
            print(_empty_index_note(), file=sys.stderr)
    elif cmd == "stats":
        i = load()
        print(json.dumps({"count": i["count"], "dim": i["dim"], "embedder": i["embedder"]} if i else {"count": 0}, indent=2))
    elif cmd == "query":
        text = sys.argv[2] if len(sys.argv) > 2 else ""
        k = int(sys.argv[3]) if len(sys.argv) > 3 else 5
        hits = query(text, k)
        # JSON stays on STDOUT and the explanation goes to STDERR: README shows
        # this as a JSON-producing command, so prose on stdout would break any
        # caller piping it into jq.
        print(json.dumps(hits, indent=2))
        if not hits and not (load() or {}).get("count"):
            print(_empty_index_note(), file=sys.stderr)
