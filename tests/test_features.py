import copy
import hashlib
import json
from pathlib import Path
import time
import unittest
from unittest.mock import patch

from intern_personal.core import Invalid
from intern_personal.feishu_live import Relay
from intern_personal.live_state import LiveState
from intern_personal.teams import TeamCommands,snapshot,validate,complete
from intern_personal import access,attachments,archive_parts,operations,progress,recovery
from test_tracking import TrackingTests
from test_live import FakeAPI,FakeHost


class FeaturesTests(unittest.TestCase):
    setUp=TrackingTests.setUp
    raw=TrackingTests.raw
    attach=TrackingTests.attach

    def conversation(self):
        if not self.state.workspace('project'):self.attach()
        s,_=self.state.reserve_session('project',new=True);self.tracker.prepare_session(s)
        self.state.set_session(s['id'],state='ready',chat_id='oc_'+s['id'],thread_id='fixture_'+s['id'])
        return self.state.session(s['id'])

    def relay(self,api=None):return Relay(self.state,api or FakeAPI(),FakeHost(self.state),self.tracker)

    def team(self,limit=2):
        lead=self.conversation();worker=self.conversation();relay=self.relay();commands=TeamCommands(relay)
        commands.command(['create','Test team','project',lead['id'],str(limit)])
        team=snapshot(self.state)['teams'][0]
        commands.command(['add',team['id'],worker['id']])
        return commands,team,lead,worker

    def task(self,s):
        from intern_personal.task_branches import command
        command(self.tracker,s,'start','Implement parser')
        command(self.tracker,s,'criteria','Parser boundaries are covered and existing behavior is preserved')
        command(self.tracker,s,'brief','verification Run parser regression tests and inspect the branch diff')
        task=self.state.ledger.selection(s['id']);command(self.tracker,s,'pause','')
        return task

    def event(self,s,text='Question',sender='ou_owner',mid=None,kind='text',content=None):
        return dict(header=dict(app_id='cli_test',event_type='im.message.receive_v1'),event=dict(sender=dict(sender_type='user',sender_id=dict(open_id=sender)),
            message=dict(chat_type='group',message_type=kind,chat_id=s['chat_id'],message_id=mid or 'om_'+s['id'],create_time=str(int(time.time()*1000)),content=json.dumps(content or dict(text=text)))))

    def test_team_limits_membership_and_archive_recovery(self):
        commands,t,lead,worker=self.team(1);extra=self.conversation()
        with self.assertRaises(Invalid):commands.command(['add',t['id'],extra['id']])
        with self.assertRaises(Invalid):commands.command(['create','Duplicate','project',worker['id']])
        binding=self.tracker.require_workspace('project');path=Path(binding['metadata_path'])
        data=json.loads((path/'.intern_workspace/recovery.json').read_text())
        documents=[(str(p.relative_to(path)),p.read_text()) for p in (path/'.intern_workspace').rglob('*.md')]
        recovery.validate(data,documents)
        fresh=LiveState(self.root/'fresh');self.addCleanup(fresh.close)
        self.assertFalse(fresh.identity()['owner'])
        # The recovery adapter's apply path is covered by its integration tests;
        # portable team state is independently checked against Markdown here.
        self.assertEqual(len(data['team_state']['members']),2)
        broken=copy.deepcopy(data);broken['team_state']['teams'][0]['max_workers']=0
        with self.assertRaises(Invalid):recovery.validate(broken,documents)
        commands.command(['archive',t['id']])
        self.assertIn('archived',operations.session_command(commands.relay,worker,'archive'))
        self.assertIn('restored',operations.session_command(commands.relay,self.state.session(worker['id']),'unarchive'))
        self.assertEqual(self.raw(self.remote,'rev-parse','main'),self.original)

    def test_assignment_requires_registered_worker_and_records_handoff(self):
        commands,t,lead,worker=self.team();task=self.task(worker)
        with self.assertRaises(Invalid):commands.command(['assign',t['id'],lead['id'],task['id']])
        commands.command(['assign',t['id'],worker['id'],task['id']])
        self.assertEqual(self.state.ledger.selection(worker['id'])['id'],task['id'])
        self.assertEqual(self.tracker.intern(worker)['code_branch'],task['code_branch'] or 'intern-task/project/'+task['id'])
        commands.command(['mail',t['id'],lead['id'],worker['id'],'Check parser boundaries'])
        from intern_personal.memory import context
        self.assertIn('Check parser boundaries',context(self.tracker,worker))
        self.assertIn('verification commands/results',context(self.tracker,worker))
        self.assertIn('review delivered changes and test evidence',context(self.tracker,lead))
        mail=snapshot(self.state)['mail'][0];commands.command(['read',t['id'],mail['id']])
        self.assertNotIn('Check parser boundaries',context(self.tracker,worker))
        with self.assertRaises(Invalid):commands.command(['accept',t['id'],task['id'],'Not delivered yet'])
        self.assertEqual(self.raw(self.remote,'rev-parse','main'),self.original)

    def test_worker_run_duplicate_and_interrupt_are_not_delivery(self):
        commands,t,lead,worker=self.team();task=self.task(worker)
        commands.command(['assign',t['id'],worker['id'],task['id']])
        commands.command(['run',t['id'],worker['id'],'Implement parser'])
        with self.assertRaises(Invalid):commands.command(['run',t['id'],worker['id'],'Duplicate'])
        item=self.state.claim('agent');self.state.ledger.update(item,activity='stopped',sync='synced')
        complete(self.state,item)
        self.assertEqual(snapshot(self.state)['assignments'][0]['state'],'assigned')
        self.state.ledger.update(item,activity='completed',sync='pending');complete(self.state,item)
        self.assertEqual(snapshot(self.state)['assignments'][0]['state'],'assigned')
        self.state.ledger.evidence(item['seq'],'Codex observed turn/completed status completed')
        self.state.ledger.update(item,activity='completed',sync='synced');complete(self.state,item)
        self.assertEqual(snapshot(self.state)['assignments'][0]['state'],'delivered')

    def test_isolated_team_and_ordinary_conversations_can_run_concurrently(self):
        commands,t,lead,worker=self.team();second=self.conversation();commands.command(['add',t['id'],second['id']])
        for s in [worker,second]:self.assertEqual(self.state.ingest(self.event(s)),'accepted')
        one=self.state.claim('agent');two=self.state.claim('agent')
        self.assertTrue(one);self.assertTrue(two);self.assertNotEqual(one['session'],two['session'])
        self.assertNotEqual(self.tracker.code_directory(worker),self.tracker.code_directory(second))
        third=self.conversation();self.state.ingest(self.event(third));self.assertEqual(self.state.claim('agent')['session'],third['id'])

    def test_roles_revoke_pending_access_and_owner_management_stays_exclusive(self):
        s=self.conversation();payload=self.event(s,sender='ou_person')
        self.assertEqual(self.state.ingest(payload),'rejected_sender')
        access.command(self.state,['grant','ou_person','project','viewer'])
        self.assertEqual(self.state.ingest(payload),'rejected_sender')
        self.assertEqual(self.state.ingest(self.event(s,'/status','ou_person','om_status')),'accepted')
        self.assertEqual(self.state.ingest(self.event(s,'/task start Bad','ou_person','om_bad')),'rejected_sender')
        self.assertEqual(self.state.ingest(self.event(s,'/model alternate-model','ou_person','om_model')),'rejected_sender')
        access.command(self.state,['grant','ou_person','project','operator'])
        self.assertEqual(self.state.ingest(payload),'accepted')
        row=dict(self.state.db.execute("SELECT * FROM events WHERE role='agent'").fetchone())
        access.command(self.state,['revoke','ou_person','project']);self.assertFalse(self.state.authorized(row))
        access.command(self.state,['grant','ou_person','project','operator'])
        fake=dict(row,text='/team list',role='control');self.assertFalse(self.state.authorized(fake))
        fake['text']='/stop';self.assertTrue(self.state.authorized(fake))
        fake['chat_id']='oc_manager';self.assertFalse(self.state.authorized(fake))

    def test_attachment_download_once_private_and_no_codex_on_permission_failure(self):
        s=self.conversation();api=FakeAPI();calls=[]
        api.download=lambda *args:calls.append(args) or b'File content\n'
        relay=self.relay(api)
        payload=self.event(s,kind='file',content=dict(file_key='file_test',file_name='notes.md'))
        self.assertEqual(self.state.ingest(payload),'accepted');self.assertEqual(self.state.ingest(payload),'duplicate')
        self.assertIsNone(self.state.claim('agent'))
        self.state.ingest(self.event(s,'Read these notes',mid='om_file_text'))
        item=self.state.claim('agent');prompt,inputs,reference=attachments.prepare(relay,item)
        self.assertEqual(prompt,item['text']);self.assertNotIn('SHA256',prompt)
        self.assertIn('SHA256',reference);self.assertEqual(inputs,[])
        attachments.prepare(relay,item);self.assertEqual(len(calls),1)
        path=self.state.runtime/'attachments'/str(item['seq'])/'0-notes.md'
        self.assertEqual(path.stat().st_mode&0o777,0o600)
        path.write_bytes(b'Tampered')
        with self.assertRaises(Invalid):attachments.prepare(relay,item)
        self.state.transition(item['seq'],'cancelled')
        second=self.event(s,kind='image',mid='om_img',content=dict(image_key='img_test'))
        self.state.ingest(second)
        self.state.ingest(self.event(s,'Describe the image',mid='om_image_text'))
        api.download=lambda *args:(_ for _ in ()).throw(RuntimeError('permission'))
        self.assertEqual(relay.process_once('agent'),'complete');self.assertEqual(relay.host.calls,[])
        self.assertIn('Codex was not started',api.messages[-1][1])

    def test_rich_text_images_and_unsupported_types_have_feedback(self):
        s=self.conversation();message=self.event(s,kind='post',content=dict(content=[[dict(tag='text',text='Describe this'),dict(tag='img',image_key='img_test')]]))
        self.assertEqual(self.state.ingest(message),'accepted')
        self.assertEqual(self.state.db.execute('SELECT COUNT(*) FROM attachments').fetchone()[0],1)
        self.assertEqual(self.state.ingest(self.event(s,kind='audio',mid='om_audio',content={})), 'accepted')
        row=self.state.db.execute("SELECT text,role FROM events WHERE message_id='om_audio'").fetchone()
        self.assertEqual(tuple(row),('/unsupported','control'))
        with self.assertRaises(Invalid):attachments.normalize(dict(message_type='file',content=json.dumps(dict(file_key='file_x',file_name='../secret.txt'))))

    def test_export_workspace_snapshots_and_optional_committed_files(self):
        s=self.conversation();relay=self.relay();sent=[]
        relay.api.upload_file=lambda name,data:sent.append((name,data)) or 'file_uploaded'
        relay.api.send_file=lambda *args:'receipt'
        self.assertIn('queued',attachments.export(relay,s,'README.md'))
        from intern_personal.file_transfer import pump
        pump(relay)
        self.assertEqual(sent[0][1],b'Original project\n')
        for path in ['../README.md','/etc/passwd','credentials.json','.intern_workspace/status.md']:
            with self.assertRaises(Invalid):attachments.export(relay,s,path)
        p=Path(self.tracker.code_directory(s))/'draft.txt';p.write_text('Draft')
        self.assertIn('queued',attachments.export(relay,s,'draft.txt'))
        with self.assertRaises(Invalid):attachments.export(relay,s,'draft.txt',committed=True)

    def test_progress_milestones_are_deduplicated_and_not_sent_after_completion(self):
        s=self.conversation();relay=self.relay();notices=[]
        relay.api.progress=lambda *args:notices.append(args)
        self.state.ingest(self.event(s));item=self.state.claim('agent')
        progress.mark(relay,item,'running');progress.mark(relay,item,'running');progress.deliver(relay)
        self.assertEqual(len(notices),0)  # The owner disabled start acknowledgements.
        progress.mark(relay,item,'uploading');progress.deliver(relay)
        self.assertEqual(len(notices),1);progress.mark(relay,item,'uploading');self.assertFalse(progress.deliver(relay))
        progress.mark(relay,item,'uploading');self.state.transition(item['seq'],'complete');progress.deliver(relay)
        self.assertEqual(len(notices),1)

    def test_archive_parts_fail_closed_and_support_more_than_two_mib(self):
        storage={}
        class Writer:
            def write(self,binding,path,text):storage[path]=text
        original=dict(schema='fixture',body='History entry\n'*200000)
        archive_parts.write(Writer(),{},original)
        manifest=json.loads(storage['.intern_workspace/recovery.json'])
        self.assertEqual(manifest['schema'],archive_parts.SCHEMA)
        self.assertEqual(archive_parts.expand(manifest,storage.__getitem__),original)
        broken=copy.deepcopy(manifest);broken['parts'][0]['path']='../outside.json'
        with self.assertRaises(Invalid):archive_parts.expand(broken,storage.__getitem__)
        storage[manifest['parts'][0]['path']]='"tampered"'
        with self.assertRaises(Invalid):archive_parts.expand(manifest,storage.__getitem__)

    def test_dashboard_diagnostics_never_expose_secrets_or_thread_identity(self):
        s=self.conversation();data=operations.dashboard(self.state);encoded=json.dumps(data)
        self.assertNotIn('fixture_'+s['id'],encoded);self.assertNotIn('private-test-sentinel',encoded)
        self.assertEqual(data['conversations'][0]['mode'],'idle')
        report=operations.diagnose(self.state)
        self.assertFalse(report['automatic_repairs']);self.assertFalse(report['account_configuration_verified'])
        self.assertTrue(any(c['id']=='receiver' and not c['passed'] for c in report['checks']))

    def test_compact_waits_for_observed_completion_and_preserves_thread(self):
        s=self.conversation();relay=self.relay();calls=[]
        class Connection:
            def request(self,method,params):calls.append(method);return {}
            def notification(self,timeout):return dict(method='turn/completed',params=dict(threadId=s['thread_id'],turn=dict(status='completed')))
            def close(self):calls.append('closed')
        relay.host.connect=lambda **kw:Connection();relay.host.thread_params=lambda s:{}
        self.assertIn('completed',operations.session_command(relay,s,'compact'))
        self.assertEqual(calls,['thread/resume','thread/compact/start','closed'])
        self.assertEqual(self.state.session(s['id'])['thread_id'],s['thread_id'])

    def test_team_validator_rejects_malformed_identifiers(self):
        commands,t,lead,worker=self.team();data=snapshot(self.state)
        data['teams'][0]['id']=[]
        with self.assertRaises(Invalid):validate(data,'project',{lead['id'],worker['id']},set())
        from intern_personal.records import render,validate_document
        metadata=dict(kind='team',workspace='project',revision=1,team=t,members=[dict(team=t['id'],session=[],role='lead')],assignments=[])
        with self.assertRaises(Invalid):validate_document('team.md',render('Malformed team',metadata,[]))

    def test_verified_branch_delivery_acceptance_is_recorded_without_merge(self):
        commands,t,lead,worker=self.team();task=self.task(worker)
        commands.command(['assign',t['id'],worker['id'],task['id']])
        commands.command(['run',t['id'],worker['id'],'Deliver parser'])
        relay=commands.relay
        def run(s,text):
            event=self.state.db.execute("SELECT seq FROM events WHERE session=? AND state='running'",(s['id'],)).fetchone()
            self.state.ledger.evidence(event[0],'Codex observed turn/completed status completed')
            (Path(self.tracker.code_directory(s))/'parser.py').write_text('answer = 42\n')
            return 'Parser implemented and verified.'
        relay.host.run=run
        self.assertEqual(relay.process_once('agent'),'complete')
        self.assertEqual(snapshot(self.state)['assignments'][0]['state'],'delivered')
        from intern_personal.task_branches import command
        command(self.tracker,worker,'report','Parser regression checks | Passed | Synthetic local test delivery')
        commands.command(['accept',t['id'],task['id'],'Owner accepted parser'])
        a=snapshot(self.state)['assignments'][0]
        self.assertEqual(a['state'],'accepted');self.assertTrue(json.loads(a['review'])['commit'])
        with self.assertRaises(Invalid):commands.command(['changes',t['id'],task['id'],'Change receipt'])
        self.assertEqual(self.raw(self.remote,'rev-parse','main'),self.original)

    def test_history_pages_and_team_state_restore_without_routing_or_access(self):
        commands,t,lead,worker=self.team()
        commands.command(['mail',t['id'],lead['id'],worker['id'],'Resume reference'])
        access.command(self.state,['grant','ou_person','project','viewer'])
        # Keep this a multipart restore test after compact JSON serialization.
        for _ in range(1600):self.state.ledger.revision('project','conversation',lead['id'],'memory',conversation=lead['id'])
        self.tracker.record(lead)
        binding=self.tracker.require_workspace('project');root=Path(binding['metadata_path'])
        self.assertTrue((root/'.intern_workspace/conversations'/lead['id']/'history/page_2.md').is_file())
        self.assertEqual(json.loads((root/'.intern_workspace/recovery.json').read_text())['schema'],archive_parts.SCHEMA)
        clone=self.root/'clone';self.raw(self.root,'clone','-q','--branch','main',str(self.remote),str(clone))
        self.raw(clone,'remote','set-url','origin','https://github.com/example-owner/example-project')
        self.raw(clone,'fetch','-q',str(self.remote),'intern-management/test:refs/remotes/origin/intern-management/test')
        fresh=LiveState(self.root/'recovery-private');self.addCleanup(fresh.close)
        from test_tracking import LocalTracking
        tracker=LocalTracking(fresh,self.remote)
        preview=recovery.restore(tracker,clone,'https://github.com/example-owner/example-project','intern-management/test')
        self.assertEqual(preview['teams'],1);self.assertFalse(preview['applied'])
        result=recovery.restore(tracker,clone,'https://github.com/example-owner/example-project','intern-management/test',True)
        self.assertTrue(result['applied']);self.assertFalse(fresh.identity()['owner'])
        self.assertEqual(snapshot(fresh),snapshot(self.state))
        self.assertEqual(fresh.db.execute('SELECT COUNT(*) FROM workspace_access').fetchone()[0],0)
        self.assertTrue(all(not s['thread_id'] and not s['chat_id'] for s in fresh.sessions()))
        self.assertIsNone(fresh.claim('agent'))

    def test_goal_uses_a_paused_native_mirror_and_tracked_continuations(self):
        s=self.conversation();relay=self.relay();calls=[]
        class Connection:
            def request(self,method,params,**kwargs):
                calls.append((method,params));return dict(goal=dict(objective='Fix parser',status='paused',threadId='never-display',tokenBudget=4000))
            def close(self):pass
        relay.host.connect=lambda **kw:Connection();relay.host.thread_params=lambda s:{}
        result=json.loads(operations.goal(relay,s,['set','4000','Fix','parser']))
        self.assertTrue(result['automatic_continuations']);self.assertNotIn('threadId',result['goal'])
        self.assertEqual(calls[-1][1]['status'],'paused')
        self.assertEqual(result['goal']['status'],'active')
        self.assertIn('Goal paused',operations.goal(relay,s,['pause']))
        self.assertIn('Goal resumed',operations.goal(relay,s,['resume']))

    def test_review_cannot_finish_a_different_selected_task(self):
        commands,t,lead,worker=self.team();task=self.task(worker)
        commands.command(['assign',t['id'],worker['id'],task['id']])
        from intern_personal.task_branches import command
        command(self.tracker,worker,'pause','');command(self.tracker,worker,'start','Different task')
        selected=self.state.ledger.selection(worker['id'])
        for action in ['accept','changes']:
            with self.assertRaisesRegex(Invalid,'another selected task'):commands.command([action,t['id'],task['id'],'Review'])
        self.assertEqual(self.state.ledger.selection(worker['id'])['id'],selected['id'])
        self.assertEqual(self.state.ledger.selection(worker['id'])['state'],'active')


del TrackingTests  # The imported fixture is not another copy of its test suite.
if __name__=='__main__':unittest.main()
