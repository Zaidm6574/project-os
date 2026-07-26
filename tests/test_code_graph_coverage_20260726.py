"""Behavioral regression cover for two unguarded code_graph fixes (2026-07-26).

Commit e1e2b95 landed two real code_graph fixes with ZERO coverage: reverting
either hunk left the whole suite green. Both are covered here by OUTPUT, never
by asserting on source text:

  1. freshness_problems() must compare REALPATHs, so the same directory reached
     through a symlink (the /tmp -> /private/tmp case that silently disabled
     orient()) is not reported as a wrong-root STALE with zero drift. The
     wrong-root gate itself must still fire for a genuinely different root.

  2. _local_names() must collect function PARAMETERS, so a parameter shadowing a
     module-level name yields an explicitly-uncertain `inferred` edge (surfaced
     as an orientation `unknown`) instead of a *verified* `ast` edge to an
     unrelated module-level def. A real, unshadowed call must stay `ast`.

Writing (2) surfaced that the parameter fix was incomplete: a nested `def` and a
function-local import bind a name in the very same scope and still produced a
false `ast` edge (probed on the shipped code, not theorized). _local_names now
claims those two bindings as well, and that is covered here too.

Pure stdlib; every fixture lives in a tempfile directory.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "memory" / "code_graph.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("code_graph_cover", TOOL)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def write_project(root: Path):
    """A tiny two-module project; contents are only ever parsed, never run."""
    pkg = root / "app"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "core.py").write_text(
        "def helper():\n    return 1\n\n\ndef work():\n    return helper()\n",
        encoding="utf-8")


def call_methods(graph):
    return {(e["source"], e["target"]): e["evidence"]["method"]
            for e in graph["edges"] if e["type"] == "calls"}


def cli(args, cwd=None):
    return subprocess.run([sys.executable, str(TOOL), *args],
                          capture_output=True, text=True, cwd=cwd)


class FreshnessAcrossSymlinkedRootTests(unittest.TestCase):
    """A symlinked project root must not manufacture phantom staleness."""

    def setUp(self):
        self.tool = load_tool()
        self.tmp = tempfile.TemporaryDirectory()
        # normalize away any symlink the temp dir itself sits behind, so the
        # only symlink in play is the one this test creates on purpose
        base = Path(os.path.realpath(self.tmp.name))
        self.real = base / "proj"
        write_project(self.real)
        self.link = base / "proj-link"
        if not hasattr(os, "symlink"):
            self.skipTest("platform has no os.symlink")
        try:
            os.symlink(str(self.real), str(self.link), target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest("cannot create a symlink here: %s" % exc)
        # graph artifact kept OUTSIDE both spellings of the root
        self.out = base / "code-graph.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_built_via_symlink_checked_via_real_path_is_fresh(self):
        self.tool.build(str(self.link), str(self.out))
        graph = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertTrue(graph["files"], "fixture should have indexed files")
        self.assertEqual(self.tool.freshness_problems(str(self.real), graph), [],
                         "same directory via its real path is not staleness")

    def test_built_via_real_path_checked_via_symlink_is_fresh(self):
        self.tool.build(str(self.real), str(self.out))
        graph = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual(self.tool.freshness_problems(str(self.link), graph), [],
                         "same directory via a symlink is not staleness")

    def test_orient_still_answers_when_root_spelling_differs(self):
        """The user-visible consequence: orient() must not fail closed here."""
        self.tool.build(str(self.link), str(self.out))
        code, packet = self.tool.orient("app.core:work", str(self.real),
                                        str(self.out))
        self.assertEqual(code, 0, packet)
        self.assertEqual(packet["freshness"], "fresh")
        self.assertIn("app.core:helper", {c["id"] for c in packet["callees"]})

    def test_cli_check_from_inside_the_real_directory_is_fresh(self):
        """Reproduces the reported shape: built with an explicit symlinked path,
        later checked as `--root .` from a cwd that resolves to the real path."""
        r = cli(["build", "--root", str(self.link), "--out", str(self.out)])
        self.assertEqual(r.returncode, 0, r.stderr)
        r2 = cli(["check", "--root", ".", "--graph", str(self.out)],
                 cwd=str(self.real))
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        self.assertIn("CODE-GRAPH: FRESH", r2.stdout)

    def test_a_genuinely_different_root_is_still_reported_stale(self):
        """Guard against 'fixing' the symlink case by deleting the root gate."""
        self.tool.build(str(self.real), str(self.out))
        graph = json.loads(self.out.read_text(encoding="utf-8"))
        other = Path(os.path.realpath(self.tmp.name)) / "decoy"
        write_project(other)  # identical file names AND identical contents
        problems = self.tool.freshness_problems(str(other), graph)
        self.assertTrue(problems, "an unrelated root must not read as fresh")
        self.assertTrue(any("different root" in p for p in problems), problems)


SHADOW_SOURCE = '''\
def alpha():
    return "module alpha"


def beta():
    return "module beta"


def gamma():
    return "module gamma"


def delta():
    return "module delta"


def epsilon():
    return "module epsilon"


def positional(alpha):
    return alpha()


def positional_only(beta, /):
    return beta()


def keyword_only(*, gamma):
    return gamma()


def star_args(*delta):
    return delta()


def star_kwargs(**epsilon):
    return epsilon()


def honest_caller():
    return alpha()
'''


class ParameterShadowingTests(unittest.TestCase):
    """A parameter shadows a module-level name, so the call is a guess."""

    def setUp(self):
        self.tool = load_tool()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        write_project(self.root)
        (self.root / "app" / "shadow.py").write_text(SHADOW_SOURCE,
                                                     encoding="utf-8")
        self.out = self.root / "code-graph.json"
        self.graph = self.tool.build(str(self.root), str(self.out))

    def tearDown(self):
        self.tmp.cleanup()

    def test_every_parameter_kind_shadows_the_module_level_def(self):
        methods = call_methods(self.graph)
        for caller, callee in (("positional", "alpha"),
                               ("positional_only", "beta"),
                               ("keyword_only", "gamma"),
                               ("star_args", "delta"),
                               ("star_kwargs", "epsilon")):
            key = ("app.shadow:%s" % caller, "app.shadow:%s" % callee)
            self.assertIn(key, methods,
                          "expected a call edge for the shadowed name")
            self.assertEqual(
                methods[key], "inferred",
                "%s() calls its PARAMETER, not the module-level %s(); that "
                "edge must never be presented as extracted from the AST"
                % (caller, callee))

    def test_an_unshadowed_call_is_still_extracted_not_downgraded(self):
        methods = call_methods(self.graph)
        self.assertEqual(methods[("app.shadow:honest_caller",
                                  "app.shadow:alpha")], "ast")
        self.assertEqual(methods[("app.core:work", "app.core:helper")], "ast")

    def test_orient_surfaces_the_shadowed_call_as_an_unknown(self):
        code, packet = self.tool.orient("app.shadow:alpha", str(self.root),
                                        str(self.out))
        self.assertEqual(code, 0, packet)
        unknowns = {(u["source"], u["target"]) for u in packet["unknowns"]}
        self.assertIn(("app.shadow:positional", "app.shadow:alpha"), unknowns,
                      "a guessed call must reach the reader as an unknown")
        self.assertNotIn(("app.shadow:honest_caller", "app.shadow:alpha"),
                         unknowns, "a real call must not be flagged uncertain")


OTHER_BINDINGS_SOURCE = '''\
def helper():
    return "module helper"


def worker():
    return "module worker"


def nested_def():
    def helper():
        return "the nested one"
    return helper()


def local_import():
    from json import worker
    return worker()


def honest_caller():
    return helper()
'''


class OtherInScopeBindingsTests(unittest.TestCase):
    """Same defect class as the parameter hunk: a nested def and a
    function-local import bind the name in THIS scope, so a bare-name call
    resolving to the module-level def is a guess, not an extraction."""

    def setUp(self):
        self.tool = load_tool()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "app").mkdir(parents=True)
        (self.root / "app" / "__init__.py").write_text("", encoding="utf-8")
        (self.root / "app" / "bind.py").write_text(OTHER_BINDINGS_SOURCE,
                                                   encoding="utf-8")
        self.methods = call_methods(
            self.tool.build(str(self.root), str(self.root / "code-graph.json")))

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_nested_def_shadows_the_module_level_name(self):
        self.assertEqual(
            self.methods.get(("app.bind:nested_def", "app.bind:helper")),
            "inferred",
            "the call goes to the NESTED helper(), so the module-level match "
            "is a guess and must be labelled as one")

    def test_a_function_local_import_shadows_the_module_level_name(self):
        self.assertEqual(
            self.methods.get(("app.bind:local_import", "app.bind:worker")),
            "inferred",
            "the call goes to the imported worker, not this module's def")

    def test_the_unshadowed_call_in_the_same_module_is_still_ast(self):
        self.assertEqual(
            self.methods[("app.bind:honest_caller", "app.bind:helper")], "ast")


if __name__ == "__main__":
    unittest.main()
