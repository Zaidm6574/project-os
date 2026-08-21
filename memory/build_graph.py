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
  python3 memory/build_graph.py --ontology-graph path/to/graph.json

The ontology graph is opt-in.  Set PROJECT_OS_ONTOLOGY_GRAPH (and, when the
graph uses them, PROJECT_OS_ONTOLOGY_SCHEMA and PROJECT_OS_ONTOLOGY_NAMESPACE)
or pass the corresponding flags.  With no graph configured this builder only
reads the local blackboard/runs and does not assume a project-specific path,
schema, or identifier namespace.
"""
import argparse
import os, re, json, glob, sys, hashlib
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # project-os/
OUT_DIR = os.path.join(ROOT, "graphify-out")
OUT = os.path.join(OUT_DIR, "graph.json")
OUT_MMD = os.path.join(OUT_DIR, "graph.mmd")


class DamagedSource(RuntimeError):
    """A required source exists but could not be safely read or validated."""


class DamagedOptionalGraph(DamagedSource):
    """The configured optional ontology graph is unsafe to merge."""


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


def _read_optional_ontology_graph(source_path, expected_schema=None, namespace=None):
    """Read an explicitly configured ontology graph.

    The public builder deliberately has no ontology default.  A caller that
    wants to merge a graph supplies its path and, when the graph has a schema
    or identifier namespace contract, supplies those values as configuration.
    This keeps the reusable template independent of any private project's
    filesystem layout, schema name, or URN vocabulary.
    """
    if not source_path:
        return [], []
    source_path = os.path.abspath(os.path.expanduser(source_path))
    if expected_schema is not None and not expected_schema:
        raise DamagedOptionalGraph("ontology schema must not be empty")
    if namespace is not None and not namespace:
        raise DamagedOptionalGraph("ontology namespace must not be empty")
    if not os.path.exists(source_path):
        raise DamagedOptionalGraph("%s: file not found" % source_path)
    try:
        with open(source_path, encoding="utf-8") as f:
            candidate = json.load(f)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DamagedOptionalGraph("%s: %s" % (source_path, exc))
    if not isinstance(candidate, dict) or not isinstance(candidate.get("nodes"), list) or not isinstance(candidate.get("edges"), list):
        raise DamagedOptionalGraph("%s: invalid top-level contract" % source_path)
    if expected_schema is not None and candidate.get("schema") != expected_schema:
        raise DamagedOptionalGraph(
            "%s: expected schema %s" % (source_path, expected_schema)
        )
    if "schema" in candidate and not isinstance(candidate["schema"], str):
        raise DamagedOptionalGraph("%s: schema must be a string" % source_path)
    nodes, edges, ids = [], [], set()
    for index, node in enumerate(candidate["nodes"]):
        allowed = {"id", "type", "label", "color", "run"}
        if not isinstance(node, dict) or set(node) - allowed or not all(isinstance(node.get(key), str) and node[key] for key in ("id", "type", "label")):
            raise DamagedOptionalGraph("%s: malformed node %d" % (source_path, index))
        if namespace is not None and not node["id"].startswith(namespace):
            raise DamagedOptionalGraph(
                "%s: node id %s is outside configured namespace %s"
                % (source_path, node.get("id"), namespace)
            )
        if node["id"] in ids:
            raise DamagedOptionalGraph("%s: duplicate node id %s" % (source_path, node.get("id")))
        if "color" in node and not re.fullmatch(r"#[0-9a-fA-F]{6}", node["color"]):
            raise DamagedOptionalGraph("%s: invalid node color" % source_path)
        ids.add(node["id"]); nodes.append(node)
    seen_edges = set()
    for index, edge in enumerate(candidate["edges"]):
        if not isinstance(edge, dict) or set(edge) != {"source", "target", "type"} or not all(isinstance(edge.get(key), str) and edge[key] for key in ("source", "target", "type")):
            raise DamagedOptionalGraph("%s: malformed edge %d" % (source_path, index))
        if edge["source"] not in ids or edge["target"] not in ids:
            raise DamagedOptionalGraph("%s: dangling edge %s -> %s" % (source_path, edge["source"], edge["target"]))
        key = (edge["source"], edge["type"], edge["target"])
        if key in seen_edges:
            raise DamagedOptionalGraph("%s: duplicate edge" % source_path)
        seen_edges.add(key); edges.append(edge)
    return sorted(nodes, key=lambda node: node["id"]), sorted(edges, key=lambda edge: (edge["source"], edge["type"], edge["target"]))


def _ontology_config(ontology_path=None, ontology_schema=None, ontology_namespace=None):
    """Resolve optional ontology settings from explicit args, then env."""
    return (
        ontology_path or os.environ.get("PROJECT_OS_ONTOLOGY_GRAPH"),
        ontology_schema or os.environ.get("PROJECT_OS_ONTOLOGY_SCHEMA"),
        ontology_namespace or os.environ.get("PROJECT_OS_ONTOLOGY_NAMESPACE"),
    )


def build(root_arg=None, ontology_path=None, ontology_schema=None, ontology_namespace=None):
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
    ontology_path, ontology_schema, ontology_namespace = _ontology_config(
        ontology_path, ontology_schema, ontology_namespace
    )
    ontology_nodes, ontology_edges = _read_optional_ontology_graph(
        ontology_path, ontology_schema, ontology_namespace
    )
    existing_ids = {node["id"] for node in nodes}
    collisions = existing_ids.intersection(node["id"] for node in ontology_nodes)
    if collisions:
        raise DamagedOptionalGraph("ontology graph id collision: %s" % sorted(collisions)[0])
    nodes.extend(ontology_nodes); edges.extend(ontology_edges)
    graph = {
        "nodes": nodes,
        "edges": edges,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "source": "blackboard+ontology" if ontology_nodes else "blackboard",
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(graph, f, indent=2)
    with open(OUT_MMD, "w", encoding="utf-8") as f:
        f.write(to_mermaid(graph))
    return graph


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", help="blackboard or run directory to scan")
    parser.add_argument("--ontology-graph", help="optional ontology JSON to merge")
    parser.add_argument("--ontology-schema", help="expected ontology JSON schema")
    parser.add_argument("--ontology-namespace", help="required prefix for ontology node ids")
    parser.add_argument("--stats", action="store_true", help="print node/edge counts")
    args = parser.parse_args()
    try:
        g = build(args.root, args.ontology_graph, args.ontology_schema, args.ontology_namespace)
    except DamagedSource as exc:
        # Non-zero and legible rather than a raw traceback, and the previous
        # graph.json is left exactly as it was: build() writes only after every
        # source has been read, so a refusal here cannot publish a graph that
        # is missing the cards it could not read (same contract as
        # mneme_adapter's "INDEX was left untouched").
        sys.exit(f"[arachne] REFUSED: cannot read or validate a graph source — {exc}\n"
                 f"[arachne] {OUT} was left untouched; repair the source, "
                 f"then rebuild")
    print(f"[arachne] wrote {OUT}")
    print(f"[arachne] wrote {OUT_MMD}  (paste into any Mermaid viewer)")
    if args.stats:
        print(f"[arachne] {len(g['nodes'])} nodes, {len(g['edges'])} edges")
