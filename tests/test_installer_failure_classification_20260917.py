"""Installer regressions must fail first-use tests on supported Python.

Run the actual unittest cases with a controlled subprocess result. This catches
skip-on-error regressions in test bodies and in setUp, without invoking an
installer or importing a different checkout through a cached package name.
"""

import importlib.util
from pathlib import Path
import subprocess
import sys
import types
import unittest
from unittest import mock


def load_sibling(filename):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location("classification_" + path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


QUICKSTART = load_sibling("test_readme_quickstart_paths_20260727.py")
NEXT_STEP = load_sibling("test_installer_next_step_exists_20260727.py")
CASES = (
    (QUICKSTART, "QuickStartTellsTheTruth",
     "test_quickstart_does_not_send_a_starter_install_to_a_slash_command"),
    (QUICKSTART, "DocumentedToolCommandsWorkInAnInstalledProject",
     "test_the_brief_file_the_readme_names_is_installed"),
    (QUICKSTART, "DocumentedToolCommandsWorkInAnInstalledProject",
     "test_promptsmith_actually_runs_there"),
    (NEXT_STEP, "ClosingMessageMatchesWhatWasInstalled",
     "test_a_starter_install_does_not_tell_you_to_type_a_slash_command"),
    (NEXT_STEP, "ClosingMessageMatchesWhatWasInstalled",
     "test_the_closing_message_still_tells_the_user_what_to_do"),
)


class InstallerFailureClassification(unittest.TestCase):
    def run_case(self, entry, version, run):
        module, class_name, method = entry
        case = getattr(module, class_name)(method)
        result = unittest.TestResult()
        # Replace module references only; do not mutate global sys/subprocess.
        interpreter = types.SimpleNamespace(version_info=version, executable=sys.executable)
        with mock.patch.object(module, "sys", interpreter), mock.patch.object(
                module, "subprocess", types.SimpleNamespace(run=run)):
            case.run(result)
        self.assertEqual(result.testsRun, 1)
        self.assertEqual(result.errors, [], result.errors)
        if hasattr(case, "work"):
            self.assertFalse(case.work.exists(), "the inner test leaked its temporary project")
        return result

    def test_nonzero_installer_exit_fails_every_supported_case(self):
        for version in ((3, 10, 0), (3, 14, 0)):
            for entry in CASES:
                with self.subTest(version=version, case=entry[2]):
                    run = mock.Mock(return_value=subprocess.CompletedProcess(
                        ["sh", "install.sh"], 97, "synthetic stdout", "synthetic stderr"))
                    result = self.run_case(entry, version, run)
                    self.assertEqual(result.skipped, [], "installer failure was hidden by a skip")
                    self.assertEqual(len(result.failures), 1, result.failures)
                    detail = result.failures[0][1]
                    for expected in ("exit 97", "synthetic stdout", "synthetic stderr"):
                        self.assertIn(expected, detail)
                    run.assert_called_once()

    def test_below_floor_skips_before_subprocess_or_temporary_writes(self):
        for entry in CASES:
            with self.subTest(case=entry[2]):
                run = mock.Mock()
                temporary = mock.Mock(side_effect=AssertionError("unexpected temporary write"))
                with mock.patch.object(entry[0], "tempfile", types.SimpleNamespace(
                        TemporaryDirectory=temporary)):
                    result = self.run_case(entry, (3, 9, 6), run)
                self.assertEqual(result.failures, [])
                self.assertEqual(len(result.skipped), 1, result.skipped)
                self.assertIn("Python 3.10+", result.skipped[0][1])
                self.assertIn("3.9", result.skipped[0][1])
                run.assert_not_called()
                temporary.assert_not_called()

    def test_zero_exit_allows_healthy_first_use_assertions(self):
        def healthy(args, **kwargs):
            if args[0] == "sh":
                target = Path(args[2])
                (target / "examples").mkdir(parents=True)
                (target / "examples/sample-brief.md").write_text("Synthetic brief.\n")
            return subprocess.CompletedProcess(args, 0, "Read AGENTS.md and describe your idea.\n", "")

        for entry in CASES:
            with self.subTest(case=entry[2]):
                run = mock.Mock(side_effect=healthy)
                result = self.run_case(entry, (3, 10, 0), run)
                self.assertTrue(result.wasSuccessful(), result.failures)
                self.assertEqual(result.skipped, [])
                expected_calls = 2 if entry[2] == "test_promptsmith_actually_runs_there" else 1
                self.assertEqual(run.call_count, expected_calls)

    def test_zero_exit_does_not_bypass_broken_first_use_assertions(self):
        def broken(args, **kwargs):
            if args[0] == "sh":
                (Path(args[2]) / ".claude").mkdir(parents=True)
            # No installed brief and no usable closing guidance.
            return subprocess.CompletedProcess(args, 0, "/project synthetic\n", "")

        for entry in CASES:
            with self.subTest(case=entry[2]):
                run = mock.Mock(side_effect=broken)
                result = self.run_case(entry, (3, 10, 0), run)
                self.assertEqual(result.skipped, [])
                self.assertEqual(len(result.failures), 1, result.failures)
                run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
