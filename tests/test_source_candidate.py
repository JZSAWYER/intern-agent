"""Protect public export boundaries independently of the VSIX allowlist."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

SPEC = importlib.util.spec_from_file_location('source_check', Path(__file__).resolve().parents[1] / 'tools/check_source_candidate.py')
check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check)


class SourceCandidateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ia-export-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.files = {'README.md': '# Synthetic fixture\n', 'extension/package.json': '{"license":"UNLICENSED"}'}
        for name, text in self.files.items():
            file = self.root / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(text)
        self.manifest = {'schema': 'intern-agent.source-candidate.v1', 'ownership_approved': False,
                         'license_approved': False, 'files': [self.entry(name) for name in self.files]}
        self.save()

    def entry(self, name):
        return {'path': name, 'sha256': hashlib.sha256((self.root / name).read_bytes()).hexdigest(),
                'provenance': 'synthetic-fixture', 'rights': 'owner_confirmation_required'}

    def save(self):
        (self.root / check.MANIFEST).write_text(json.dumps(self.manifest))

    def test_export_ignores_private_state_and_all_git_history(self):
        for name in ['.git/objects/private', '.intern_workspace/history.md', 'runtime/queue.db', 'credentials.json']:
            file = self.root / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text('private synthetic state')
        payload = check.review(self.root)
        archive = self.root / 'review.zip'
        check.archive(payload, archive)
        with zipfile.ZipFile(archive) as output:
            self.assertEqual(set(output.namelist()), set(self.files) | {check.MANIFEST})
            self.assertEqual(output.read('README.md'), self.files['README.md'].encode())
        with self.assertRaises(check.Rejected):
            check.archive(payload, archive)

    def test_private_inputs_cannot_be_added_to_manifest(self):
        for name in ['runtime/queue.db', '.intern_workspace/history.md', '../outside.py', '/absolute.py']:
            self.manifest['files'] = [{'path': name, 'sha256': 'unused', 'provenance': 'fixture', 'rights': 'pending'}]
            self.save()
            with self.assertRaises(check.Rejected):
                check.review(self.root)

    def test_hash_change_duplicate_and_unlisted_code_are_rejected(self):
        (self.root / 'README.md').write_text('unexpected edit')
        with self.assertRaisesRegex(check.Rejected, 'source_hash_mismatch'):
            check.review(self.root)
        (self.root / 'README.md').write_text(self.files['README.md'])
        self.manifest['files'].append(self.manifest['files'][0]); self.save()
        with self.assertRaisesRegex(check.Rejected, 'duplicate_source_entry'):
            check.review(self.root)
        self.manifest['files'].pop(); self.save()
        (self.root / 'extension/unreviewed.js').write_text('unreviewed')
        with self.assertRaisesRegex(check.Rejected, 'unlisted_source_file'):
            check.review(self.root)

    def test_installed_editor_metadata_cannot_be_exported_as_source(self):
        name = 'extension/package.json'
        (self.root / name).write_text(json.dumps({'license': 'UNLICENSED',
                                                '__metadata': {'synthetic_installation': True}}))
        self.manifest['files'] = [self.entry(path) for path in self.files]
        self.save()
        with self.assertRaisesRegex(check.Rejected, 'installed_editor_metadata_in_source'):
            check.review(self.root)

    def test_ci_workflow_must_be_in_reviewed_source_inventory(self):
        name = '.github/workflows/release.yml'
        workflow = self.root / name
        workflow.parent.mkdir(parents=True)
        workflow.write_text('name: Synthetic fixture\non: push\n')
        with self.assertRaisesRegex(check.Rejected, 'unlisted_source_file'):
            check.review(self.root)
        self.manifest['files'].append(self.entry(name))
        self.save()
        self.assertIn(name, check.review(self.root))

    def test_linked_parent_directory_is_rejected(self):
        directory = self.root / 'external'
        directory.mkdir(); (directory / 'module.py').write_text('# fixture\n')
        (self.root / 'linked').symlink_to(directory, target_is_directory=True)
        self.manifest['files'].append(self.entry('linked/module.py')); self.save()
        with self.assertRaisesRegex(check.Rejected, 'linked_source_input'):
            check.review(self.root)

    def test_sensitive_content_never_appears_in_error_label(self):
        synthetic = 'ghp_' + 'X' * 24
        (self.root / 'README.md').write_text(synthetic)
        self.manifest['files'][0] = self.entry('README.md'); self.save()
        with self.assertRaises(check.Rejected) as caught:
            check.review(self.root)
        self.assertEqual(str(caught.exception), 'private_content_pattern')
        self.assertNotIn(synthetic, str(caught.exception))

    def test_release_cannot_be_cleared_by_inventory_integrity_alone(self):
        check.review(self.root)
        with self.assertRaisesRegex(check.Rejected, 'ownership_review_pending'):
            check.review(self.root, release=True)
        self.manifest['ownership_approved'] = True; self.save()
        with self.assertRaisesRegex(check.Rejected, 'license_review_pending'):
            check.review(self.root, release=True)

    def test_plaintext_json_credential_cannot_hide_under_an_innocent_name(self):
        file = self.root / 'templates/fixture.json'
        file.parent.mkdir()
        file.write_text(json.dumps({'app_secret': 'synthetic-value-for-rejection'}))
        self.manifest['files'].append(self.entry('templates/fixture.json')); self.save()
        with self.assertRaisesRegex(check.Rejected, 'credential_literal_requires_review'):
            check.review(self.root)

    def test_source_archive_is_reproducible(self):
        payload = check.review(self.root)
        first = check.archive(payload, self.root / 'first.zip')
        second = check.archive(payload, self.root / 'second.zip')
        self.assertEqual(first, second)
