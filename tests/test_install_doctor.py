"""Installation diagnostics work before setup and never create or repair state."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools import doctor_install as doctor
from tools import build_release, install_skill_bundle
from intern_personal import skill_bundle


class InstallDoctorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ia-doctor-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        skill = self.root / 'skills/example-skill/SKILL.md'
        skill.parent.mkdir(parents=True)
        skill.write_bytes(b'---\nname: example-skill\ndescription: Reviewed example\n---\nRead [guide](references/guide.md).\n')
        reference = skill.parent / 'references/guide.md'
        reference.parent.mkdir()
        reference.write_text('Reviewed guide\n')
        (self.root / 'LICENSE').write_text('MIT example notice\n')
        patches = [patch.object(doctor.sys, 'platform', 'linux'),
                   patch.object(doctor.sys, 'version_info', (3, 12, 4)),
                   patch.object(doctor.platform, 'machine', return_value='x86_64'),
                   patch.object(doctor.platform, 'python_implementation', return_value='CPython'),
                   patch.object(doctor.importlib.util, 'find_spec', return_value=object()),
                   patch.object(doctor.shutil, 'which', return_value='/synthetic/tool'),
                   patch.object(doctor, 'verify_runtime', return_value={'snapshot_sha256': 'a' * 64}),
                   patch.object(doctor, 'dependency_profile', return_value={'example-runtime': '1.0'}),
                   patch.object(doctor.importlib.metadata, 'version', return_value='1.0'),
                   patch.object(doctor, '_run', return_value=True)]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def check(self, report, identity):
        return next(item for item in report['checks'] if item['id'] == identity)

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
                for p in self.root.rglob('*') if p.is_file()}

    def test_fresh_setup_doctor_never_creates_state_and_keeps_discovery_separate(self):
        before = self.snapshot()
        report = doctor.diagnose(self.root)
        self.assertTrue(report['ok'])
        self.assertTrue(report['runtime_ready'])
        self.assertTrue(report['ready_for_setup'])
        self.assertEqual(self.check(report, 'bundled_skills')['status'], 'pass')
        self.assertEqual(self.check(report, 'installed_skills')['status'], 'warn')
        for key in ['writes_files', 'uses_network', 'starts_relay', 'reads_private_state',
                    'skills_execution_verified', 'skill_discovery_verified', 'account_configuration_verified']:
            self.assertFalse(report[key])
        self.assertEqual(before, self.snapshot())
        self.assertFalse((self.root / 'runtime').exists())
        self.assertFalse((self.root / 'live.json').exists())

    def test_missing_preinstall_dependencies_warn_but_strict_runtime_fails(self):
        with patch.object(doctor.importlib.metadata, 'version', side_effect=doctor.importlib.metadata.PackageNotFoundError):
            report = doctor.diagnose(self.root)
            self.assertTrue(report['ok'])
            self.assertFalse(report['runtime_ready'])
            self.assertEqual(self.check(report, 'runtime_dependencies')['status'], 'warn')
            doctor._run.assert_not_called()
            report = doctor.diagnose(self.root, require_runtime=True)
            self.assertFalse(report['ok'])
            self.assertEqual(self.check(report, 'runtime_dependencies')['status'], 'fail')

    def test_changed_versions_and_bad_imports_cannot_pass_runtime_acceptance(self):
        with patch.object(doctor.importlib.metadata, 'version', return_value='2.0'):
            report = doctor.diagnose(self.root, require_runtime=True)
            self.assertFalse(report['ok'])
            self.assertFalse(report['runtime_ready'])
        with patch.object(doctor, '_run', side_effect=[False, True]):
            report = doctor.diagnose(self.root)
            self.assertFalse(report['ok'])
            self.assertEqual(self.check(report, 'runtime_imports')['status'], 'fail')
            self.assertEqual(self.check(report, 'dependency_closure')['status'], 'pass')

    def test_unsupported_host_python_and_missing_tool_have_actionable_failures(self):
        with patch.object(doctor.platform, 'machine', return_value='arm64'), \
                patch.object(doctor.sys, 'version_info', (3, 11, 9)), \
                patch.object(doctor.shutil, 'which', return_value=None):
            report = doctor.diagnose(self.root)
        self.assertFalse(report['ok'])
        for identity in ['platform', 'python', 'git', 'tmux', 'codex']:
            item = self.check(report, identity)
            self.assertEqual(item['status'], 'fail')
            self.assertTrue(item['next_action'])

    def test_baseline_and_profile_errors_are_fixed_diagnostics_without_rejected_content(self):
        with patch.object(doctor, 'verify_runtime', side_effect=ValueError('private-sentinel')), \
                patch.object(doctor, 'dependency_profile', side_effect=ValueError('private-sentinel')):
            report = doctor.diagnose(self.root)
        self.assertFalse(report['ok'])
        self.assertNotIn('private-sentinel', json.dumps(report))
        self.assertEqual(self.check(report, 'release_integrity')['status'], 'fail')
        self.assertEqual(self.check(report, 'dependency_profile')['status'], 'fail')

    def test_explicit_skill_verification_preserves_modified_or_missing_instructions(self):
        destination = self.root / 'installed-skills'
        skill_bundle.install(destination, self.root / 'skills')
        before = self.snapshot()
        report = doctor.diagnose(self.root, skills_directory=destination)
        self.assertEqual(self.check(report, 'installed_skills')['status'], 'pass')
        self.assertEqual(self.check(report, 'installed_skills')['files_verified'], 3)
        self.assertEqual(before, self.snapshot())
        resource = destination / 'example-skill/references/guide.md'
        resource.write_text('Owner changes\n')
        report = doctor.diagnose(self.root, skills_directory=destination)
        self.assertFalse(report['ok'])
        self.assertEqual(resource.read_text(), 'Owner changes\n')
        resource.unlink()
        report = doctor.diagnose(self.root, skills_directory=destination)
        self.assertFalse(report['ok'])
        self.assertFalse(resource.exists())

    def test_missing_explicit_skill_directory_is_reported_without_creation(self):
        destination = self.root / 'missing-skills'
        report = doctor.diagnose(self.root, skills_directory=destination)
        self.assertFalse(report['ok'])
        self.assertFalse(destination.exists())

    def test_missing_release_notice_or_invalid_skill_metadata_cannot_pass(self):
        (self.root / 'LICENSE').unlink()
        report = doctor.diagnose(self.root)
        self.assertFalse(report['ok'])
        self.assertEqual(self.check(report, 'bundled_skills')['status'], 'fail')
        (self.root / 'LICENSE').write_text('MIT example notice\n')
        (self.root / 'skills/example-skill/SKILL.md').write_text('Missing metadata\n')
        report = doctor.diagnose(self.root)
        self.assertFalse(report['ok'])
        self.assertEqual(self.check(report, 'bundled_skills')['status'], 'fail')

    def test_real_release_skills_standalone_install_verify_and_doctor_agree(self):
        config = json.loads((doctor.ROOT / 'release-product.json').read_text())
        payload = {p.relative_to(doctor.ROOT).as_posix(): p.read_bytes()
                   for p in (doctor.ROOT / 'skills').rglob('*') if p.is_file()}
        payload['LICENSE'] = (doctor.ROOT / 'LICENSE').read_bytes()
        bundle = build_release.skill_payload(config, payload, config['final_handoff']['source_commit'], False)
        archive = self.root / 'released-skills.zip'
        build_release.write_archive(archive, bundle)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        destination = self.root / 'standalone-installed-skills'
        install_skill_bundle.install(archive, destination, digest)
        verified = install_skill_bundle.verify(archive, destination, digest)
        report = doctor.diagnose(doctor.ROOT, skills_directory=destination)
        self.assertTrue(report['ok'])
        check = self.check(report, 'installed_skills')
        self.assertEqual(check['status'], 'pass')
        self.assertEqual(check['files_verified'], verified['files_verified'])
        self.assertEqual(set(check['skills']), set(config['skills']))
        before = self.snapshot()
        # Installing through the runtime path after the standalone path is
        # idempotent and agrees on each skill's complete resource/license tree.
        skill_bundle.install(destination, doctor.ROOT / 'skills')
        self.assertEqual(before, self.snapshot())

    def test_cli_nonzero_failure_still_outputs_structured_report(self):
        output = io.StringIO()
        with patch.object(doctor, 'diagnose', return_value={'ok': False, 'checks': []}), contextlib.redirect_stdout(output):
            code = doctor.main(['--require-runtime', '--skills-directory', '/synthetic/skills'])
        self.assertEqual(code, 2)
        self.assertFalse(json.loads(output.getvalue())['ok'])


class DependencyProfileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ia-doctor-profile-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        requirements = self.root / 'requirements'; requirements.mkdir()
        digest = 'a' * 64
        self.lock = requirements / 'public-linux-py312.lock'
        self.lock.write_text('# Reviewed profile\nexample-runtime==1.0 --hash=sha256:' + digest + '\n')
        self.path = requirements / 'public-profile.json'
        self.profile = dict(schema='intern-agent.dependency-profile.v1', profile='public-linux-py312',
            platform='linux-x86_64', python='3.12', implementation='CPython', public_install_recommended=True,
            lock_file='requirements/public-linux-py312.lock', lock_sha256=hashlib.sha256(self.lock.read_bytes()).hexdigest(),
            wheels=[dict(name='example-runtime', version='1.0', sha256=digest)])
        self.path.write_text(json.dumps(self.profile))

    def test_exact_locked_wheel_versions_hashes_and_profile_match(self):
        self.assertEqual(doctor.dependency_profile(self.root), {'example-runtime': '1.0'})
        self.profile['wheels'][0]['version'] = '2.0'
        self.path.write_text(json.dumps(self.profile))
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            doctor.dependency_profile(self.root)

    def test_tampered_lock_invalid_json_and_linked_profiles_fail(self):
        self.lock.write_text('Changed lock\n')
        with self.assertRaises(ValueError):
            doctor.dependency_profile(self.root)
        self.path.write_text('[]')
        with self.assertRaises(ValueError):
            doctor.dependency_profile(self.root)
        self.path.unlink(); self.path.symlink_to(self.lock)
        with self.assertRaises(ValueError):
            doctor.dependency_profile(self.root)


class ReadOnlyProcessTests(unittest.TestCase):
    def test_import_and_pip_checks_do_not_inherit_credentials_or_expose_process_errors(self):
        with patch.dict(doctor.os.environ, {'SYNTHETIC_SECRET': 'private-sentinel'}), \
                patch.object(doctor.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run:
            self.assertTrue(doctor._run(['selected-python', '-I', '-B', '-m', 'pip', 'check'], Path('/tmp')))
        self.assertNotIn('SYNTHETIC_SECRET', run.call_args.kwargs['env'])
        self.assertTrue(run.call_args.kwargs['capture_output'])
        with patch.object(doctor.subprocess, 'run', side_effect=subprocess.TimeoutExpired('private-sentinel', 45)):
            self.assertFalse(doctor._run(['selected-python'], Path('/tmp')))


if __name__ == '__main__':
    unittest.main()
