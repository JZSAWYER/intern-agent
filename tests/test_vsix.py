import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from intern_personal.core import Invalid
from tools.build_vsix import build


class PreviewPackageTests(unittest.TestCase):
    def fixture(self, root):
        (root / "extension").mkdir()
        (root / "intern_personal").mkdir()
        (root / "distribution.json").write_text(json.dumps({
            "schema": "intern-agent.distribution-files.v1", "include": ["intern_personal/*.py"]}))
        (root / "intern_personal/__init__.py").write_text('"""Public backend."""\n')
        (root / "extension/package.json").write_text(json.dumps({
            "name": "preview", "version": "0.2.0", "publisher": "local", "displayName": "Preview",
            "description": "Test preview", "categories": ["Other"], "engines": {"vscode": "^1.90.0"}}))
        (root / "extension/extension.js").write_text("exports.activate = () => {};\n")
        (root / "extension/README.md").write_text("Public preview\n")

    def test_archive_excludes_private_state_and_is_installable_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            (root / "credentials.json").write_text("PRIVATE SENTINEL")
            (root / "personal.json").write_text("PRIVATE SENTINEL")
            (root / "runtime").mkdir()
            (root / "runtime/task.sqlite3").write_text("PRIVATE SENTINEL")
            output = root / "preview.vsix"
            build(root, output)
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(set(archive.namelist()), {
                    "[Content_Types].xml", "extension.vsixmanifest", "extension/package.json",
                    "extension/extension.js", "extension/README.md", "extension/backend/intern_personal/__init__.py"})
                self.assertTrue(all(b"PRIVATE SENTINEL" not in archive.read(n) for n in archive.namelist()))
            with self.assertRaises(Invalid):
                build(root, output)

    def test_personal_owner_in_extension_is_rejected_before_packaging(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            (root / "extension/extension.js").write_text("const owner = 'private-fixture-owner';\n")
            with self.assertRaises(Invalid):
                build(root, root / "preview.vsix", "private-fixture-owner")
            self.assertFalse((root / "preview.vsix").exists())

    def test_stable_release_cannot_relabel_unlicensed_development_package(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); self.fixture(root)
            with self.assertRaisesRegex(Invalid, 'licensed'):
                build(root, root / 'release.vsix', release=True)
            self.assertFalse((root / 'release.vsix').exists())
            manifest = json.loads((root / 'extension/package.json').read_text())
            manifest.update(name='intern-agent', displayName='Intern Agent', publisher='intern-agent', version='1.0.0', license='MIT')
            (root / 'extension/package.json').write_text(json.dumps(manifest))
            (root / 'LICENSE').write_text('MIT License\n')
            # A licensed label still needs the reviewed product configuration,
            # source inventory and owner handoff tested by test_stable_vsix.py.
            with self.assertRaisesRegex(Invalid, 'final release handoff'):
                build(root, root / 'release.vsix', release=True)
            self.assertFalse((root / 'release.vsix').exists())

    def test_preview_preserves_license_notice_without_enabling_stable_release(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); self.fixture(root)
            (root / 'LICENSE').write_text('MIT License\n')
            output = root / 'preview.vsix'
            result = build(root, output)
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(archive.read('extension/LICENSE'), b'MIT License\n')
                from xml.etree import ElementTree as ET
                metadata = ET.fromstring(archive.read('extension.vsixmanifest'))
                properties = {entry.attrib['Id']: entry.attrib['Value'] for entry in metadata.iter() if entry.tag.endswith('Property')}
                self.assertEqual(properties['Microsoft.VisualStudio.Code.PreRelease'], 'true')
            self.assertFalse(result['stable'])
            self.assertFalse(result['release_approved'])

    def test_private_files_cannot_enter_through_an_internal_linked_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); self.fixture(root)
            private = root / 'runtime'; private.mkdir()
            (private / 'private.md').write_text('Private project history sentinel\n')
            (root / 'docs').symlink_to(private, target_is_directory=True)
            manifest = json.loads((root / 'distribution.json').read_text())
            manifest['include'].append('docs/*.md')
            (root / 'distribution.json').write_text(json.dumps(manifest))
            with self.assertRaises(Invalid):
                build(root, root / 'preview.vsix')
            self.assertFalse((root / 'preview.vsix').exists())
