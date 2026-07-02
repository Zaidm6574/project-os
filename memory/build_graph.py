#!/usr/bin/env python3
"""Graphify — build a Project OS knowledge graph from the blackboard/runs.

Scans runs/<slug>/ (and the root blackboard/) for the numbered markdown files and
emits arachne-out/graph.json: nodes for the OS, each run, and its decisions / risks /
open-questions, with edges connecting them. Pure stdlib — no external graph tooling.

Usage:
  python3 memory/build_graph.py            # build graph.json
  python3 memory/build_graph.py --stats    # build + print node/edge counts
"""
import os, re, json, glob, sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # project-os/
OUT_DIR = os.path.join(ROOT, "arachne-out")
OUT = os.path.join(OUT_DIR, "graph.json")


def read(p):
    try:
        with open(p, encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


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


def discover_run_dirs():
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


def build():
    nodes = [{"id": "project-os", "type": "os", "label": "Project OS", "color": "#4da3ff"}]
    edges = []
    for run_id, d in discover_run_dirs():
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
    return graph


if __name__ == "__main__":
    g = build()
    print(f"[arachne] wrote {OUT}")
    if "--stats" in sys.argv:
        print(f"[arachne] {len(g['nodes'])} nodes, {len(g['edges'])} edges")
