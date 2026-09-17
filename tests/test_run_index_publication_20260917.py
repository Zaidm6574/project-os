"""Run catalogue publication is serial, atomic, and confined to runs/.

All projects and lock directories are disposable. The two-process control
pauses a real scan after its directory snapshot, before it can publish.
"""
import builtins
import importlib.util
import os
from pathlib import Path
import selectors
import shutil
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
WORKER = r'''
import importlib.util, os, pathlib, sys
script, project, mode = sys.argv[1:]
spec = importlib.util.spec_from_file_location('index_worker', script)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
original = os.listdir
first = True
def snapshot(path):
    global first
    names = original(path)
    if first:
        first = False
        print('SNAPSHOT', flush=True)
        if mode == 'pause':
            assert sys.stdin.readline().strip() == 'continue'
    return names
os.listdir = snapshot
print('START', flush=True)
module.regenerate_index()
print('DONE', flush=True)
'''


class IndexPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='index-publication-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.project = self.base / 'project'
        (self.project / 'memory').mkdir(parents=True)
        (self.project / 'scripts').mkdir()
        (self.project / 'blackboard').mkdir()
        self.runs = self.project / 'runs'
        self.runs.mkdir()
        self.index = self.runs / 'INDEX.md'
        self.script = self.project / 'memory/new_run.py'
        shutil.copy2(ROOT / 'memory/new_run.py', self.script)
        shutil.copy2(ROOT / 'scripts/bb_lock.py', self.project / 'scripts/bb_lock.py')
        self.env = dict(os.environ, BB_LOCK_DIR=str(self.base / 'locks'))
        self.env_patch = mock.patch.dict(os.environ, self.env)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        spec = importlib.util.spec_from_file_location('index_under_test', self.script)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.add_run('alpha')
        self.before = b'# Previously published catalogue\noperator marker\n'
        self.index.write_bytes(self.before)
        self.index.chmod(0o640)

    def add_run(self, slug):
        dest = self.runs / slug
        dest.mkdir()
        (dest / '00-project-goal.md').write_text('## Canonical Goal\n%s\nTier: solo\n' % slug)

    def assert_preserved(self):
        self.assertEqual(self.before, self.index.read_bytes())
        self.assertEqual(0o640, stat.S_IMODE(self.index.stat().st_mode))
        self.assertEqual({'alpha', 'INDEX.md'}, set(os.listdir(self.runs)))

    def worker(self, mode):
        proc = subprocess.Popen(
            [sys.executable, '-u', '-c', WORKER, str(self.script), str(self.project), mode],
            cwd=self.project, env=self.env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        def cleanup():
            if proc.poll() is None:
                proc.kill()
            proc.communicate(timeout=5)
        self.addCleanup(cleanup)
        return proc

    def line(self, proc, timeout=5):
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            self.assertTrue(selector.select(timeout), 'worker did not reach expected boundary')
        return proc.stdout.readline().decode().strip()

    def test_older_scan_cannot_overwrite_a_newer_catalogue(self):
        first = self.worker('pause')
        self.assertEqual('START', self.line(first))
        self.assertEqual('SNAPSHOT', self.line(first))
        self.add_run('beta')
        second = self.worker('normal')
        self.assertEqual('START', self.line(second))
        # If there is no lock, B can finish while A holds a stale snapshot.
        # With serialization, release A and let B take a fresh snapshot next.
        with selectors.DefaultSelector() as selector:
            selector.register(second.stdout, selectors.EVENT_READ)
            advanced = bool(selector.select(0.5))
        if advanced:
            out, err = second.communicate(timeout=5)
            self.assertEqual(0, second.returncode, err)
            self.assertIn('| beta |', self.index.read_text())
        out, err = first.communicate(b'continue\n', timeout=5)
        self.assertEqual(0, first.returncode, err)
        if not advanced:
            out, err = second.communicate(timeout=5)
            self.assertEqual(0, second.returncode, err)
        final = self.index.read_text()
        self.assertIn('| alpha |', final)
        self.assertIn('| beta |', final, 'stale writer erased the newer catalogue row')
        self.assertFalse(advanced, 'second writer enumerated before first publication completed')
        self.assertTrue((self.runs / 'beta/00-project-goal.md').is_file())
        self.assertEqual(0o640, stat.S_IMODE(self.index.stat().st_mode))

    def test_serial_rebuild_keeps_rows_skipped_runs_and_scratch_exclusion(self):
        self.add_run('beta')
        self.add_run('_scratch')
        (self.runs / 'unfinished').mkdir()
        self.assertEqual(['unfinished'], self.module.regenerate_index())
        result = self.index.read_text()
        self.assertIn('| alpha |', result)
        self.assertIn('| beta |', result)
        self.assertIn('`unfinished`', result)
        self.assertNotIn('_scratch', result)
        self.assertEqual(0o640, stat.S_IMODE(self.index.stat().st_mode))

    def test_partial_write_failure_keeps_old_bytes_mode_and_no_staging_litter(self):
        class BrokenWriter:
            def __init__(self, stream):
                self.stream = stream
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return self.stream.__exit__(*args)
            def write(self, data):
                self.stream.write(data[:7])
                self.stream.flush()
                raise OSError('injected partial index write')
        original_fdopen = os.fdopen
        def fdopen(*args, **kwargs):
            return BrokenWriter(original_fdopen(*args, **kwargs))
        def direct_open(path, mode='r', *args, **kwargs):
            stream = builtins.open(path, mode, *args, **kwargs)
            return BrokenWriter(stream) if str(path) == str(self.index) and 'w' in mode else stream
        with mock.patch.object(self.module.os, 'fdopen', side_effect=fdopen), \
                mock.patch.object(self.module, 'open', side_effect=direct_open, create=True):
            with self.assertRaisesRegex(OSError, 'injected partial'):
                self.module.regenerate_index()
        self.assert_preserved()

    def test_fsync_failure_preserves_prior_index(self):
        with mock.patch.object(self.module.os, 'fsync', side_effect=OSError('injected fsync')):
            with self.assertRaisesRegex(OSError, 'injected fsync'):
                self.module.regenerate_index()
        self.assert_preserved()

    def test_replace_failure_preserves_prior_index(self):
        with mock.patch.object(self.module.os, 'replace', side_effect=OSError('injected replace')):
            with self.assertRaisesRegex(OSError, 'injected replace'):
                self.module.regenerate_index()
        self.assert_preserved()

    def test_new_index_is_private(self):
        self.index.unlink()
        self.module.regenerate_index()
        self.assertEqual(0o600, stat.S_IMODE(self.index.stat().st_mode))

    def test_index_symlink_refuses_without_changing_target(self):
        victim = self.base / 'outside.md'
        victim.write_bytes(self.before)
        self.index.unlink()
        self.index.symlink_to(victim)
        with self.assertRaises((OSError, RuntimeError)):
            self.module.regenerate_index()
        self.assertTrue(self.index.is_symlink())
        self.assertEqual(self.before, victim.read_bytes())

    def test_index_hardlink_refuses_without_changing_either_name(self):
        victim = self.base / 'outside.md'
        os.link(self.index, victim)
        with self.assertRaises((OSError, RuntimeError)):
            self.module.regenerate_index()
        self.assert_preserved()
        self.assertEqual(self.before, victim.read_bytes())

    def test_runs_symlink_refuses_without_writing_outside(self):
        relocated = self.base / 'outside-runs'
        self.runs.rename(relocated)
        self.runs.symlink_to(relocated, target_is_directory=True)
        with self.assertRaises((OSError, RuntimeError)):
            self.module.regenerate_index()
        self.assertEqual(self.before, (relocated / 'INDEX.md').read_bytes())

    def test_run_directory_links_are_not_followed(self):
        outside = self.base / 'outside-run'
        outside.mkdir()
        (outside / '00-project-goal.md').write_text('## Canonical Goal\nOUTSIDE SENTINEL\n')
        (self.runs / 'linked').symlink_to(outside, target_is_directory=True)
        self.module.regenerate_index()
        self.assertNotIn('OUTSIDE SENTINEL', self.index.read_text())

    def test_goal_symlink_refuses_without_changing_index_or_target(self):
        outside = self.base / 'outside-goal.md'
        outside.write_text('## Canonical Goal\nOUTSIDE SENTINEL\n')
        goal = self.runs / 'alpha/00-project-goal.md'
        goal.unlink()
        goal.symlink_to(outside)
        with self.assertRaises((OSError, RuntimeError)):
            self.module.regenerate_index()
        self.assert_preserved()
        self.assertEqual('## Canonical Goal\nOUTSIDE SENTINEL\n', outside.read_text())

    def test_missing_lock_helper_refuses_without_rewriting_index(self):
        (self.project / 'scripts/bb_lock.py').unlink()
        with self.assertRaises((OSError, RuntimeError, ImportError)):
            self.module.regenerate_index()
        self.assert_preserved()

    def test_directory_swapped_during_staging_cannot_redirect_publication(self):
        outside = self.base / 'outside'
        outside.mkdir()
        victim = outside / 'INDEX.md'
        victim.write_bytes(b'outside index\n')
        original_runs = self.base / 'original-runs'
        real_fsync = os.fsync
        def swapped(fd):
            self.runs.rename(original_runs)
            self.runs.symlink_to(outside, target_is_directory=True)
            real_fsync(fd)
        with mock.patch.object(self.module.os, 'fsync', side_effect=swapped):
            with self.assertRaises((OSError, RuntimeError)):
                self.module.regenerate_index()
        self.assertEqual(b'outside index\n', victim.read_bytes())
        self.assertEqual(self.before, (original_runs / 'INDEX.md').read_bytes())
        self.assertEqual({'alpha', 'INDEX.md'}, set(os.listdir(original_runs)))
        self.assertEqual({'INDEX.md'}, set(os.listdir(outside)))

    def test_index_swapped_to_symlink_during_staging_is_not_replaced_or_followed(self):
        victim = self.base / 'outside.md'
        victim.write_bytes(b'outside index\n')
        old_index = self.base / 'old-index.md'
        real_fsync = os.fsync
        def swapped(fd):
            self.index.rename(old_index)
            self.index.symlink_to(victim)
            real_fsync(fd)
        with mock.patch.object(self.module.os, 'fsync', side_effect=swapped):
            with self.assertRaises((OSError, RuntimeError)):
                self.module.regenerate_index()
        self.assertTrue(self.index.is_symlink())
        self.assertEqual(b'outside index\n', victim.read_bytes())
        self.assertEqual(self.before, old_index.read_bytes())
        self.assertEqual({'alpha', 'INDEX.md'}, set(os.listdir(self.runs)))

    def test_planted_staging_symlink_is_never_followed(self):
        victim = self.base / 'outside.md'
        victim.write_bytes(b'outside index\n')
        planted = self.runs / '.INDEX.md.planted.tmp'
        planted.symlink_to(victim)
        # uuid is shared with bb_lock, whose lease remains a valid opaque token.
        with mock.patch('uuid.uuid4', return_value=types.SimpleNamespace(hex='planted')):
            with self.assertRaises((OSError, RuntimeError)):
                self.module.regenerate_index()
        self.assertTrue(planted.is_symlink())
        self.assertEqual(b'outside index\n', victim.read_bytes())
        self.assertEqual(self.before, self.index.read_bytes())

    def test_reader_sees_old_complete_index_until_atomic_replace(self):
        original_replace = os.replace
        observed = []
        def replace(*args, **kwargs):
            observed.append(self.index.read_bytes())
            return original_replace(*args, **kwargs)
        with mock.patch.object(self.module.os, 'replace', side_effect=replace):
            self.module.regenerate_index()
        self.assertEqual([self.before], observed)
        self.assertIn('| alpha |', self.index.read_text())


if __name__ == '__main__':
    unittest.main()
