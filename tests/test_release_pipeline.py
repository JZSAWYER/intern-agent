"""Release preparation must preserve reviewed bytes and fail closed before publication."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from tools import build_release as release


class ReleasePipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ia-release-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'product'
        self.root.mkdir()
        self.source_commit = 'a' * 40
        self.config = {
            'schema': 'intern-agent.release-product.v1', 'repository': release.REPOSITORY,
            'version': '0.1.0',
            'extension': {'publisher': 'intern-agent', 'name': 'intern-agent',
                          'namespace': 'internAgentPublic', 'license': 'MIT', 'displayName': 'Intern Agent'},
            'skills': ['example-skill', 'recover-intern-agent', 'feishu-file-transfer'],
            'dependency_profile': 'requirements/public-profile.json',
            'dependency_lock': 'requirements/public-linux-py312.lock',
            'gates': {name: True for name in ['ownership_approved', 'license_approved',
                                            'dependency_security_approved', 'clean_install_approved']},
            'final_handoff': {'approved': True, 'source_commit': self.source_commit,
                              'repository': release.REPOSITORY, 'version': '0.1.0'}}
        self.files = {
            'release-product.json': release.encoded(self.config), 'LICENSE': b'MIT synthetic test notice\n',
            'extension/package.json': release.encoded({'license': 'MIT'}),
            'intern_personal/core.py': b'# synthetic implementation\n',
            'requirements/public-linux-py312.lock': b'# synthetic empty dependency closure\n',
            'skills/example-skill/SKILL.md': b'---\nname: example-skill\n---\n# Example\n',
            'skills/example-skill/references/lifecycle.md': b'# Reviewed lifecycle resource\n',
            'skills/recover-intern-agent/SKILL.md': b'# Synthetic recovery skill\n',
            'skills/feishu-file-transfer/SKILL.md': b'# Synthetic file transfer skill\n',
            'tools/install_skill_bundle.py': b'# Synthetic standalone installer\n',
        }
        self.files['requirements/public-profile.json'] = release.encoded({
            'schema': 'intern-agent.dependency-profile.v1', 'lock_file': self.config['dependency_lock'],
            'lock_sha256': release.digest(self.files['requirements/public-linux-py312.lock']),
            'public_install_recommended': True})
        name = 'intern_personal/core.py'
        self.files['release-runtime.json'] = release.encoded({
            'schema': 'intern-agent.runtime-baseline.v1', 'source_commit': self.source_commit,
            'snapshot_sha256': release.digest(name.encode() + b'\0' + self.files[name] + b'\0'),
            'runtime_files': [{'path': name, 'sha256': release.digest(self.files[name])}],
            'frontend': {'files': [{'path': 'extension/package.json',
                                   'sha256': release.digest(self.files['extension/package.json'])}]}})
        self.write_fixture()

    def write_fixture(self):
        self.files['release-product.json'] = release.encoded(self.config)
        for name, data in self.files.items():
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        manifest = {'schema': 'intern-agent.source-candidate.v1', 'source_commit': self.source_commit,
                    'ownership_approved': True, 'license_approved': True, 'dependency_security_approved': True,
                    'files': [{'path': name, 'sha256': release.digest(data),
                               'rights': 'approved_for_distribution', 'provenance': 'synthetic-test-fixture'}
                              for name, data in sorted(self.files.items())]}
        (self.root / 'source-distribution.json').write_bytes(release.encoded(manifest))

    def fake_vsix(self, root, output, *, stable, product, prepare):
        self.assertTrue(stable)
        package = {'name': product['extension']['name'], 'publisher': product['extension']['publisher'],
                   'version': product['version'], 'license': 'UNLICENSED' if prepare else 'MIT',
                   'contributes': {'commands': [{'command': 'internAgentPublic.setup'}]}}
        release.write_archive(output, {'extension/package.json': release.encoded(package)})
        return {'artifact': str(output), 'files': 1, 'published': False, 'stable': True,
                'overlay_receipt': {'source_frontend_sha256': 'b' * 64}}

    def build(self, output, prepare=True):
        with patch('tools.build_vsix.build', side_effect=self.fake_vsix):
            with patch.object(release, 'check_checkout'):
                with patch.object(release, 'check_public_inventory'):
                    return release.build(self.root, output, prepare=prepare, expected_export_commit='c' * 40)

    def test_preparation_is_deterministic_explicit_unlicensed_and_history_free(self):
        first, second = self.base / 'first', self.base / 'second'
        result = self.build(first)
        self.build(second)
        self.assertFalse(result['release_approved'])
        self.assertTrue(result['preparation_only'])
        self.assertEqual(result['license'], 'UNLICENSED')
        self.assertIsNone(result['export_commit'])
        self.assertIsNone(result['public_source_commit'])
        self.assertEqual(result['maintained_source_commit'], self.source_commit)
        self.assertNotIn('artifact', result['vsix_build'])
        self.assertEqual((first / 'intern-agent-0.1.0-install-skills.py').read_bytes(),
                         self.files['tools/install_skill_bundle.py'])
        self.assertEqual({f.name: f.read_bytes() for f in first.iterdir()},
                         {f.name: f.read_bytes() for f in second.iterdir()})
        expected = self.files.keys() | {'source-distribution.json'}
        with zipfile.ZipFile(first / 'intern-agent-0.1.0-source.zip') as archive:
            self.assertEqual(set(archive.namelist()), expected)
            self.assertFalse(any('.git' in Path(name).parts for name in archive.namelist()))
        with zipfile.ZipFile(first / 'intern-agent-0.1.0-skills.zip') as archive:
            self.assertEqual(set(archive.namelist()), {
                'skills-manifest.json', 'skills/example-skill/SKILL.md',
                'skills/example-skill/references/lifecycle.md',
                'skills/recover-intern-agent/SKILL.md', 'skills/feishu-file-transfer/SKILL.md'})
            manifest = json.loads(archive.read('skills-manifest.json'))
            self.assertFalse(manifest['release_approved'])
            for entry in manifest['files']:
                self.assertEqual(release.digest(archive.read(entry['path'])), entry['sha256'])
        for line in (first / 'SHA256SUMS').read_text().splitlines():
            checksum, name = line.split('  ')
            self.assertEqual(release.digest((first / name).read_bytes()), checksum)

    def test_final_bundle_carries_exact_approved_license_and_commit(self):
        output = self.base / 'final'
        result = self.build(output, prepare=False)
        self.assertTrue(result['release_approved'])
        self.assertEqual(result['export_commit'], 'c' * 40)
        self.assertEqual(result['public_source_commit'], 'c' * 40)
        self.assertEqual(result['maintained_source_commit'], self.source_commit)
        self.assertFalse(result['published'])
        with zipfile.ZipFile(output / 'intern-agent-0.1.0-skills.zip') as archive:
            manifest = json.loads(archive.read('skills-manifest.json'))
            self.assertEqual(archive.read('LICENSE'), self.files['LICENSE'])
            self.assertEqual(manifest['license_file'], {'path': 'LICENSE', 'sha256': release.digest(self.files['LICENSE'])})

    def test_pending_or_different_handoff_never_creates_output(self):
        scenarios = [({'approved': False}, 'final_source_handoff_pending'),
                     ({'source_commit': 'd' * 40}, 'verified_source_commit_differs'),
                     ({'version': '0.2.0'}, 'handoff_identity_or_version_differs'),
                     ({'repository': 'other/example'}, 'handoff_identity_or_version_differs')]
        for changes, error in scenarios:
            with self.subTest(error=error):
                old = self.config['final_handoff'].copy()
                self.config['final_handoff'].update(changes)
                self.write_fixture()
                with self.assertRaisesRegex(release.ReleaseError, error):
                    release.build(self.root, self.base / 'blocked')
                self.assertFalse((self.base / 'blocked').exists())
                self.config['final_handoff'] = old

    def test_each_owner_install_and_security_gate_is_required(self):
        manifest = {'source_commit': self.source_commit}
        for gate in self.config['gates']:
            with self.subTest(gate=gate):
                self.config['gates'][gate] = False
                with self.assertRaisesRegex(release.ReleaseError, gate + '_pending'):
                    release.check_handoff(self.config, manifest)
                self.config['gates'][gate] = True

    def test_source_drift_and_unlisted_private_state_are_not_packaged(self):
        target = self.root / 'intern_personal/core.py'
        target.write_bytes(b'# unreviewed update\n')
        with self.assertRaisesRegex(ValueError, 'source_hash_mismatch'):
            release.prepare_inputs(self.root, prepare=True)
        target.write_bytes(self.files['intern_personal/core.py'])
        (self.root / 'intern_personal/bindings.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'unlisted_source_file'):
            release.prepare_inputs(self.root, prepare=True)

    def test_skill_resources_require_inventory_entrypoint_and_safe_text(self):
        payload = dict(self.files)
        del payload['skills/example-skill/SKILL.md']
        with self.assertRaisesRegex(release.ReleaseError, 'skill_entrypoint_not_in_inventory'):
            release.skill_payload(self.config, payload, self.source_commit, True)
        payload = dict(self.files)
        payload['skills/example-skill/scripts/run.py'] = b'print(1)'
        with self.assertRaisesRegex(release.ReleaseError, 'inert_text_resources'):
            release.skill_payload(self.config, payload, self.source_commit, True)

    def test_final_release_requires_recovery_and_file_transfer_guidance(self):
        for missing in release.REQUIRED_RELEASE_SKILLS:
            with self.subTest(missing=missing):
                config = dict(self.config, skills=[name for name in self.config['skills'] if name != missing])
                with self.assertRaisesRegex(release.ReleaseError, 'recovery_and_file_transfer_skills_required'):
                    release.skill_payload(config, self.files, self.source_commit, False)

    def test_public_export_commit_tracks_exactly_the_reviewed_source_tree(self):
        payload = dict(self.files)
        for names, accepted in [(sorted(payload), True), (sorted(payload) + ['private-state.json'], False),
                                (sorted(payload)[1:], False)]:
            with self.subTest(accepted=accepted, count=len(names)):
                data = ('\0'.join(names) + '\0').encode()
                with patch.object(release.subprocess, 'check_output', return_value=data):
                    if accepted:
                        release.check_public_inventory(self.root, payload)
                    else:
                        with self.assertRaisesRegex(release.ReleaseError, 'tracked_public_inventory_differs'):
                            release.check_public_inventory(self.root, payload)

    def test_changed_dependency_lock_or_unaccepted_profile_blocks_final_build(self):
        payload = dict(self.files)
        payload[self.config['dependency_lock']] += b'# unreviewed\n'
        with self.assertRaisesRegex(release.ReleaseError, 'public_dependency_lock_differs'):
            release.check_dependency_profile(self.root, self.config, payload, False)
        payload = dict(self.files)
        profile = json.loads(payload[self.config['dependency_profile']])
        profile['public_install_recommended'] = False
        payload[self.config['dependency_profile']] = release.encoded(profile)
        with self.assertRaisesRegex(release.ReleaseError, 'public_dependency_profile_not_accepted'):
            release.check_dependency_profile(self.root, self.config, payload, False)
        profile['public_install_recommended'] = True
        profile['lock_file'] = 'requirements/old-profile.lock'
        payload[self.config['dependency_profile']] = release.encoded(profile)
        with self.assertRaisesRegex(release.ReleaseError, 'invalid_public_dependency_profile'):
            release.check_dependency_profile(self.root, self.config, payload, False)

    def test_runtime_manifest_cannot_claim_a_different_verified_source_pin(self):
        runtime = json.loads(self.files['release-runtime.json'])
        runtime['source_commit'] = 'e' * 40
        self.files['release-runtime.json'] = release.encoded(runtime)
        self.write_fixture()
        with self.assertRaisesRegex(release.ReleaseError, 'runtime_verified_source_commit_differs'):
            release.prepare_inputs(self.root, prepare=True)

    def test_preview_identity_and_unapproved_license_cannot_pass_final_asset_check(self):
        path = self.base / 'preview.vsix'
        self.fake_vsix(self.root, path, stable=True, product=self.config, prepare=True)
        with self.assertRaisesRegex(release.ReleaseError, 'vsix_product_identity_differs'):
            release.validate_vsix(path, self.config, False)

    def test_output_never_clobbers_existing_asset_or_writes_into_source(self):
        output = self.base / 'exists'
        output.mkdir()
        with self.assertRaisesRegex(release.ReleaseError, 'release_output_already_exists'):
            self.build(output)
        with self.assertRaisesRegex(release.ReleaseError, 'release_output_must_be_outside_source'):
            self.build(self.root / 'dist')
        real = self.base / 'real'
        real.mkdir()
        linked = self.base / 'linked'
        linked.symlink_to(real, target_is_directory=True)
        with self.assertRaisesRegex(release.ReleaseError, 'linked_release_output'):
            self.build(linked / 'assets')

    def test_export_checkout_rejects_wrapper_private_origin_and_wrong_ci_context(self):
        expected = 'c' * 40
        def git_output(args, **kwargs):
            command = args[3:]
            if command == ['rev-parse', '--show-toplevel']:
                return str(self.root).encode()
            if command == ['rev-parse', 'HEAD']:
                return expected.encode()
            if command[:2] == ['status', '--porcelain']:
                return b''
            if command == ['remote', 'get-url', 'origin']:
                return ('https://github.com/' + release.REPOSITORY + '.git').encode()
            raise AssertionError(command)
        env = {'GITHUB_REPOSITORY': release.REPOSITORY, 'GITHUB_EVENT_NAME': 'push',
               'GITHUB_REF': 'refs/heads/main', 'GITHUB_SHA': expected}
        with patch.object(release.subprocess, 'check_output', side_effect=git_output):
            with patch.dict(os.environ, env):
                release.check_checkout(self.root, expected, ci=True)
                with patch.dict(os.environ, {'GITHUB_REPOSITORY': 'owner/private-tracking'}):
                    with self.assertRaisesRegex(release.ReleaseError, 'unexpected_release_workflow_context'):
                        release.check_checkout(self.root, expected, ci=True)
            with self.assertRaisesRegex(release.ReleaseError, 'export_commit_differs'):
                release.check_checkout(self.root, 'd' * 40)
        def wrapper_output(args, **kwargs):
            if args[3:] == ['rev-parse', '--show-toplevel']:
                return str(self.base).encode()
            return git_output(args, **kwargs)
        with patch.object(release.subprocess, 'check_output', side_effect=wrapper_output):
            with self.assertRaisesRegex(release.ReleaseError, 'clean_product_repository_required'):
                release.check_checkout(self.root, expected)
        def private_origin_output(args, **kwargs):
            if args[3:] == ['remote', 'get-url', 'origin']:
                return b'https://github.com/owner/private-tracking.git'
            return git_output(args, **kwargs)
        with patch.object(release.subprocess, 'check_output', side_effect=private_origin_output):
            with self.assertRaisesRegex(release.ReleaseError, 'unexpected_export_origin'):
                release.check_checkout(self.root, expected)


if __name__ == '__main__':
    unittest.main()
