"""Stable artifacts isolate the preview and remain reproducible and guarded."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

from tools import build_vsix

ROOT = Path(__file__).resolve().parents[1]


class StableVSIXTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ia-stable-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / 'source'
        for directory in ('extension', 'intern_personal', 'docs'):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        for name in ('package.json', 'extension.js', 'README.md'):
            shutil.copyfile(ROOT / 'extension' / name, self.root / 'extension' / name)
        # Exercise conversion from a local preview even when public source already
        # carries the stable identity. Fixtures do not inherit real release approval.
        manifest_path = self.root / 'extension/package.json'
        manifest = json.loads(manifest_path.read_text())
        namespace = manifest['contributes']['commands'][0]['command'].split('.')[0]
        manifest = json.loads(json.dumps(manifest).replace(namespace, 'internAgent'))
        manifest.update(name='intern-agent-personal', publisher='intern-agent-local',
                        displayName='Intern Agent Personal (Preview)', version='0.8.0', license='UNLICENSED')
        manifest_path.write_text(json.dumps(manifest))
        script = self.root / 'extension/extension.js'
        script.write_text(script.read_text().replace(namespace, 'internAgent')
                          .replace("createOutputChannel('Intern Agent (Public)')", "createOutputChannel('Intern Agent')"))
        (self.root / 'intern_personal/core.py').write_text('# Synthetic runtime\n')
        (self.root / 'docs/guide.md').write_text('# Synthetic guide\n')
        (self.root / 'distribution.json').write_text(json.dumps({'schema': 'intern-agent.distribution-files.v1',
            'include': ['intern_personal/*.py', 'docs/*.md', 'LICENSE']}))
        self.product = json.loads((ROOT / 'release-product.json').read_text())
        self.product['gates'] = {key: False for key in self.product['gates']}
        self.product['final_handoff'].update(approved=False, source_commit=None, version=None)

    def build(self, name='product.vsix', **kwargs):
        output = self.base / name
        result = build_vsix.build(self.root, output, stable=True, product=self.product, **kwargs)
        return output, result

    def test_preparation_has_separate_identity_namespace_and_pending_license(self):
        before = {p.name: p.read_bytes() for p in (self.root / 'extension').iterdir()}
        output, result = self.build(prepare=True)
        with zipfile.ZipFile(output) as archive:
            manifest = json.loads(archive.read('extension/package.json'))
            script = archive.read('extension/extension.js').decode()
            old = json.loads(before['package.json'])
            new_commands = {v['command'] for v in manifest['contributes']['commands']}
            self.assertFalse(new_commands & {v['command'] for v in old['contributes']['commands']})
            self.assertFalse(set(manifest['contributes']['configuration']['properties']) &
                             set(old['contributes']['configuration']['properties']))
            self.assertIn("getConfiguration('internAgentPublic')", script)
            self.assertNotRegex(script, r'internAgent(?:[.\x27\x22]|Conversation|Initialization)')
            self.assertEqual(manifest['publisher'] + '.' + manifest['name'], 'intern-agent.intern-agent')
            self.assertEqual(manifest['version'], self.product['version'])
            self.assertEqual(manifest['license'], 'UNLICENSED')
            xml = ET.fromstring(archive.read('extension.vsixmanifest'))
            prop = next(v for v in xml.iter() if v.attrib.get('Id', '').endswith('.PreRelease'))
            self.assertEqual(prop.attrib['Value'], 'false')
            types = ET.fromstring(archive.read('[Content_Types].xml'))
            self.assertEqual({v.attrib['PartName'] for v in types},
                             {'/' + n for n in archive.namelist() if n != '[Content_Types].xml'})
        self.assertTrue(result['preparation_only'])
        self.assertFalse(result['release_approved'])
        self.assertFalse(result['published'])
        self.assertEqual(before, {p.name: p.read_bytes() for p in (self.root / 'extension').iterdir()})

    def test_zip_bytes_ignore_source_mtime_and_refuse_clobber(self):
        first, _ = self.build(prepare=True)
        for file in self.root.rglob('*'):
            if file.is_file():
                os.utime(file, (1700000000, 1700000000))
        second, _ = self.build('again.vsix', prepare=True)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.build(prepare=True)

    def test_final_handoff_cannot_be_bypassed_by_stable_flag(self):
        with self.assertRaisesRegex(ValueError, 'handoff pending'):
            self.build()
        with self.assertRaisesRegex(ValueError, 'Preparation mode requires'):
            build_vsix.build(self.root, self.base / 'wrong.vsix', prepare=True)
        self.product['extension']['namespace'] = 'internAgent'
        with self.assertRaisesRegex(ValueError, 'Invalid stable product identity'):
            self.build(prepare=True)

    def test_approved_synthetic_product_embeds_license_and_remains_stable(self):
        (self.root / 'LICENSE').write_text('MIT License\n\nCopyright synthetic fixture\n')
        manifest = json.loads((self.root / 'extension/package.json').read_text())
        manifest = json.loads(json.dumps(manifest).replace('internAgent', 'internAgentPublic'))
        manifest.update(license='MIT', name='intern-agent', publisher='intern-agent',
                        displayName='Intern Agent', version=self.product['version'])
        script = self.root / 'extension/extension.js'
        script.write_text(script.read_text().replace('internAgent', 'internAgentPublic'))
        (self.root / 'extension/package.json').write_text(json.dumps(manifest))
        self.product['gates'] = {k: True for k in self.product['gates']}
        self.product['final_handoff'].update(approved=True, source_commit='a' * 40,
            repository=self.product['repository'], version=self.product['version'])
        files = [p for p in self.root.rglob('*') if p.is_file()]
        inventory = {'schema': 'intern-agent.source-candidate.v1', 'source_commit': 'a' * 40,
            'ownership_approved': True, 'license_approved': True, 'dependency_security_approved': True,
            'files': [{'path': str(p.relative_to(self.root)), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest(),
                       'provenance': 'synthetic-fixture', 'rights': 'approved_for_distribution'} for p in files]}
        (self.root / 'source-distribution.json').write_text(json.dumps(inventory))
        output, result = self.build()
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(archive.read('extension/LICENSE'), (self.root / 'LICENSE').read_bytes())
            self.assertEqual(json.loads(archive.read('extension/package.json'))['license'], 'MIT')
        self.assertTrue(result['release_approved'])
        alias = self.base / 'release-alias.vsix'
        alias_result = build_vsix.build(self.root, alias, release=True, product=self.product)
        self.assertEqual(alias.read_bytes(), output.read_bytes())
        self.assertTrue(alias_result['stable'])

    def test_editor_installation_metadata_is_rejected_as_a_build_input(self):
        path = self.root / 'extension/package.json'
        manifest = json.loads(path.read_text())
        manifest['__metadata'] = {'synthetic_installation': True}
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'metadata is not a source input'):
            self.build(prepare=True)

    def test_output_parent_symlink_is_rejected_before_writing(self):
        outside = self.base / 'outside'
        outside.mkdir()
        linked = self.base / 'linked'
        linked.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'Linked extension output'):
            build_vsix.build(self.root, linked / 'product.vsix', stable=True,
                             product=self.product, prepare=True)
        self.assertEqual(list(outside.iterdir()), [])

    def test_linked_frontend_directory_is_rejected_even_within_source(self):
        stored = self.root / 'stored-frontend'
        (self.root / 'extension').rename(stored)
        (self.root / 'extension').symlink_to(stored, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'Linked/outside extension input'):
            self.build(prepare=True)
        self.assertFalse((self.base / 'product.vsix').exists())

    def test_generated_product_passes_existing_editor_behaviour_checks(self):
        node = os.environ.get('NODE_BINARY') or shutil.which('node')
        if not node:
            self.skipTest('Node 18+ is required for the editor harness')
        version = subprocess.check_output([node, '--version'], text=True).strip()
        if int(version.lstrip('v').split('.')[0]) < 18:
            self.skipTest('Select Node 18+ through NODE_BINARY')
        output, _ = self.build(prepare=True)
        extension = self.base / 'installed/extension'
        extension.mkdir(parents=True)
        with zipfile.ZipFile(output) as archive:
            script = extension / 'extension.js'
            script.write_bytes(archive.read('extension/extension.js'))
            (extension / 'package.json').write_bytes(archive.read('extension/package.json'))
        harness = (ROOT / 'tests/extension.test.js').read_text()
        test = self.base / 'product-editor-test.js'
        test.write_text(harness)
        env = os.environ | {'INTERN_AGENT_TEST_EXTENSION_ROOT': str(extension)}
        result = subprocess.run([node, str(test)], capture_output=True, text=True, timeout=45, env=env)
        self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[-2000:])
