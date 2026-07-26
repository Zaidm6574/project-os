#!/usr/bin/env python3
"""Regression tests for the 2026-07-25 line-comb audit fixes.

Every test here encodes a defect that shipped green: the suite passed while the
behaviour was wrong, because nothing asserted the behaviour. Each one fails if
its fix is reverted.

Grouped by the root cause the comb kept finding:

  * A gate that matches VOCABULARY instead of reading a FIELD
    (validate_run tier lock / manifest, harvest reject rows, score_rubric's
    first-match User Need Gate).
  * A writer that does not VERIFY ITS OWN WRITE (adopt_project's dead regex).
  * A second COPY of a security list that quietly drifted (osvec_adapter vs
    brain.py secret patterns).
  * An input trusted because it is "ours" (workflow names, plan schema).
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE_MEMORY = ROOT / "addons" / "full-engine" / "memory"
BRAIN_DIR = ROOT / "addons" / "full-engine" / "brain"

# osvec_adapter.py is an OPTIONAL add-on that imports numpy at module scope and
# uses it on its core add/search path, so it cannot be exercised on a
# stdlib-only interpreter. CI runs python 3.9/3.10/3.x with no pip install step,
# where importing it raises ModuleNotFoundError. Per the house rule in
# .github/workflows/test.yml, a below-floor dependency SKIPS with a reason --
# it never fails, and it never gets papered over by making the add-on's numpy
# import lazy (numpy is genuinely required there, not optional).
try:
    import numpy  # noqa: F401  (probe only)
    HAVE_NUMPY = True
except ImportError:  # pragma: no cover - depends on the interpreter
    HAVE_NUMPY = False

NO_NUMPY_REASON = (
    "numpy is not installed on this interpreter, and "
    "addons/full-engine/memory/osvec_adapter.py imports numpy at module scope "
    "(it is required, not optional -- the add/search path calls np.float32), "
    "so the vector-memory secret gate cannot be loaded here. "
    "Install numpy to run this check."
)
needs_numpy = unittest.skipUnless(HAVE_NUMPY, NO_NUMPY_REASON)


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _osvec_on_a_throwaway_store(name: str, tmp: str):
    """Load osvec_adapter with its store paths pointed at `tmp`.

    ProjectMemory() mkdirs STORE_DIR in __init__, so this must happen BEFORE
    any store is constructed -- otherwise the test would touch the caller's
    real vector memory under addons/full-engine/memory/store/.
    """
    osvec = load(name, ENGINE_MEMORY / "osvec_adapter.py")
    osvec.STORE_DIR = tmp
    osvec.INDEX_PATH = os.path.join(tmp, "project.tvim")
    osvec.SIDECAR_PATH = os.path.join(tmp, "project.sidecar.json")
    return osvec


class _MangledWriter:
    """A write handle that corrupts what actually lands on disk.

    Stands in for a short write / truncated filesystem, so a writer's
    self-verification can be proved to read the FILE and not its own string.
    """

    def __init__(self, fh, mangle):
        self._fh = fh
        self._mangle = mangle

    def write(self, data):
        return self._fh.write(self._mangle(data))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self._fh.close()
        return False


class ValidatorReadsFieldsNotProse(unittest.TestCase):
    """Run-closure gates must read fields, not scan for words."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ENGINE_MEMORY))
        cls.v = load("vr_fix", ENGINE_MEMORY / "validate_run.py")

    def test_tier_lock_rejects_prose_that_merely_contains_the_words(self):
        """'locked' in low and 'tier' in low passed on unrelated sentences."""
        self.assertFalse(
            self.v._tier_locked("The door is locked.\nWe shipped the beta tier."),
            "unrelated prose satisfied the tier-lock gate",
        )

    def test_tier_lock_reads_the_locked_field(self):
        self.assertTrue(self.v._tier_locked("Chosen tier: solo\nLocked: yes"))
        self.assertTrue(self.v._tier_locked("**Locked**: yes"))
        self.assertFalse(self.v._tier_locked("Locked: no"))
        self.assertFalse(self.v._tier_locked("Chosen tier: solo"))

    def test_manifest_gate_rejects_a_denial_of_a_manifest(self):
        """"No artifact manifest was produced" used to CLOSE the run."""
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "14-artifact-manifest.md").write_text(
                "# Artifact Manifest\n\nNo artifact manifest was produced.\n",
                encoding="utf-8",
            )
            self.assertFalse(
                self.v._has_manifest(tmp),
                "a report DENYING a manifest satisfied the manifest gate",
            )

    def test_manifest_gate_requires_entries_not_just_the_word(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "14-artifact-manifest.md"
            p.write_text("# Artifact Manifest\n\n(manifest pending)\n", encoding="utf-8")
            self.assertFalse(self.v._has_manifest(tmp), "empty manifest passed")
            p.write_text(
                "# Artifact Manifest\n\n- site/index.html — shipped\n", encoding="utf-8"
            )
            self.assertTrue(self.v._has_manifest(tmp), "real manifest was rejected")


class HarvestDropsRowsNotWords(unittest.TestCase):
    """`\\breject|private[- ]only\\b` matched any word starting with 'reject'."""

    @classmethod
    def setUpClass(cls):
        cls.h = load("harvest_fix", ROOT / "scripts" / "harvest.py")

    def test_a_lesson_bullet_that_mentions_rejecting_is_kept(self):
        """Free text is judged by _is_reject_bullet, not the table-cell rule.

        Three regexes tried to tell a verdict from a lesson by TEXT and all
        three failed (dropped real lessons, then let annotated rejections
        through, then both). The rule is now structural, so a bullet must carry
        an explicit directive or annotation punctuation to be dropped.
        """
        for text in (
            "Evaluator must reject on missing evidence",
            "The plan was rejected by the board, so we revised it",
            "Rejection criteria belong in the rubric",
            "Reject-first workflow works well when scoping new features",
        ):
            with self.subTest(text=text[:40]):
                self.assertFalse(
                    self.h._is_reject_bullet(text),
                    f"a real lesson was silently dropped: {text!r}",
                )

    def test_an_explicitly_marked_bullet_is_dropped(self):
        for text in (
            "Private-only: internal note",
            "Do not harvest until reviewed by the board",
            "Rejected: dupe",
            "[Rejected] superseded",
        ):
            with self.subTest(text=text[:40]):
                self.assertTrue(
                    self.h._is_reject_bullet(text),
                    f"an explicit rejection marker harvested through: {text!r}",
                )

    def test_a_status_cell_marking_rejection_is_dropped(self):
        """Table cells past the first may say anything after the marker."""
        for cell in (
            "Rejected", "rejected", "private-only", "do-not-harvest",
            "Rejected (see note)", "REJECTED — superseded",
            "Rejected because of scope creep", "Rejection pending review",
        ):
            with self.subTest(cell=cell):
                self.assertIsNotNone(
                    self.h.REJECT_CELL.match(cell),
                    f"a rejection verdict was harvested as a lesson: {cell!r}",
                )

    def test_the_marker_must_be_a_standalone_word(self):
        """`Reject-first` is a compound, not a verdict."""
        for cell in ("Reject-first workflow works well", "Rejects are logged", "Approved"):
            with self.subTest(cell=cell):
                self.assertIsNone(self.h.REJECT_CELL.match(cell))

    def setUp(self):
        # DROPPED is module state; each behavioural test inspects its own rows.
        del self.h.DROPPED[:]

    def _harvest(self, md):
        """Real parse of a real 19-memory-harvest.md body -> harvested texts."""
        return [text for _typ, text in self.h.bullets_by_section(md)]

    def _lessons_table(self, *rows):
        return (
            "## Lessons\n\n"
            "| Lesson | Evidence | Future Safeguard | Approved For Reuse? |\n"
            "|---|---|---|---|\n"
            + "".join(rows)
        )

    def test_a_lesson_cell_that_discusses_rejection_is_still_harvested(self):
        """cells[0] holds the LESSON, so verdict matching must not apply there.

        Applying the status-column rule to the lesson cell is what made every
        text-only regex drop real lessons (adversarial verify 2026-07-25). This
        asserts on the harvested OUTPUT, not on the source text: the previous
        version of this test only checked that the string "cells[1:]" appeared
        somewhere in bullets_by_section, which an unrelated pre-existing line
        already satisfied, so the fix could be reverted entirely and it passed.
        """
        md = self._lessons_table(
            "| Evaluator must reject on missing evidence | wave 2 | add a gate | Approved |\n",
            "| Rejection criteria belong in the rubric | wave 3 | write them down | Yes |\n",
            "| Reject-first workflow works well when scoping | wave 4 | keep it | Yes |\n",
        )
        got = self._harvest(md)
        self.assertEqual(len(got), 3, f"a real lesson was dropped: {got}")
        self.assertTrue(got[0].startswith("Evaluator must reject on missing evidence"))
        self.assertEqual(self.h.DROPPED, [], "nothing here is a verdict")

    def test_a_verdict_in_the_first_cell_still_drops_the_row(self):
        """Checking only cells[1:] let a column-one verdict harvest through.

        harvest feeds a PUBLIC brain, so a missed marker is a content leak.
        """
        md = self._lessons_table(
            "| Rejected: holds a private access token | wave 2 | none | |\n",
            "| Rejected | this row holds a private token | none | |\n",
            "| **Private-only** | internal client note | none | |\n",
        )
        got = self._harvest(md)
        self.assertEqual(got, [], f"a rejected row harvested through: {got}")
        self.assertEqual(len(self.h.DROPPED), 3, self.h.DROPPED)

    def test_a_marker_later_inside_a_status_cell_drops_the_row(self):
        """Anchoring the status rule at the cell start let verdicts through."""
        for verdict in ("No — rejected for reuse", "not approved, rejected",
                        "Yes but private-only", "keep internal; do not harvest"):
            with self.subTest(verdict=verdict):
                del self.h.DROPPED[:]
                md = self._lessons_table(
                    "| A lesson that must not reach the public brain | wave 2 "
                    f"| none | {verdict} |\n"
                )
                self.assertEqual(
                    self._harvest(md), [],
                    f"row harvested through despite verdict {verdict!r}",
                )
                self.assertEqual(len(self.h.DROPPED), 1)

    def test_a_bullet_rejection_annotated_with_a_comma_is_dropped(self):
        """`,` was missing from the annotation punctuation class."""
        bullet = "Rejected, this contains a private token, do not keep"
        self.assertTrue(
            self.h._is_reject_bullet(bullet),
            "a comma-annotated rejection was treated as ordinary prose",
        )
        got = self._harvest("## Lessons\n\n- " + bullet + "\n")
        self.assertEqual(got, [], f"a rejected bullet harvested through: {got}")
        self.assertEqual(len(self.h.DROPPED), 1)

    def test_a_bare_marker_bullet_is_dropped(self):
        """Prose that is nothing but the marker is a verdict, not a sentence."""
        for bullet in ("Rejected", "**REJECTED**", "rejected."):
            with self.subTest(bullet=bullet):
                del self.h.DROPPED[:]
                self.assertEqual(
                    self._harvest("## Lessons\n\n- " + bullet + "\n"), [],
                    f"bare verdict harvested through: {bullet!r}",
                )

    def test_a_directive_anywhere_in_a_bullet_is_honoured(self):
        """"do not harvest" / "private-only" are never ordinary prose."""
        bullet = "Client onboarding checklist, private-only, keep it internal"
        got = self._harvest("## Lessons\n\n- " + bullet + "\n")
        self.assertEqual(got, [], f"a private-only bullet harvested through: {got}")

    def test_ordinary_bullets_and_rows_still_harvest(self):
        """The filter must not eat the ordinary case (this is the whole point)."""
        md = (
            "## Lessons\n\n- Verify a fix by reverting it and watching the test fail\n"
            + self._lessons_table(
                "| Read the record, not a projection of it | wave 1 | print whole rows | Approved |\n"
            )
        )
        got = self._harvest(md)
        self.assertEqual(len(got), 2, got)
        self.assertIn("Read the record, not a projection of it — wave 1", got[1])
        self.assertEqual(self.h.DROPPED, [])

    def test_dropped_rows_are_recorded_for_reporting(self):
        """Silent filtering is why the over-broad pattern went unnoticed."""
        row = "| secret client detail | wave 2 | none | Rejected |\n"
        self.assertEqual(self._harvest(self._lessons_table(row)), [])
        self.assertEqual(len(self.h.DROPPED), 1, "the dropped row was not reported")
        self.assertIn("secret client detail", self.h.DROPPED[0])


class SecretPatternsStayInSync(unittest.TestCase):
    """Two doors into one store must use the same lock."""

    def _patterns_from_source(self, path: Path) -> list:
        src = path.read_text(encoding="utf-8")
        block = src.split("_SECRET_PATTERNS = [", 1)[1].split("\n]", 1)[0]
        return re.findall(r'^\s*r"(.*?)",\s*(?:#.*)?$', block, re.M)

    def test_osvec_patterns_match_brain_patterns_exactly(self):
        brain = load("brain_parity", BRAIN_DIR / "brain.py")
        want = [p.pattern for p in brain.SECRET_PATTERNS]
        got = self._patterns_from_source(ENGINE_MEMORY / "osvec_adapter.py")
        self.assertEqual(
            got,
            want,
            "osvec_adapter's secret list drifted from brain.py's -- vector "
            "memory would accept keys the brain refuses",
        )

    @needs_numpy
    def test_osvec_detects_modern_key_formats(self):
        osvec = load("osvec_fix", ENGINE_MEMORY / "osvec_adapter.py")
        for name, secret in {
            "anthropic": "sk-ant-api03-" + "A" * 40,
            "openai_proj": "sk-proj-" + "B" * 40,
            "stripe": "sk_live_" + "C" * 24,
            "figma": "figd_" + "E" * 30,
            "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc123",
            "pg_dsn": "postgres://admin:hunter2@db.example.com:5432/prod",
        }.items():
            with self.subTest(kind=name):
                self.assertIsNotNone(
                    osvec.looks_like_secret("my key is " + secret),
                    f"{name} was NOT detected by vector memory",
                )

    @needs_numpy
    def test_osvec_does_not_false_positive_on_ordinary_text(self):
        osvec = load("osvec_fp", ENGINE_MEMORY / "osvec_adapter.py")
        for text in (
            "risk-001-d885ad348d19 was closed",
            "The sk- prefix identifies OpenAI keys; never paste one here.",
            "Deployed revision teas-prep-00003-czf to Cloud Run.",
            "AC power draw dropped after the fix.",
        ):
            with self.subTest(text=text[:40]):
                self.assertIsNone(
                    osvec.looks_like_secret(text), f"false positive on {text!r}"
                )

    # `memory_type` is the ONE parameter of add() that is exempt from the secret
    # scan, and only because it is not free text: add() rejects any value
    # outside the closed VALID_TYPES set, so nothing pasteable survives to be
    # stored. test_osvec_add_rejects_a_memory_type_outside_the_closed_set is
    # what makes that exemption true rather than asserted.
    NOT_FREE_TEXT = {"self", "memory_type"}

    @staticmethod
    def _scanned_fields_of_add(path: Path):
        """The field names add()'s secret scan actually iterates over.

        Read from the AST, not a substring: the point of this test is that a
        parameter NAME appearing in the source proves nothing (the function
        signature already contains every one of them). This returns the string
        literals in the `for field, value in ((...), ...)` header, so deleting
        or narrowing the loop shrinks the set instead of leaving it intact.
        """
        tree = ast.parse(path.read_text(encoding="utf-8"))
        store = None
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "ProjectMemory":
                store = node
                break
        assert store is not None, "osvec_adapter has no ProjectMemory any more"
        add = None
        for node in store.body:
            if isinstance(node, ast.FunctionDef) and node.name == "add":
                add = node
                break
        assert add is not None, "ProjectMemory has no add() any more"
        params = [a.arg for a in list(add.args.posonlyargs) + list(add.args.args)
                  + list(add.args.kwonlyargs)]
        scanned = set()
        for node in ast.walk(add):
            if not isinstance(node, ast.For):
                continue
            if not isinstance(node.iter, (ast.Tuple, ast.List)):
                continue
            for pair in node.iter.elts:
                if not isinstance(pair, (ast.Tuple, ast.List)) or not pair.elts:
                    continue
                head = pair.elts[0]
                if isinstance(head, ast.Constant) and isinstance(head.value, str):
                    scanned.add(head.value)
        return params, scanned

    def test_osvec_scan_covers_every_free_text_parameter_of_add(self):
        """Nothing a caller can paste into may skip the secret scan.

        This is the stdlib-only half of the vector-memory gate: CI installs no
        numpy on any leg, so the behavioural tests below all skip there and
        this is the only check that runs. It is a two-copies-must-agree test
        (signature vs scan tuple), not a proxy -- deleting the loop empties
        `scanned`, narrowing it to `text` drops three names, and adding a new
        parameter without scanning it fails too. `run_slug` was exactly that
        miss: CLI-exposed, folded into memory_id, persisted to the side-car,
        and unscanned (adversarial verify 2026-07-26).
        """
        params, scanned = self._scanned_fields_of_add(
            ENGINE_MEMORY / "osvec_adapter.py"
        )
        expected = [p for p in params if p not in self.NOT_FREE_TEXT]
        self.assertTrue(expected, "add() lost all of its free-text parameters")
        missing = sorted(set(expected) - scanned)
        self.assertEqual(
            missing, [],
            "osvec_adapter.add() accepts %s but never scans %s for secrets -- "
            "a key pasted there is stored verbatim" % (expected, missing),
        )
        stray = sorted(scanned - set(params))
        self.assertEqual(
            stray, [],
            "the scan names %s, which add() no longer accepts" % stray,
        )

    @needs_numpy
    def test_osvec_add_rejects_a_memory_type_outside_the_closed_set(self):
        """Justifies memory_type's exemption from the secret scan above."""
        with tempfile.TemporaryDirectory() as tmp:
            osvec = _osvec_on_a_throwaway_store("osvec_type", tmp)
            mem = osvec.ProjectMemory()
            with self.assertRaises(ValueError):
                mem.add("clean text", "sk-ant-api03-" + "A" * 40)
            self.assertEqual(len(mem.index), 0)
            self.assertEqual(mem.sidecar, {})

    @needs_numpy
    def test_osvec_add_refuses_a_secret_in_any_field_not_only_text(self):
        """The exact hole brain.py had: a secret in a non-body field.

        This drives add() itself. The previous version of this test grepped
        add()'s source for the field NAMES, which the function SIGNATURE
        already satisfies -- deleting the whole scan loop kept it green
        (audit 2026-07-26).
        """
        secret = "sk-ant-api03-" + "A" * 40
        cases = (
            ("text", {"text": secret}),
            ("source_file", {"source_file": secret}),
            ("memory_id", {"memory_id": secret}),
            ("tags", {"tags": ["harmless", secret]}),
            # CLI-exposed as --run-slug, folded into memory_id, and persisted
            # to the side-car by save(): it leaked exactly like `tags` did
            # (adversarial verify 2026-07-26).
            ("run_slug", {"run_slug": secret}),
        )
        with tempfile.TemporaryDirectory() as tmp:
            osvec = _osvec_on_a_throwaway_store("osvec_fields", tmp)
            for field, overrides in cases:
                with self.subTest(field=field):
                    mem = osvec.ProjectMemory()
                    kwargs = {
                        "text": "Prefer the Solo tier before escalating",
                        "memory_type": "lesson",
                    }
                    kwargs.update(overrides)
                    with self.assertRaises(ValueError) as ctx:
                        mem.add(**kwargs)
                    self.assertIn(
                        field, str(ctx.exception),
                        f"add() stored a secret pasted into '{field}'",
                    )
                    self.assertEqual(
                        len(mem.index), 0,
                        f"a secret in '{field}' still reached the vector index",
                    )
                    self.assertEqual(
                        mem.sidecar, {},
                        f"a secret in '{field}' still reached the side-car",
                    )

    @needs_numpy
    def test_osvec_add_still_accepts_an_ordinary_record(self):
        """The field scan must not turn into a blanket refusal."""
        with tempfile.TemporaryDirectory() as tmp:
            osvec = _osvec_on_a_throwaway_store("osvec_clean", tmp)
            mem = osvec.ProjectMemory()
            rec = mem.add(
                "Prefer the Solo tier before escalating to a full swarm",
                "user-preference",
                source_file="blackboard/01-user-memory.md",
                memory_id="pref-001",
                tags=["tier", "solo"],
                run_slug="run-alpha",
            )
        self.assertEqual(rec.memory_id, "run-alpha/pref-001")
        self.assertEqual(rec.run_slug, "run-alpha")
        self.assertEqual(rec.tags, ["tier", "solo"])
        self.assertEqual(len(mem.index), 1)


class GeneratorsDistrustTheirOwnInputs(unittest.TestCase):
    def test_workflow_name_cannot_escape_the_repo(self):
        """`name:` frontmatter went straight into a path join."""
        sync = load("sync_fix", ROOT / "scripts" / "sync_runtime_assets.py")
        with tempfile.TemporaryDirectory() as tmp:
            wf = Path(tmp) / "prompts" / "workflows"
            wf.mkdir(parents=True)
            (wf / "evil.md").write_text(
                "---\nname: ../../../../tmp/PWNED\ndescription: probe\n---\nbody\n",
                encoding="utf-8",
            )
            with self.assertRaises(SystemExit) as ctx:
                sync.sync(Path(tmp))
            self.assertIn("unsafe workflow name", str(ctx.exception))

    def test_ordinary_workflow_names_still_load(self):
        sync = load("sync_ok", ROOT / "scripts" / "sync_runtime_assets.py")
        for good in ("kickoff", "ui-review", "new_run", "plan.v2"):
            with self.subTest(name=good):
                self.assertEqual(sync.safe_workflow_name(good, Path("x")), good)

    def test_plan_schema_downgrade_is_refused(self):
        """Rewriting plan/v2 -> plan/v1 disabled every hardening check."""
        plan_mod = load("plan_fix", ROOT / "scripts" / "plan_artifact.py")
        plan = {
            "schema": "plan/v2",
            "id": "P1",
            "created": "2026-07-25",
            "goal": "Ship the thing",
            "status": "planned",
            "steps": [{"id": "s1", "role": "builder", "task": "do it"}],
            "instructions": [
                {"ref": "user-1", "trust": "authoritative", "digest": "abc"}
            ],
        }
        plan["goal_anchor"] = plan_mod.compute_goal_anchor(plan)
        downgraded = dict(plan, schema="plan/v1")
        probs = plan_mod.validate(downgraded)
        self.assertTrue(
            any("schema downgrade" in p for p in probs),
            f"plan/v2 -> plan/v1 downgrade was accepted: {probs}",
        )

    def test_goal_anchor_binds_the_schema(self):
        plan_mod = load("plan_anchor", ROOT / "scripts" / "plan_artifact.py")
        base = {
            "id": "P1",
            "created": "2026-07-25",
            "goal": "Ship the thing",
            "instructions": [],
        }
        self.assertNotEqual(
            plan_mod.compute_goal_anchor(dict(base, schema="plan/v2")),
            plan_mod.compute_goal_anchor(dict(base, schema="plan/v1")),
            "the anchor does not cover `schema`, so a downgrade keeps matching",
        )


class ScaffolderWorksFromAFreshClone(unittest.TestCase):
    def test_new_run_finds_a_template_and_includes_the_goal_roster(self):
        """`blackboard/` is gitignored; a clone has only blackboard-template/.

        And goal_guard compares against `21-agent-roster.md`, which lived only
        in the addon dir and never reached a scaffolded run.
        """
        new_run = load("new_run_fix", ENGINE_MEMORY / "new_run.py")
        self.assertTrue(
            os.path.isdir(new_run.BLACKBOARD),
            f"no blackboard template resolved (looked under {new_run.ROOT})",
        )
        names = new_run.numbered_templates("solo")
        self.assertIn(
            "21-agent-roster.md",
            names,
            "a solo run scaffolds without the roster goal_guard reads",
        )

    def test_installer_does_not_downgrade_build_graph(self):
        """--force copied the older full-engine helper over the canonical one."""
        with tempfile.TemporaryDirectory() as tmp:
            for args in ([], ["--force"]):
                subprocess.run(
                    [sys.executable, str(ROOT / "scripts" / "setup_project_os.py"),
                     "--target", tmp] + args,
                    capture_output=True, text=True, check=False,
                )
            installed = Path(tmp) / "memory" / "build_graph.py"
            self.assertTrue(installed.is_file(), "installer did not deliver build_graph.py")
            self.assertEqual(
                installed.read_text(encoding="utf-8"),
                (ROOT / "memory" / "build_graph.py").read_text(encoding="utf-8"),
                "installer replaced the canonical build_graph.py with the "
                "older full-engine helper",
            )


class AdoptVerifiesItsOwnWrite(unittest.TestCase):
    def test_two_adopted_projects_get_different_goal_hashes(self):
        """The dead stub regex left every adopted run on one placeholder goal,
        so they all hashed identically and goal_guard could never see drift."""
        proc = subprocess.run(
            [sys.executable, str(ENGINE_MEMORY / "adopt_project.py"), "--selftest"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(
            proc.returncode, 0,
            f"adopt_project selftest failed:\n{proc.stdout}\n{proc.stderr}",
        )

    def test_stub_writer_replaces_the_canonical_goal_line(self):
        adopt = load("adopt_fix", ENGINE_MEMORY / "adopt_project.py")
        goal_guard = load("gg_fix", ENGINE_MEMORY / "goal_guard.py")
        template = (
            "# Project Goal\n\n## Canonical Goal\n\n"
            "<!-- ONE sentence. -->\n\n"
            "Replace this line with the one-sentence canonical goal.\n\n"
            "## Summary\n\nDescribe it.\n"
        )
        out = adopt._replace_canonical_goal(template, "Track widgets locally", "README.md")
        self.assertEqual(goal_guard.canonical_goal(out), "Track widgets locally")

    def test_stub_writer_adds_the_anchor_when_it_is_missing(self):
        adopt = load("adopt_anchor", ENGINE_MEMORY / "adopt_project.py")
        goal_guard = load("gg_anchor", ENGINE_MEMORY / "goal_guard.py")
        out = adopt._replace_canonical_goal(
            "# Project Goal\n\n## Summary\n\nSomething.\n", "Reconcile ledgers", "README.md"
        )
        self.assertEqual(goal_guard.canonical_goal(out), "Reconcile ledgers")


class Round2AdversarialBypasses(unittest.TestCase):
    """Every case below defeated the FIRST version of that day's fix.

    An independent skeptic pass broke 6 of 8 fix clusters. These are its
    reproductions, kept so the second version cannot regress to the first.
    """

    def test_tier_lock_ignores_a_rejected_option_block_above_the_real_one(self):
        v = load("vr_r2", ENGINE_MEMORY / "validate_run.py")
        doc = (
            "## Options Considered\n### Option B (rejected)\n"
            "Tier: Full Swarm\nLocked: yes\n\n"
            "## Current Execution Level\nTier: Solo\nLocked: no\n"
        )
        self.assertFalse(
            v._tier_locked(doc),
            "a rejected-option 'Locked: yes' above the real 'Locked: no' "
            "satisfied the gate (first-match-wins)",
        )

    def test_manifest_table_with_only_header_and_alignment_row_is_empty(self):
        v = load("vr_r2b", ENGINE_MEMORY / "validate_run.py")
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "14-artifact-manifest.md").write_text(
                "# Artifact Manifest\n\n| path | what |\n| :--- | ---: |\n",
                encoding="utf-8",
            )
            self.assertFalse(
                v._has_manifest(tmp),
                "a table with a header + alignment separator and NO data rows "
                "counted as a populated manifest",
            )

    def test_manifest_denial_wording_omitted_and_skipped(self):
        v = load("vr_r2c", ENGINE_MEMORY / "validate_run.py")
        for wording in (
            "Artifact manifest: omitted for this run.",
            "Artifact manifest skipped this run to save time.",
        ):
            with tempfile.TemporaryDirectory() as tmp:
                (Path(tmp) / "13-delivery-report.md").write_text(
                    f"## Notes\n{wording}\n- item one\n- item two\n", encoding="utf-8"
                )
                with self.subTest(wording=wording[:30]):
                    self.assertFalse(
                        v._has_manifest(tmp),
                        "an explicit denial still passed because unrelated "
                        "bullets existed later in the file",
                    )

    def test_annotated_rejection_cells_are_still_dropped(self):
        h = load("harvest_r2", ROOT / "scripts" / "harvest.py")
        for cell in ("Rejected (see note)", "REJECTED — superseded", "**Rejected**",
                     "REJECTED ", "private only"):
            with self.subTest(cell=cell):
                self.assertIsNotNone(
                    h.REJECT_ROW.match(cell),
                    f"annotated rejection harvested through as a lesson: {cell!r}",
                )

    @needs_numpy
    def test_bare_modern_credentials_are_detected(self):
        """Pasted with no `KEY=` label — the realistic accidental-leak shape."""
        osvec = load("osvec_r2", ENGINE_MEMORY / "osvec_adapter.py")
        for name, text in {
            "huggingface": "clone with hf_" + "A" * 34 + " as the auth",
            "vercel": "deploy hook uses vercel_" + "b" * 24 + " in the script",
            "linear": "automation calls lin_api_" + "C" * 36 + " directly",
            "notion": "token ntn_" + "d" * 44 + " is in the config",
            "sentry_dsn": "endpoint is https://" + "a" * 32 + "@o123456.ingest.sentry.io/1234567",
            "azure": "AccountKey = " + "Zm9vYmFy" * 6 + "==",
            "slack_webhook": "post to https://hooks.slack.com/services/T00000000/B00000000/" + "X" * 24,
        }.items():
            with self.subTest(kind=name):
                self.assertIsNotNone(
                    osvec.looks_like_secret(text), f"{name} passed the gate"
                )

    def test_plan_downgrade_survives_stripping_both_hardened_fields(self):
        """The bypass: delete goal_anchor AND instructions, then set plan/v1."""
        plan_mod = load("plan_r2", ROOT / "scripts" / "plan_artifact.py")
        approved = {
            "schema": "plan/v2",
            "id": "P1",
            "created": "2026-07-25",
            "goal": "Ship it",
            "status": "approved",
            "approved_at": "2026-07-25T10:00:00",
            "approved_schema": "plan/v2",
            "steps": [{"id": "s1", "role": "builder", "task": "build",
                       "depends_on": [], "outputs": []}],
        }
        attack = dict(approved)
        attack.pop("goal_anchor", None)
        attack.pop("instructions", None)
        attack["schema"] = "plan/v1"
        probs = plan_mod.validate(attack)
        self.assertTrue(
            any("after approval" in p for p in probs),
            f"stripped-and-downgraded plan validated clean: {probs}",
        )

    def test_a_single_user_need_lookalike_does_not_satisfy_the_gate(self):
        """One decoy named 'User Needs Documented' shipped a Pass verdict.

        The User Need Gate lives only in the private engine, so this test is
        opt-in via PROJECT_OS_PRIVATE_ROOT and skips everywhere else. It must
        never hardcode a developer's home directory -- the first version of
        this test did, and put an absolute personal path into a public repo.
        """
        private_root = os.environ.get("PROJECT_OS_PRIVATE_ROOT")
        if not private_root:
            self.skipTest("set PROJECT_OS_PRIVATE_ROOT to run this check")
        src = Path(private_root) / "memory" / "score_rubric.py"
        if not src.is_file():
            self.skipTest(f"no score_rubric.py under {private_root}")
        body = src.read_text(encoding="utf-8")
        self.assertIn(
            'CANONICAL = "user need gate"', body,
            "the gate no longer requires the canonical criterion name",
        )
        self.assertIn(
            "decoys and not ung_matches", body,
            "a single look-alike criterion can still satisfy the gate",
        )

    def test_sync_refuses_to_write_through_a_symlink(self):
        """A legit name + a planted symlink still redirected the write."""
        sync = load("sync_r2", ROOT / "scripts" / "sync_runtime_assets.py")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "prompts" / "workflows").mkdir(parents=True)
            (root / "prompts" / "workflows" / "normal.md").write_text(
                "---\nname: normal-workflow\ndescription: legit\n---\nbody\n",
                encoding="utf-8",
            )
            cmds = root / "addons" / "full-engine" / "staged" / "commands"
            cmds.mkdir(parents=True)
            evil = Path(tmp) / "evil_target"
            evil.write_text("", encoding="utf-8")
            (cmds / "normal-workflow.md").symlink_to(evil)
            with self.assertRaises(SystemExit) as ctx:
                sync.sync(root)
            self.assertIn("symlink", str(ctx.exception).lower())
            self.assertEqual(evil.read_text(encoding="utf-8"), "",
                             "content was written through the symlink")

    def test_goal_guard_skips_whole_multiline_comment_blocks(self):
        """The template ships a 4-line HTML comment.

        canonical_goal() skipped only lines STARTING with '<!--', so line 2 of
        that comment was returned as the goal: every project hashed the same
        sentence and editing the real goal did not change the hash at all.
        Caught only by running the documented install flow end-to-end -- the
        unit selftest used a one-line comment and passed throughout.
        """
        gg = load("gg_comment", ENGINE_MEMORY / "goal_guard.py")
        text = (
            "# Project Goal\n\n## Canonical Goal\n\n"
            "<!-- ONE sentence. This exact line is hashed by goal_guard.py and\n"
            "     the hash is recorded in 21-agent-roster.md, so wave-boundary\n"
            "     drift checks can tell whether the goal changed. -->\n\n"
            "Build a credit-card tracker that flags overspend.\n\n"
            "## Summary\n\nDescribe it.\n"
        )
        self.assertEqual(
            gg.canonical_goal(text),
            "Build a credit-card tracker that flags overspend.",
            "a line from inside the multi-line comment was read as the goal",
        )

    def test_goal_guard_refuses_the_untouched_template_placeholder(self):
        """A placeholder hashes identically for every run -- same blindness."""
        gg = load("gg_placeholder", ENGINE_MEMORY / "goal_guard.py")
        for placeholder in (
            "Replace this line with the one-sentence canonical goal.",
            "TODO: write the one-sentence canonical goal.",
            "TBD",
        ):
            text = "# Project Goal\n\n## Canonical Goal\n\n%s\n" % placeholder
            with self.subTest(placeholder=placeholder[:30]):
                with self.assertRaises(gg.GoalAnchorMissing):
                    gg.canonical_goal(text)

    def test_shipped_template_produces_a_usable_goal_after_editing(self):
        """Guard the REAL template, not a hand-written fixture."""
        gg = load("gg_tmpl", ENGINE_MEMORY / "goal_guard.py")
        template = (ROOT / "blackboard-template" / "00-project-goal.md").read_text(
            encoding="utf-8"
        )
        with self.assertRaises(gg.GoalAnchorMissing):
            gg.canonical_goal(template)  # untouched template must refuse
        edited = template.replace(
            "Replace this line with the one-sentence canonical goal.",
            "Build a credit-card tracker that flags overspend.",
        )
        self.assertEqual(
            gg.canonical_goal(edited),
            "Build a credit-card tracker that flags overspend.",
        )
        other = template.replace(
            "Replace this line with the one-sentence canonical goal.",
            "Build a budgeting app for freelancers.",
        )
        self.assertNotEqual(
            gg.goal_hash(gg.canonical_goal(edited)),
            gg.goal_hash(gg.canonical_goal(other)),
            "two different goals hash identically -- drift is undetectable",
        )

    def test_selftests_do_not_touch_the_live_runs_index(self):
        """Both selftests scaffolded into the REAL runs/ and rewrote INDEX.md.

        That made the suite intermittently RED under concurrency (two runs
        collided on the same slug) and left debris in a user's live runs dir.
        Caught only by running suites in parallel.
        """
        index = ROOT / "runs" / "INDEX.md"
        before = index.read_text(encoding="utf-8") if index.is_file() else None
        for script in ("new_run.py", "adopt_project.py"):
            proc = subprocess.run(
                [sys.executable, str(ENGINE_MEMORY / script), "--selftest"],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(proc.returncode, 0,
                             f"{script} selftest failed: {proc.stderr[-400:]}")
        if before is not None:
            self.assertEqual(
                index.read_text(encoding="utf-8"), before,
                "a selftest rewrote the live runs/INDEX.md",
            )
        stray = [p.name for p in (ROOT / "runs").glob("*selftest*")] if (ROOT / "runs").is_dir() else []
        self.assertEqual(stray, [], f"selftest left debris in live runs/: {stray}")

    def test_index_reports_run_dirs_it_could_not_index(self):
        """A run dir with no goal file used to vanish from INDEX silently."""
        new_run = load("nr_skip", ENGINE_MEMORY / "new_run.py")
        with new_run.isolated_root():
            os.makedirs(os.path.join(new_run.RUNS, "ghost-run"), exist_ok=True)
            skipped = new_run.regenerate_index()
            body = Path(new_run.INDEX).read_text(encoding="utf-8")
        self.assertIn("ghost-run", skipped)
        self.assertIn("ghost-run", body,
                      "an unindexable run directory is invisible in INDEX.md")

    def test_index_goal_skips_multiline_comment_blocks(self):
        """Same comment bug as goal_guard, in the INDEX summariser."""
        new_run = load("nr_goal", ENGINE_MEMORY / "new_run.py")
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "00-project-goal.md"
            p.write_text(
                "# Project Goal\n\n## Canonical Goal\n\n"
                "<!-- ONE sentence. This exact line is hashed by goal_guard.py\n"
                "     and recorded in 21-agent-roster.md. -->\n\n"
                "Build a credit-card tracker that flags overspend.\n",
                encoding="utf-8",
            )
            goal, _tier = new_run._read_goal(str(p))
        self.assertEqual(goal, "Build a credit-card tracker that flags overspend.")

    PLACEHOLDER = "Replace this line with the one-sentence canonical goal."
    GOAL_TEMPLATE = (
        "# Project Goal\n\n## Canonical Goal\n\n"
        "<!-- ONE sentence. This exact line is hashed by goal_guard.py. -->\n\n"
        + PLACEHOLDER + "\n\n"
        "## Success Criteria\n\n- [ ] TBD\n"
    )
    GOAL = "Track widgets locally"

    def _write_stub(self, tmp, mangle=None):
        """Run adopt's goal-stub writer in `tmp`, optionally corrupting the write.

        Returns the on-disk text. Raises whatever _write_goal_stub raises.
        """
        adopt = load("adopt_disk", ENGINE_MEMORY / "adopt_project.py")
        run_dir = os.path.join(tmp, "run")
        os.makedirs(run_dir, exist_ok=True)
        goal_path = os.path.join(run_dir, "00-project-goal.md")
        with open(goal_path, "w", encoding="utf-8") as fh:
            fh.write(self.GOAL_TEMPLATE)
        if mangle is not None:
            real_open = open

            def patched_open(path, mode="r", *args, **kwargs):
                fh = real_open(path, mode, *args, **kwargs)
                if "w" in mode or "a" in mode:
                    return _MangledWriter(fh, mangle)
                return fh

            adopt.open = patched_open
        try:
            adopt._write_goal_stub(run_dir, self.GOAL, "README.md", tmp)
        finally:
            if mangle is not None:
                del adopt.open
        with open(goal_path, encoding="utf-8") as fh:
            return fh.read()

    def test_adopt_verifies_the_file_on_disk_not_its_own_string(self):
        """A short/corrupted write must not report a successful adoption.

        The previous version of this test only grepped the verify block for
        'fh.read()'. Re-parsing the in-memory string kept that substring on a
        now-dead line, so the defect it names came back green (audit
        2026-07-26). These cases drive the writer instead.
        """
        goal = self.GOAL
        PLACEHOLDER = self.PLACEHOLDER
        cases = {
            # A truncated write: goal_guard cannot parse a goal at all.
            "short write": lambda data: data[:40],
            # A corrupted write: parses fine, but says something else.
            "wrong goal": lambda data: data.replace(
                goal, "A goal nobody inferred"
            ),
            # The goal sentence is STILL IN THE FILE, just not under the
            # '## Canonical Goal' anchor goal_guard reads -- what a regressed
            # _replace_canonical_goal that appends instead of replaces would
            # produce. Only a verify that re-runs the real parser catches this;
            # a cruder `if goal not in on_disk` check reports success and kept
            # all 46 tests of this module green (adversarial verify
            # 2026-07-26).
            "goal moved out of the anchor": lambda data: data.replace(
                goal + "\n", PLACEHOLDER + "\n", 1
            ) + "\n<!-- stray copy -->\n" + goal + "\n",
        }
        for label, mangle in cases.items():
            with self.subTest(case=label):
                with tempfile.TemporaryDirectory() as tmp:
                    with self.assertRaises(SystemExit) as ctx:
                        self._write_stub(tmp, mangle)
                self.assertIn(
                    "adopt:", str(ctx.exception),
                    "adopt reported success over a bad disk write",
                )

    def test_adopt_accepts_a_write_that_actually_landed(self):
        """The disk check must not reject a correct write."""
        goal_guard = load("gg_disk", ENGINE_MEMORY / "goal_guard.py")
        with tempfile.TemporaryDirectory() as tmp:
            on_disk = self._write_stub(tmp)
        self.assertEqual(goal_guard.canonical_goal(on_disk), self.GOAL)


if __name__ == "__main__":
    unittest.main()
