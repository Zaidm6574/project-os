"""Graph-builder documentation claims, checked against observed behaviour.

Same doctrine as tests/test_docs_claims_20260726.py: read the claim out of the
doc AT RUNTIME, then run the real code and compare. Two claims are pinned here.

1. Mermaid output. Four shipped files told the reader that
   `python3 memory/build_graph.py --root ...` produces `graphify-out/graph.json`
   **and a Mermaid view** -- prompts/workflows/memory-sync.md, its two generated
   adapters under addons/full-engine/staged/, and the memory-librarian agent.
   The canonical memory/build_graph.py wrote only graph.json; the Mermaid
   emitter lived exclusively in the older addons/full-engine/memory fork, which
   scripts/setup_project_os.py deliberately never installs over the canonical
   file (tests/test_audit_fixes_20260725.py pins that). So the documented
   command produced no .mmd in any install, ever.

2. mneme_adapter's index sources. memory/README.md said `build` embeds "the
   shared brain and memory files". _gather() reads the shared brain JSONL, its
   archive tier, and each run's 00-project-goal.md -- it never opens a single
   file in memory/.

Both tests fail in either direction: silently dropping the emitter fails, and
so does letting the prose drift away from _scanned_sources().
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE_BUILDER = ROOT / "memory" / "build_graph.py"
MNEME = ROOT / "memory" / "mneme_adapter.py"
MEMORY_README = ROOT / "memory" / "README.md"

MERMAID_RE = re.compile(r"mermaid|\.mmd\b", re.I)


def read(path):
    return path.read_text(encoding="utf-8")


def load_module(path, name):
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def tracked_files():
    out = subprocess.run(
        ["git", "ls-files"], cwd=str(ROOT), capture_output=True, text=True, check=True
    )
    return [ROOT / line for line in out.stdout.splitlines() if line]


def mermaid_promisers():
    """Tracked prose that tells a reader `memory/build_graph.py` emits Mermaid.

    Discovered at runtime, not hardcoded: the audit that found this counted
    four files, and a fifth would otherwise slip past unnoticed.
    """
    found = []
    for path in tracked_files():
        if path.suffix.lower() != ".md":
            continue
        if str(path.relative_to(ROOT)).startswith("tests/"):
            continue
        try:
            text = read(path)
        except (OSError, UnicodeDecodeError):
            continue
        if "memory/build_graph.py" in text and MERMAID_RE.search(text):
            found.append(path.relative_to(ROOT))
    return sorted(found)


def build_in_sandbox(tmp, decision_title="Ship it", run_title="Demo Run"):
    """Copy the canonical builder into a throwaway project and run it there.

    build_graph.py resolves its output dir from its own __file__, so it has to
    be copied -- running it from the repo would write into the repo.
    """
    project = Path(tmp) / "project"
    memory = project / "memory"
    run = project / "runs" / "demo"
    memory.mkdir(parents=True)
    run.mkdir(parents=True)
    shutil.copy2(CORE_BUILDER, memory / "build_graph.py")
    (run / "00-project-goal.md").write_text("# %s\n" % run_title, encoding="utf-8")
    (run / "03-decisions.md").write_text(
        "| ID | Decision |\n| --- | --- |\n| D1 | %s |\n" % decision_title,
        encoding="utf-8",
    )
    (run / "04-risks.md").write_text(
        "| ID | Risk |\n| --- | --- |\n| R1 | Disk fills up |\n", encoding="utf-8"
    )
    (run / "06-open-questions.md").write_text(
        "| # | Question |\n| --- | --- |\n| 1 | Who owns it? |\n", encoding="utf-8"
    )
    result = subprocess.run(
        [sys.executable, str(memory / "build_graph.py"), "--root", "runs/demo"],
        cwd=str(project),
        capture_output=True,
        text=True,
    )
    return project, result


class MermaidOutputClaimTests(unittest.TestCase):
    def test_docs_that_promise_mermaid_exist_to_be_checked(self):
        """If nothing promises Mermaid any more, the emitter is dead weight."""
        promisers = mermaid_promisers()
        self.assertTrue(
            promisers,
            "no tracked doc promises Mermaid output from memory/build_graph.py "
            "any more -- either this test is looking in the wrong place, or the "
            "claim was removed and the emitter in memory/build_graph.py plus "
            "this test should be removed with it",
        )

    def test_documented_mermaid_view_is_actually_written(self):
        promisers = mermaid_promisers()
        with tempfile.TemporaryDirectory() as tmp:
            project, result = build_in_sandbox(tmp)
            self.assertEqual(result.returncode, 0, result.stderr)
            out = project / "graphify-out"
            self.assertTrue(
                (out / "graph.json").exists(),
                "the builder stopped writing graph.json: %s" % result.stdout,
            )
            mmd = out / "graph.mmd"
            self.assertTrue(
                mmd.exists(),
                "%d shipped file(s) promise a Mermaid view from "
                "`python3 memory/build_graph.py`, but the canonical builder "
                "wrote no graphify-out/graph.mmd. Promisers: %s"
                % (len(promisers), ", ".join(str(p) for p in promisers)),
            )
            body = read(mmd)
            self.assertEqual(
                body.splitlines()[0].strip(),
                "graph TD",
                "graph.mmd is not a Mermaid `graph TD` document as documented",
            )

    def test_mermaid_view_covers_every_node_and_edge_in_graph_json(self):
        """The .mmd is a VIEW of graph.json, not a second, smaller graph."""
        with tempfile.TemporaryDirectory() as tmp:
            project, result = build_in_sandbox(tmp)
            self.assertEqual(result.returncode, 0, result.stderr)
            out = project / "graphify-out"
            graph = json.loads(read(out / "graph.json"))
            body = read(out / "graph.mmd")
            lines = [ln.strip() for ln in body.splitlines()[1:] if ln.strip()]

            builder = load_module(CORE_BUILDER, "mermaid_builder_ids")
            ids = [builder.mermaid_id(n["id"]) for n in graph["nodes"]]
            self.assertEqual(
                len(set(ids)), len(ids),
                "two graph nodes collapsed onto one Mermaid id: %s" % ids,
            )
            declarations = [ln for ln in lines if "-->" not in ln]
            self.assertEqual(
                len(declarations), len(graph["nodes"]),
                "graph.json has %d nodes but graph.mmd declares %d"
                % (len(graph["nodes"]), len(declarations)),
            )
            for node, mid in zip(graph["nodes"], ids):
                with self.subTest(node=node["id"]):
                    self.assertTrue(
                        any(ln.startswith(mid + "[") or ln.startswith(mid + "(")
                            or ln.startswith(mid + "{") for ln in declarations),
                        "node %r missing from graph.mmd" % node["id"],
                    )
            arrows = [ln for ln in lines if "-->" in ln]
            self.assertEqual(
                len(arrows), len(graph["edges"]),
                "graph.json has %d edges but graph.mmd draws %d"
                % (len(graph["edges"]), len(arrows)),
            )
            for edge in graph["edges"]:
                src = builder.mermaid_id(edge["source"])
                tgt = builder.mermaid_id(edge["target"])
                with self.subTest(edge=(edge["source"], edge["target"])):
                    self.assertTrue(
                        any(ln.startswith(src + " -->") and ln.endswith(" " + tgt)
                            for ln in arrows),
                        "edge %s -> %s missing from graph.mmd"
                        % (edge["source"], edge["target"]),
                    )

    def test_mermaid_ids_and_labels_survive_hostile_blackboard_text(self):
        """Both directions: the label must be KEPT, and its syntax DEFANGED.

        Node ids are `run:kind:ID` and labels are raw blackboard prose, so `:`,
        `"`, `#` and `|` all reach the emitter -- `|` via the run heading, which
        is not table-parsed. A raw `"` closes the label early and produces a
        Mermaid document no viewer will parse: the exact failure the doc claim
        would send a reviewer into.
        """
        hostile_decision = 'Adopt "quoted" #33 plan'
        hostile_run = 'Demo | Run "one"'
        with tempfile.TemporaryDirectory() as tmp:
            project, result = build_in_sandbox(
                tmp, decision_title=hostile_decision, run_title=hostile_run
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            out = project / "graphify-out"
            graph = json.loads(read(out / "graph.json"))
            body = read(out / "graph.mmd")
            builder = load_module(CORE_BUILDER, "mermaid_builder_hostile")

            def rendered_label(node):
                mid = builder.mermaid_id(node["id"])
                self.assertNotIn(":", mid, "Mermaid node ids may not contain ':'")
                self.assertTrue(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", mid), mid)
                line = next(
                    ln.strip()
                    for ln in body.splitlines()
                    if ln.strip().startswith(mid + "[")
                )
                return line[len(mid) + 2:-2]  # strip `mid["` and `"]`

            decision = next(n for n in graph["nodes"] if n["type"] == "decision")
            self.assertEqual(
                decision["label"], hostile_decision, "graph.json lost the raw label"
            )
            label = rendered_label(decision)
            self.assertNotIn('"', label, "an unescaped quote breaks the Mermaid label")
            # kept, not silently dropped
            self.assertIn("quoted", label)
            self.assertIn("33", label)
            self.assertIn("plan", label)

            run_node = next(n for n in graph["nodes"] if n["type"] == "run")
            self.assertEqual(
                run_node["label"], hostile_run, "graph.json lost the raw run title"
            )
            run_label = rendered_label(run_node)
            self.assertNotIn("|", run_label, "an unescaped pipe breaks the Mermaid label")
            self.assertNotIn('"', run_label, "an unescaped quote breaks the Mermaid label")
            self.assertIn("Demo", run_label)
            self.assertIn("Run", run_label)
            self.assertIn("one", run_label)


class MnemeIndexSourceClaimTests(unittest.TestCase):
    """memory/README.md's mneme bullet vs mneme_adapter._scanned_sources()."""

    def load_mneme(self, name):
        """Load the adapter with the DEFAULT brain path the README documents.

        SHARED_BRAIN honours $PROJECT_OS_SHARED_BRAIN, so a developer who has
        pointed it at /somewhere/else.jsonl would otherwise see this test fail
        on their own environment rather than on a real doc/code drift.
        """
        env = dict(os.environ)
        env.pop("PROJECT_OS_SHARED_BRAIN", None)
        with unittest.mock.patch.dict(os.environ, env, clear=True):
            return load_module(MNEME, name)

    def mneme_bullet(self):
        text = read(MEMORY_README)
        match = re.search(
            r"^- `mneme_adapter\.py`.*?(?=^- `|\Z)", text, re.S | re.M
        )
        self.assertIsNotNone(
            match, "memory/README.md no longer documents mneme_adapter.py"
        )
        return " ".join(match.group(0).split())

    def test_readme_names_every_source_mneme_actually_reads(self):
        mneme = self.load_mneme("mneme_for_docs_claim")
        sources = mneme._scanned_sources()
        self.assertTrue(sources, "_scanned_sources() went empty")
        bullet = self.mneme_bullet()
        for source in sources:
            token = os.path.basename(source)
            if token.endswith("shared-brain.jsonl"):
                token = "shared brain"
            with self.subTest(source=source):
                self.assertIn(
                    token,
                    bullet,
                    "mneme_adapter indexes %s but memory/README.md never says so"
                    % source,
                )

    def test_readme_does_not_claim_mneme_indexes_this_folder(self):
        mneme = self.load_mneme("mneme_for_docs_claim_negative")
        memory_dir = os.path.join(mneme.ROOT, "memory")
        touched = [
            s for s in mneme._scanned_sources()
            if os.path.abspath(s).startswith(os.path.abspath(memory_dir) + os.sep)
        ]
        self.assertEqual(
            touched, [],
            "this test asserts the README must not claim mneme indexes memory/; "
            "_gather() now DOES read %s, so fix the README instead" % touched,
        )
        bullet = self.mneme_bullet()
        self.assertNotIn(
            "memory files",
            bullet,
            "memory/README.md says mneme_adapter `build` embeds \"memory "
            "files\", but _gather() reads only %s -- nothing in memory/"
            % ", ".join(mneme._scanned_sources()),
        )


if __name__ == "__main__":
    unittest.main()
