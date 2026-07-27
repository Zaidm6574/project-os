"""Documentation claims, checked against observed behaviour.

Every assertion in this module reads a claim out of a doc AT RUNTIME and then
compares it to something the code actually does -- a real install, the real
probe loop in install.sh, the real output of the real plan gate. None of it
compares a hardcoded copy of a doc against another hardcoded copy, because that
is exactly how the drift these tests exist to catch got shipped:

  * README's "check it yourself" line advertised 438 tests while the suite ran 508
  * README's "Project structure after install" tree omitted `addons/`, which the
    installer has always created
  * README's Python-probe sentence listed neither `python3.14` nor the real order
  * README's privacy gate is an `rg` command while Requirements never named ripgrep
  * README pointed at a launchd snippet "in the file header" of os_nightly.py,
    where no snippet lives
  * README then pointed at blackboard/22-automation-log.md -- true of the
    shipped template, false of any project that had run the heartbeat once,
    because os_nightly.py rewrote the file and dropped the snippet
  * README's claims table said a plan that "fakes its verification step" is
    rejected; the gate rejects EMPTY/PLACEHOLDER verification and cannot know
    whether a plausible-looking method ever ran
  * check_optional_tools printed "Verified" for OSVec off a bare Path.exists()

Each test therefore fails when the doc drifts again, in either direction:
changing the behaviour without the doc fails too.
"""

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
README = ROOT / "README.md"
LADDER = ROOT / "docs" / "verifier-ladder.md"
MEMORY_README = ROOT / "memory" / "README.md"
INSTALL_SH = ROOT / "install.sh"
SETUP = ROOT / "scripts" / "setup_project_os.py"
TOOL_CHECK = ROOT / "scripts" / "check_optional_tools.py"

POSIX_BASELINE = frozenset(
    {"git", "grep", "egrep", "find", "ls", "sed", "awk", "cat", "python3", "printf", "echo", "cd"}
)


def read(path):
    return path.read_text(encoding="utf-8")


def load_module(path, name):
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def section(text, heading):
    """Body of a markdown section, from its heading to the next same-level one."""
    lines = text.split("\n")
    level = heading.split(" ", 1)[0]
    out = []
    inside = False
    for line in lines:
        if line.strip() == heading:
            inside = True
            continue
        if inside and line.startswith(level + " "):
            break
        if inside:
            out.append(line)
    return "\n".join(out)


def fenced_blocks(text):
    """Every fenced code block in ``text`` as (info_string, body)."""
    blocks = []
    fence = None
    body = []
    info = ""
    for line in text.split("\n"):
        stripped = line.strip()
        if fence is None:
            if stripped.startswith("```"):
                fence = "```"
                info = stripped[3:].strip()
                body = []
            continue
        if stripped == fence:
            blocks.append((info, "\n".join(body)))
            fence = None
            continue
        body.append(line)
    return blocks


def paragraph_with(text, *needles):
    """The first line containing every needle."""
    for line in text.split("\n"):
        if all(n in line for n in needles):
            return line
    return ""


class ReadmeInstallTreeTests(unittest.TestCase):
    """The documented tree must be what a real install produces."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.target = Path(cls._tmp.name) / "installed"
        done = subprocess.run(
            [sys.executable, str(SETUP), "--target", str(cls.target)],
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert done.returncode == 0, "installer failed:\n%s" % done.stderr
        cls.created = {p.name for p in cls.target.iterdir()}

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def documented_entries(self):
        body = section(read(README), "## Project structure after install")
        blocks = [b for _, b in fenced_blocks(body)]
        self.assertTrue(blocks, "no fenced tree under '## Project structure after install'")
        entries = set()
        for line in blocks[0].split("\n"):
            if not line.startswith("  ") or not line.strip():
                continue  # the root `my-new-project/` line
            name = line.strip().split("#", 1)[0].strip().rstrip("/")
            if name:
                entries.add(name)
        return entries

    def test_the_tree_parses_at_all(self):
        """Guards the guard: a parser that finds nothing proves nothing."""
        self.assertGreater(len(self.documented_entries()), 5)

    def test_documented_tree_equals_what_the_installer_creates(self):
        documented = self.documented_entries()
        missing = sorted(self.created - documented)
        invented = sorted(documented - self.created)
        self.assertEqual(
            [],
            missing,
            "the installer creates these, and README's 'Project structure after "
            "install' tree does not list them: %s" % missing,
        )
        self.assertEqual(
            [],
            invented,
            "README's install tree lists entries a real install never creates: %s" % invented,
        )

    def test_memory_readme_documents_every_memory_tool_the_installer_ships(self):
        """`memory/code_graph.py` shipped everywhere and was documented nowhere."""
        shipped = sorted(p.name for p in (self.target / "memory").glob("*.py"))
        self.assertTrue(shipped, "install delivered no memory/*.py -- extraction broke")
        doc = read(MEMORY_README)
        for tool in shipped:
            with self.subTest(tool=tool):
                self.assertIn(
                    tool,
                    doc,
                    "the installer ships memory/%s into every project, but "
                    "memory/README.md never mentions it" % tool,
                )


class ReadmePythonProbeTests(unittest.TestCase):
    """README's interpreter-probe sentence vs install.sh's actual loop."""

    def documented_probe_order(self):
        line = paragraph_with(read(README), "The installer probes these names")
        self.assertTrue(line, "README no longer describes how Python is probed")
        for token in re.findall(r"`([^`]+)`", line):
            names = token.split()
            if len(names) > 1 and all(re.fullmatch(r"python[0-9.]*", n) for n in names):
                return names
        return []

    def actual_probe_order(self):
        match = re.search(r"for candidate in ([^;\n]+); do", read(INSTALL_SH))
        self.assertIsNotNone(match, "install.sh no longer has a probe loop this test can read")
        return match.group(1).split()

    def test_probe_lists_are_nonempty(self):
        """Guards the guard."""
        self.assertGreater(len(self.actual_probe_order()), 2)
        self.assertGreater(len(self.documented_probe_order()), 2)

    def test_readme_lists_every_probed_interpreter_in_order(self):
        self.assertEqual(
            self.actual_probe_order(),
            self.documented_probe_order(),
            "README's probe sentence and install.sh's `for candidate in ...` "
            "loop disagree. Order is load-bearing: the loop takes the FIRST "
            "interpreter reporting 3.10+, so a name's position decides which "
            "Python a user gets.",
        )


class ReadmePlanGateTests(unittest.TestCase):
    """The README's plan-gate block, executed, must say what README says.

    plan_artifact.py resolves blackboard/plans relative to its own location, so
    this runs an INSTALLED copy in a temp dir -- the live blackboard is never
    touched.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.target = Path(cls._tmp.name) / "installed"
        done = subprocess.run(
            [sys.executable, str(SETUP), "--target", str(cls.target)],
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert done.returncode == 0, "installer failed:\n%s" % done.stderr

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def readme_block(self):
        for info, body in fenced_blocks(read(README)):
            if info == "bash" and "plan_artifact.py create" in body:
                return body
        self.fail("README no longer shows a plan_artifact.py create block")

    def sham_steps(self):
        body = self.readme_block()
        payload = re.search(r"printf\s+'(.+?)'\s*>", body, re.S)
        self.assertIsNotNone(payload, "could not read the steps JSON out of README's printf")
        return json.loads(payload.group(1))

    def expected_output_lines(self):
        """The `# ...` comment lines README prints as the gate's answer."""
        lines = []
        seen_command = False
        for line in self.readme_block().split("\n"):
            stripped = line.strip()
            if "plan_artifact.py create" in stripped:
                seen_command = True
                continue
            if seen_command and stripped.startswith("#"):
                text = stripped.lstrip("#").strip()
                if text:
                    lines.append(text)
        return lines

    def create(self, steps, name):
        """Create a plan under a goal unique to ``name``.

        Plan ids are derived from the date plus the goal, and `create` refuses
        to overwrite an existing id -- sharing one goal across tests would make
        the second call fail for a reason that has nothing to do with the gate.
        """
        steps_file = self.target / (name + ".json")
        steps_file.write_text(json.dumps(steps), encoding="utf-8")
        return subprocess.run(
            [
                sys.executable,
                str(self.target / "scripts" / "plan_artifact.py"),
                "create",
                "--goal",
                "demo %s" % name,
                "--steps-file",
                str(steps_file),
            ],
            capture_output=True,
            text=True,
            cwd=str(self.target),
            timeout=120,
        )

    def test_readme_quotes_a_nonempty_rejection(self):
        """Guards the guard."""
        self.assertTrue(self.expected_output_lines())
        self.assertEqual(2, len(self.sham_steps()))

    def test_placeholder_verification_is_rejected_with_the_quoted_reason(self):
        done = self.create(self.sham_steps(), "sham")
        combined = done.stdout + done.stderr
        self.assertNotEqual(0, done.returncode, "README's sham plan was ACCEPTED:\n" + combined)
        for expected in self.expected_output_lines():
            with self.subTest(line=expected):
                self.assertIn(
                    expected,
                    combined,
                    "README quotes a rejection the gate no longer prints.\nGot:\n" + combined,
                )

    def test_near_miss_a_real_verification_is_accepted(self):
        """The adjacent legitimate case: same plan, real method/expected.

        Without this, a gate that rejected EVERYTHING would pass the test above
        while making the tool useless -- and README's "swap the placeholders and
        it succeeds" sentence would be false.
        """
        steps = self.sham_steps()
        for step in steps:
            if "verification" in step:
                step["verification"] = {
                    "method": "run python3 -m unittest tests.test_bb_lock_hardening",
                    "expected": "exit code 0, no failures reported",
                }
        done = self.create(steps, "real")
        combined = done.stdout + done.stderr
        self.assertEqual(0, done.returncode, "a plan with real verification was rejected:\n" + combined)
        self.assertTrue(
            list((self.target / "blackboard" / "plans").glob("*.json")),
            "create reported success but wrote no plan artifact",
        )

    def test_readme_does_not_claim_the_gate_proves_the_check_ran(self):
        """A plausible-but-never-run method is ACCEPTED -- observed, then documented.

        The old claim ("a plan that fakes its verification step is rejected")
        was falsified by exactly this input.
        """
        steps = self.sham_steps()
        for step in steps:
            if "verification" in step:
                step["verification"] = {
                    "method": "I already ran the full suite",
                    "expected": "everything passed",
                }
        done = self.create(steps, "unrun")
        self.assertEqual(
            0,
            done.returncode,
            "behaviour changed: the gate now rejects unrun-but-plausible "
            "verification. README's paragraph under the plan-gate block says it "
            "cannot -- update the doc.",
        )
        claims = section(read(README), "## Check these claims yourself")
        self.assertIn(
            "never proof the check ran",
            claims,
            "README's claims table must keep saying the gate is structural: it "
            "accepts a verification method that was never executed.",
        )


class ReadmeTestCountTests(unittest.TestCase):
    """A count in the README must match the count in the suite."""

    @classmethod
    def setUpClass(cls):
        code = (
            "import unittest,sys\n"
            "n=0;bad=[]\n"
            "def walk(s):\n"
            "    global n\n"
            "    for t in s:\n"
            "        if isinstance(t, unittest.TestSuite): walk(t)\n"
            "        else:\n"
            "            n+=1\n"
            "            if '_FailedTest' in type(t).__name__: bad.append(str(t))\n"
            "walk(unittest.TestLoader().discover('tests'))\n"
            "print(n);print('|'.join(bad))\n"
        )
        done = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            timeout=300,
        )
        assert done.returncode == 0, "discovery failed:\n%s" % done.stderr
        out = done.stdout.strip().split("\n")
        cls.count = int(out[0])
        cls.broken = out[1] if len(out) > 1 else ""

    def test_discovery_found_a_real_suite(self):
        """Guards the guard: a count of 0 would make every claim below vacuous."""
        self.assertGreater(self.count, 100)

    def test_readme_test_counts_match_the_real_suite(self):
        text = read(README)
        floors = [int(n) for n in re.findall(r"(\d+)\+\s+tests", text)]
        exact = [int(n) for n in re.findall(r"(?<![\d+])\b(\d+)\s+tests\b", text)]
        self.assertTrue(
            floors or exact,
            "README no longer states a test count in any form this guard "
            "understands ('N tests' or 'N+ tests'). If the phrasing changed on "
            "purpose, update this test; do not let an unchecked number back in.",
        )
        # A module that fails to import contributes one _FailedTest instead of
        # its real cases, so it deflates the count. Say so in the message rather
        # than failing separately: the broken module is its own test's problem.
        note = (" (note: these modules failed to import, deflating the count: %s)" % self.broken) if self.broken else ""
        for floor in floors:
            self.assertGreaterEqual(
                self.count,
                floor,
                "README advertises %d+ tests; discovery finds %d%s" % (floor, self.count, note),
            )
        for n in exact:
            self.assertEqual(
                self.count,
                n,
                "README advertises exactly %d tests; discovery finds %d. Prefer "
                "an 'N+ tests' floor -- an exact count rots on the next commit."
                % (n, self.count),
            )


class ReadmePrivacyGateTests(unittest.TestCase):
    """Every non-POSIX tool the pre-publish check invokes must be a stated requirement."""

    def privacy_commands(self):
        body = section(read(README), "## Privacy rules")
        blocks = [b for _, b in fenced_blocks(body)]
        self.assertTrue(blocks, "no runnable privacy check under '## Privacy rules'")
        commands = []
        for line in blocks[0].split("\n"):
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                commands.append(stripped.split()[0])
        return commands

    def test_privacy_block_parses(self):
        """Guards the guard."""
        self.assertTrue(self.privacy_commands())

    def test_tools_the_privacy_gate_needs_are_listed_under_requirements(self):
        requirements = paragraph_with(read(README), "Requirements:")
        self.assertTrue(requirements, "README no longer states its requirements")
        for command in self.privacy_commands():
            if command in POSIX_BASELINE:
                continue
            with self.subTest(command=command):
                self.assertIn(
                    command,
                    requirements,
                    "the documented pre-publish privacy check runs `%s`, which "
                    "is not POSIX-baseline and is not named in Requirements -- "
                    "a stranger following the README cannot run the gate" % command,
                )


class ReadmeLaunchdPointerTests(unittest.TestCase):
    """README's launchd pointer must name a file that actually holds the snippet."""

    MARKERS = ("LaunchAgents", "StartCalendarInterval", "launchctl")

    # The doc must point at the snippet in a form a machine can follow. Prose
    # like "the file header includes a macOS launchd snippet" reads fine and
    # named a file with no snippet in it for months; a backticked path
    # immediately before the claim can be resolved and checked.
    CLAIM = re.compile(r"`([^`\n]+)`[^`\n]{0,40}?carries (?:a|the) macOS launchd snippet")

    def candidate_paths(self):
        line = paragraph_with(read(README), "os_nightly.py", "launchd")
        self.assertTrue(line, "README no longer says where the launchd snippet lives")
        paths = self.CLAIM.findall(line)
        self.assertTrue(
            paths,
            "README's launchd sentence does not name the snippet's file in a "
            "checkable form. Write it as: `<path>` ... carries a macOS launchd "
            "snippet. Got: " + line,
        )
        return paths

    def resolve(self, ref):
        """A doc path names the INSTALLED layout; map it back to the shipped source.

        The template is checked FIRST: this machine may have its own untracked
        `blackboard/`, and a claim about what users receive must be checked
        against what the installer ships, not against local state.
        """
        if ref.startswith("blackboard/"):
            template = ROOT / "blackboard-template" / ref.split("/", 1)[1]
            if template.is_file():
                return template
        direct = ROOT / ref
        if direct.is_file():
            return direct
        return None

    def install(self):
        """A real install plus an isolated HOME, so nothing here reads local state."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        target = Path(tmp.name) / "installed"
        home = Path(tmp.name) / "home"
        home.mkdir()
        done = subprocess.run(
            [sys.executable, str(SETUP), "--target", str(target)],
            capture_output=True,
            text=True,
            timeout=300,
        )
        self.assertEqual(0, done.returncode, "installer failed:\n%s" % done.stderr)
        return target, home

    def holders_under(self, root):
        """The paths README names that really carry the snippet inside ``root``."""
        holders = []
        for ref in self.candidate_paths():
            path = root / ref
            if path.is_file() and all(m in read(path) for m in self.MARKERS):
                holders.append(ref)
        return holders

    def test_the_pointer_line_names_paths(self):
        """Guards the guard."""
        self.assertTrue(self.candidate_paths())

    def test_the_snippet_survives_the_heartbeat_that_rewrites_the_log(self):
        """The claim must hold on a LIVE project, not just on the pristine template.

        `resolve()` above maps `blackboard/...` back to `blackboard-template/...`,
        so every other assertion in this class inspects a file the runtime never
        opens. `os_nightly.py` rewrites the real log in "w" mode on every run; it
        used to substitute its own HEADER for the whole preamble, which deleted
        the launchd plist -- the installed project's only copy of its own
        scheduling instructions -- on the very first heartbeat, while this class
        stayed green forever.
        """
        target, home = self.install()
        self.assertTrue(
            self.holders_under(target),
            "guards the guard: a real install does not even ship the snippet at "
            "%s, so this test could never observe it being lost" % self.candidate_paths(),
        )

        log = target / "blackboard" / "22-automation-log.md"
        before = read(log)
        env = dict(os.environ)
        env["HOME"] = str(home)
        env["BB_LOCK_DIR"] = str(home / "locks")
        done = subprocess.run(
            [sys.executable, str(target / "scripts" / "os_nightly.py")],
            cwd=str(target),
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        after = read(log)
        # Guards the guard again: if the heartbeat had refused to run, or written
        # somewhere else, the markers would survive for a reason that proves
        # nothing about the rewrite this test exists to police.
        self.assertIn(
            "gauge:",
            after,
            "the heartbeat never wrote an entry, so the rewrite was never "
            "exercised:\n%s%s" % (done.stdout, done.stderr),
        )
        self.assertNotEqual(
            before, after, "the heartbeat left the log byte-identical -- nothing was rewritten"
        )

        self.assertTrue(
            self.holders_under(target),
            "one run of scripts/os_nightly.py deleted the launchd snippet (%s) "
            "from %s. README still tells the user to read it there, and the "
            "installed project keeps no other copy."
            % (", ".join(self.MARKERS), self.candidate_paths()),
        )

    def test_the_named_file_contains_the_launchd_snippet(self):
        holders = []
        for ref in self.candidate_paths():
            resolved = self.resolve(ref)
            if resolved is None:
                continue
            text = read(resolved)
            if all(marker in text for marker in self.MARKERS):
                holders.append(ref)
        self.assertTrue(
            holders,
            "README's launchd sentence points at %s, and none of those files "
            "contains a launchd snippet (%s). Point at the file that does."
            % (self.candidate_paths(), ", ".join(self.MARKERS)),
        )


class DocPathClaimTests(unittest.TestCase):
    """Docs may only reference files that exist."""

    DOCS = (README, LADDER, MEMORY_README)

    def resolve(self, doc, ref):
        for candidate in (
            doc.parent / ref,
            ROOT / ref,
            ROOT / "addons" / "full-engine" / ref,
            ROOT / "blackboard-template" / ref.split("/", 1)[-1],
        ):
            if candidate.exists():
                return candidate
        return None

    def test_relative_markdown_links_resolve(self):
        found = 0
        for doc in self.DOCS:
            for target in re.findall(r"\]\(([^)]+)\)", read(doc)):
                if target.startswith(("http://", "https://", "#", "mailto:")):
                    continue
                found += 1
                with self.subTest(doc=doc.name, link=target):
                    self.assertIsNotNone(
                        self.resolve(doc, target.split("#", 1)[0]),
                        "%s links to %s, which does not exist" % (doc.name, target),
                    )
        self.assertGreater(found, 3, "guards the guard: no relative links were checked")

    def test_scripts_the_verifier_ladder_offers_as_runnable_evidence_exist(self):
        """The ladder downgraded its private-run numbers; what stays must be real."""
        refs = [
            r
            for r in re.findall(r"`([^`]+)`", read(LADDER))
            if r.endswith(".py") and "/" in r
        ]
        self.assertTrue(refs, "guards the guard: the ladder names no runnable script")
        for ref in refs:
            with self.subTest(ref=ref):
                self.assertIsNotNone(
                    self.resolve(LADDER, ref),
                    "docs/verifier-ladder.md offers `%s` as evidence a reader can "
                    "run, and it is not in this repo" % ref,
                )


class OptionalToolHonestyTests(unittest.TestCase):
    """`check_optional_tools.py` may only print Verified for something it checked."""

    def setUp(self):
        self.tool_check = load_module(TOOL_CHECK, "docs_claims_check_optional_tools")
        self._tmp = tempfile.TemporaryDirectory()
        self.target = Path(self._tmp.name) / "proj"
        (self.target / "memory").mkdir(parents=True)
        self.addCleanup(self._tmp.cleanup)

    def write_adapter(self, name="osvec_adapter.py"):
        (self.target / "memory" / name).write_text("raise SystemExit(1)\n", encoding="utf-8")

    def test_a_file_on_disk_is_not_reported_as_verified(self):
        self.write_adapter()
        status, evidence = self.tool_check.local_osvec_status(self.target)
        self.assertNotEqual(
            "Verified",
            status,
            "the only check performed was Path.exists(); the adapter was never "
            "imported or run, so this cannot be reported as Verified",
        )
        self.assertIn("selftest", evidence, "the honest label must name what would verify it")

    def test_a_populated_sidecar_is_still_not_verified(self):
        self.write_adapter()
        store = self.target / "memory" / "store"
        store.mkdir()
        (store / "project.sidecar.json").write_text("{}", encoding="utf-8")
        status, _ = self.tool_check.local_osvec_status(self.target)
        self.assertNotEqual("Verified", status)

    def test_legacy_adapter_is_still_not_verified(self):
        self.write_adapter("turbovec_adapter.py")
        status, _ = self.tool_check.local_osvec_status(self.target)
        self.assertNotEqual("Verified", status)

    def test_near_miss_a_real_command_on_path_still_earns_verified(self):
        """The adjacent legitimate case: a check that really ran must still pass.

        Downgrading every status would be its own defect -- it would tell users
        a configured, working tool is unproven.
        """
        bin_dir = Path(self._tmp.name) / "bin"
        bin_dir.mkdir()
        tool = bin_dir / "projectos-fake-osvec"
        tool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        tool.chmod(0o755)
        env = dict(os.environ)
        env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
        env["PROJECT_OS_OSVEC_CMD"] = tool.name
        old = os.environ.copy()
        os.environ.clear()
        os.environ.update(env)
        try:
            self.assertIsNotNone(shutil.which(tool.name))
            status, _ = self.tool_check.env_command_status("PROJECT_OS_OSVEC_CMD")
            self.write_adapter()
            report_status, _ = self.tool_check.preferred_env_command_status(
                "PROJECT_OS_OSVEC_CMD", "PROJECT_OS_VECTOR_CMD"
            )
        finally:
            os.environ.clear()
            os.environ.update(old)
        self.assertEqual("Verified", status)
        self.assertEqual("Verified", report_status)

    def test_report_table_shape_is_unchanged(self):
        """Anything parsing this report keeps working: same header, 4 columns."""
        self.write_adapter()
        report = self.tool_check.build_report(self.target)
        self.assertIn("| Capability | Status | Evidence | Recommendation |", report)
        rows = [line for line in report.split("\n") if line.startswith("| OSVec |")]
        self.assertEqual(1, len(rows), "expected exactly one OSVec row")
        self.assertEqual(
            4,
            len(rows[0].split(" | ")),
            "OSVec row lost a column: " + rows[0],
        )


if __name__ == "__main__":
    unittest.main()
