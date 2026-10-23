"""A VSIX must contain the allowlist and helpers needed to install its backend."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('transfer_vsix_build',ROOT/'tools/build_vsix.py')
builder=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(builder)


class TransferPackagingTests(unittest.TestCase):
    def test_real_preview_contains_self_sufficient_installer_inputs(self):
        with tempfile.TemporaryDirectory(prefix='ia-package-') as tmp:
            output=Path(tmp)/'preview.vsix';builder.build(ROOT,output)
            with zipfile.ZipFile(output) as z:
                names=set(z.namelist())
                for name in ['distribution.json','release-runtime.json','requirements/deployed-linux-py312.lock',
                             'requirements/deployed-profile.json','tools/install_runtime.py','tools/check_release_runtime.py',
                             'skills/install-on-linux/SKILL.md','skills/install-on-linux/references/installation-lifecycle.md',
                             'skills/collaborate-on-source/SKILL.md']:
                    self.assertIn('extension/backend/'+name,names)
                baseline=json.loads(z.read('extension/backend/release-runtime.json'))
                self.assertEqual(baseline['snapshot_algorithm'],'deployment-input-order-v3')
                inputs={entry['path'] for entry in baseline['runtime_files']}
                modules={str(p.relative_to(ROOT)) for p in (ROOT/'intern_personal').glob('*.py')}
                guides={str(p.relative_to(ROOT)) for p in (ROOT/'docs').glob('*.md')}
                self.assertTrue(modules)
                self.assertEqual(inputs,modules|guides|{'skills/feishu-file-transfer/SKILL.md'})
                self.assertEqual(len(baseline['runtime_files']),len(inputs))
                for entry in baseline['runtime_files']:
                    self.assertIn('extension/backend/'+entry['path'],names)
                self.assertIn('extension/backend/docs/BOT_COMMUNICATION.md', names)
                self.assertIn('extension/backend/docs/AUTORESEARCH.md', names)
                self.assertIn('extension/backend/docs/SESSION_FEATURES.md', names)
                self.assertIn('extension/backend/docs/RECORD_QUALITY.md', names)
                self.assertIn('extension/backend/docs/RECORD_STORAGE.md', names)
                self.assertNotIn('extension/backend/personal.json',names)
                self.assertFalse(any(n.endswith('.whl') for n in names))
