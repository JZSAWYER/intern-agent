import io
import json
import os
from pathlib import Path
import stat
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from intern_personal import attachments,file_transfer
from intern_personal.core import Invalid
from intern_personal.feishu_live import FeishuAPI,FeishuError
from intern_personal.feishu_live import main as relay_main
from intern_personal.prompt_context import publish
from intern_personal.git_hooks import validate as git_validate
from test_features import FeaturesTests
from test_tracking import TrackingTests


def archive(entries):
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as z:
        for name,data in entries:z.writestr(name,data)
    return stream.getvalue()


PDF=b'%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\n%%EOF\n'


class FileTransferTests(unittest.TestCase):
    setUp=TrackingTests.setUp
    raw=TrackingTests.raw
    attach=TrackingTests.attach
    conversation=FeaturesTests.conversation
    relay=FeaturesTests.relay
    event=FeaturesTests.event

    def setup_export(self,name='report.pdf',data=PDF):
        s=self.conversation();relay=self.relay();path=Path(self.tracker.code_directory(s))/name
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
        uploads=[];sends=[]
        relay.api.upload_file=lambda name,data:uploads.append((name,data)) or 'file_uploaded'
        relay.api.send_file=lambda *args:sends.append(args) or 'om_file_receipt'
        return s,relay,path,uploads,sends

    def test_pdf_zip_private_inputs_and_unchanged_hook_prompt(self):
        s=self.conversation();relay=self.relay();downloads=[]
        for i,(name,data) in enumerate([('paper with spaces.pdf',PDF),('results.zip',archive([('notes.md',b'# Notes\n')]))]):
            relay.api.download=lambda *args:downloads.append(args) or data
            self.assertEqual(self.state.ingest(self.event(s,mid='om_file_'+str(i),kind='file',content=dict(file_name=name,file_key='file_resource_'+str(i),file_size=len(data)))),'accepted')
            item=dict(self.state.db.execute('SELECT * FROM events ORDER BY seq DESC LIMIT 1').fetchone())
            text,images,resources=attachments.prepare(relay,item)
            self.assertEqual(text,item['text']);self.assertFalse(images)
            path=self.state.runtime/'attachments'/str(item['seq'])/('0-'+name)
            self.assertEqual(path.read_bytes(),data);self.assertEqual(path.stat().st_mode&0o777,0o600)
            self.assertIn(str(path),resources)
            self.assertEqual(publish(self.tracker,s,resources)['intern-agent.attachments']['kind'],'untrusted')
            attachments.prepare(relay,item)
        self.assertEqual(len(downloads),2)
        self.assertFalse(any(Path(self.tracker.code_directory(s)).glob('*.pdf')))

    def test_cache_crash_recovery_and_legacy_cache(self):
        s=self.conversation();relay=self.relay()
        relay.api.download=lambda *args:(_ for _ in ()).throw(AssertionError('Must reuse saved cache'))
        for i,name in enumerate(['paper.pdf','notes.md']):
            self.state.ingest(self.event(s,mid='om_cache_'+str(i),kind='file',content=dict(file_name=name,file_key='file_saved')))
            item=dict(self.state.db.execute('SELECT * FROM events ORDER BY seq DESC LIMIT 1').fetchone())
            parent=attachments.private_directory(self.state.runtime/'attachments')
            directory=attachments.private_directory(parent/str(item['seq']))
            path=directory/('0-paper.pdf' if i==0 else '0.bin')
            path.write_bytes(PDF if i==0 else b'Old cache\n');path.chmod(0o600)
            attachments.prepare(relay,item)
            self.assertTrue(self.state.db.execute('SELECT sha256 FROM attachments WHERE seq=?',(item['seq'],)).fetchone()[0])
        path.chmod(0o644)
        with self.assertRaises(Invalid):attachments.prepare(relay,item)

    def test_input_rejection_has_actionable_feedback_without_codex(self):
        s=self.conversation();relay=self.relay()
        self.assertEqual(relay.receive(self.event(s,kind='file',content=dict(file_name='report.pdf',file_key='file_large',file_size=attachments.MAX_BYTES+1))),'accepted')
        self.assertEqual(relay.process_once('control'),'complete')
        self.assertIn('20 MiB',relay.api.messages[-1][1]);self.assertEqual(relay.host.calls,[])
        for name in ['../a.pdf','a\r.pdf','a\x00.pdf','x/y.zip']:
            with self.assertRaises(Invalid):attachments.normalize(dict(message_type='file',content=json.dumps(dict(file_name=name,file_key='file_x'))))
        for size in [-1,'invalid',True]:
            with self.assertRaises(Invalid):attachments.normalize(dict(message_type='file',content=json.dumps(dict(file_name='a.pdf',file_key='file_x',file_size=size))))
        text,resources=attachments.normalize(dict(message_type='file',content=json.dumps(dict(file_name='a.pdf',file_key='file_x',file_size='4'))))
        self.assertEqual(resources[0]['name'],'a.pdf')

    def test_archive_checks_traversal_symlinks_bombs_and_credentials(self):
        for name in ['/etc/file','../file','a/../../file','a\\file','C:/file']:
            with self.assertRaises(Invalid):attachments.validate_file('a.zip',archive([(name,b'File')]))
        linked=io.BytesIO()
        with zipfile.ZipFile(linked,'w') as z:
            member=zipfile.ZipInfo('link');member.create_system=3;member.external_attr=(stat.S_IFLNK|0o777)<<16
            z.writestr(member,'../private')
        with self.assertRaises(Invalid):attachments.validate_file('links.zip',linked.getvalue())
        with self.assertRaises(Invalid):attachments.validate_file('bomb.zip',archive([('huge.txt',b'A'*100000)]))
        with self.assertRaises(Invalid):attachments.validate_file('a.pdf',b'Not a PDF')
        for entries in [[('.env',b'Private')],[('a.txt',('ghp_'+'z'*24).encode())]]:
            data=archive(entries)
            with self.assertRaises(Invalid):git_validate('results.zip',data)
        git_validate('results.zip',archive([('report.md',b'# Safe report\n')]))

    def test_snapshot_is_immutable_uncommitted_and_delivery_does_not_commit(self):
        s,relay,path,uploads,sends=self.setup_export('result bundle.zip',archive([('report.md',b'# Results\n')]))
        before=self.raw(path.parent,'rev-parse','HEAD')
        transfer=file_transfer.enqueue(self.state,self.tracker,s,path.name)
        original=path.read_bytes();path.write_bytes(archive([('changed.txt',b'Changed')]))
        file_transfer.pump(relay);file_transfer.pump(relay)
        self.assertEqual(uploads,[(path.name,original)]);self.assertEqual(len(sends),1)
        self.assertEqual(file_transfer.listing(self.state,s,transfer['id'])[0]['state'],'delivered')
        self.assertEqual(self.raw(path.parent,'rev-parse','HEAD'),before)
        self.assertEqual(self.raw(self.remote,'rev-parse','main'),self.original)
        cache=self.state.runtime/'file-outbox'/(transfer['id']+'.bin')
        self.assertEqual(cache.stat().st_mode&0o777,0o600)

    def test_committed_switch_and_safe_path_walk(self):
        s,relay,path,uploads,sends=self.setup_export('README.md',b'Changed current bytes\n')
        current=file_transfer.enqueue(self.state,self.tracker,s,'README.md',request_key='current')
        committed=file_transfer.enqueue(self.state,self.tracker,s,'README.md',request_key='committed',committed=True)
        file_transfer.pump(relay)
        self.assertEqual([u[1] for u in uploads],[b'Changed current bytes\n',b'Original project\n'])
        self.assertNotEqual(current['sha256'],committed['sha256'])
        directory=path.parent
        (directory/'outside').symlink_to(self.root,target_is_directory=True)
        (directory/'linked.txt').symlink_to(directory/'README.md')
        for relative in ['outside/source/README.md','linked.txt','../source/README.md','/etc/passwd','.git/config','credentials.json','.intern_workspace/history.md']:
            with self.assertRaises(Invalid):file_transfer.enqueue(self.state,self.tracker,s,relative)

    def test_dedup_includes_name_request_and_hash(self):
        s,relay,path,uploads,sends=self.setup_export('one.pdf')
        one=file_transfer.enqueue(self.state,self.tracker,s,'one.pdf',request_key='same')
        self.assertEqual(one['id'],file_transfer.enqueue(self.state,self.tracker,s,'one.pdf',request_key='same')['id'])
        path.write_bytes(PDF+b'New revision\n')
        with self.assertRaises(Invalid):file_transfer.enqueue(self.state,self.tracker,s,'one.pdf',request_key='same')
        two=path.with_name('two.pdf');two.write_bytes(PDF)
        other=file_transfer.enqueue(self.state,self.tracker,s,'two.pdf')
        self.assertNotEqual(one['id'],other['id'])
        file_transfer.pump(relay);self.assertEqual(len(sends),2)
        self.assertNotEqual(sends[0][2],sends[1][2])

    def test_permission_failure_notifies_and_manual_retry_uses_saved_bytes(self):
        s,relay,path,uploads,sends=self.setup_export()
        relay.api.upload_file=lambda *args:(_ for _ in ()).throw(FeishuError('upload_file',99991672))
        transfer=file_transfer.enqueue(self.state,self.tracker,s,path.name)
        file_transfer.pump(relay);file_transfer.pump(relay)
        row=file_transfer.listing(self.state,s,transfer['id'])[0]
        self.assertEqual((row['state'],row['attempts']),('failed',1))
        self.assertIn('upload_file',row['error_reason']);self.assertIn('im:resource:upload',row['error_reason'])
        self.assertIn('publish',row['error_reason']);self.assertIn('preserved',row['error_reason'])
        self.assertEqual(len(relay.api.messages),1);self.assertIn('im:resource:upload',relay.api.messages[0][1]);self.assertFalse(sends)
        path.write_bytes(PDF+b'Changed\n')
        relay.api.upload_file=lambda name,data:uploads.append((name,data)) or 'file_repaired'
        file_transfer.retry(self.state,s,transfer['id']);file_transfer.pump(relay)
        self.assertEqual(uploads[0][1],PDF);self.assertEqual(len(sends),1)

    def test_send_permission_diagnosis_preserves_uploaded_resource(self):
        s,relay,path,uploads,sends=self.setup_export()
        relay.api.send_file=lambda *args:(_ for _ in ()).throw(FeishuError('send_file',99991672))
        transfer=file_transfer.enqueue(self.state,self.tracker,s,path.name)
        file_transfer.pump(relay)
        row=file_transfer.listing(self.state,s,transfer['id'])[0]
        self.assertEqual(row['state'],'failed');self.assertIn('im:message:send_as_bot',row['error_reason'])
        self.assertNotIn('im:resource:upload',relay.api.messages[-1][1])
        relay.api.send_file=lambda *args:sends.append(args) or 'om_fixed_receipt'
        file_transfer.retry(self.state,s,transfer['id']);file_transfer.pump(relay)
        self.assertEqual(len(uploads),1);self.assertEqual(len(sends),1)

    def test_legacy_scope_failure_is_explained_without_mutating_history(self):
        s,relay,path,uploads,sends=self.setup_export()
        transfer=file_transfer.enqueue(self.state,self.tracker,s,path.name)
        self.state.db.execute("UPDATE file_transfers SET state='failed',error_code=99991672,error_reason=NULL WHERE id=?",(transfer['id'],))
        row=file_transfer.listing(self.state,s,transfer['id'])[0]
        self.assertIn('im:resource:upload',row['error_reason'])
        self.assertIsNone(self.state.db.execute('SELECT error_reason FROM file_transfers WHERE id=?',(transfer['id'],)).fetchone()[0])
        file_transfer.notices(relay);self.assertIn('im:resource:upload',relay.api.messages[-1][1])

    def test_token_failure_does_not_recommend_granting_upload_scope(self):
        s,relay,path,uploads,sends=self.setup_export()
        relay.api.upload_file=lambda *args:(_ for _ in ()).throw(FeishuError('upload_file',99991668))
        transfer=file_transfer.enqueue(self.state,self.tracker,s,path.name);file_transfer.pump(relay)
        message=relay.api.messages[-1][1]
        self.assertIn('99991668',message);self.assertNotIn('im:resource:upload',message)
        self.assertEqual(file_transfer.listing(self.state,s,transfer['id'])[0]['state'],'failed')

    def test_download_errors_distinguish_permissions_from_network_failures(self):
        s=self.conversation();relay=self.relay()
        for i,error in enumerate([FeishuError('download_resource',99991672),ConnectionError('sensitive transport text')]):
            self.state.ingest(self.event(s,mid='om_diagnose_'+str(i),kind='file',content=dict(file_name='paper.pdf',file_key='file_saved')))
            item=dict(self.state.db.execute('SELECT * FROM events ORDER BY seq DESC LIMIT 1').fetchone())
            relay.api.download=lambda *args:(_ for _ in ()).throw(error)
            with self.assertRaises(Invalid) as caught:attachments.prepare(relay,item)
            text=str(caught.exception)
            self.assertIn('Codex was not started',text);self.assertNotIn('sensitive transport text',text)
            self.assertEqual('im:message:readonly' in text,i==0)
            if i==1:self.assertIn('network failure does not prove missing permissions',text)

    def test_permission_command_is_read_only_and_unavailable_is_unknown(self):
        s,relay,path,uploads,sends=self.setup_export()
        value=dict(schema='intern-agent.file-permissions.v1',verified=True,upload=dict(granted=False))
        relay.api.file_permissions=lambda:value
        item=dict(seq=42,text='/files permissions',session=s['id'],chat_id=s['chat_id'],sender=self.state.identity()['owner'])
        self.assertEqual(json.loads(relay.control(item)),value)
        controller=relay.attach_controller('project');item.update(session=controller['id'],chat_id=controller['chat_id'])
        self.assertEqual(json.loads(relay.control(item)),value)
        relay.api.file_permissions=lambda:(_ for _ in ()).throw(ConnectionError('secret connection text'))
        unknown=json.loads(relay.control(item))
        self.assertFalse(unknown['verified']);self.assertNotIn('upload',unknown)
        self.assertNotIn('secret connection text',str(unknown));self.assertFalse(uploads);self.assertFalse(sends)

    def test_upload_transport_failures_have_bounded_retry(self):
        s,relay,path,uploads,sends=self.setup_export()
        relay.api.upload_file=lambda *args:(_ for _ in ()).throw(ConnectionError('private diagnostic must not be copied'))
        transfer=file_transfer.enqueue(self.state,self.tracker,s,path.name)
        for attempt in range(3):
            file_transfer.pump(relay)
            self.state.db.execute('UPDATE file_transfers SET retry_at=0 WHERE id=?',(transfer['id'],))
        row=file_transfer.listing(self.state,s,transfer['id'])[0]
        self.assertEqual((row['state'],row['attempts']),('failed',3));self.assertFalse(sends)
        self.assertNotIn('private diagnostic',str(row));self.assertNotIn('private diagnostic',str(relay.api.messages))

    def test_uncertain_send_never_blindly_repeats_and_reuses_upload(self):
        s,relay,path,uploads,sends=self.setup_export()
        identities=[]
        def lost(chat,key,identity):identities.append(identity);raise ConnectionError('lost acknowledgement')
        relay.api.send_file=lost
        transfer=file_transfer.enqueue(self.state,self.tracker,s,path.name)
        file_transfer.pump(relay);file_transfer.pump(relay)
        self.assertEqual(len(identities),1);self.assertEqual(file_transfer.listing(self.state,s,transfer['id'])[0]['state'],'uncertain')
        relay.api.send_file=lambda chat,key,identity:identities.append(identity) or 'om_recovered'
        self.assertIn('duplicate',file_transfer.retry(self.state,s,transfer['id']))
        file_transfer.pump(relay)
        self.assertEqual(len(uploads),1);self.assertEqual(identities[0],identities[1])
        with self.assertRaises(Invalid):file_transfer.retry(self.state,s,transfer['id'])

    def test_restart_preserves_upload_keys_and_blocks_unknown_send(self):
        s,relay,path,uploads,sends=self.setup_export()
        one=file_transfer.enqueue(self.state,self.tracker,s,path.name,request_key='upload')
        two=file_transfer.enqueue(self.state,self.tracker,s,path.name,request_key='send')
        self.state.db.execute("UPDATE file_transfers SET state='uploading' WHERE id=?",(one['id'],))
        self.state.db.execute("UPDATE file_transfers SET state='sending',file_key='file_saved' WHERE id=?",(two['id'],))
        file_transfer.recover(self.state);file_transfer.pump(relay)
        self.assertEqual(file_transfer.listing(self.state,s,one['id'])[0]['state'],'delivered')
        self.assertEqual(file_transfer.listing(self.state,s,two['id'])[0]['state'],'uncertain');self.assertEqual(len(sends),1)

    def test_atomic_lease_prevents_duplicate_worker_send(self):
        s,relay,path,uploads,sends=self.setup_export();started=threading.Event();release=threading.Event()
        def upload(name,data):
            uploads.append((name,data));started.set()
            if not release.wait(5):raise RuntimeError('Test did not release upload')
            return 'file_leased'
        relay.api.upload_file=upload
        transfer=file_transfer.enqueue(self.state,self.tracker,s,path.name)
        errors=[]
        def worker():
            try:file_transfer.deliver(relay,transfer['id'])
            except Exception as error:errors.append(error)
            finally:self.state.close()
        thread=threading.Thread(target=worker);thread.start()
        try:
            self.assertTrue(started.wait(5));file_transfer.deliver(relay,transfer['id'])
        finally:release.set();thread.join(5)
        self.assertFalse(errors);self.assertEqual(len(uploads),1);self.assertEqual(len(sends),1)

    def test_tampered_snapshot_is_not_uploaded(self):
        s,relay,path,uploads,sends=self.setup_export()
        transfer=file_transfer.enqueue(self.state,self.tracker,s,path.name)
        (self.state.runtime/'file-outbox'/(transfer['id']+'.bin')).write_bytes(PDF+b'Injected')
        file_transfer.pump(relay)
        self.assertEqual(file_transfer.listing(self.state,s,transfer['id'])[0]['state'],'failed');self.assertFalse(uploads);self.assertFalse(sends)

    def test_context_is_thread_pinned_and_contains_file_workflow(self):
        s=self.conversation();refs=publish(self.tracker,s)
        context=self.state.runtime/'prompt-context'/(s['id']+'.json')
        with patch.dict(os.environ,{'CODEX_THREAD_ID':s['thread_id']}):
            self.assertEqual(file_transfer.context_session(self.state,context)['id'],s['id'])
        with patch.dict(os.environ,{'CODEX_THREAD_ID':'different-thread'}):
            with self.assertRaises(Invalid):file_transfer.context_session(self.state,context)
        self.assertIn('file-send --context',refs['intern-agent.workspace']['value'])
        self.assertIn('skills/feishu-file-transfer/SKILL.md',refs['intern-agent.workspace']['value'])
        self.assertNotIn('file-transfers',refs.get('intern-agent.memory',{}).get('value',''))

    def test_commands_support_spaces_head_option_and_controller_defaults(self):
        s,relay,path,uploads,sends=self.setup_export('report final.pdf')
        for command in ['/send-file "report final.pdf"','/files','/help files']:
            item=dict(seq=42,text=command,session=s['id'],chat_id=s['chat_id'],sender=self.state.identity()['owner'])
            result=relay.control(item);self.assertTrue(result)
        self.assertEqual(file_transfer.listing(self.state,s)[0]['name'],'report final.pdf')
        file_transfer.pump(relay);self.assertEqual(len(sends),1)
        controller=relay.attach_controller('project')
        text=relay.control(dict(seq=43,text='/help files',session=controller['id'],chat_id=controller['chat_id'],sender=self.state.identity()['owner']))
        self.assertIn('File transfer',text)
        result=relay.control(dict(seq=44,text='/send-file --committed README.md',session=controller['id'],chat_id=controller['chat_id'],sender=self.state.identity()['owner']))
        self.assertIn('queued',result)

    def test_operator_turn_cannot_export_under_owner_identity(self):
        from intern_personal import access
        s,relay,path,uploads,sends=self.setup_export()
        access.command(self.state,['grant','ou_operator','project','operator'])
        self.state.ingest(self.event(s,'Send a file',sender='ou_operator'))
        self.state.claim('agent')
        with self.assertRaises(Invalid):file_transfer.enqueue(self.state,self.tracker,s,path.name)
        self.assertFalse(file_transfer.listing(self.state,s));self.assertFalse(sends)

    def test_agent_cli_queues_and_reports_actual_delivery(self):
        s,relay,path,uploads,sends=self.setup_export();publish(self.tracker,s)
        context=self.state.runtime/'prompt-context'/(s['id']+'.json')
        prefix=['--state',str(self.state.root)]
        output=io.StringIO()
        with patch.dict(os.environ,{'CODEX_THREAD_ID':s['thread_id']}),patch('sys.stdout',output):
            relay_main(prefix+['file-send','--context',str(context),path.name])
        transfer=json.loads(output.getvalue());self.assertEqual(transfer['state'],'queued')
        self.assertFalse(sends);file_transfer.pump(relay)
        output=io.StringIO()
        with patch.dict(os.environ,{'CODEX_THREAD_ID':s['thread_id']}),patch('sys.stdout',output):
            relay_main(prefix+['file-status','--context',str(context),transfer['id']])
        value=json.loads(output.getvalue())[0]
        self.assertEqual(value['state'],'delivered');self.assertNotIn('file_key',value);self.assertNotIn('receipt',value)

    def test_agent_permission_cli_pins_context_and_never_changes_grants(self):
        s=self.conversation();publish(self.tracker,s)
        context=self.state.runtime/'prompt-context'/(s['id']+'.json');output=io.StringIO()
        expected=dict(schema='intern-agent.file-permissions.v1',verified=True,upload=dict(granted=False))
        api=SimpleNamespace(file_permissions=lambda:expected)
        with patch.dict(os.environ,{'CODEX_THREAD_ID':s['thread_id']}),patch('sys.stdout',output),patch('intern_personal.feishu_live.FeishuAPI',return_value=api):
            relay_main(['--state',str(self.state.root),'file-permissions','--context',str(context)])
        self.assertEqual(json.loads(output.getvalue()),expected)
        with patch.dict(os.environ,{'CODEX_THREAD_ID':'foreign-thread'}),patch('sys.stdout',io.StringIO()),patch('intern_personal.feishu_live.FeishuAPI') as create:
            self.assertEqual(relay_main(['--state',str(self.state.root),'file-permissions','--context',str(context)]),2)
            create.assert_not_called()

    def test_delivery_evidence_schedules_history_without_running_git(self):
        s,relay,path,uploads,sends=self.setup_export()
        self.state.ingest(self.event(s,'Generate and send a report'))
        item=self.state.claim('agent')
        transfer=file_transfer.enqueue(self.state,self.tracker,s,path.name)
        with patch.object(self.tracker,'git',side_effect=AssertionError('Delivery must not wait for Git')):
            file_transfer.pump(relay)
        self.assertEqual(file_transfer.listing(self.state,s,transfer['id'])[0]['state'],'delivered')
        self.assertTrue(self.state.db.execute('SELECT 1 FROM tracking_pending WHERE workspace=?',(s['workspace'],)).fetchone())
        record=self.state.db.execute('SELECT evidence FROM turn_records WHERE seq=?',(item['seq'],)).fetchone()
        self.assertIn('Delivered file report.pdf',record[0]);self.assertNotIn('file_uploaded',record[0])


class ResourceAPITests(unittest.TestCase):
    def api(self):
        # Real SDK request builders, stubbed transport; no network or credentials.
        api=object.__new__(FeishuAPI);api.lock=threading.Lock();api.last_send=0
        api.lark=SimpleNamespace(FEISHU_DOMAIN='https://open.feishu.cn')
        return api

    def test_streamed_download_enforces_limit_and_never_follows_redirects(self):
        api=self.api();api.client=SimpleNamespace(_config=object())
        response=SimpleNamespace(status_code=200,headers={},iter_content=lambda **kw:iter([b'123',b'456']))
        class Response:
            def __enter__(self):return response
            def __exit__(self,*args):return False
        with patch('lark_oapi.core.token.manager.TokenManager.get_self_tenant_token',return_value='fixture-private-bearer'),patch('requests.get',return_value=Response()) as get,patch('intern_personal.attachments.MAX_BYTES',4):
            with self.assertRaises(Invalid):api.download('om_message','file_resource','file')
            self.assertFalse(get.call_args.kwargs['allow_redirects']);self.assertTrue(get.call_args.kwargs['stream'])
        response.status_code=302;response.raw=io.BytesIO(b'{}')
        with patch('lark_oapi.core.token.manager.TokenManager.get_self_tenant_token',return_value='fixture-private-bearer'),patch('requests.get',return_value=Response()):
            with self.assertRaises(FeishuError):api.download('om_message','file_resource','file')

    def test_sdk_upload_pdf_type_zip_stream_and_missing_receipts(self):
        api=self.api();requests=[]
        def create(request):
            requests.append(request)
            return SimpleNamespace(success=lambda:True,code=0,data=SimpleNamespace(file_key='file_uploaded'))
        api.client=SimpleNamespace(im=SimpleNamespace(v1=SimpleNamespace(file=SimpleNamespace(create=create))))
        self.assertEqual(api.upload_file('report.pdf',PDF),'file_uploaded')
        self.assertEqual(api.upload_file('results.zip',archive([('r.md',b'# Report\n')])),'file_uploaded')
        self.assertEqual([r.body.file_type for r in requests],['pdf','stream'])
        api.client.im.v1.file.create=lambda request:SimpleNamespace(success=lambda:True,code=0,data=None)
        with self.assertRaises(FeishuError):api.upload_file('report.pdf',PDF)

    def test_live_scope_check_accepts_tenant_grants_and_combined_upload_scope(self):
        api=self.api();requests=[]
        scopes=[SimpleNamespace(scope_name='im:resource',grant_status=1,scope_type='tenant'),
                SimpleNamespace(scope_name='im:message:send_as_bot',grant_status=1,scope_type=None),
                SimpleNamespace(scope_name='im:message:readonly',grant_status=1,scope_type='user'),
                SimpleNamespace(scope_name='im:message:readonly',grant_status=2,scope_type='tenant')]
        def listing(request):
            requests.append(request)
            return SimpleNamespace(success=lambda:True,code=0,data=SimpleNamespace(scopes=scopes))
        api.client=SimpleNamespace(application=SimpleNamespace(v6=SimpleNamespace(scope=SimpleNamespace(list=listing))))
        value=api.file_permissions()
        self.assertTrue(value['verified']);self.assertTrue(value['upload']['granted']);self.assertTrue(value['send']['granted'])
        self.assertFalse(value['download']['granted']);self.assertFalse(value['round_trip_verified'])
        self.assertEqual(requests[0].uri,'/open-apis/application/v6/scopes')
        scopes.append(SimpleNamespace(scope_name='im:message:readonly',grant_status=1,scope_type='tenant'))
        self.assertTrue(api.file_permissions()['download']['granted'])
        scopes[0].scope_name='im:resource:upload'
        self.assertTrue(api.file_permissions()['upload']['granted'])

    def test_scope_api_failure_is_not_reported_as_missing_permissions(self):
        api=self.api()
        api.client=SimpleNamespace(application=SimpleNamespace(v6=SimpleNamespace(scope=SimpleNamespace(
            list=lambda request:SimpleNamespace(success=lambda:False,code=212100,data=None)))))
        value=file_transfer.permissions(api)
        self.assertFalse(value['verified']);self.assertEqual(value['error_code'],212100)
        self.assertNotIn('download',value)
