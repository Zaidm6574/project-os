"""Code graph with freshness gates — tests written RED-first (2026-07-24).

The blackboard graph (build_graph.py) maps run relationships; THIS graph maps
current source code: typed nodes/edges with evidence, per-file sha256 so
freshness is provable, and fail-closed orientation — a stale graph or an
absent symbol REFUSES with an explicit insufficient-evidence outcome instead
of narrating plausible architecture.
"""
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "memory" / "code_graph.py"

FALLBACK = "Graph evidence is insufficient or stale. Continuing from direct source inspection."


def load_tool():
    spec = importlib.util.spec_from_file_location("code_graph_under_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def write_fixture(root: Path):
    (root / "app").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "app" / "__init__.py").write_text("", encoding="utf-8")
    (root / "app" / "core.py").write_text(
        "def helper():\n    return 1\n\n\ndef work():\n    return helper()\n",
        encoding="utf-8")
    (root / "app" / "main.py").write_text(
        "from app.core import work\n\n\ndef entry():\n    return work()\n",
        encoding="utf-8")
    (root / "tests" / "test_core.py").write_text(
        "from app.core import work\n\n\ndef test_work():\n    assert work() == 1\n",
        encoding="utf-8")


def cli(args, cwd=None):
    return subprocess.run([sys.executable, str(TOOL), *args],
                          capture_output=True, text=True, cwd=cwd)


def edge_set(graph, etype):
    return {(e["source"], e["target"]) for e in graph["edges"] if e["type"] == etype}


class CodeGraphBuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        write_fixture(self.root)
        self.out = self.root / "code-graph.json"
        self.tool = load_tool()
        self.tool.build(str(self.root), str(self.out))
        self.graph = json.loads(self.out.read_text(encoding="utf-8"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_schema_files_hashes_and_typed_nodes(self):
        self.assertEqual(self.graph["schema"], "code-graph/v1")
        # every indexed file carries its sha256 so staleness is provable
        for rel, digest in self.graph["files"].items():
            data = (self.root / rel).read_bytes()
            self.assertEqual(digest, hashlib.sha256(data).hexdigest(), rel)
        types = {n["type"] for n in self.graph["nodes"]}
        self.assertLessEqual({"module", "function", "test"}, types)
        # every node is anchored to a file (+line for defs)
        for n in self.graph["nodes"]:
            if n["type"] != "external":
                self.assertIn(n["file"], self.graph["files"], n["id"])

    def test_defines_imports_and_call_edges_are_typed_with_evidence(self):
        self.assertIn(("app.core", "app.core:work"), edge_set(self.graph, "defines"))
        self.assertIn(("app.main", "app.core"), edge_set(self.graph, "imports"))
        calls = edge_set(self.graph, "calls")
        self.assertIn(("app.core:work", "app.core:helper"), calls)   # same module
        self.assertIn(("app.main:entry", "app.core:work"), calls)    # via import
        for e in self.graph["edges"]:
            self.assertIn(e["evidence"]["method"], ("ast", "inferred"), e)

    def test_import_resolved_calls_are_extracted_not_inferred(self):
        methods = {(e["source"], e["target"]): e["evidence"]["method"]
                   for e in self.graph["edges"] if e["type"] == "calls"}
        self.assertEqual(methods[("app.main:entry", "app.core:work")], "ast")
        self.assertEqual(methods[("app.core:work", "app.core:helper")], "ast")

    def test_bare_name_match_without_import_is_labeled_inferred(self):
        (self.root / "app" / "loose.py").write_text(
            "def rogue():\n    return mystery()\n", encoding="utf-8")
        (self.root / "app" / "vault.py").write_text(
            "def mystery():\n    return 42\n", encoding="utf-8")
        self.tool.build(str(self.root), str(self.out))
        graph = json.loads(self.out.read_text(encoding="utf-8"))
        hit = [e for e in graph["edges"] if e["type"] == "calls"
               and e["source"] == "app.loose:rogue"
               and e["target"] == "app.vault:mystery"]
        self.assertTrue(hit, "bare-name cross-module match should appear")
        self.assertEqual(hit[0]["evidence"]["method"], "inferred",
                         "unresolvable call must NOT be presented as extracted")

    def test_test_functions_produce_tests_edges(self):
        self.assertIn(("tests.test_core:test_work", "app.core:work"),
                      edge_set(self.graph, "tests"))


class CodeGraphFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        write_fixture(self.root)
        self.out = self.root / "code-graph.json"
        r = cli(["build", "--root", str(self.root), "--out", str(self.out)])
        self.assertEqual(r.returncode, 0, r.stderr)

    def tearDown(self):
        self.tmp.cleanup()

    def test_check_fresh_then_stale_after_source_edit(self):
        r = cli(["check", "--root", str(self.root), "--graph", str(self.out)])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("CODE-GRAPH: FRESH", r.stdout)
        (self.root / "app" / "core.py").write_text(
            "def helper():\n    return 2\n", encoding="utf-8")
        r2 = cli(["check", "--root", str(self.root), "--graph", str(self.out)])
        self.assertEqual(r2.returncode, 1)
        self.assertIn("CODE-GRAPH: STALE", r2.stdout)
        self.assertIn(FALLBACK, r2.stdout)

    def test_orient_refuses_on_stale_graph(self):
        (self.root / "app" / "main.py").write_text("entry = None\n", encoding="utf-8")
        r = cli(["orient", "app.core:work", "--root", str(self.root),
                 "--graph", str(self.out), "--json"])
        self.assertEqual(r.returncode, 2)
        self.assertIn(FALLBACK, r.stdout)

    def test_orient_absent_symbol_refuses_instead_of_guessing(self):
        r = cli(["orient", "app.core:imaginary", "--root", str(self.root),
                 "--graph", str(self.out), "--json"])
        self.assertEqual(r.returncode, 3)
        self.assertIn("ABSENT", r.stdout)
        self.assertIn(FALLBACK, r.stdout)


class CodeGraphOrientTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        write_fixture(self.root)
        self.out = self.root / "code-graph.json"
        cli(["build", "--root", str(self.root), "--out", str(self.out)])

    def tearDown(self):
        self.tmp.cleanup()

    def _orient(self, symbol, *extra):
        r = cli(["orient", symbol, "--root", str(self.root),
                 "--graph", str(self.out), "--json", *extra])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return json.loads(r.stdout)

    def test_orientation_packet_contents(self):
        pkt = self._orient("app.core:work")
        self.assertEqual(pkt["freshness"], "fresh")
        self.assertEqual(pkt["symbol"], "app.core:work")
        self.assertTrue(pkt["file"].endswith("core.py"))
        callers = {c["id"] for c in pkt["callers"]}
        self.assertIn("app.main:entry", callers)
        self.assertIn("tests.test_core:test_work", callers)
        callees = {c["id"] for c in pkt["callees"]}
        self.assertIn("app.core:helper", callees)
        self.assertIn("tests.test_core:test_work",
                      {t["id"] for t in pkt["tests"]})
        # inferred edges are surfaced as unknowns, never silently blended
        self.assertIn("unknowns", pkt)

    def test_traversal_is_bounded_by_depth(self):
        (self.root / "app" / "chain.py").write_text(
            "def a():\n    return b()\n\n\ndef b():\n    return c()\n\n\n"
            "def c():\n    return d()\n\n\ndef d():\n    return 1\n",
            encoding="utf-8")
        cli(["build", "--root", str(self.root), "--out", str(self.out)])
        shallow = self._orient("app.chain:d", "--depth", "1")
        callers = {c["id"] for c in shallow["callers"]}
        self.assertIn("app.chain:c", callers)
        self.assertNotIn("app.chain:a", callers,
                         "depth 1 must not dump the whole ancestor chain")
        self.assertTrue(shallow["truncated"],
                        "packet must admit when expansion was cut off")

    def test_artifact_is_separate_from_blackboard_graph(self):
        # default output must never collide with graphify-out/graph.json
        tool = load_tool()
        self.assertNotEqual(Path(tool.DEFAULT_OUT).name, "graph.json")


if __name__ == "__main__":
    unittest.main()
