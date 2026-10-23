"""Release skills install only from a complete reviewed archive into an explicit target."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import stat
import tempfile
import unittest
import warnings
import zipfile

from tools import install_skill_bundle as installer


class SkillBundleInstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='ia-skill-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.destination = self.root / 'explicit-skills'

    def bundle(self, *, changes=None, manifest_changes=None, extra=None, symlink=None, duplicate=False):
        files = {'skills/example-skill/SKILL.md': b'---\nname: example-skill\ndescription: Example\n---\nRead [the guide](references/guide.md).\n',
                 'skills/example-skill/references/guide.md': b'# Reference\nReviewed inert instructions.\n',
                 'skills/second-skill/SKILL.md': b'# Another reviewed skill\n'}
        files.update(changes or {})
        manifest = {'schema': installer.SCHEMA, 'product': 'intern-agent', 'version': '1.0.0',
                    'source_commit': 'a' * 40, 'license': 'MIT', 'release_approved': True,
                    'files': [{'path': name, 'sha256': hashlib.sha256(data).hexdigest()} for name, data in sorted(files.items())],
                    'license_file': {'path': 'LICENSE', 'sha256': hashlib.sha256(b'MIT fixture notice\n').hexdigest()}}
        manifest.update(manifest_changes or {})
        path = self.root / ('bundle-' + str(len(list(self.root.glob('bundle-*')))) + '.zip')
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(installer.MANIFEST, json.dumps(manifest))
            archive.writestr('LICENSE', b'MIT fixture notice\n')
            for name, data in files.items():
                archive.writestr(name, data)
            for name, data in (extra or {}).items():
                archive.writestr(name, data)
            if symlink:
                entry = zipfile.ZipInfo(symlink)
                entry.create_system = 3
                entry.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(entry, b'outside')
            if duplicate:
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore', UserWarning)
                    archive.writestr('skills/example-skill/SKILL.md', b'duplicate')
        return path, hashlib.sha256(path.read_bytes()).hexdigest(), files

    def test_plan_writes_nothing_and_explicit_apply_copies_resources_and_private_receipt(self):
        archive, digest, files = self.bundle()
        result = installer.plan(archive, self.destination)
        self.assertFalse(result['writes_files'])
        self.assertFalse(self.destination.exists())
        result = installer.install(archive, self.destination, digest)
        self.assertEqual(result['files_installed'], len(files))
        for name, data in files.items():
            self.assertEqual((self.destination / name.removeprefix('skills/')).read_bytes(), data)
        receipt = Path(result['receipt'])
        self.assertEqual(receipt.stat().st_mode & 0o777, 0o600)
        self.assertEqual(receipt.parent.stat().st_mode & 0o777, 0o700)
        metadata = json.loads(receipt.read_bytes())
        self.assertEqual(metadata['bundle_sha256'], digest)
        self.assertEqual(len(metadata['files']), len(files))
        self.assertNotIn(str(self.root), receipt.read_text())
        self.assertFalse((self.destination / 'LICENSE').exists())
        notice = receipt.with_suffix('.LICENSE')
        self.assertEqual(hashlib.sha256(notice.read_bytes()).hexdigest(), metadata['license_file']['sha256'])
        self.assertEqual(notice.stat().st_mode & 0o777, 0o600)

    def test_idempotence_preserves_identical_and_unrelated_files(self):
        archive, digest, _ = self.bundle()
        first = installer.install(archive, self.destination, digest)
        unrelated = self.destination / 'example-skill/owner-notes.md'
        unrelated.write_text('Keep my notes\n')
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.destination.rglob('*') if p.is_file()}
        directories = {p: p.stat().st_mtime_ns for p in [self.destination, *self.destination.rglob('*')] if p.is_dir()}
        second = installer.install(archive, self.destination, digest)
        self.assertEqual(second['files_installed'], 0)
        self.assertEqual(second['files_preserved'], 3)
        self.assertEqual({p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before}, before)
        self.assertEqual({p: p.stat().st_mtime_ns for p in directories}, directories)
        self.assertEqual(first['receipt'], second['receipt'])

    def test_conflict_in_later_skill_causes_no_partial_install(self):
        archive, digest, _ = self.bundle()
        existing = self.destination / 'second-skill/SKILL.md'
        existing.parent.mkdir(parents=True)
        existing.write_text('Owner-maintained instructions\n')
        with self.assertRaisesRegex(installer.SkillInstallError, 'existing_skill_resource_differs'):
            installer.install(archive, self.destination, digest)
        self.assertFalse((self.destination / 'example-skill').exists())
        self.assertFalse((self.destination / installer.RECEIPTS).exists())
        self.assertEqual(existing.read_text(), 'Owner-maintained instructions\n')

    def test_existing_resource_conflict_and_reused_release_version_are_preserved(self):
        archive, digest, _ = self.bundle()
        installer.install(archive, self.destination, digest)
        changed, changed_digest, _ = self.bundle(changes={'skills/example-skill/references/guide.md': b'Changed instructions\n'})
        with self.assertRaisesRegex(installer.SkillInstallError, 'existing_skill_resource_differs'):
            installer.install(changed, self.destination, changed_digest)
        changed, changed_digest, _ = self.bundle(manifest_changes={'source_commit': 'b' * 40})
        with self.assertRaisesRegex(installer.SkillInstallError, 'installed_bundle_version_conflict'):
            installer.install(changed, self.destination, changed_digest)

    def test_existing_notice_conflict_is_preflighted_before_skill_writes(self):
        archive, digest, _ = self.bundle()
        receipts = self.destination / installer.RECEIPTS
        receipts.mkdir(mode=0o700, parents=True)
        notice = receipts / '1.0.0.LICENSE'; notice.write_text('Owner-maintained notice\n'); notice.chmod(0o600)
        with self.assertRaisesRegex(installer.SkillInstallError, 'installed_bundle_notice_conflict'):
            installer.install(archive, self.destination, digest)
        self.assertFalse((self.destination / 'example-skill').exists())
        self.assertEqual(notice.read_text(), 'Owner-maintained notice\n')

    def test_apply_requires_external_checksum_and_rejects_mismatch_before_writes(self):
        archive, digest, _ = self.bundle()
        with self.assertRaisesRegex(installer.SkillInstallError, 'apply_requires_external_bundle_sha256'):
            installer.install(archive, self.destination, None)
        with self.assertRaisesRegex(installer.SkillInstallError, 'bundle_sha256_mismatch'):
            installer.install(archive, self.destination, '0' * 64)
        self.assertFalse(self.destination.exists())

    def test_preparation_bundle_can_only_be_planned(self):
        archive, digest, _ = self.bundle(manifest_changes={'license': 'UNLICENSED', 'release_approved': False})
        # A preparation bundle has no license_file/member, unlike the fixture default.
        with zipfile.ZipFile(archive) as original:
            entries = {name: original.read(name) for name in original.namelist() if name != 'LICENSE'}
        metadata = json.loads(entries[installer.MANIFEST]); metadata.pop('license_file')
        entries[installer.MANIFEST] = json.dumps(metadata).encode()
        archive.unlink()
        with zipfile.ZipFile(archive, 'w') as rebuilt:
            for name, data in entries.items():
                rebuilt.writestr(name, data)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        self.assertFalse(installer.plan(archive, self.destination)['apply_allowed'])
        with self.assertRaisesRegex(installer.SkillInstallError, 'preparation_bundle_is_plan_only'):
            installer.install(archive, self.destination, digest)
        self.assertFalse(self.destination.exists())

    def test_source_and_destination_ancestor_links_are_rejected(self):
        archive, digest, _ = self.bundle()
        link = self.root / 'linked-bundle.zip'; link.symlink_to(archive)
        with self.assertRaises(OSError):
            installer.plan(link, self.destination)
        parent_link = self.root / 'linked-parent'; parent_link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError):
            installer.plan(parent_link / archive.name, self.destination)
        with self.assertRaises(OSError):
            installer.install(archive, parent_link / 'other-skills', digest)
        self.destination.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError):
            installer.plan(archive, self.destination)

    def test_linked_skill_or_receipt_directory_is_rejected_without_writes(self):
        archive, digest, _ = self.bundle()
        self.destination.mkdir()
        external = self.root / 'elsewhere'; external.mkdir()
        skill = self.destination / 'example-skill'; skill.symlink_to(external, target_is_directory=True)
        with self.assertRaises(OSError):
            installer.install(archive, self.destination, digest)
        self.assertEqual(list(external.iterdir()), [])
        skill.unlink()
        (self.destination / installer.RECEIPTS).symlink_to(external, target_is_directory=True)
        with self.assertRaises(OSError):
            installer.install(archive, self.destination, digest)
        self.assertFalse((self.destination / 'example-skill').exists())

    def test_malformed_inventory_and_members_are_rejected(self):
        cases = [dict(extra={'unlisted.md': b'Unlisted'}), dict(extra={'../outside.md': b'Traversal'}),
                 dict(extra={'skills/example-skill/./hidden.md': b'Not normalized'}),
                 dict(extra={'skills\\example-skill\\hidden.md': b'Backslash'}),
                 dict(symlink='skills/example-skill/references/link.md'), dict(duplicate=True),
                 dict(extra={'skills/EXAMPLE-skill/SKILL.md': b'Case collision'}),
                 dict(changes={'skills/example-skill/run.py': b'print(1)\n'}),
                 dict(changes={'skills/example-skill/SKILL.md': b'[Missing guide](references/missing.md)\n'}),
                 dict(changes={'skills/example-skill/SKILL.md': b'[Outside guide](../../outside.md)\n'}),
                 dict(changes={'skills/example-skill/references/binary.md': b'bad\x00text'}),
                 dict(changes={'skills/example-skill/references/invalid.md': b'\xff'}),
                 dict(changes={'skills/example-skill/references/large.md': b'x' * (installer.MAX_FILE + 1)}),
                 dict(manifest_changes={'schema': 'unknown'}), dict(manifest_changes={'release_approved': 'true'}),
                 dict(manifest_changes={'license_file': {'path': 'LICENSE', 'sha256': '0' * 64}})]
        for case in cases:
            with self.subTest(case=list(case)):
                archive, digest, _ = self.bundle(**case)
                with self.assertRaises((ValueError, OSError)):
                    installer.install(archive, self.destination, digest)
                self.assertFalse(self.destination.exists())

    def test_invalid_zip_crc_and_resource_hash_are_rejected_without_writes(self):
        invalid = self.root / 'invalid.zip'; invalid.write_bytes(b'not a ZIP archive')
        with self.assertRaises(zipfile.BadZipFile):
            installer.inspect(invalid)
        archive, digest, files = self.bundle()
        corrupted = self.root / 'corrupted.zip'
        with zipfile.ZipFile(archive) as original, zipfile.ZipFile(corrupted, 'w', zipfile.ZIP_STORED) as output:
            for name in original.namelist():
                output.writestr(name, original.read(name))
        raw = corrupted.read_bytes(); marker = files['skills/second-skill/SKILL.md']
        self.assertIn(marker, raw)
        corrupted.write_bytes(raw.replace(marker, b'!' + marker[1:], 1))
        with self.assertRaises(zipfile.BadZipFile):
            installer.install(corrupted, self.destination, hashlib.sha256(corrupted.read_bytes()).hexdigest())
        manifest = {'files': [{'path': name, 'sha256': '0' * 64} for name in files]}
        wrong, wrong_digest, _ = self.bundle(manifest_changes=manifest)
        with self.assertRaisesRegex(installer.SkillInstallError, 'bundle_resource_sha256_mismatch'):
            installer.install(wrong, self.destination, wrong_digest)
        self.assertFalse(self.destination.exists())

    def test_cli_returns_fixed_errors_and_does_not_apply_by_default(self):
        archive, digest, _ = self.bundle()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(installer.main([str(archive), '--destination', str(self.destination)]), 0)
        self.assertFalse(json.loads(output.getvalue())['writes_files'])
        self.assertFalse(self.destination.exists())
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(installer.main([str(archive), '--destination', str(self.destination), '--apply']), 2)
        self.assertEqual(json.loads(output.getvalue())['error'], 'apply_requires_external_bundle_sha256')
        self.assertNotIn(str(self.root), output.getvalue())


if __name__ == '__main__':
    unittest.main()
