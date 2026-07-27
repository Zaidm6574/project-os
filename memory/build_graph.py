#!/usr/bin/env python3
"""Graphify — build a Project OS knowledge graph from the blackboard/runs.

Scans runs/<slug>/ (and the root blackboard/) for the numbered markdown files and
emits nodes for the OS, each run, and its decisions / risks / open-questions, with
edges connecting them. Pure stdlib — no external graph tooling.

Writes two views of the same graph into graphify-out/:
  graph.json  — the machine-readable graph {nodes, edges, built_at, source}
  graph.mmd   — a Mermaid `graph TD` view you can paste into any Mermaid viewer

Usage:
  python3 memory/build_graph.py                    # scan blackboard/ + all runs/
  python3 memory/build_graph.py --root blackboard  # scan one dir (or a run folder)
  python3 memory/build_graph.py --stats            # build + print node/edge counts
"""
import os, re, json, glob, sys, hashlib
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # project-os/
OUT_DIR = os.path.join(ROOT, "graphify-out")
OUT = os.path.join(OUT_DIR, "graph.json")
OUT_MMD = os.path.join(OUT_DIR, "graph.mmd")


class DamagedSource(RuntimeError):
    """A blackboard source exists but could not be read as UTF-8 text."""


def read(p):
    """Read a blackboard source. ABSENT IS FINE; DAMAGED OR UNREADABLE IS NOT.

    This was `except Exception: return ""`, which conflated "this run has no
    03-decisions.md" with "03-decisions.md is corrupt / not readable"
    (audit 2026-07-27). One non-UTF-8 byte — or a chmod 000, or any IO fault —
    made build() see an EMPTY document, so that run's decisions, risks and open
    questions produced ZERO nodes while the tool printed
    "[arachne] wrote .../graph.json" and exited 0. Downstream consumers
    (scripts/check_optional_tools.py::local_graphos_status,
    validate_run.py::_has_graph_or_memory) only look for the artifact, so the
    loss was invisible on every surface: the repo's
    data-loss-looks-like-success signature.

    Same distinction memory/mneme_adapter.py::read() draws for the brain file,
    and the same one scripts/harvest.py::read() already draws for
    19-memory-harvest.md (an unreadable source is REFUSED there rather than
    stamped as harvested). The three per-run card files are genuinely optional,
    so a missing one still reads as "" and a run with none of them still builds
    and exits 0 — the defect is the silence, not the exit code.
    """
    try:
        with open(p, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""
    except (OSError, UnicodeDecodeError) as exc:
        # UnicodeDecodeError carries no filename at all, so name the path here:
        # the refusal in __main__ has to say WHICH file to repair.
        raise DamagedSource("%s: %s" % (p, exc))


def first_heading(md):
    m = re.search(r"^#{1,3}\s+(.+)$", md, re.M)
    return m.group(1).strip() if m else ""


def table_rows(md):
    rows = []
    lines = [l for l in md.splitlines() if l.strip().startswith("|")]
    if len(lines) < 2:
        return rows
    headers = [c.strip() for c in lines[0].strip("|").split("|")]
    for line in lines[2:]:
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not any(cells):
            continue
        rows.append(dict(zip(headers, cells)))
    return rows


def add_cards(nodes, edges, run_id, md, kind, color, id_keys, title_keys):
    for i, r in enumerate(table_rows(md)):
        rid = next((r[k] for k in id_keys if r.get(k)), str(i + 1))
        title = next((r[k] for k in title_keys if r.get(k)), "")
        if not title:
            continue
        nid = f"{run_id}:{kind}:{rid}"
        nodes.append({"id": nid, "type": kind, "label": title[:120], "color": color, "run": run_id})
        edges.append({"source": run_id, "target": nid, "type": f"has-{kind}"})


_UNSAFE_ID = re.compile(r"[^A-Za-z0-9_]")


def mermaid_id(node_id):
    """A Mermaid-safe identifier for a graph node id.

    Graph ids look like `run:decision:D1`, and `:` / `-` are not legal in a
    Mermaid node id. Sanitizing alone is not enough on its own: `a-b` and `a.b`
    both flatten to `a_b`, which would silently MERGE two nodes into one, and a
    bare id of `end` or `graph` is a Mermaid keyword. So every id is prefixed
    and carries a short digest of the ORIGINAL id, which keeps distinct nodes
    distinct and the output stable across runs.
    """
    digest = hashlib.sha1(node_id.encode("utf-8")).hexdigest()[:8]
    return "n_%s_%s" % (_UNSAFE_ID.sub("_", node_id), digest)


def mermaid_label(text):
    """Defang a blackboard string for use inside a quoted Mermaid label.

    Labels are raw markdown table cells, so `"`, `|`, `#` and newlines all
    arrive verbatim. A raw quote closes the label early and yields a document
    no viewer will parse. Mermaid has no backslash escape here; it uses HTML
    entity codes, so `#` is escaped FIRST or it would corrupt the others.
    """
    flat = " ".join(str(text).split())
    return (flat.replace("#", "#35;")
                .replace('"', "#quot;")
                .replace("|", "#124;"))


def to_mermaid(graph):
    """Render the built graph as a Mermaid `graph TD` document.

    One declaration line per node and one arrow per edge, in graph.json order,
    so the .mmd is a faithful view of the .json rather than a second graph.
    """
    lines = ["graph TD"]
    for n in graph["nodes"]:
        lines.append('  %s["%s"]' % (mermaid_id(n["id"]), mermaid_label(n.get("label") or n["id"])))
    for e in graph["edges"]:
        lines.append("  %s -->|%s| %s" % (mermaid_id(e["source"]),
                                          mermaid_label(e.get("type") or "related"),
                                          mermaid_id(e["target"])))
    return "\n".join(lines) + "\n"


def discover_run_dirs(root_arg=None):
    if root_arg:
        d = root_arg if os.path.isabs(root_arg) else os.path.join(os.getcwd(), root_arg)
        d = os.path.abspath(d)
        if not os.path.isfile(os.path.join(d, "00-project-goal.md")):
            sys.exit(f"[arachne] --root {root_arg}: no 00-project-goal.md found there")
        return [(os.path.basename(d.rstrip(os.sep)), d)]
    dirs = []
    runs = os.path.join(ROOT, "runs")
    if os.path.isdir(runs):
        for name in os.listdir(runs):
            d = os.path.join(runs, name)
            if os.path.isfile(os.path.join(d, "00-project-goal.md")):
                dirs.append((name, d))
    rbb = os.path.join(ROOT, "blackboard")
    if os.path.isfile(os.path.join(rbb, "00-project-goal.md")):
        dirs.append(("blackboard", rbb))
    return dirs


def build(root_arg=None):
    nodes = [{"id": "project-os", "type": "os", "label": "Project OS", "color": "#4da3ff"}]
    edges = []
    for run_id, d in discover_run_dirs(root_arg):
        goal_md = read(os.path.join(d, "00-project-goal.md"))
        title = first_heading(goal_md) or run_id
        nodes.append({"id": run_id, "type": "run", "label": title[:120], "color": "#3dcf82"})
        edges.append({"source": "project-os", "target": run_id, "type": "has-run"})
        add_cards(nodes, edges, run_id, read(os.path.join(d, "03-decisions.md")),
                  "decision", "#c792ea", ["ID", "id"], ["Decision", "decision"])
        add_cards(nodes, edges, run_id, read(os.path.join(d, "04-risks.md")),
                  "risk", "#f76d6d", ["ID", "id", "#"], ["Risk", "risk", "Description"])
        add_cards(nodes, edges, run_id, read(os.path.join(d, "06-open-questions.md")),
                  "question", "#f5b545", ["#", "ID", "id"], ["Question", "question"])
    graph = {
        "nodes": nodes,
        "edges": edges,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "source": "blackboard",
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(graph, f, indent=2)
    with open(OUT_MMD, "w", encoding="utf-8") as f:
        f.write(to_mermaid(graph))
    return graph


if __name__ == "__main__":
    root_arg = None
    if "--root" in sys.argv:
        i = sys.argv.index("--root")
        if i + 1 >= len(sys.argv):
            sys.exit("[arachne] --root needs a directory argument")
        root_arg = sys.argv[i + 1]
    try:
        g = build(root_arg)
    except DamagedSource as exc:
        # Non-zero and legible rather than a raw traceback, and the previous
        # graph.json is left exactly as it was: build() writes only after every
        # source has been read, so a refusal here cannot publish a graph that
        # is missing the cards it could not read (same contract as
        # mneme_adapter's "INDEX was left untouched").
        sys.exit(f"[arachne] REFUSED: cannot read a blackboard source — {exc}\n"
                 f"[arachne] {OUT} was left untouched; repair the source, "
                 f"then rebuild")
    print(f"[arachne] wrote {OUT}")
    print(f"[arachne] wrote {OUT_MMD}  (paste into any Mermaid viewer)")
    if "--stats" in sys.argv:
        print(f"[arachne] {len(g['nodes'])} nodes, {len(g['edges'])} edges")
