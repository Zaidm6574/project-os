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
import os, re, json, glob, sys, math, hashlib
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # project-os/
HOME = os.path.expanduser("~")
INDEX = os.path.join(ROOT, "memory", "mneme_index.json")
SHARED_BRAIN = os.path.join(HOME, ".project-os", "central-brain", "shared-brain.jsonl")
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
    try:
        with open(p, encoding="utf-8") as f:
            return f.read()
    except Exception:
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
    # shared-brain lessons: active file + archive tier
    for path, source in ((SHARED_BRAIN, "lesson"),
                         (SHARED_BRAIN.replace(".jsonl", "-archive.jsonl"), "lesson-archived")):
        if os.path.isfile(path):
            for line in read(path).splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                items.append((o.get("id", f"lesson-{len(items)}"), source, _brain_text(o)))
    # run goals
    for goal in glob.glob(os.path.join(ROOT, "runs", "*", "00-project-goal.md")):
        slug = os.path.basename(os.path.dirname(goal))
        txt = read(goal)
        m = re.search(r"## Canonical Goal.*?\n(.+?)(?:\n##|\Z)", txt, re.S)
        body = (m.group(1) if m else txt)[:600]
        items.append((f"{slug}:goal", "goal", body))
    return items


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
    os.makedirs(os.path.dirname(INDEX), exist_ok=True)
    with open(INDEX, "w", encoding="utf-8") as f:
        json.dump(index, f)
    return index


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
    if cmd == "build":
        i = build()
        print(f"[mneme] built {INDEX} — {i['count']} vectors (dim {i['dim']}, {i['embedder']})")
    elif cmd == "stats":
        i = load()
        print(json.dumps({"count": i["count"], "dim": i["dim"], "embedder": i["embedder"]} if i else {"count": 0}, indent=2))
    elif cmd == "query":
        text = sys.argv[2] if len(sys.argv) > 2 else ""
        k = int(sys.argv[3]) if len(sys.argv) > 3 else 5
        print(json.dumps(query(text, k), indent=2))
