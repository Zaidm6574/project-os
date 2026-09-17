"""Regression controls for installer refusal, migration recovery and payloads."""
import hashlib
import importlib.util
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location('audit_' + name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class InstallerAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.target = self.base / 'project'
        self.target.mkdir()
        self.env = dict(os.environ)
        self.env.pop('PROJECT_OS_SHARED_BRAIN', None)
        self.env.pop('CENTRAL_BRAIN_PATH', None)
        self.env['PROJECT_OS_LEGACY_CENTRAL_BRAIN'] = str(self.base / 'empty-legacy')

    def command(self, script, *args):
        command = ['sh', str(ROOT / script)] if script.endswith('.sh') else [sys.executable, str(ROOT / script)]
        return subprocess.run(command + list(map(str, args)), env=self.env, cwd=ROOT,
                              capture_output=True, text=True, timeout=45)

    @unittest.skipUnless(sys.version_info >= (3, 10), 'installer wrapper requires Python >=3.10')
    def test_full_engine_contained_symlink_refuses_without_success_claim(self):
        (self.target / 'memory').mkdir()
        (self.target / 'inside').mkdir()
        (self.target / 'memory/store').symlink_to('../inside', target_is_directory=True)
        result = self.command('install.sh', self.target, '--claude-engine')
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('REFUSED', result.stderr)
        self.assertNotIn('are installed', result.stdout)
        self.assertFalse((self.target / 'AGENTS.md').exists())
        self.assertFalse((self.target / '.claude/commands/project.md').exists())

    @unittest.skipUnless(sys.version_info >= (3, 10), 'installer wrapper requires Python >=3.10')
    def test_full_engine_normal_host_install(self):
        result = self.command('install.sh', self.target, '--claude-engine', '--codex-engine')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.target / '.claude/commands/project.md').is_file())
        self.assertTrue((self.target / '.agents/skills/project/SKILL.md').is_file())

    @unittest.skipUnless(sys.version_info >= (3, 10), 'installer wrapper requires Python >=3.10')
    def test_dry_run_does_not_claim_host_installed(self):
        result = self.command('install.sh', self.target / 'absent', '--dry-run', '--claude-engine')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn('are installed', result.stdout)
        self.assertIn('would install', result.stdout)
        self.assertFalse((self.target / 'absent').exists())

    def test_starter_hardlinked_destination_and_backup_are_untouched(self):
        for backup in (False, True):
            with self.subTest(backup=backup):
                target = self.target / str(backup)
                target.mkdir()
                outside = self.base / ('outside-' + str(backup))
                outside.write_bytes(b'synthetic outside sentinel\n')
                destination = target / 'CLAUDE.md'
                if backup:
                    destination.write_bytes(b'original project notes\n')
                    os.link(outside, destination.with_name('CLAUDE.md.pre-force'))
                else:
                    os.link(outside, destination)
                before = destination.read_bytes()
                result = self.command('scripts/setup_project_os.py', '--target', target, '--force')
                self.assertEqual(outside.read_bytes(), b'synthetic outside sentinel\n')
                self.assertEqual(destination.read_bytes(), before)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('hardlink', result.stderr)

    def test_starter_regular_force_preserves_backup(self):
        destination = self.target / 'CLAUDE.md'
        destination.write_bytes(b'original project notes\n')
        result = self.command('scripts/setup_project_os.py', '--target', self.target, '--force')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(destination.read_bytes(), (ROOT / 'CLAUDE.md').read_bytes())
        self.assertEqual(destination.with_name('CLAUDE.md.pre-force').read_bytes(), b'original project notes\n')

    def test_report_rejects_ancestor_and_leaf_symlinks(self):
        for kind in ('ancestor', 'leaf'):
            with self.subTest(kind=kind):
                target = self.target / kind
                target.mkdir()
                outside = self.base / ('outside-' + kind)
                outside.mkdir()
                sentinel = outside / '17-capability-preflight.md'
                sentinel.write_bytes(b'outside report sentinel\n')
                if kind == 'ancestor':
                    (target / 'blackboard').symlink_to(outside, target_is_directory=True)
                else:
                    (target / 'blackboard').mkdir()
                    (target / 'blackboard/17-capability-preflight.md').symlink_to(sentinel)
                result = self.command('scripts/check_optional_tools.py', '--target', target)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(sentinel.read_bytes(), b'outside report sentinel\n')

    def test_report_regular_target_preserves_prose(self):
        (self.target / 'blackboard').mkdir()
        report = self.target / 'blackboard/17-capability-preflight.md'
        report.write_text('Personal project preflight notes.\n')
        result = self.command('scripts/check_optional_tools.py', '--target', self.target)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Personal project preflight notes.', report.read_text())
        self.assertIn('optional-tool-check:begin', report.read_text())

    def test_starter_uses_private_distribution_filter(self):
        starter = load('setup_project_os')
        full = load('install_full_engine')
        source = self.base / 'source'
        source.mkdir()
        blocked = ('.env', '.env.test', 'shared-brain-archive.jsonl', 'secrets/notes.txt',
                   'cache.sqlite3', 'notes.pre-force', 'credentials.json')
        for relative in (*blocked, 'safe.py'):
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'harmless synthetic payload\n')
        (source / 'linked.py').symlink_to(source / 'safe.py')
        starter.copy_tree_files(source, self.target, force=False)
        self.assertTrue((self.target / 'safe.py').is_file())
        for relative in (*blocked, 'linked.py'):
            with self.subTest(relative=relative):
                self.assertFalse((self.target / relative).exists())
                self.assertFalse(starter.is_distributable(source / relative, source))
                self.assertFalse(full.is_distributable(source / relative, source))

    def migration_fixture(self):
        installer = load('install_full_engine')
        (self.target / 'blackboard').mkdir()
        (self.target / 'AGENTS.md').write_text('# Synthetic starter\n')
        (self.target / 'blackboard/00-project-goal.md').write_text('# Goal\n')
        legacy = self.base / 'legacy'
        legacy.mkdir()
        active = legacy / 'shared-brain.jsonl'
        archive = legacy / 'shared-brain-archive.jsonl'
        active.write_bytes(b'{"id":"synthetic-active","text":"migration control"}\n')
        archive.write_bytes(b'{"id":"synthetic-archive","text":"archive control"}\n')
        signatures = {}
        for relative in installer.RESOLVER_COHORT_SOURCES:
            if relative == 'scripts/brain_paths.py':
                continue
            payload = ('synthetic legacy: ' + relative + '\n').encode()
            path = self.target / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            signatures[relative] = hashlib.sha256(payload).hexdigest()
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(installer, 'LEGACY_RESOLVER_SIGNATURES', {'synthetic': signatures}).start()
        mock.patch.dict(os.environ, self.env, clear=True).start()
        return installer, legacy, active, archive

    def assert_migrated(self, installer, active, archive):
        self.assertEqual((self.target / 'brain/shared-brain.jsonl').read_bytes(), active.read_bytes())
        self.assertEqual((self.target / 'brain/shared-brain-archive.jsonl').read_bytes(), archive.read_bytes())
        self.assertTrue((self.target / 'brain/shared-brain-migration-receipt.jsonl').is_file())
        managed = {**installer.RESOLVER_COHORT_SOURCES, **installer.RUNTIME_DEPENDENCY_SOURCES}
        for relative, source in managed.items():
            self.assertEqual((self.target / relative).read_bytes(), (ROOT / source).read_bytes())

    def test_migration_lost_fence_then_retry(self):
        installer, legacy, active, archive = self.migration_fixture()
        loader = installer._load_sibling_module
        calls = 0
        def instrument(name):
            module = loader(name)
            if name != 'bb_lock':
                return module
            def renew(path, token):
                nonlocal calls
                calls += 1
                return False if calls >= 2 else module.renew(path, token)
            return types.SimpleNamespace(acquire=module.acquire, release=module.release, renew=renew,
                                         fenced=module.fenced, LockLeaseLost=module.LockLeaseLost)
        before = active.read_bytes(), archive.read_bytes()
        with mock.patch.object(installer, '_load_sibling_module', instrument):
            with self.assertRaisesRegex(OSError, 'lock lease was lost before publication'):
                installer.install_full_engine(self.target, brain_migration='migrate', legacy_central_brain=legacy)
        self.assertFalse((self.target / 'brain/shared-brain.jsonl').exists())
        installer.install_full_engine(self.target, brain_migration='migrate', legacy_central_brain=legacy)
        self.assert_migrated(installer, active, archive)
        self.assertEqual((active.read_bytes(), archive.read_bytes()), before)

    def test_migration_publication_failure_then_retry(self):
        installer, legacy, active, archive = self.migration_fixture()
        original = installer.os.replace
        failed = False
        def replace(source, destination):
            nonlocal failed
            if Path(destination) == self.target / 'brain/shared-brain-archive.jsonl' and not failed:
                failed = True
                raise OSError('synthetic publication failure')
            return original(source, destination)
        with mock.patch.object(installer.os, 'replace', replace):
            with self.assertRaisesRegex(OSError, 'synthetic publication failure'):
                installer.install_full_engine(self.target, brain_migration='migrate', legacy_central_brain=legacy)
        self.assertTrue(failed)
        self.assertFalse((self.target / 'brain/shared-brain.jsonl').exists())
        installer.install_full_engine(self.target, brain_migration='migrate', legacy_central_brain=legacy)
        self.assert_migrated(installer, active, archive)

    def test_starter_force_defers_existing_resolvers_to_migration(self):
        installer, legacy, active, archive = self.migration_fixture()
        before = {relative: (self.target / relative).read_bytes()
                  for relative in installer.RESOLVER_COHORT_SOURCES
                  if (self.target / relative).exists()}
        result = self.command('scripts/setup_project_os.py', '--target', self.target,
                              '--force', '--defer-resolvers')
        self.assertEqual(result.returncode, 0, result.stderr)
        for relative, data in before.items():
            self.assertEqual((self.target / relative).read_bytes(), data)
        installer.install_full_engine(self.target, brain_migration='migrate', legacy_central_brain=legacy)
        self.assert_migrated(installer, active, archive)

    def test_mid_resolver_failure_rolls_back_migration_and_resolvers(self):
        installer, legacy, active, archive = self.migration_fixture()
        before = {relative: (self.target / relative).read_bytes()
                  for relative in installer.RESOLVER_COHORT_SOURCES
                  if (self.target / relative).exists()}
        original = installer.os.replace
        failed = False
        def replace(source, destination):
            nonlocal failed
            if Path(destination) == self.target / 'scripts/brain_archive.py' and not failed:
                failed = True
                raise OSError('synthetic resolver publication failure')
            return original(source, destination)
        with mock.patch.object(installer.os, 'replace', replace):
            with self.assertRaisesRegex(OSError, 'synthetic resolver publication failure'):
                installer.install_full_engine(self.target, brain_migration='migrate', legacy_central_brain=legacy)
        for relative, data in before.items():
            self.assertEqual((self.target / relative).read_bytes(), data)
        self.assertFalse((self.target / 'brain/shared-brain.jsonl').exists())
        self.assertFalse((self.target / 'brain/shared-brain-migration-receipt.jsonl').exists())
        installer.install_full_engine(self.target, brain_migration='migrate', legacy_central_brain=legacy)
        self.assert_migrated(installer, active, archive)

    def test_completed_data_with_partial_resolvers_recovers_after_failed_rollback(self):
        installer, legacy, active, archive = self.migration_fixture()
        original = installer.os.replace
        def replace(source, destination):
            if Path(destination) == self.target / 'scripts/brain_archive.py':
                raise OSError('synthetic resolver publication failure')
            return original(source, destination)
        with mock.patch.object(installer.os, 'replace', replace), mock.patch.object(
                installer, '_restore_snapshot', side_effect=OSError('synthetic rollback failure')):
            with self.assertRaisesRegex(OSError, 'rollback was incomplete'):
                installer.install_full_engine(self.target, brain_migration='migrate', legacy_central_brain=legacy)
        self.assertEqual((self.target / 'brain/shared-brain.jsonl').read_bytes(), active.read_bytes())
        installer.install_full_engine(self.target, brain_migration='migrate', legacy_central_brain=legacy)
        self.assert_migrated(installer, active, archive)

    def test_failed_resolver_rollback_cannot_remove_published_brain_data(self):
        installer, legacy, active, archive = self.migration_fixture()
        original_restore = installer._restore_snapshot
        managed = {**installer.RESOLVER_COHORT_SOURCES, **installer.RUNTIME_DEPENDENCY_SOURCES}
        def restore(destination, snapshot):
            if destination.relative_to(self.target).as_posix() in managed:
                raise OSError('synthetic resolver rollback failure')
            return original_restore(destination, snapshot)
        with mock.patch.object(installer, '_fsync_directory', side_effect=OSError('synthetic fsync failure')), \
                mock.patch.object(installer, '_restore_snapshot', restore):
            with self.assertRaisesRegex(OSError, 'rollback was incomplete'):
                installer.install_full_engine(self.target, brain_migration='migrate', legacy_central_brain=legacy)
        self.assert_migrated(installer, active, archive)
        installer.install_full_engine(self.target, brain_migration='migrate', legacy_central_brain=legacy)
        self.assert_migrated(installer, active, archive)

    def test_takeover_after_renew_cannot_publish(self):
        installer, legacy, active, archive = self.migration_fixture()
        loader = installer._load_sibling_module
        calls = 0
        replacement = []
        def instrument(name):
            module = loader(name)
            if name != 'bb_lock':
                return module
            def renew(path, token):
                nonlocal calls
                calls += 1
                outcome = module.renew(path, token)
                if calls == 2:
                    self.assertTrue(module.release(path, token=token))
                    new_token = module.acquire(path, agent='replacement', wait=0)
                    self.assertTrue(new_token)
                    replacement.append((module, path, new_token))
                return outcome
            return types.SimpleNamespace(acquire=module.acquire, release=module.release, renew=renew,
                                         fenced=module.fenced, LockLeaseLost=module.LockLeaseLost)
        with mock.patch.object(installer, '_load_sibling_module', instrument):
            with self.assertRaises(OSError):
                installer.install_full_engine(self.target, brain_migration='migrate', legacy_central_brain=legacy)
        self.assertEqual(calls, 2)
        self.assertFalse((self.target / 'brain/shared-brain.jsonl').exists())
        for module, path, token in replacement:
            self.assertTrue(module.release(path, token=token))
        installer.install_full_engine(self.target, brain_migration='migrate', legacy_central_brain=legacy)
        self.assert_migrated(installer, active, archive)

    def _assert_local_upgrade(self, old_payloads=None):
        installer = load('install_full_engine')
        distribution = self.base / 'distribution'
        shutil.copytree(ROOT / 'scripts', distribution / 'scripts')
        shutil.copytree(ROOT / 'addons', distribution / 'addons')
        signatures = {}
        managed = {**installer.RESOLVER_COHORT_SOURCES, **installer.RUNTIME_DEPENDENCY_SOURCES}
        for relative, source in managed.items():
            data = (old_payloads[source] if old_payloads is not None
                    else (ROOT / source).read_bytes())
            signatures[relative] = hashlib.sha256(data).hexdigest()
            destination = self.target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            updated = distribution / source
            updated.parent.mkdir(parents=True, exist_ok=True)
            updated.write_bytes((ROOT / source).read_bytes() + b'\n# Synthetic newer distribution.\n')
        (self.target / 'AGENTS.md').write_text('# Synthetic starter\n')
        (self.target / 'blackboard').mkdir()
        (self.target / 'blackboard/00-project-goal.md').write_text('# Goal\n')
        local = self.target / 'brain/shared-brain.jsonl'
        binding = self.target / 'brain/shared-brain-binding.jsonl'
        local.write_bytes(b'{"id":"local","text":"preserve local data"}\n')
        binding.write_text(json.dumps({'schema': 'project-os/shared-brain-binding/v1',
                                       'target': str(self.base / 'external.jsonl')}) + '\n')
        before = local.read_bytes(), binding.read_bytes()
        with mock.patch.dict(os.environ, self.env, clear=True), mock.patch.multiple(
                installer, TEMPLATE_ROOT=distribution, ADDON_ROOT=distribution / 'addons/full-engine',
                LEGACY_RESOLVER_SIGNATURES=(installer.LEGACY_RESOLVER_SIGNATURES
                                           if old_payloads is not None else {'synthetic-local': signatures}),
                RUNTIME_DEPENDENCY_SIGNATURES=(installer.RUNTIME_DEPENDENCY_SIGNATURES
                                              if old_payloads is not None else
                                              {'scripts/bb_lock.py': {'synthetic': signatures['scripts/bb_lock.py']}}),
                KNOWN_LOCAL_RESOLVER_COHORTS=(installer.KNOWN_LOCAL_RESOLVER_COHORTS
                                             if old_payloads is not None else frozenset({'synthetic-local'}))), mock.patch.object(
                installer, '_resolve_legacy_central_brain', side_effect=AssertionError('unexpected HOME access')):
            installer.install_full_engine(self.target)
            self.assertEqual((local.read_bytes(), binding.read_bytes()), before)

            for relative, source in managed.items():
                self.assertEqual((self.target / relative).read_bytes(), (distribution / source).read_bytes())
            edited = self.target / 'scripts/brain_append.py'
            edited.write_bytes(edited.read_bytes() + b'# Local user edit.\n')
            with self.assertRaisesRegex(ValueError, 'edited, unknown, or mixed'):
                installer.install_full_engine(self.target, force=True)
            self.assertEqual((local.read_bytes(), binding.read_bytes()), before)

    def test_known_local_cohort_upgrades_without_legacy_access(self):
        self._assert_local_upgrade()

    def _historical_payloads(self, sources):
        if not os.path.lexists(ROOT / '.git'):
            self.skipTest('source archive has no Git metadata for historical upgrade fixture')
        revision = '272c6005b8c81fc71a77c474148b6200a1371e8d'
        shallow = subprocess.run(['git', 'rev-parse', '--is-shallow-repository'],
                                 cwd=ROOT, capture_output=True, text=True, timeout=15)
        self.assertEqual(shallow.returncode, 0, shallow.stderr)
        present = subprocess.run(['git', 'cat-file', '-e', revision + '^{commit}'],
                                 cwd=ROOT, capture_output=True, timeout=15)
        if present.returncode and shallow.stdout.strip() == 'true':
            self.skipTest('shallow checkout does not contain historical upgrade commit')
        self.assertEqual(present.returncode, 0, present.stderr)
        payloads = {}
        for source in sources:
            result = subprocess.run(['git', 'show', revision + ':' + source],
                                    cwd=ROOT, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, (source, result.stderr))
            payloads[source] = result.stdout
        return payloads

    @unittest.skipUnless(shutil.which('git'), 'Git required for historical source fixture')
    def test_published_272c600_bytes_upgrade_without_legacy_access(self):
        installer = load('install_full_engine')
        managed = {**installer.RESOLVER_COHORT_SOURCES, **installer.RUNTIME_DEPENDENCY_SOURCES}
        payloads = self._historical_payloads(managed.values())
        self._assert_local_upgrade(payloads)

    @unittest.skipUnless(shutil.which('git'), 'Git required for historical source fixture')
    @unittest.skipUnless(sys.version_info >= (3, 10), 'installer wrapper requires Python >=3.10')
    def test_wrapper_no_force_upgrades_old_lock_and_preserves_brain(self):
        installer = load('install_full_engine')
        managed = {**installer.RESOLVER_COHORT_SOURCES, **installer.RUNTIME_DEPENDENCY_SOURCES}
        payloads = self._historical_payloads(managed.values())
        for relative, source in managed.items():
            destination = self.target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(payloads[source])
        brain = self.target / 'brain/shared-brain.jsonl'
        brain.write_bytes(b'{"id":"project-data","text":"preserved"}\n')
        binding = self.target / 'brain/shared-brain-binding.jsonl'
        binding.write_text(json.dumps({'schema': 'project-os/shared-brain-binding/v1',
                                      'target': str(self.base / 'external.jsonl')}) + '\n')
        before = brain.read_bytes(), binding.read_bytes()
        # If migration touches this configured HOME-era source, it must fail.
        self.env['PROJECT_OS_LEGACY_CENTRAL_BRAIN'] = 'invalid-relative-source'
        result = self.command('install.sh', self.target, '--full-engine')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((brain.read_bytes(), binding.read_bytes()), before)
        for relative, source in managed.items():
            self.assertEqual((self.target / relative).read_bytes(), (ROOT / source).read_bytes())
        spec = importlib.util.spec_from_file_location('upgraded_lock', self.target / 'scripts/bb_lock.py')
        lock = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(lock)
        self.assertTrue(callable(lock.fenced))
        edited = self.target / 'scripts/bb_lock.py'
        edited.write_bytes(edited.read_bytes() + b'# Edited helper.\n')
        result = self.command('install.sh', self.target, '--full-engine', '--force')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('dependency has edited or unknown', result.stderr)
        self.assertEqual((brain.read_bytes(), binding.read_bytes()), before)

    def test_published_local_registry_retains_complete_baseline(self):
        installer = load('install_full_engine')
        signatures = installer.LEGACY_RESOLVER_SIGNATURES['published-272c600']
        self.assertTrue(set(installer.RESOLVER_COHORT_SOURCES).issubset(signatures))
        self.assertIn('published-272c600', installer.KNOWN_LOCAL_RESOLVER_COHORTS)
        current = {relative: 'f' * 64 for relative in installer.RESOLVER_COHORT_SOURCES}
        self.assertEqual(installer.match_resolver_cohort(signatures, current), 'published-272c600')


if __name__ == '__main__':
    unittest.main()
