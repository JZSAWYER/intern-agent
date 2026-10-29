"""Public and explicit reference installs preserve source parity and isolate state."""
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import Mock,patch
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import check_release_runtime as parity
import install_runtime as installer


class TransferRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='ia-transfer-');self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.root=self.base/'source';self.root.mkdir()
        self.prefix=self.base/'installed';self.state=self.base/'private';self.wheels=self.base/'wheels';self.wheels.mkdir()
        payload={'intern_personal/core.py':b'# synthetic runtime\n','skills/feishu-file-transfer/SKILL.md':b'# Synthetic skill\n'}
        for name,data in payload.items():
            f=self.root/name;f.parent.mkdir(parents=True,exist_ok=True);f.write_bytes(data)
        frontend=[]
        for name in ['extension/package.json','extension/extension.js']:
            f=self.root/name;f.parent.mkdir(exist_ok=True);f.write_text('{}');frontend.append({'path':name,'sha256':hashlib.sha256(f.read_bytes()).hexdigest()})
        manifest={'schema':'intern-agent.runtime-baseline.v1','snapshot_sha256':parity.fingerprint(payload),
                  'runtime_files':[{'path':n,'sha256':hashlib.sha256(d).hexdigest()} for n,d in payload.items()],
                  'frontend':{'files':frontend}}
        (self.root/'release-runtime.json').write_text(json.dumps(manifest))
        (self.root/'requirements').mkdir()
        self.wheel_data=b'synthetic reviewed wheel bytes\n'
        self.wheel_entry={'name':'fixture','version':'1.0.0','wheel':'fixture-1.0.0-py3-none-any.whl',
            'sha256':hashlib.sha256(self.wheel_data).hexdigest(),'size':len(self.wheel_data),
            'download_url':'https://files.pythonhosted.org/packages/aa/bb/'+'c'*60+'/fixture-1.0.0-py3-none-any.whl'}
        (self.wheels/self.wheel_entry['wheel']).write_bytes(self.wheel_data)
        for lock_name,profile_name,profile_id,recommended in [
            (installer.LOCK,installer.PUBLIC_PROFILE,'public-linux-py312',True),
            (installer.REFERENCE_LOCK,installer.REFERENCE_PROFILE,'deployed-reference-only',False)]:
            lock=self.root/lock_name;lock.write_text('fixture==1.0.0 --hash=sha256:'+self.wheel_entry['sha256']+'\n')
            (self.root/profile_name).write_text(json.dumps({'profile':profile_id,'lock_file':lock_name,
                'public_install_recommended':recommended,'lock_sha256':hashlib.sha256(lock.read_bytes()).hexdigest(),
                'wheels':[self.wheel_entry]}))
        self.inputs=['intern_personal/core.py','skills/feishu-file-transfer/SKILL.md','release-runtime.json',
                     installer.LOCK,installer.PUBLIC_PROFILE,installer.REFERENCE_LOCK,installer.REFERENCE_PROFILE]

    def installed_frontend(self):
        package={'name':'intern-agent','publisher':'example','version':'0.1.0',
                 'contributes':{'commands':[{'command':'internAgent.setup','title':'Set up'}]}}
        data=json.dumps(package,indent=2).encode()
        (self.root/'extension/package.json').write_bytes(data)
        manifest=json.loads((self.root/'release-runtime.json').read_text())
        entry=next(e for e in manifest['frontend']['files'] if e['path']=='extension/package.json')
        entry['sha256']=hashlib.sha256(data).hexdigest()
        entry['canonical_sha256']=parity.canonical_manifest_sha256(data)
        (self.root/'release-runtime.json').write_text(json.dumps(manifest))
        installed=self.base/'synthetic-editor-extension/backend'
        shutil.copytree(self.root,installed,ignore=shutil.ignore_patterns('extension'))
        package['__metadata']={'synthetic_installation':True}
        (installed.parent/'package.json').write_text(json.dumps(package,sort_keys=True))
        (installed.parent/'extension.js').write_bytes((self.root/'extension/extension.js').read_bytes())
        return installed,package

    def test_installed_editor_metadata_is_accepted_only_by_optional_canonical_hash(self):
        installed,package=self.installed_frontend()
        self.assertTrue(parity.verify(self.root)['ok'])
        self.assertTrue(parity.verify(installed)['ok'])
        canonical=json.loads((self.root/'release-runtime.json').read_text())['frontend']['files'][0]['canonical_sha256']
        self.assertEqual(parity.canonical_manifest_sha256(json.dumps(package),installed=True),canonical)
        manifest=json.loads((installed/'release-runtime.json').read_text())
        del manifest['frontend']['files'][0]['canonical_sha256']
        (installed/'release-runtime.json').write_text(json.dumps(manifest))
        with self.assertRaisesRegex(parity.ParityError,'frontend_source_drift'):parity.verify(installed)
        (installed.parent/'package.json').write_bytes((self.root/'extension/package.json').read_bytes())
        self.assertTrue(parity.verify(installed)['ok'])

    def test_source_and_vsix_still_require_exact_manifest_bytes(self):
        _,package=self.installed_frontend();original=(self.root/'extension/package.json').read_bytes()
        changed=json.dumps(package).encode();(self.root/'extension/package.json').write_bytes(changed)
        with self.assertRaisesRegex(parity.ParityError,'frontend_source_drift'):parity.verify(self.root)
        (self.root/'extension/package.json').write_bytes(original)
        archive=self.base/'metadata-is-not-a-source-input.vsix'
        manifest=json.loads((self.root/'release-runtime.json').read_text())
        with zipfile.ZipFile(archive,'w') as z:
            for entry in manifest['runtime_files']:
                z.writestr('extension/backend/'+entry['path'],(self.root/entry['path']).read_bytes())
            for entry in manifest['frontend']['files']:
                z.writestr(entry['path'],changed if entry['path']=='extension/package.json' else (self.root/entry['path']).read_bytes())
        with self.assertRaisesRegex(parity.ParityError,'vsix_frontend_source_drift'):
            parity.verify(self.root,vsix=archive)

    def test_editor_metadata_cannot_hide_changed_identity_commands_or_javascript(self):
        installed,package=self.installed_frontend();path=installed.parent/'package.json'
        for field in ['name','publisher','version','command']:
            changed=json.loads(json.dumps(package))
            if field=='command':changed['contributes']['commands'][0]['command']='internAgent.unexpected'
            else:changed[field]='unexpected'
            path.write_text(json.dumps(changed))
            with self.subTest(field=field),self.assertRaisesRegex(parity.ParityError,'frontend_source_drift'):
                parity.verify(installed)
        path.write_text(json.dumps(package));(installed.parent/'extension.js').write_text('{};')
        with self.assertRaisesRegex(parity.ParityError,'frontend_source_drift'):parity.verify(installed)

    def test_metadata_normalization_never_applies_to_backend_embedded_source(self):
        _,package=self.installed_frontend();backend=self.base/'embedded/backend'
        shutil.copytree(self.root,backend)
        (backend/'extension/package.json').write_text(json.dumps(package))
        with self.assertRaisesRegex(parity.ParityError,'frontend_source_drift'):parity.verify(backend)

    def test_malformed_or_duplicate_editor_metadata_fails_closed(self):
        installed,package=self.installed_frontend();path=installed.parent/'package.json'
        for metadata in [None,[],True,'unexpected']:
            changed={**package,'__metadata':metadata};path.write_text(json.dumps(changed))
            with self.subTest(metadata=metadata),self.assertRaisesRegex(parity.ParityError,'frontend_source_drift'):
                parity.verify(installed)
        valid=json.dumps(package)
        invalid=[valid.replace('"name": "intern-agent"','"name": "intern-agent", "name": "intern-agent"'),
                 valid.replace('"synthetic_installation": true','"synthetic_installation": true, "synthetic_installation": true'),
                 valid.replace('"synthetic_installation": true','"synthetic_installation": NaN'),
                 valid.replace('"synthetic_installation": true','"synthetic_installation": 1e10000'),'{malformed']
        for data in invalid:
            path.write_text(data)
            with self.subTest(data=data),self.assertRaisesRegex(parity.ParityError,'frontend_source_drift'):
                parity.verify(installed)

    def test_canonical_manifest_refuses_ambiguous_json_and_missing_editor_metadata(self):
        for data in ['[]','{"name":"one","name":"two"}','{"__metadata":null}','{"value":NaN}']:
            with self.subTest(data=data),self.assertRaises(parity.ParityError):
                parity.canonical_manifest_sha256(data)
        with self.assertRaisesRegex(parity.ParityError,'frontend_installation_metadata_missing'):
            parity.canonical_manifest_sha256('{}',installed=True)

    def test_parity_rejects_changed_missing_extra_and_linked_runtime(self):
        parity.verify(self.root)
        f=self.root/'intern_personal/core.py';original=f.read_bytes();f.write_text('# changed\n')
        with self.assertRaisesRegex(parity.ParityError,'runtime_bytes_differ'):parity.verify(self.root)
        f.write_bytes(original);extra=self.root/'intern_personal/extra.py';extra.write_text('# extra')
        with self.assertRaisesRegex(parity.ParityError,'runtime_module_set_differs'):parity.verify(self.root)
        extra.unlink();other=self.base/'outside.py';other.write_bytes(original);f.unlink();f.symlink_to(other)
        with self.assertRaisesRegex(parity.ParityError,'linked_runtime_file'):parity.verify(self.root)

    def test_vsix_payload_drift_is_rejected_independently(self):
        path=self.base/'preview.vsix'
        manifest=json.loads((self.root/'release-runtime.json').read_text())
        def build(changed=False):
            with zipfile.ZipFile(path,'w') as z:
                for entry in manifest['runtime_files']:
                    data=(self.root/entry['path']).read_bytes()
                    if changed and entry['path'].endswith('core.py'):data=b'# changed'
                    z.writestr('extension/backend/'+entry['path'],data)
                for entry in manifest['frontend']['files']:
                    z.writestr(entry['path'],(self.root/entry['path']).read_bytes())
        build();self.assertTrue(parity.verify(self.root,vsix=path)['vsix_compared'])
        build(True)
        with self.assertRaisesRegex(parity.ParityError,'vsix_runtime_drift'):
            parity.verify(self.root,vsix=path)

    def test_fresh_plan_never_creates_private_state_or_installs(self):
        with patch.object(installer,'build_opener',side_effect=AssertionError('network forbidden')):
            plan=installer.plan(self.root,self.prefix,self.state)
        self.assertFalse(self.prefix.exists());self.assertFalse(self.state.exists())
        self.assertFalse(plan['starts_relay']);self.assertTrue(plan['public_install_recommended'])
        self.assertEqual(plan['dependency_profile'],'public-linux-py312')
        self.assertEqual(plan['dependency_lock'],installer.LOCK)
        self.assertFalse(plan['network_access'])

    def test_runtime_guides_and_deployment_order_are_part_of_runtime_identity(self):
        guide=self.root/'docs/BOT_COMMUNICATION.md'
        guide.parent.mkdir();guide.write_bytes(b'# Synthetic communication contract\n')
        research=self.root/'docs/AUTORESEARCH.md'
        research.write_bytes(b'# Synthetic research contract\n')
        manifest=json.loads((self.root/'release-runtime.json').read_text())
        manifest['runtime_files'].append({'path':'docs/BOT_COMMUNICATION.md',
            'sha256':hashlib.sha256(guide.read_bytes()).hexdigest()})
        manifest['runtime_files'].append({'path':'docs/AUTORESEARCH.md',
            'sha256':hashlib.sha256(research.read_bytes()).hexdigest()})
        payload={e['path']:(self.root/e['path']).read_bytes() for e in manifest['runtime_files']}
        manifest['snapshot_algorithm']='deployment-input-order-v2'
        digest=hashlib.sha256(b''.join(n.encode()+b'\0'+payload[n]+b'\0' for n in
            ['intern_personal/core.py','skills/feishu-file-transfer/SKILL.md',
             'docs/BOT_COMMUNICATION.md','docs/AUTORESEARCH.md'])).hexdigest()
        manifest['snapshot_sha256']=digest
        (self.root/'release-runtime.json').write_text(json.dumps(manifest))
        self.assertEqual(parity.verify(self.root)['snapshot_sha256'],digest)
        self.assertNotEqual(parity.fingerprint(payload),digest)
        # A legacy v2 snapshot intentionally included only these two guides.
        (self.root/'docs/INSTALL.md').write_text('# Source installation guide\n')
        parity.verify(self.root)
        original=research.read_bytes();research.write_bytes(b'# Changed research contract\n')
        with self.assertRaisesRegex(parity.ParityError,'runtime_bytes_differ'):
            parity.verify(self.root)
        research.write_bytes(original)
        guide.write_bytes(b'# Changed communication contract\n')
        with self.assertRaisesRegex(parity.ParityError,'runtime_bytes_differ'):
            parity.verify(self.root)

    def test_all_operational_guides_use_v3_order_and_deployed_set_is_exact(self):
        manifest=json.loads((self.root/'release-runtime.json').read_text())
        for name in ['docs/SESSION_FEATURES.md','docs/BOT_COMMUNICATION.md','docs/AUTORESEARCH.md']:
            file=self.root/name;file.parent.mkdir(exist_ok=True);file.write_bytes(('# '+name+'\n').encode())
            manifest['runtime_files'].append({'path':name,'sha256':hashlib.sha256(file.read_bytes()).hexdigest()})
        payload={e['path']:(self.root/e['path']).read_bytes() for e in manifest['runtime_files']}
        manifest['snapshot_algorithm']='deployment-input-order-v3'
        ordered=['intern_personal/core.py','skills/feishu-file-transfer/SKILL.md',
                 'docs/AUTORESEARCH.md','docs/BOT_COMMUNICATION.md','docs/SESSION_FEATURES.md']
        digest=hashlib.sha256(b''.join(n.encode()+b'\0'+payload[n]+b'\0' for n in ordered)).hexdigest()
        manifest['snapshot_sha256']=digest
        (self.root/'release-runtime.json').write_text(json.dumps(manifest))
        deployed=self.base/'deployed';shutil.copytree(self.root,deployed)
        self.assertEqual(parity.verify(self.root,deployed=deployed)['snapshot_sha256'],digest)
        self.assertNotEqual(parity.fingerprint(payload),digest)
        archive=self.base/'v3-preview.vsix'
        with zipfile.ZipFile(archive,'w') as z:
            for name,data in payload.items():z.writestr('extension/backend/'+name,data)
            for entry in manifest['frontend']['files']:
                z.writestr(entry['path'],(self.root/entry['path']).read_bytes())
        parity.verify(self.root,vsix=archive)
        with zipfile.ZipFile(archive,'a') as z:
            z.writestr('extension/backend/docs/UNDECLARED.md','# Missing from baseline\n')
        with self.assertRaisesRegex(parity.ParityError,'vsix_guide_set_differs'):
            parity.verify(self.root,vsix=archive)
        # v3 sweeps every guide, including authored installation/review docs.
        # Omitting any guide would misrepresent the real prepare_runtime digest.
        (self.root/'docs/INSTALL.md').write_text('# Source installation guide\n')
        with self.assertRaisesRegex(parity.ParityError,'runtime_guide_set_differs'):
            parity.verify(self.root)
        (self.root/'docs/INSTALL.md').unlink()
        (deployed/'docs/UNDECLARED.md').write_text('# Missing from baseline\n')
        with self.assertRaisesRegex(parity.ParityError,'runtime_guide_set_differs'):
            parity.verify(self.root,deployed=deployed)
        (deployed/'docs/UNDECLARED.md').unlink()
        (deployed/'docs/SESSION_FEATURES.md').write_text('# Changed contract\n')
        with self.assertRaisesRegex(parity.ParityError,'runtime_bytes_differ'):
            parity.verify(self.root,deployed=deployed)
        with self.assertRaisesRegex(parity.ParityError,'invalid_deployment_fingerprint_inputs'):
            parity.fingerprint({'docs/nested/guide.md':b'unsupported'},'deployment-input-order-v3')

    def test_existing_target_and_overlapping_state_are_rejected(self):
        self.prefix.mkdir()
        with self.assertRaisesRegex(installer.InstallError,'install_target_already_exists'):
            installer.plan(self.root,self.prefix,self.state)
        self.prefix.rmdir()
        with self.assertRaisesRegex(installer.InstallError,'keep_install_and_state_separate'):
            installer.plan(self.root,self.prefix,self.prefix/'state')

    def test_long_socket_path_and_linked_parent_are_rejected(self):
        with self.assertRaisesRegex(installer.InstallError,'choose_shorter_private_state_path'):
            installer.plan(self.root,self.prefix,self.base/('x'*100))
        parent=self.base/'linked';parent.symlink_to(self.base,target_is_directory=True)
        with self.assertRaisesRegex(installer.InstallError,'linked_install_path'):
            installer.plan(self.root,parent/'new-install',self.state)

    def test_dependency_lock_drift_and_unselected_reference_mode_are_rejected(self):
        prepared=installer.plan(self.root,self.prefix,self.state,True)
        self.assertFalse(prepared['public_install_recommended'])
        with self.assertRaisesRegex(installer.InstallError,'explicit_selection'):
            installer.apply(self.root,prepared,self.wheels)
        (self.root/installer.LOCK).write_text('# unexpected lock')
        with self.assertRaisesRegex(installer.InstallError,'dependency_lock_changed'):
            installer.plan(self.root,self.prefix,self.state)

    def test_apply_uses_offline_hashed_wheels_and_does_not_touch_private_state(self):
        prepared=installer.plan(self.root,self.prefix,self.state)
        self.state.mkdir(mode=0o700);sentinel=self.state/'owner-data';sentinel.write_bytes(b'preserve')
        def fake_venv(path):
            (path/'bin').mkdir(parents=True);(path/'bin/python').write_text('# fixture')
        def run(*args,**kwargs):
            env=kwargs['env'];self.assertNotIn('GH_TOKEN',env);self.assertNotIn('PIP_INDEX_URL',env)
            return SimpleNamespace(returncode=0)
        with patch.object(installer.venv.EnvBuilder,'create',side_effect=fake_venv),patch.object(installer.subprocess,'run',side_effect=run) as runner,patch('intern_personal.distribution.package_files',return_value=self.inputs),patch.object(installer,'build_opener',side_effect=AssertionError('network forbidden')):
            result=installer.apply(self.root,prepared,self.wheels)
        args=runner.call_args_list[0].args[0]
        for flag in ['--no-index','--require-hashes','--no-deps']:self.assertIn(flag,args)
        self.assertEqual(sentinel.read_bytes(),b'preserve');self.assertFalse(result['starts_relay'])
        receipt=json.loads((self.prefix/'install-receipt.json').read_text());self.assertEqual(receipt['status'],'installed')
        self.assertTrue(receipt['dependency_imports_verified'])
        self.assertEqual(receipt['dependency_profile'],'public-linux-py312')
        self.assertFalse(receipt['downloads_requested']);self.assertEqual(receipt['wheels_downloaded'],0)
        self.assertEqual(runner.call_count,3)
        self.assertIn('websockets.sync.client',runner.call_args_list[2].args[0][-1])

    def test_install_failure_preserves_own_receipt_and_owner_state(self):
        prepared=installer.plan(self.root,self.prefix,self.state)
        with patch.object(installer.venv.EnvBuilder,'create'),patch.object(installer.subprocess,'run',return_value=SimpleNamespace(returncode=1)),patch('intern_personal.distribution.package_files',return_value=[]):
            with self.assertRaisesRegex(installer.InstallError,'locked_dependency_install_failed'):
                installer.apply(self.root,prepared,self.wheels)
        self.assertFalse(self.state.exists())
        self.assertEqual(json.loads((self.prefix/'install-receipt.json').read_text())['status'],'failed')

    def test_reference_install_requires_matching_plan_and_explicit_selection(self):
        public=installer.plan(self.root,self.prefix,self.state)
        with self.assertRaisesRegex(installer.InstallError,'install_plan_changed'):
            installer.apply(self.root,public,self.wheels,True)
        reference=installer.plan(self.root,self.prefix,self.state,True)
        with patch.object(installer.venv.EnvBuilder,'create'),patch.object(installer.subprocess,'run',return_value=SimpleNamespace(returncode=1)) as runner,patch('intern_personal.distribution.package_files',return_value=[]):
            with self.assertRaisesRegex(installer.InstallError,'locked_dependency_install_failed'):
                installer.apply(self.root,reference,self.wheels,True)
        self.assertEqual(runner.call_args.args[0][-1],str(self.root/installer.REFERENCE_LOCK))

    def test_unaccepted_and_changed_public_profiles_are_rejected(self):
        prepared=installer.plan(self.root,self.prefix,self.state)
        path=self.root/installer.PUBLIC_PROFILE;profile=json.loads(path.read_text())
        profile['public_install_recommended']=False;path.write_text(json.dumps(profile))
        with self.assertRaisesRegex(installer.InstallError,'public_dependency_profile_not_accepted'):
            installer.plan(self.root,self.prefix,self.state)
        profile['public_install_recommended']=True;profile['review_note']='changed after plan';path.write_text(json.dumps(profile))
        with self.assertRaisesRegex(installer.InstallError,'install_plan_changed'):
            installer.apply(self.root,prepared,self.wheels)
        profile['lock_file']=installer.REFERENCE_LOCK;path.write_text(json.dumps(profile))
        with self.assertRaisesRegex(installer.InstallError,'dependency_profile_invalid'):
            installer.plan(self.root,self.prefix,self.state)

    def test_sdk_import_failure_cannot_produce_an_installed_receipt(self):
        prepared=installer.plan(self.root,self.prefix,self.state)
        results=[SimpleNamespace(returncode=0),SimpleNamespace(returncode=0),SimpleNamespace(returncode=1)]
        with patch.object(installer.venv.EnvBuilder,'create'),patch.object(installer.subprocess,'run',side_effect=results),patch('intern_personal.distribution.package_files',return_value=self.inputs):
            with self.assertRaisesRegex(installer.InstallError,'installed_runtime_import_failed'):
                installer.apply(self.root,prepared,self.wheels)
        receipt=json.loads((self.prefix/'install-receipt.json').read_text())
        self.assertEqual(receipt['status'],'failed');self.assertFalse(receipt['dependency_imports_verified'])
        self.assertFalse(self.state.exists())

    def test_linked_or_malformed_dependency_metadata_is_rejected(self):
        path=self.root/installer.PUBLIC_PROFILE;original=path.read_bytes()
        path.write_text('[]')
        with self.assertRaisesRegex(installer.InstallError,'dependency_profile_invalid'):
            installer.plan(self.root,self.prefix,self.state)
        outside=self.base/'outside-profile.json';outside.write_bytes(original);path.unlink();path.symlink_to(outside)
        with self.assertRaisesRegex(installer.InstallError,'dependency_profile_missing'):
            installer.plan(self.root,self.prefix,self.state)

    def test_copied_dependency_metadata_drift_cannot_produce_installed_receipt(self):
        prepared=installer.plan(self.root,self.prefix,self.state);write=Path.write_bytes
        def corrupt(path,data):
            if path==self.prefix/'backend'/installer.PUBLIC_PROFILE:data+=b' '
            return write(path,data)
        with patch.object(installer.venv.EnvBuilder,'create'),patch.object(installer.subprocess,'run',return_value=SimpleNamespace(returncode=0)),patch('intern_personal.distribution.package_files',return_value=self.inputs),patch.object(Path,'write_bytes',corrupt):
            with self.assertRaisesRegex(installer.InstallError,'copied_dependency_profile_drift'):
                installer.apply(self.root,prepared,self.wheels)
        self.assertEqual(json.loads((self.prefix/'install-receipt.json').read_text())['status'],'failed')


class RuntimeDownloadTests(unittest.TestCase):
    # Reuse only the synthetic fixture, without rediscovering its test methods.
    setUp=TransferRuntimeTests.setUp

    def metadata(self):
        return json.loads((self.root/installer.PUBLIC_PROFILE).read_text())

    def prepare(self,*,download=False):
        return installer.prepare_wheels(self.root,self.wheels,self.metadata(),installer.LOCK,download=download)

    def response(self,data=None,*,headers=None,url=None,status=200):
        result=io.BytesIO(self.wheel_data if data is None else data)
        result.status=status
        result.headers={'Content-Length':str(len(self.wheel_data))} if headers is None else headers
        result.geturl=lambda:self.wheel_entry['download_url'] if url is None else url
        return result

    def remove_cached(self):
        (self.wheels/self.wheel_entry['wheel']).unlink()

    def test_download_flag_requires_explicit_apply_and_cache_before_planning(self):
        with patch.object(installer,'plan',side_effect=AssertionError('must reject first')):
            for flags in [['--download'],['--download','--apply'],['--download','--wheelhouse',str(self.wheels)]]:
                with self.subTest(flags=flags),patch('sys.stdout',new_callable=io.StringIO) as output:
                    code=installer.main(['--prefix',str(self.prefix),'--state',str(self.state),*flags])
                    self.assertEqual(code,2)
                    self.assertEqual(json.loads(output.getvalue())['error'],'download_requires_apply_and_explicit_wheelhouse')
        self.assertFalse(self.prefix.exists());self.assertFalse(self.state.exists())

    def test_prerequisite_errors_happen_before_target_creation_without_system_pip(self):
        import builtins
        real_import=builtins.__import__
        def missing(name,*args,**kwargs):
            if name=='ensurepip':raise ModuleNotFoundError('fixture missing ensurepip')
            if name=='pip':raise AssertionError('system pip must not be imported')
            return real_import(name,*args,**kwargs)
        with patch.object(installer,'venv',None),self.assertRaisesRegex(installer.InstallError,'python_venv_unavailable'):
            installer.plan(self.root,self.prefix,self.state)
        with patch('builtins.__import__',side_effect=missing),self.assertRaisesRegex(installer.InstallError,'python_ensurepip_unavailable'):
            installer.plan(self.root,self.prefix,self.state)
        self.assertFalse(self.prefix.exists());self.assertFalse(self.state.exists())

    def test_profile_inventory_must_match_every_locked_name_version_hash_and_filename(self):
        original=self.metadata()
        for change,error in [({'wheels':[]},'dependency_wheel_inventory_invalid'),
                ({'wheels':[dict(self.wheel_entry,name='other')]},'dependency_wheel_lock_closure_mismatch'),
                ({'wheels':[dict(self.wheel_entry,version='2.0')]},'dependency_wheel_lock_closure_mismatch'),
                ({'wheels':[dict(self.wheel_entry,sha256='0'*64)]},'dependency_wheel_lock_closure_mismatch'),
                ({'wheels':[dict(self.wheel_entry,wheel='other-1.0.0-py3-none-any.whl')]},'dependency_wheel_filename_mismatch'),
                ({'wheels':[self.wheel_entry,self.wheel_entry]},'dependency_wheel_lock_closure_mismatch'),
                ({'wheels':[dict(self.wheel_entry,size=installer.MAX_WHEEL_BYTES+1)]},'dependency_wheel_size_invalid')]:
            (self.root/installer.PUBLIC_PROFILE).write_text(json.dumps({**original,**change}))
            with self.subTest(change=change),self.assertRaisesRegex(installer.InstallError,error):
                installer.plan(self.root,self.prefix,self.state)
        (self.root/installer.PUBLIC_PROFILE).write_text(json.dumps(original))
        lock=self.root/installer.LOCK
        lock.write_text(lock.read_text()+'extra==1.0.0 --hash=sha256:'+'1'*64+'\n')
        original['lock_sha256']=hashlib.sha256(lock.read_bytes()).hexdigest()
        (self.root/installer.PUBLIC_PROFILE).write_text(json.dumps(original))
        with self.assertRaisesRegex(installer.InstallError,'dependency_wheel_lock_closure_mismatch'):
            installer.plan(self.root,self.prefix,self.state)

    def test_missing_or_tampered_offline_wheel_fails_before_install_prefix(self):
        prepared=installer.plan(self.root,self.prefix,self.state)
        self.remove_cached()
        with patch.object(installer,'build_opener',side_effect=AssertionError('network forbidden')):
            with self.assertRaisesRegex(installer.InstallError,'locked_wheel_missing'):
                installer.apply(self.root,prepared,self.wheels)
        (self.wheels/self.wheel_entry['wheel']).write_bytes(b'x'*len(self.wheel_data))
        with self.assertRaisesRegex(installer.InstallError,'cached_wheel_sha256_mismatch'):
            installer.apply(self.root,prepared,self.wheels)
        self.assertFalse(self.prefix.exists());self.assertFalse(self.state.exists())

    def test_download_uses_exact_https_wheel_without_system_pip_and_reuses_verified_cache(self):
        self.remove_cached();opener=Mock();opener.open.return_value=self.response()
        with patch.object(installer,'build_opener',return_value=opener) as build,patch.object(installer.subprocess,'run',side_effect=AssertionError('no pip')):
            self.assertEqual(self.prepare(download=True),1)
        handlers=build.call_args.args
        self.assertEqual(handlers[0].proxies,{})
        self.assertIsInstance(handlers[1],installer._NoRedirects)
        request=opener.open.call_args.args[0]
        self.assertEqual(request.full_url,self.wheel_entry['download_url'])
        self.assertFalse(request.has_header('Authorization'))
        wheel=self.wheels/self.wheel_entry['wheel']
        self.assertEqual(wheel.read_bytes(),self.wheel_data)
        self.assertEqual(wheel.stat().st_mode&0o777,0o600)
        self.assertEqual(list(self.wheels.glob('.intern-agent-wheel-*')),[])
        before=wheel.stat().st_mtime_ns
        with patch.object(installer,'build_opener',side_effect=AssertionError('cache must avoid network')):
            self.assertEqual(self.prepare(download=True),0)
        self.assertEqual(wheel.stat().st_mtime_ns,before)

    def test_all_cached_files_are_preflighted_before_any_download_and_preserved_if_modified(self):
        wheel=self.wheels/self.wheel_entry['wheel'];wheel.write_bytes(b'x'*len(self.wheel_data))
        with patch.object(installer,'build_opener',side_effect=AssertionError('reject before network')):
            with self.assertRaisesRegex(installer.InstallError,'cached_wheel_sha256_mismatch'):
                self.prepare(download=True)
        self.assertEqual(wheel.read_bytes(),b'x'*len(self.wheel_data))

    def test_only_reviewed_specific_pypi_wheel_urls_are_downloadable(self):
        original=self.metadata();base=self.wheel_entry['download_url']
        urls=['http'+base[5:],base.replace('files.pythonhosted.org','example.com'),
              base.replace('files.pythonhosted.org','user:secret@files.pythonhosted.org'),
              base.replace('files.pythonhosted.org','files.pythonhosted.org:443'),
              base+'?token=secret',base+'#fragment',base+'?',base+'#','\n'+base,base+'\n',
              base.replace('/packages/','/simple/'),
              base.replace('/aa/','/%61a/'),base.replace('fixture-1.0.0','fixture-2.0.0')]
        for url in urls:
            metadata={**original,'wheels':[dict(self.wheel_entry,download_url=url)]}
            with self.subTest(url=url),patch.object(installer,'build_opener',side_effect=AssertionError('reject before network')):
                with self.assertRaisesRegex(installer.InstallError,'unapproved_wheel_download_url'):
                    installer.prepare_wheels(self.root,self.wheels,metadata,installer.LOCK,download=True)
        missing=dict(self.wheel_entry);missing.pop('download_url')
        with self.assertRaisesRegex(installer.InstallError,'reviewed_wheel_download_url_unavailable'):
            installer.wheel_entries(self.root/installer.LOCK,{**original,'wheels':[missing]},download=True)
        missing=dict(self.wheel_entry);missing.pop('size')
        with self.assertRaisesRegex(installer.InstallError,'reviewed_wheel_download_size_unavailable'):
            installer.wheel_entries(self.root/installer.LOCK,{**original,'wheels':[missing]},download=True)

    def test_corrupt_or_oversized_download_never_becomes_a_cached_wheel(self):
        self.remove_cached()
        cases=[(self.wheel_data[:-1],{},'size_mismatch'),
               (self.wheel_data+b'x',{},'size_mismatch'),
               (b'x'*len(self.wheel_data),{},'sha256_mismatch'),
               (self.wheel_data,{'Content-Length':'999'},'size_mismatch'),
               (self.wheel_data,{'Content-Encoding':'gzip'},'encoding_rejected')]
        for data,headers,error in cases:
            opener=Mock();opener.open.return_value=self.response(data,headers=headers)
            with self.subTest(error=error),patch.object(installer,'build_opener',return_value=opener):
                with self.assertRaisesRegex(installer.InstallError,error):self.prepare(download=True)
            self.assertEqual(list(self.wheels.iterdir()),[])

    def test_failed_redirect_foreign_response_or_slow_download_cannot_install(self):
        self.remove_cached()
        with self.assertRaisesRegex(installer.InstallError,'redirect_rejected'):
            installer._NoRedirects().redirect_request(None,None,302,'redirect',{},'https://example.com/')
        cases=[(self.response(url='https://example.com/wheel'),'response_rejected',None),
               (self.response(status=302),'response_rejected',None),
               (self.response(),'time_limit_exceeded',[0,installer.DOWNLOAD_SECONDS+1])]
        for response,error,times in cases:
            opener=Mock();opener.open.return_value=response
            with self.subTest(error=error),patch.object(installer,'build_opener',return_value=opener):
                if times is None:
                    with self.assertRaisesRegex(installer.InstallError,error):self.prepare(download=True)
                else:
                    with patch.object(installer.time,'monotonic',side_effect=times),self.assertRaisesRegex(installer.InstallError,error):
                        self.prepare(download=True)
            self.assertEqual(list(self.wheels.iterdir()),[])
        opener=Mock();opener.open.side_effect=installer.URLError('private proxy value must not escape')
        with patch.object(installer,'build_opener',return_value=opener):
            with self.assertRaisesRegex(installer.InstallError,'^wheel_download_failed_check_network_and_retry$'):
                self.prepare(download=True)
        self.assertEqual(list(self.wheels.iterdir()),[])

    def test_linked_unowned_writable_and_overlapping_wheel_caches_are_rejected(self):
        prepared=installer.plan(self.root,self.prefix,self.state)
        wheel=self.wheels/self.wheel_entry['wheel'];wheel.unlink()
        outside=self.base/'outside.whl';outside.write_bytes(self.wheel_data);wheel.symlink_to(outside)
        with self.assertRaises(installer.InstallError):self.prepare(download=True)
        wheel.unlink();wheel.write_bytes(self.wheel_data);wheel.chmod(0o666)
        with self.assertRaisesRegex(installer.InstallError,'cached_wheel_must_be_owned_regular_file'):
            self.prepare(download=True)
        wheel.chmod(0o600);self.wheels.chmod(0o777)
        with self.assertRaisesRegex(installer.InstallError,'wheelhouse_must_be_owner_controlled'):
            self.prepare(download=True)
        self.wheels.chmod(0o700)
        with patch.object(installer.os,'getuid',return_value=self.wheels.stat().st_uid+1):
            with self.assertRaisesRegex(installer.InstallError,'wheelhouse_must_be_owner_controlled'):
                self.prepare(download=True)
        linked=self.base/'linked-cache';linked.symlink_to(self.wheels,target_is_directory=True)
        with self.assertRaisesRegex(installer.InstallError,'linked_install_path'):
            installer.apply(self.root,prepared,linked,download=True)
        with self.assertRaisesRegex(installer.InstallError,'keep_wheelhouse_install_and_state_separate'):
            installer.apply(self.root,prepared,self.prefix/'wheels',download=True)
        self.assertFalse(self.prefix.exists());self.assertFalse(self.state.exists())

    def test_new_cache_is_private_and_unrelated_files_are_preserved(self):
        self.remove_cached();self.wheels.rmdir()
        opener=Mock();opener.open.return_value=self.response()
        with patch.object(installer,'build_opener',return_value=opener):
            self.assertEqual(self.prepare(download=True),1)
        self.assertEqual(self.wheels.stat().st_mode&0o777,0o700)
        note=self.wheels/'owner-note.txt';note.write_bytes(b'Preserve user cache notes\n')
        with patch.object(installer,'build_opener',side_effect=AssertionError('verified cache is offline')):
            self.assertEqual(self.prepare(download=True),0)
        self.assertEqual(note.read_bytes(),b'Preserve user cache notes\n')

    def test_download_apply_still_installs_only_offline_into_a_separate_prefix(self):
        prepared=installer.plan(self.root,self.prefix,self.state)
        self.remove_cached();opener=Mock();opener.open.return_value=self.response()
        def fake_venv(path):
            (path/'bin').mkdir(parents=True);(path/'bin/python').write_text('# fixture')
        with patch.object(installer,'build_opener',return_value=opener),patch.object(installer.venv.EnvBuilder,'create',side_effect=fake_venv),patch.object(installer.subprocess,'run',return_value=SimpleNamespace(returncode=0)) as runner,patch('intern_personal.distribution.package_files',return_value=self.inputs):
            result=installer.apply(self.root,prepared,self.wheels,download=True)
        self.assertEqual(result['wheels_downloaded'],1)
        self.assertTrue(result['downloads_requested']);self.assertFalse(result['starts_relay'])
        pip_args=runner.call_args_list[0].args[0]
        self.assertEqual(pip_args[0],str(self.prefix/'venv/bin/python'))
        self.assertIn('--no-index',pip_args);self.assertIn('--require-hashes',pip_args)
        self.assertNotIn('download',pip_args);self.assertFalse(self.state.exists())
        receipt=json.loads((self.prefix/'install-receipt.json').read_text())
        self.assertEqual(receipt['status'],'installed');self.assertEqual(receipt['wheels_downloaded'],1)
