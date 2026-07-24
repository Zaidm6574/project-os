#!/usr/bin/env python3
"""Code graph with freshness gates — orient before explaining, fail closed.

Unlike build_graph.py (which maps blackboard RUN relationships), this maps
CURRENT SOURCE CODE: typed nodes (module / function / class / test / external)
and typed edges (defines / imports / calls / tests), each carrying evidence of
how it was established — ``ast`` (extracted from the syntax tree) vs ``inferred``
(a plausible match that must be verified). Every indexed file is fingerprinted
with sha256 so staleness is provable, not guessed.

The point is orientation, not a prettier visualization. ``orient`` refuses to
narrate architecture when the graph is stale or the symbol is absent — it emits
the explicit fallback instead of filling the gap with plausible fiction:

    Graph evidence is insufficient or stale. Continuing from direct source inspection.

Pure stdlib. No network, no personal data.

Usage:
  python3 memory/code_graph.py build  --root . [--out code-graph.json]
  python3 memory/code_graph.py check  --root . [--graph code-graph.json]
  python3 memory/code_graph.py orient <module:symbol> --root . [--graph ...]
                                       [--depth 2] [--json]
"""
import argparse
import ast
import hashlib
import json
import os
import sys

SCHEMA = "code-graph/v1"
# Deliberately NOT graph.json — must never collide with the blackboard graph.
DEFAULT_OUT = "code-graph.json"
FALLBACK = ("Graph evidence is insufficient or stale. "
            "Continuing from direct source inspection.")

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv",
             "graphify-out", ".project-os-runtime", ".serena"}


def _sha256_file(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _module_name(rel):
    """app/core.py -> app.core ; app/__init__.py -> app  (rel uses '/')"""
    parts = rel[:-3].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else rel


def _is_test_module(rel):
    base = os.path.basename(rel)
    return (base.startswith("test_") or base.endswith("_test.py")
            or "tests" in rel.split("/"))


def _iter_py_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            if name.endswith(".py"):
                full = os.path.join(dirpath, name)
                yield os.path.relpath(full, root).replace(os.sep, "/"), full


class _Module:
    def __init__(self, rel, name, tree, is_test):
        self.rel = rel
        self.name = name
        self.tree = tree
        self.is_test = is_test
        self.defs = {}          # local symbol name -> (node id, lineno, kind)
        self.import_symbols = {}  # local name -> fully-qualified node id
        self.import_modules = {}  # local alias -> module name


def _scan_module(rel, full):
    try:
        with open(full, encoding="utf-8", errors="replace") as f:
            tree = ast.parse(f.read())
    except SyntaxError:
        return None
    mod = _Module(rel, _module_name(rel), tree, _is_test_module(rel))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            kind = "test" if (mod.is_test and node.name.startswith("test")) else "function"
            mod.defs[node.name] = (f"{mod.name}:{node.name}", node.lineno, kind)
        elif isinstance(node, ast.ClassDef):
            mod.defs[node.name] = (f"{mod.name}:{node.name}", node.lineno, "class")
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    mod.defs[f"{node.name}.{item.name}"] = (
                        f"{mod.name}:{node.name}.{item.name}", item.lineno, "function")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                mod.import_modules[alias.asname or alias.name.split(".")[0]] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                mod.import_symbols[alias.asname or alias.name] = \
                    f"{node.module}:{alias.name}"
    return mod


def _call_targets(func_node):
    """Yield (callee expression, lineno) for every call inside a function."""
    for sub in ast.walk(func_node):
        if isinstance(sub, ast.Call):
            yield sub.func, sub.lineno


def build(root, out=None):
    root = os.path.abspath(root)
    out = out or os.path.join(root, DEFAULT_OUT)
    files, modules = {}, {}
    for rel, full in _iter_py_files(root):
        files[rel] = _sha256_file(full)
        mod = _scan_module(rel, full)
        if mod is not None:
            modules[mod.name] = mod

    nodes, edges, seen_edges = [], [], set()
    known_ids = set()

    def add_edge(source, target, etype, rel, lineno, method):
        key = (source, target, etype)
        if key in seen_edges:
            return
        seen_edges.add(key)
        edges.append({"source": source, "target": target, "type": etype,
                      "evidence": {"file": rel, "line": lineno, "method": method}})

    # Pass 1 — nodes, defines, imports. bare_names maps a symbol's base name to
    # every node that defines it, for the inference pass.
    bare_names = {}
    for mod in modules.values():
        nodes.append({"id": mod.name, "type": "module", "file": mod.rel, "line": 1})
        known_ids.add(mod.name)
        for local, (nid, lineno, kind) in mod.defs.items():
            nodes.append({"id": nid, "type": kind, "file": mod.rel, "line": lineno})
            known_ids.add(nid)
            add_edge(mod.name, nid, "defines", mod.rel, lineno, "ast")
            bare_names.setdefault(local.split(".")[-1], []).append(nid)
        for target_mod in set(mod.import_modules.values()) | {
                sid.split(":")[0] for sid in mod.import_symbols.values()}:
            if target_mod in modules:
                add_edge(mod.name, target_mod, "imports", mod.rel, 1, "ast")
            else:
                if target_mod not in known_ids:
                    nodes.append({"id": target_mod, "type": "external",
                                  "file": None, "line": None})
                    known_ids.add(target_mod)
                add_edge(mod.name, target_mod, "imports", mod.rel, 1, "ast")

    # Pass 2 — calls. Resolution order decides the evidence label:
    #   local def -> ast; imported symbol -> ast; imported-module attribute -> ast;
    #   unique bare-name match elsewhere -> inferred (never presented as extracted).
    for mod in modules.values():
        for local, (caller_id, _lineno, kind) in mod.defs.items():
            func_node = _find_def(mod.tree, local)
            if func_node is None:
                continue
            for callee, lineno in _call_targets(func_node):
                target, method = None, None
                if isinstance(callee, ast.Name):
                    name = callee.id
                    if name in mod.defs:
                        target, method = mod.defs[name][0], "ast"
                    elif name in mod.import_symbols:
                        sid = mod.import_symbols[name]
                        target, method = sid, "ast"
                        if sid not in known_ids:
                            target = None  # imported from an unscanned module
                    elif name in bare_names and len(bare_names[name]) == 1 \
                            and bare_names[name][0] != caller_id:
                        target, method = bare_names[name][0], "inferred"
                elif isinstance(callee, ast.Attribute) and isinstance(callee.value, ast.Name):
                    alias = callee.value.id
                    if alias in mod.import_modules:
                        tmod = mod.import_modules[alias]
                        cand = f"{tmod}:{callee.attr}"
                        if cand in known_ids:
                            target, method = cand, "ast"
                if target is None or target == caller_id:
                    continue
                add_edge(caller_id, target, "calls", mod.rel, lineno, method)
                if kind == "test":
                    add_edge(caller_id, target, "tests", mod.rel, lineno, method)

    graph = {"schema": SCHEMA, "root": root, "files": files,
             "nodes": nodes, "edges": edges}
    with open(out, "w", encoding="utf-8") as f:
        json.dump(graph, f, indent=2)
    return graph


def _find_def(tree, dotted):
    parts = dotted.split(".")
    body = tree.body
    node = None
    for part in parts:
        node = next((n for n in body
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                                       ast.ClassDef)) and n.name == part), None)
        if node is None:
            return None
        body = getattr(node, "body", [])
    return node


def load_graph(path):
    try:
        with open(path, encoding="utf-8") as f:
            graph = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if graph.get("schema") != SCHEMA:
        return None
    return graph


def freshness_problems(root, graph):
    """Empty list == provably fresh. Any hash drift, missing file, or
    unindexed new file makes the graph untrustworthy for orientation."""
    root = os.path.abspath(root)
    problems = []
    for rel, digest in graph["files"].items():
        full = os.path.join(root, rel)
        if not os.path.isfile(full):
            problems.append(f"indexed file missing: {rel}")
        elif _sha256_file(full) != digest:
            problems.append(f"source changed since build: {rel}")
    indexed = set(graph["files"])
    for rel, _full in _iter_py_files(root):
        if rel not in indexed:
            problems.append(f"new file not in graph: {rel}")
    return problems


def orient(symbol, root, graph_path, depth=2):
    """Returns (exit_code, packet_or_message). Fail-closed by construction:
    the freshness gate runs BEFORE any traversal, and an absent symbol is an
    explicit refusal, never a fuzzy guess."""
    graph = load_graph(graph_path)
    if graph is None:
        return 2, f"CODE-GRAPH: STALE (missing or unreadable graph)\n{FALLBACK}"
    problems = freshness_problems(root, graph)
    if problems:
        listing = "; ".join(problems[:5])
        return 2, f"CODE-GRAPH: STALE ({listing})\n{FALLBACK}"

    by_id = {n["id"]: n for n in graph["nodes"]}
    if symbol not in by_id:
        return 3, f"CODE-GRAPH: SYMBOL ABSENT — {symbol}\n{FALLBACK}"

    fwd, rev = {}, {}
    for e in graph["edges"]:
        if e["type"] == "calls":
            fwd.setdefault(e["source"], []).append(e["target"])
            rev.setdefault(e["target"], []).append(e["source"])

    def bounded_bfs(start, adjacency):
        found, frontier, truncated = {}, [start], False
        for hop in range(1, depth + 1):
            nxt = []
            for node in frontier:
                for neighbor in adjacency.get(node, []):
                    if neighbor != start and neighbor not in found:
                        found[neighbor] = hop
                        nxt.append(neighbor)
            frontier = nxt
        for node in frontier:
            if any(n not in found and n != start
                   for n in adjacency.get(node, [])):
                truncated = True
        return found, truncated

    callers, up_trunc = bounded_bfs(symbol, rev)
    callees, down_trunc = bounded_bfs(symbol, fwd)

    def describe(ids):
        out = []
        for nid, hop in sorted(ids.items(), key=lambda kv: (kv[1], kv[0])):
            n = by_id.get(nid, {})
            out.append({"id": nid, "file": n.get("file"), "line": n.get("line"),
                        "hops": hop})
        return out

    neighborhood = set(callers) | set(callees) | {symbol}
    unknowns = [{"source": e["source"], "target": e["target"]}
                for e in graph["edges"]
                if e["evidence"]["method"] == "inferred"
                and (e["source"] in neighborhood or e["target"] in neighborhood)]
    tests = [c for c in describe(callers)
             if by_id.get(c["id"], {}).get("type") == "test"]

    node = by_id[symbol]
    packet = {
        "symbol": symbol,
        "file": node.get("file"),
        "line": node.get("line"),
        "freshness": "fresh",
        "depth": depth,
        "callers": describe(callers),
        "callees": describe(callees),
        "tests": tests,
        "unknowns": unknowns,
        "truncated": up_trunc or down_trunc,
    }
    return 0, packet


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--root", required=True)
    b.add_argument("--out", default=None)
    c = sub.add_parser("check")
    c.add_argument("--root", required=True)
    c.add_argument("--graph", default=None)
    o = sub.add_parser("orient")
    o.add_argument("symbol")
    o.add_argument("--root", required=True)
    o.add_argument("--graph", default=None)
    o.add_argument("--depth", type=int, default=2)
    o.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    default_graph = os.path.join(os.path.abspath(args.root), DEFAULT_OUT)
    if args.cmd == "build":
        graph = build(args.root, args.out)
        print(f"[code-graph] wrote {args.out or default_graph} "
              f"({len(graph['nodes'])} nodes, {len(graph['edges'])} edges, "
              f"{len(graph['files'])} files fingerprinted)")
        return 0
    if args.cmd == "check":
        graph = load_graph(args.graph or default_graph)
        problems = (["graph missing or unreadable"] if graph is None
                    else freshness_problems(args.root, graph))
        if problems:
            print("CODE-GRAPH: STALE")
            for p in problems[:10]:
                print(f"  - {p}")
            print(FALLBACK)
            return 1
        print("CODE-GRAPH: FRESH (all indexed file hashes match current source)")
        return 0
    code, result = orient(args.symbol, args.root,
                          args.graph or default_graph, args.depth)
    if code != 0:
        print(result)
        return code
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"[orient] {result['symbol']} @ {result['file']}:{result['line']} "
              f"(freshness: {result['freshness']})")
        for label in ("callers", "callees", "tests"):
            for item in result[label]:
                print(f"  {label[:-1]}: {item['id']} ({item['file']}:{item['line']})")
        if result["unknowns"]:
            print(f"  unknowns (inferred, verify before trusting): "
                  f"{len(result['unknowns'])}")
        if result["truncated"]:
            print(f"  truncated: expansion cut at depth {result['depth']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
