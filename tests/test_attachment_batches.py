"""File-first messages form one private, durable, sender-scoped Codex input."""
import io
import json
from pathlib import Path
import time
import unittest
import zipfile

from intern_personal import access,attachment_batches as batches,attachments
from intern_personal.core import Invalid
from intern_personal.live_state import LiveState
import test_live as live_fixture
import test_steering as steering_fixture


PDF=b'%PDF-1.4\n%%EOF\n'


def word():
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w') as archive:
        archive.writestr('[Content_Types].xml','<Types/>')
        archive.writestr('word/document.xml','<document>A document</document>')
    return output.getvalue()


class AttachmentBatchTests(unittest.TestCase):
    setUp=live_fixture.LiveTests.setUp
    event=live_fixture.LiveTests.event
    bot=steering_fixture.SteeringTests.bot
    busy=steering_fixture.SteeringTests.busy
    worker=steering_fixture.SteeringTests.worker

    def upload(self,s,name='paper.pdf',mid='om_file',key='file_test',sender='ou_owner',at=None):
        payload=self.event('',s['chat_id'],mid,sender)
        message=payload['event']['message']
        message.update(message_type='file',content=json.dumps(dict(file_name=name,file_key=key)))
        if at is not None:message['create_time']=str(int(at*1000))
        self.assertEqual(self.relay.receive(payload),'accepted')
        return payload

    def message(self,s,text='Compare these files',mid='om_text',sender='ou_owner',at=None):
        payload=self.event(text,s['chat_id'],mid,sender)
        if at is not None:payload['event']['message']['create_time']=str(int(at*1000))
        self.assertEqual(self.relay.receive(payload),'accepted')
        return self.row(mid)

    def row(self,mid):
        return dict(self.state.db.execute('SELECT * FROM events WHERE message_id=?',(mid,)).fetchone())

    def names(self,item):
        return [r[0] for r in self.state.db.execute('SELECT name FROM attachments WHERE seq=? ORDER BY ordinal',(item['seq'],))]

    def control(self,s,text,sender='ou_owner'):
        return self.relay.control(dict(seq=999,text=text,session=s['id'],chat_id=s['chat_id'],sender=sender))

    def test_files_wait_then_one_unchanged_message_with_real_bytes_and_origins(self):
        s=self.relay.open_session('project');downloads=[];received=[]
        data={'file_0':PDF,'file_1':b'# Notes\n','file_2':b'Plain text\n','file_3':word(),'file_4':b'\x00\x01\xffdata'}
        names=['paper.pdf','notes.md','notes.txt','paper.docx','data.bin']
        for i,name in enumerate(names):
            self.upload(s,name,'om_file_'+str(i),'file_'+str(i))
            self.assertEqual(self.relay.process_once('agent'),'idle')
        self.assertFalse(self.host.calls);self.assertFalse(self.api.reactions)
        self.assertEqual(len(batches.pending(self.state,s)),5)
        self.api.download=lambda *args:downloads.append(args) or data[args[1]]
        def run(session,text,inputs=None,resource_context=''):
            received.append((text,inputs,resource_context));return 'Compared all files'
        self.host.run=run
        text='  Compare these files.\nKeep my exact wording.  '
        item=self.message(s,text)
        self.assertEqual(self.names(item),names)
        self.assertEqual(self.relay.process_once('agent'),'complete')
        self.assertEqual(len(received),1);self.assertEqual(received[0][0],text)
        self.assertEqual([r[0] for r in downloads],['om_file_'+str(i) for i in range(5)])
        directory=self.state.runtime/'attachments'/str(item['seq'])
        for i,name in enumerate(names):
            path=directory/(str(i)+'-'+name)
            self.assertEqual(path.read_bytes(),data['file_'+str(i)])
            self.assertIn(str(path),received[0][2]);self.assertEqual(path.stat().st_mode&0o777,0o600)
        self.assertEqual(self.state.db.execute("SELECT COUNT(*) FROM events WHERE state='attachment_bound'").fetchone()[0],5)
        self.assertFalse(batches.pending(self.state,s))
        self.assertFalse(list(Path(self.tracker.code_directory(s)).glob('*.pdf')))
        next_item=self.message(s,'Continue without files','om_next')
        self.assertFalse(self.names(next_item))

    def test_duplicates_do_not_rebind_or_start_an_upload_turn(self):
        s=self.relay.open_session('project');payload=self.upload(s)
        self.assertEqual(self.relay.receive(payload),'duplicate')
        item=self.message(s)
        self.assertEqual(self.relay.receive(self.event(item['text'],s['chat_id'],'om_text')),'duplicate')
        self.assertEqual(self.names(item),['paper.pdf'])
        self.assertEqual(self.state.db.execute('SELECT COUNT(*) FROM attachment_origins').fetchone()[0],1)
        self.assertEqual(self.relay.receive(payload),'duplicate')
        self.assertFalse(batches.pending(self.state,s))

    def test_pending_survives_restart_and_bound_batch_is_not_repeated(self):
        s=self.relay.open_session('project');self.upload(s)
        self.state.close();self.state=LiveState(self.temp.name);self.addCleanup(self.state.close)
        self.relay.state=self.state;self.tracker.state=self.state;self.state.recover()
        self.assertEqual(len(batches.pending(self.state,s)),1)
        item=self.message(s);self.state.recover()
        self.assertEqual(self.names(item),['paper.pdf']);self.assertFalse(batches.pending(self.state,s))
        self.assertFalse(self.names(self.message(s,'Next','om_next')))

    def test_sender_and_bot_isolation_even_in_same_workspace(self):
        a=self.relay.open_session('project',new=True);b=self.relay.open_session('project',new=True)
        access.command(self.state,['grant','ou_operator','project','operator'])
        self.upload(a,'owner.txt','om_owner','file_owner')
        self.upload(a,'operator.txt','om_operator','file_operator','ou_operator')
        self.upload(b,'other.txt','om_other','file_other')
        self.assertEqual(self.names(self.message(a,mid='om_optext',sender='ou_operator')),['operator.txt'])
        self.assertEqual(self.names(self.message(b,mid='om_othertext')),['other.txt'])
        self.assertEqual(self.names(self.message(a)),['owner.txt'])

    def test_mention_only_chat_can_stage_files_but_requires_mention_for_submission(self):
        s=self.relay.open_session('project')
        self.state.db.execute("INSERT INTO trigger_settings VALUES(?,'mentions')",(s['id'],))
        self.state.db.execute("INSERT INTO bot_identity VALUES('cli_test','ou_bot')")
        self.upload(s)
        self.assertEqual(self.relay.receive(self.event('Unmentioned text',s['chat_id'],'om_ignored')),'ignored_unmentioned')
        self.assertEqual(len(batches.pending(self.state,s)),1);self.assertFalse(self.host.calls)
        payload=self.event('@_user_1 Summarize this',s['chat_id'],'om_mentioned')
        payload['event']['message']['mentions']=[dict(key='@_user_1',id=dict(open_id='ou_bot'))]
        self.assertEqual(self.relay.receive(payload),'accepted')
        item=self.row('om_mentioned');self.assertEqual(item['text'],'Summarize this')
        self.assertEqual(self.names(item),['paper.pdf'])

    def test_untrusted_sender_cannot_stage_or_consume_owner_files(self):
        s=self.relay.open_session('project');self.upload(s)
        outsider=self.event('',s['chat_id'],'om_outsider','ou_outsider')
        outsider['event']['message'].update(message_type='file',content=json.dumps(dict(file_name='foreign.txt',file_key='file_foreign')))
        self.assertEqual(self.relay.receive(outsider),'rejected_sender')
        self.assertEqual(self.relay.receive(self.event('Use these',s['chat_id'],'om_other','ou_outsider')),'rejected_sender')
        self.assertEqual([r['name'] for r in batches.pending(self.state,s)],['paper.pdf'])

    def test_commands_preserve_waiting_files_and_clear_is_sender_scoped(self):
        s=self.relay.open_session('project');access.command(self.state,['grant','ou_operator','project','operator'])
        self.upload(s);self.upload(s,'operator.txt','om_operator','file_operator','ou_operator')
        self.message(s,'/status','om_status')
        self.message(s,'/model','om_model')
        self.assertEqual(len(batches.pending(self.state,s)),2)
        self.assertIn('paper.pdf',self.control(s,'/files pending'))
        self.assertNotIn('operator.txt',self.control(s,'/files pending'))
        self.assertIn('uploads waiting',self.state.turn_status(s['id']))
        self.assertIn('active Codex turn continues',self.control(s,'/files clear'))
        self.assertEqual([r['name'] for r in batches.pending(self.state,s)],['operator.txt'])
        self.assertEqual(self.row('om_file')['state'],'cancelled');self.assertFalse(self.host.calls)
        self.assertFalse(self.names(self.message(s)))

    def test_control_defaults_to_its_own_pending_files_and_explicit_target_works(self):
        s=self.relay.open_session('control');s['chat_id']='oc_manager'
        self.state.set_session(s['id'],chat_id='oc_manager')
        self.state.db.execute('INSERT OR REPLACE INTO controller_routes VALUES(1,?,?,?)',('cli_test','oc_manager',s['id']))
        self.upload(s,'control.docx')
        self.assertIn('control.docx',self.control(s,'/files pending'))
        self.assertIn('control.docx',self.control(s,'/files '+s['id']+' pending'))
        item=self.message(s,'Review this document')
        self.assertEqual(self.names(item),['control.docx'])

    def test_captioned_post_consumes_prior_files_before_its_own_images(self):
        s=self.relay.open_session('project');self.upload(s)
        payload=self.event('',s['chat_id'],'om_post')
        payload['event']['message'].update(message_type='post',content=json.dumps(dict(content=[[
            dict(tag='text',text='Compare the paper with this figure'),dict(tag='img',image_key='img_test')]])))
        self.assertEqual(self.relay.receive(payload),'accepted');item=self.row('om_post')
        self.assertEqual(self.names(item),['paper.pdf','image'])
        calls=[];self.api.download=lambda *args:calls.append(args) or (PDF if args[1]=='file_test' else b'\x89PNG\r\n\x1a\nfigure')
        text,images,refs=attachments.prepare(self.relay,item)
        self.assertEqual(text,'Compare the paper with this figure');self.assertEqual(len(images),1)
        self.assertEqual([r[0] for r in calls],['om_file','om_post']);self.assertIn('paper.pdf',refs)

    def test_image_only_post_waits_and_keeps_structured_image_input(self):
        s=self.relay.open_session('project');payload=self.event('',s['chat_id'],'om_image')
        payload['event']['message'].update(message_type='post',content=json.dumps(dict(zh_cn=dict(content=[[
            dict(tag='at',user_id='all'),dict(tag='img',image_key='img_test')]]))))
        self.assertEqual(self.relay.receive(payload),'accepted');self.assertIsNone(self.state.claim('agent'))
        item=self.message(s,'Describe this picture')
        self.api.download=lambda *args:b'\x89PNG\r\n\x1a\npicture'
        text,images,refs=attachments.prepare(self.relay,item)
        self.assertEqual(text,'Describe this picture');self.assertEqual(images[0]['type'],'localImage')

    def test_busy_upload_never_steers_and_message_retains_batch_as_one_queued_input(self):
        s,parent=self.busy();self.upload(s)
        self.assertFalse(self.state.db.execute('SELECT 1 FROM steered_inputs WHERE seq=?',(self.row('om_file')['seq'],)).fetchone())
        item=self.message(s);worker,calls,_=self.worker(s);worker.scan()
        self.assertFalse(any(method=='turn/steer' for method,_ in calls))
        self.assertEqual(self.row('om_text')['state'],'queued')
        self.assertEqual(self.names(item),['paper.pdf'])
        self.assertIsNone(self.state.claim('agent'));self.assertEqual(self.row('om_file')['state'],'attachment_bound')

    def test_old_thread_files_are_visible_but_never_bound_to_new_thread(self):
        s=self.relay.open_session('project');self.upload(s)
        self.state.set_session(s['id'],thread_id='new-thread')
        self.assertFalse(self.names(self.message(s)))
        self.assertIn('previous thread',self.control(s,'/files pending'))
        self.control(s,'/files clear');self.assertFalse(batches.pending(self.state,self.state.session(s['id'])))

    def test_original_upload_order_and_duplicate_names_are_preserved(self):
        s=self.relay.open_session('project');now=time.time()
        self.upload(s,'same.txt','om_second','file_second',at=now-.1)
        self.upload(s,'same.txt','om_first','file_first',at=now-.2)
        self.upload(s,'future.txt','om_future','file_future',at=now+2)
        item=self.message(s,at=now)
        self.assertEqual(self.names(item),['same.txt','same.txt'])
        calls=[];self.api.download=lambda *args:calls.append(args) or args[1].encode()
        attachments.prepare(self.relay,item)
        self.assertEqual([r[0] for r in calls],['om_first','om_second'])
        directory=self.state.runtime/'attachments'/str(item['seq'])
        self.assertEqual((directory/'0-same.txt').read_bytes(),b'file_first')
        self.assertEqual((directory/'1-same.txt').read_bytes(),b'file_second')
        self.assertEqual([r['name'] for r in batches.pending(self.state,s)],['future.txt'])

    def test_failed_download_never_starts_codex_or_reassigns_files(self):
        s=self.relay.open_session('project');self.upload(s)
        self.api.download=lambda *args:(_ for _ in ()).throw(RuntimeError('offline'))
        item=self.message(s);self.assertEqual(self.relay.process_once('agent'),'complete')
        self.assertFalse(self.host.calls);self.assertIn('Codex was not started',self.api.messages[-1][1])
        self.assertEqual(self.names(item),['paper.pdf']);self.assertFalse(batches.pending(self.state,s))
        self.assertFalse(self.names(self.message(s,'Different task','om_next')))

    def test_previously_cached_original_is_reused_and_hash_checked(self):
        s=self.relay.open_session('project');self.upload(s)
        self.api.download=lambda *args:PDF
        source=self.row('om_file');attachments.prepare(self.relay,source)
        item=self.message(s)
        self.api.download=lambda *args:(_ for _ in ()).throw(AssertionError('Must reuse validated saved bytes'))
        attachments.prepare(self.relay,item)
        self.assertEqual((self.state.runtime/'attachments'/str(item['seq'])/'0-paper.pdf').read_bytes(),PDF)

    def test_partial_batch_failure_keeps_files_but_never_starts_partial_codex_turn(self):
        s=self.relay.open_session('project');self.upload(s)
        self.upload(s,'second.pdf','om_second','file_second')
        self.api.download=lambda message,key,kind:PDF if key=='file_test' else b'not a pdf'
        item=self.message(s);self.assertEqual(self.relay.process_once('agent'),'complete')
        self.assertFalse(self.host.calls)
        self.assertEqual(self.names(item),['paper.pdf','second.pdf'])
        self.assertTrue((self.state.runtime/'attachments'/str(item['seq'])/'0-paper.pdf').is_file())
        self.assertFalse(batches.pending(self.state,s))

    def test_formats_still_validate_and_opaque_input_does_not_enable_opaque_export(self):
        attachments.validate_file('document.docx',word())
        attachments.validate_file('document.doc',b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1document')
        attachments.validate_file('document.rtf',b'{\\rtf1 document}')
        attachments.validate_file('data.bin',b'\x00\xff',allow_opaque=True)
        for data in ['Windows text'.encode('utf-16'),'中文文本'.encode('gbk')]:
            attachments.validate_file('windows.txt',data,allow_opaque=True)
            with self.assertRaises(Invalid):attachments.validate_file('windows.txt',data)
        for name,data in [('data.bin',b'\x00\xff'),('bad.docx',b'not a zip'),('bad.pdf',b'bad'),('bad.doc',b'bad')]:
            with self.subTest(name=name),self.assertRaises(Invalid):attachments.validate_file(name,data)
        with self.assertRaises(Invalid):attachments.validate_file('document.docx',word(),credential_check=lambda value:True)

    def test_download_origin_cannot_cross_a_sender_or_conversation(self):
        a=self.relay.open_session('project',new=True);b=self.relay.open_session('project',new=True)
        self.upload(a);item=self.message(a)
        other=self.message(b,'Another conversation','om_other')
        self.state.db.execute('UPDATE attachment_origins SET source_seq=? WHERE seq=?',(other['seq'],item['seq']))
        self.api.download=lambda *args:(_ for _ in ()).throw(AssertionError('Must reject before download'))
        with self.assertRaises(Invalid):attachments.prepare(self.relay,item)


if __name__=='__main__':unittest.main()
