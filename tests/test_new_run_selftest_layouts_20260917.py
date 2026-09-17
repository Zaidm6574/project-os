"""Exercise the actual source, starter install, and full-engine install.

Self-test success and ordinary scaffolding have separate assertions. Optional
roster checks cannot waive the full engine's runtime requirement.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOLO_FILES = {
    '00-project-goal.md', '07-approved-plan.md', '09-cost-estimate.md',
    '12-evaluation-log.md', '13-delivery-report.md', '14-artifact-manifest.md',
    '19-memory-harvest.md', '23-loop-closeout.md', 'PACKETS.md',
}


class SelftestLayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='new-run-layouts-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.home = self.base / 'home'
        self.home.mkdir()
        self.env = dict(os.environ, HOME=str(self.home),
                        BB_LOCK_DIR=str(self.home / 'locks'), PYTHONDONTWRITEBYTECODE='1')

    def cli(self, script, *args):
        return subprocess.run([sys.executable, '-B', str(script)] + list(map(str, args)),
                              cwd=self.base, env=self.env, capture_output=True,
                              text=True, timeout=30)

    def layout(self, kind):
        project = self.base / kind
        if kind == 'source':
            # A real source snapshot, with the addon still nested and optional.
            shutil.copytree(ROOT, project, ignore=shutil.ignore_patterns('.git', '__pycache__'))
        else:
            result = self.cli(ROOT / 'scripts/setup_project_os.py', '--target', project)
            self.assertEqual(0, result.returncode, result.stderr + result.stdout)
            if kind == 'full-engine':
                result = self.cli(ROOT / 'scripts/install_full_engine.py', '--target', project)
                self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        return project

    def snapshot(self, directory):
        return {str(p.relative_to(directory)): (p.read_bytes(), p.stat().st_mode)
                for p in directory.rglob('*') if p.is_file()}

    def helper(self, project, kind):
        if kind == 'starter':
            # Starter stages the full-engine addon; it does not activate this
            # helper in memory/. Exercise that actual shipped location.
            self.assertFalse((project / 'memory/new_run.py').exists())
            return project / 'addons/full-engine/memory/new_run.py'
        return project / 'memory/new_run.py'

    def check_selftest(self, kind):
        project = self.layout(kind)
        before = self.snapshot(project)
        home_before = self.snapshot(self.home)
        result = self.cli(self.helper(project, kind), '--selftest')
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn('new_run selftest: OK', result.stdout)
        self.assertEqual(before, self.snapshot(project), 'selftest changed the real layout')
        self.assertEqual(home_before, self.snapshot(self.home), 'selftest left live lock files')

    def check_scaffold(self, kind):
        project = self.layout(kind)
        result = self.cli(self.helper(project, kind), 'ordinary', '--tier', 'solo')
        self.assertEqual(0, result.returncode, result.stderr)
        run = project / 'runs/ordinary'
        self.assertTrue(SOLO_FILES.issubset({p.name for p in run.iterdir()}))
        self.assertIn('| ordinary |', (project / 'runs/INDEX.md').read_text())
        self.assertEqual(kind != 'source', (run / '21-agent-roster.md').exists())
        self.assertFalse((run / 'packets').exists())
        self.assertIn('no-packets: solo run', (run / 'PACKETS.md').read_text())

    def test_source_selftest(self):
        self.check_selftest('source')

    def test_starter_selftest(self):
        self.check_selftest('starter')

    def test_full_engine_selftest(self):
        self.check_selftest('full-engine')

    def test_source_ordinary_scaffolding(self):
        self.check_scaffold('source')

    def test_starter_ordinary_scaffolding(self):
        self.check_scaffold('starter')

    def test_full_engine_ordinary_scaffolding(self):
        self.check_scaffold('full-engine')

    def test_full_engine_missing_roster_still_refuses_every_tier_before_writing(self):
        project = self.layout('full-engine')
        self.assertTrue((project / 'memory/goal_guard.py').is_file())
        for roster in project.rglob('21-agent-roster.md'):
            roster.unlink()
        for tier in ('solo', 'mini', 'full'):
            with self.subTest(tier=tier):
                before = self.snapshot(project)
                result = self.cli(project / 'memory/new_run.py', 'missing-' + tier, '--tier', tier)
                self.assertEqual(2, result.returncode, result.stderr)
                self.assertIn('21-agent-roster.md', result.stderr)
                self.assertEqual(before, self.snapshot(project))
        result = self.cli(project / 'memory/new_run.py', '--selftest')
        self.assertNotEqual(0, result.returncode)
        self.assertNotIn('selftest: OK', result.stdout)

    def test_canonical_and_addon_sources_match(self):
        self.assertEqual((ROOT / 'memory/new_run.py').read_bytes(),
                         (ROOT / 'addons/full-engine/memory/new_run.py').read_bytes())


if __name__ == '__main__':
    unittest.main()
