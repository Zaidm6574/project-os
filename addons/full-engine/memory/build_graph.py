#!/usr/bin/env python3
"""
Project OS - GraphOS-style knowledge-graph builder.

Turns the human-readable blackboard into a GraphOS graph so agents (and you)
can answer "how do these connect?" - e.g. which packet supports which decision,
which risk attaches to which feature.

It scans `blackboard/*.md` and `blackboard/packets/*.md` and extracts:

  nodes : one per blackboard file and one per packet file (plus the Agent that
          authored each packet)
  edges : - file -> file references (a file mentions another blackboard file)
          - [[wiki-links]]
          - packet "Inputs (files/packets cited):" -> cited nodes
          - packet -> authoring agent ("Agent:" field)

Outputs (in ../graphify-out/):
  graph.json  - {nodes, edges, generated_at}   (the machine-readable graph)
  graph.mmd   - a Mermaid `graph TD` view you can paste into any Mermaid viewer

No third-party dependencies. Run:

    python build_graph.py                  # scans blackboard/ (default)
    python build_graph.py --root runs/credit-card-tracker   # scan a run dir

The --root dir is scanned for `*.md` and `<root>/packets/*.md`, so it works for
both the global blackboard/ and any per-run runs/<slug>/ directory.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "graphify-out")

FILE_REF_RE = re.compile(r"(\b\d{2}-[a-z0-9-]+\.md)\b")
WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
PACKET_REF_RE = re.compile(r"\b([a-z0-9]+-[a-z0-9]+-\d+)\b", re.IGNORECASE)
AGENT_RE = re.compile(r"^\s*Agent:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
STATUS_RE = re.compile(r"^\s*Status:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
INPUTS_RE = re.compile(r"^\s*Inputs[^:]*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)


def _node(nodes, nid, ntype, label, path=""):
    if nid not in nodes:
        nodes[nid] = {"id": nid, "type": ntype, "label": label, "path": path}


def build(root=None):
    # Default to blackboard/; accept any dir (e.g. runs/<slug>/) so the graph
    # can be built per-run. Relative roots resolve against the project root.
    if root is None:
        root = os.path.join(ROOT, "blackboard")
    elif not os.path.isabs(root):
        root = os.path.join(ROOT, root)

    nodes, edges = {}, []

    bb_files = sorted(glob.glob(os.path.join(root, "*.md")))
    packet_files = sorted(glob.glob(os.path.join(root, "packets", "*.md")))

    # blackboard file nodes
    for fp in bb_files:
        name = os.path.basename(fp)
        _node(nodes, name, "blackboard", name.replace(".md", ""), os.path.relpath(fp, ROOT))

    # packet nodes + their edges
    for fp in packet_files:
        name = os.path.basename(fp)
        if name.lower() in ("readme.md",):
            continue
        pid = name.replace(".md", "")
        try:
            text = open(fp, encoding="utf-8").read()
        except Exception:
            continue
        _node(nodes, pid, "packet", pid, os.path.relpath(fp, ROOT))

        m = AGENT_RE.search(text)
        if m and m.group(1):
            agent = "agent:" + m.group(1).strip().lower()
            _node(nodes, agent, "agent", m.group(1).strip())
            edges.append({"source": pid, "target": agent, "type": "authored_by"})

        m = STATUS_RE.search(text)
        if m:
            nodes[pid]["status"] = m.group(1).strip()

        # cited inputs (files + packet ids)
        for line in INPUTS_RE.findall(text):
            for ref in FILE_REF_RE.findall(line):
                _node(nodes, ref, "blackboard", ref.replace(".md", ""))
                edges.append({"source": pid, "target": ref, "type": "cites"})
            for ref in PACKET_REF_RE.findall(line):
                if ref != pid:
                    edges.append({"source": pid, "target": ref, "type": "cites"})

    # generic file -> file references and wiki-links across all docs
    for fp in bb_files + packet_files:
        name = os.path.basename(fp)
        src = name if name in nodes else name.replace(".md", "")
        if src not in nodes:
            continue
        try:
            text = open(fp, encoding="utf-8").read()
        except Exception:
            continue
        for ref in set(FILE_REF_RE.findall(text)):
            if ref != name and ref in nodes:
                edges.append({"source": src, "target": ref, "type": "references"})
        for link in set(WIKILINK_RE.findall(text)):
            tgt = link if link.endswith(".md") else link + ".md"
            if tgt in nodes and tgt != name:
                edges.append({"source": src, "target": tgt, "type": "links"})

    # de-dup edges
    seen, uniq = set(), []
    for e in edges:
        key = (e["source"], e["target"], e["type"])
        if key not in seen and e["source"] in nodes and e["target"] in nodes:
            seen.add(key)
            uniq.append(e)

    graph = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "node_count": len(nodes),
        "edge_count": len(uniq),
        "nodes": list(nodes.values()),
        "edges": uniq,
    }

    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "graph.json"), "w") as f:
        json.dump(graph, f, indent=2)

    # Mermaid view
    shape = {"blackboard": ('["', '"]'), "packet": ('("', '")'), "agent": ('{{"', '"}}')}
    lines = ["graph TD"]
    for n in graph["nodes"]:
        o, c = shape.get(n["type"], ('["', '"]'))
        safe = re.sub(r"[^A-Za-z0-9_]", "_", n["id"])
        lines.append(f'  {safe}{o}{n["label"]}{c}')
    for e in graph["edges"]:
        s = re.sub(r"[^A-Za-z0-9_]", "_", e["source"])
        t = re.sub(r"[^A-Za-z0-9_]", "_", e["target"])
        lines.append(f"  {s} -->|{e['type']}| {t}")
    with open(os.path.join(OUT, "graph.mmd"), "w") as f:
        f.write("\n".join(lines) + "\n")

    return graph


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Project OS GraphOS graph builder")
    ap.add_argument("--root", default="blackboard",
                    help="directory to scan (default: blackboard); e.g. runs/<slug>")
    args = ap.parse_args()
    g = build(args.root)
    print(f"GraphOS: {g['node_count']} nodes, {g['edge_count']} edges")
    print(f"  -> {os.path.join('graphify-out', 'graph.json')}")
    print(f"  -> {os.path.join('graphify-out', 'graph.mmd')}  (paste into a Mermaid viewer)")
