import hashlib
import json
from pathlib import Path
import tarfile
import unittest
from unittest.mock import patch

import test_tracking as tracking
from intern_personal import archive_parts, record_storage
from intern_personal.core import Invalid


class RecordStorageTests(unittest.TestCase):
    setUp=tracking.TrackingTests.setUp
    raw=tracking.TrackingTests.raw

    def binding(self):
        return dict(metadata_path=str(self.source))

    def current(self):
        data=dict(schema='intern-agent.recovery.v1',reference='Keep current memory and every turn')
        archive_parts.write(self.tracker,self.binding(),data)
        return data

    def orphan(self, text='Uncommitted old snapshot fragment'):
        digest=hashlib.sha256(text.encode()).hexdigest()
        path=self.source/'.intern_workspace/recovery-parts'/(digest+'.json')
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(text)+'\n')
        return path

    def archives(self):
        return sorted((self.state.runtime/'recovery-backups').glob('*/*.tar.gz'))

    def test_failed_publication_fragments_are_backed_up_verified_and_collected(self):
        old=dict(schema='intern-agent.recovery.v1',text='Earlier result with 中文 and exact newlines\n'*20000)
        archive_parts.write(self.tracker,self.binding(),old)
        original=json.loads((self.source/'.intern_workspace/recovery.json').read_text())
        self.assertEqual(original['schema'],archive_parts.SCHEMA)
        old_bytes={p['path']:(self.source/p['path']).read_bytes() for p in original['parts']}
        newer=dict(schema='intern-agent.recovery.v1',text='A new result; do not replay Codex')
        archive_parts.write(self.tracker,self.binding(),newer)
        self.assertTrue(self.archives())
        recovered={}
        for path in self.archives():
            with tarfile.open(path,'r:gz') as archive:
                manifest=json.load(archive.extractfile('backup.json'))
                self.assertTrue(record_storage.verify_backup(path,manifest))
                recovered.update({e['path']:archive.extractfile(e['path']).read() for e in manifest['files']})
            self.assertEqual(path.stat().st_mode&0o777,0o600)
            self.assertEqual(path.parent.stat().st_mode&0o777,0o700)
        self.assertEqual(recovered,old_bytes)
        self.assertEqual(archive_parts.expand(original,lambda name:recovered[name]),old)
        self.assertFalse(any((self.source/name).exists() for name in old_bytes))
        self.assertEqual(json.loads((self.source/'.intern_workspace/recovery.json').read_text()),newer)
        self.assertEqual(self.raw(self.source,'rev-parse','HEAD'),self.original)
        self.assertEqual(self.raw(self.source,'diff','--cached','--name-only'),'')

    def test_no_fragment_is_removed_when_backup_verification_fails(self):
        self.current();path=self.orphan();before=path.read_bytes()
        with patch.object(record_storage,'verify_backup',side_effect=Invalid('fixture broken backup')):
            result=record_storage.collect(self.tracker,self.binding())
        self.assertEqual(result['removed'],0)
        self.assertEqual(result['error_type'],'Invalid')
        self.assertEqual(path.read_bytes(),before)
        self.assertFalse(self.archives())

    def test_required_parts_are_kept_and_current_archive_still_restores(self):
        data=dict(schema='intern-agent.recovery.v1',text='保留所有当前的历史和记忆。'*30000)
        archive_parts.write(self.tracker,self.binding(),data)
        manifest=json.loads((self.source/'.intern_workspace/recovery.json').read_text())
        self.orphan();result=record_storage.collect(self.tracker,self.binding(),strict=True)
        self.assertEqual(result['removed'],1)
        self.assertEqual(archive_parts.expand(manifest,lambda name:(self.source/name).read_bytes()),data)

    def test_staged_and_modified_tracked_data_is_preserved_even_during_explicit_maintenance(self):
        self.current();path=self.orphan()
        self.raw(self.source,'add','.intern_workspace')
        result=record_storage.collect(self.tracker,self.binding(),include_tracked=True,strict=True)
        self.assertEqual(result['removed'],0);self.assertTrue(path.is_file())
        self.raw(self.source,'commit','-qm','Keep tracked record fixture')
        unchanged=record_storage.collect(self.tracker,self.binding(),strict=True)
        self.assertEqual(unchanged['removed'],0)
        path.write_text(path.read_text()+' ')
        changed=record_storage.collect(self.tracker,self.binding(),include_tracked=True,strict=True)
        self.assertEqual(changed['removed'],0);self.assertEqual(changed['skipped'],1)
        self.assertTrue(path.is_file())

    def test_explicit_tracked_cleanup_keeps_a_verified_archive_and_old_git_commit(self):
        self.current();path=self.orphan();before=path.read_bytes()
        self.raw(self.source,'add','.intern_workspace');self.raw(self.source,'commit','-qm','Original records')
        head=self.raw(self.source,'rev-parse','HEAD');name=str(path.relative_to(self.source))
        result=record_storage.collect(self.tracker,self.binding(),include_tracked=True,strict=True)
        self.assertEqual(result['removed'],1);self.assertFalse(path.exists())
        with tarfile.open(result['backup'],'r:gz') as archive:self.assertEqual(archive.extractfile(name).read(),before)
        self.assertEqual(self.raw(self.source,'rev-parse','HEAD'),head)
        self.assertEqual(self.raw(self.source,'show',head+':'+name).encode()+b'\n',before)

    def test_late_owner_edit_or_manifest_change_prevents_deletion(self):
        self.current();path=self.orphan();real=record_storage.backup
        def changed(tracker,snapshot):
            answer=real(tracker,snapshot);path.write_text(path.read_text()+' ');return answer
        with patch.object(record_storage,'backup',side_effect=changed):
            result=record_storage.collect(self.tracker,self.binding())
        self.assertEqual(result['removed'],0);self.assertTrue(path.exists())
        self.assertEqual(result['error_type'],'Invalid')
        path.unlink();path=self.orphan('second fragment')
        def new_manifest(tracker,snapshot):
            answer=real(tracker,snapshot)
            (self.source/'.intern_workspace/recovery.json').write_text(json.dumps(dict(schema='intern-agent.recovery.v1',new='owner change')))
            return answer
        with patch.object(record_storage,'backup',side_effect=new_manifest):
            result=record_storage.collect(self.tracker,self.binding())
        self.assertEqual(result['removed'],0);self.assertTrue(path.exists())

    def test_symlinks_and_unknown_manifest_are_not_authority_to_delete_files(self):
        self.current();path=self.orphan();outside=self.root/'outside.txt';outside.write_text('preserve outside')
        path.unlink();path.symlink_to(outside)
        result=record_storage.collect(self.tracker,self.binding(),strict=True)
        self.assertEqual(result['removed'],0);self.assertEqual(result['skipped'],1)
        self.assertEqual(outside.read_text(),'preserve outside')
        path.unlink();path=self.orphan()
        (self.source/'.intern_workspace/recovery.json').write_text('{}')
        result=record_storage.collect(self.tracker,self.binding())
        self.assertEqual(result['error_type'],'Invalid');self.assertTrue(path.is_file())

    def test_cleanup_budget_and_backup_reuse_bound_work_without_losing_fragments(self):
        self.current();one=self.orphan('first fragment');two=self.orphan('second fragment')
        snapshot=record_storage.plan(self.tracker,self.binding(),limit=25)
        self.assertEqual(len(snapshot['entries']),1)
        path,manifest=record_storage.backup(self.tracker,snapshot)
        same,_=record_storage.backup(self.tracker,snapshot)
        self.assertEqual(path,same);self.assertTrue(record_storage.verify_backup(path,manifest))
        result=record_storage.collect(self.tracker,self.binding(),strict=True,limit=25)
        self.assertEqual(result['removed'],1)
        self.assertEqual(sum(p.exists() for p in (one,two)),1)
        self.assertEqual(len(self.archives()),1)

    def test_compact_utf8_snapshots_preserve_unicode_boundaries_and_every_value(self):
        data=dict(schema='intern-agent.recovery.v1',turns=[dict(request='精确保留这个问题🚀\n'*7000,response='回答不改变。'*7000) for _ in range(3)])
        archive_parts.write(self.tracker,self.binding(),data)
        manifest=json.loads((self.source/'.intern_workspace/recovery.json').read_text())
        self.assertEqual(manifest['schema'],archive_parts.SCHEMA)
        self.assertEqual(archive_parts.expand(manifest,lambda name:(self.source/name).read_bytes()),data)
        old=(json.dumps(data,ensure_ascii=True,indent=2,sort_keys=True)+'\n').encode()
        self.assertLess(manifest['bytes'],len(old)//2)
        for part in manifest['parts']:self.assertLessEqual(part['bytes'],archive_parts.CHUNK)

    def test_compact_json_preserves_escaped_surrogates_without_invalid_utf8(self):
        data=dict(schema='intern-agent.recovery.v1',text='exact\ud800\nowner\udfff input')
        archive_parts.write(self.tracker,self.binding(),data)
        raw=(self.source/'.intern_workspace/recovery.json').read_bytes()
        self.assertEqual(json.loads(raw.decode('utf-8')),data)

    def test_legacy_pretty_ascii_manifests_and_chunks_remain_readable_after_cleanup(self):
        old=dict(schema='intern-agent.recovery.v1',text='原有历史\n'*30000)
        raw=(json.dumps(old,ensure_ascii=True,indent=2,sort_keys=True)+'\n').encode()
        parts=[];directory=self.source/'.intern_workspace/recovery-parts';directory.mkdir(parents=True)
        for offset in range(0,len(raw),archive_parts.CHUNK):
            chunk=raw[offset:offset+archive_parts.CHUNK];digest=hashlib.sha256(chunk).hexdigest()
            name='.intern_workspace/recovery-parts/'+digest+'.json'
            (self.source/name).write_text(json.dumps(chunk.decode(),ensure_ascii=True)+'\n')
            parts.append(dict(path=name,sha256=digest,bytes=len(chunk)))
        manifest=dict(schema=archive_parts.SCHEMA,sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw),parts=parts)
        (self.source/'.intern_workspace/recovery.json').write_text(json.dumps(manifest))
        self.orphan();result=record_storage.collect(self.tracker,self.binding(),strict=True)
        self.assertEqual(result['removed'],1)
        self.assertEqual(archive_parts.expand(manifest,lambda name:(self.source/name).read_bytes()),old)

    def test_incomplete_current_archive_prevents_cleanup_and_private_directory_links_are_rejected(self):
        data=dict(schema='intern-agent.recovery.v1',text='retain-current'*50000)
        archive_parts.write(self.tracker,self.binding(),data)
        manifest=json.loads((self.source/'.intern_workspace/recovery.json').read_text())
        path=self.orphan();required=self.source/manifest['parts'][0]['path'];required.unlink()
        result=record_storage.collect(self.tracker,self.binding())
        self.assertIsNotNone(result['error_type']);self.assertTrue(path.is_file())
        self.current();path=self.orphan('keep across unsafe backup root')
        parent=self.state.runtime/'recovery-backups'
        import shutil
        if parent.exists():shutil.rmtree(parent)
        outside=self.root/'public-directory';outside.mkdir()
        parent.symlink_to(outside,target_is_directory=True)
        result=record_storage.collect(self.tracker,self.binding())
        self.assertEqual(result['removed'],0);self.assertTrue(path.is_file())
        self.assertEqual(list(outside.iterdir()),[])

    def test_backup_verifier_rejects_extra_paths_without_extracting_any_files(self):
        self.current();self.orphan();snapshot=record_storage.plan(self.tracker,self.binding())
        path,manifest=record_storage.backup(self.tracker,snapshot)
        corrupted=self.state.runtime/'bad-backup.tar.gz'
        import io
        with tarfile.open(path,'r:gz') as source,tarfile.open(corrupted,'w:gz') as target:
            for member in source:target.addfile(member,source.extractfile(member))
            extra=tarfile.TarInfo('../../unexpected');extra.size=1;target.addfile(extra,io.BytesIO(b'x'))
        corrupted.chmod(0o600)
        with self.assertRaises(Invalid):record_storage.verify_backup(corrupted,manifest)
        self.assertFalse((self.root/'unexpected').exists())


if __name__=='__main__':unittest.main()
