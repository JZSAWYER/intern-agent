"""Steering keeps one turn, durable original input, and exact acceptance evidence."""
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_live as fixture
from intern_personal.codex_bridge import BridgeError, RequestRejected
from intern_personal.core import Invalid
from intern_personal.steering import Steering, reserve_waiting


class SteeringTests(unittest.TestCase):
    setUp=fixture.LiveTests.setUp
    event=fixture.LiveTests.event

    def bot(self):
        session=self.relay.open_session('project',new=True)
        self.state.db.execute('''INSERT INTO tracked_interns
            (session,intern_name,code_path,code_branch,home_branch,published,metadata_mode)
            VALUES(?,?,?,?,?,1,'bot_branch')''',(session['id'],'fixture_'+session['id'],str(self.state.root/session['id']),
                'intern-agent/'+session['id'],'intern-agent/'+session['id']))
        return session

    def busy(self,session=None,manual=False):
        s=session or self.bot();parent=None
        if not manual:
            self.relay.receive(self.event('Original task',s['chat_id'],'om_parent_'+s['id']))
            parent=self.state.claim('agent',s['id'])
        self.state.db.execute('INSERT OR REPLACE INTO codex_outcomes VALUES(?,?,?,?,NULL,?,?)',
            (s['id'],s['thread_id'],'turn-'+s['id'],'inProgress',parent['seq'] if parent else None,time.time()))
        snapshot=dict(active='turn-'+s['id'],event_seq=parent['seq'] if parent else None)
        self.state.db.execute('INSERT OR REPLACE INTO console_views(session,config_path,cursor,snapshot) VALUES(?,?,0,?)',
                              (s['id'],'fixture-private-config',json.dumps(snapshot)))
        return s,parent

    def followup(self,s,text='Change direction without stopping',mid='om_followup'):
        self.assertEqual(self.relay.receive(self.event(text,s['chat_id'],mid)),'accepted')
        return dict(self.state.db.execute('SELECT * FROM events WHERE message_id=?',(mid,)).fetchone())

    def worker(self,s,*,active=True,error=None,rollout=None):
        calls=[]
        class Connection:
            def request(inner,method,params,timeout=0):
                calls.append((method,params))
                if method=='thread/read':return {'thread':{'id':s['thread_id'],'status':{'type':'active' if active else 'idle'}}}
                if method=='turn/steer':
                    if error:raise error
                    return {'turnId':params['expectedTurnId']}
                raise AssertionError('Steering must not start, resume or interrupt a turn')
            def close(inner):pass
        if rollout is None:
            rollout=self.state.root/'rollout.jsonl';rollout.write_text('')
        observer=SimpleNamespace(config=lambda session:dict(rollout=str(rollout),rollout_inode=rollout.stat().st_ino))
        host=SimpleNamespace(connect=lambda **kwargs:Connection(),observer=observer)
        worker=Steering(self.state,host=host,terminal=SimpleNamespace(config=observer.config,input_lock=threading.Lock()),api=self.api)
        return worker,calls,rollout

    def row(self,seq):return dict(self.state.db.execute('SELECT * FROM steered_inputs WHERE seq=?',(seq,)).fetchone())

    def test_busy_input_is_reserved_before_normal_worker_can_claim_it(self):
        s,_=self.busy(manual=True)
        text='  Complementary material\nkeep original spaces  '
        e=self.followup(s,text)
        self.assertEqual(e['text'],text);self.assertEqual(e['state'],'awaiting_terminal')
        self.assertIsNone(self.state.claim('agent',s['id']))
        self.assertEqual(self.row(e['seq'])['state'],'pending')

    def test_api_uses_fenced_active_turn_and_unchanged_owner_text(self):
        s,_=self.busy();text='  Change the scope\nkeep all evidence  '
        e=self.followup(s,text);worker,calls,_=self.worker(s);worker.scan()
        request=next(p for m,p in calls if m=='turn/steer')
        self.assertEqual(request,dict(threadId=s['thread_id'],expectedTurnId='turn-'+s['id'],input=[{'type':'text','text':text}]))
        self.assertEqual([m for m,_ in calls],['thread/read','turn/steer'])
        self.assertEqual(self.row(e['seq'])['state'],'accepted')
        current=self.state.db.execute('SELECT * FROM events WHERE seq=?',(e['seq'],)).fetchone()
        self.assertEqual(current['state'],'awaiting_terminal');self.assertIsNone(current['receipt'])
        self.assertTrue(self.state.db.execute('SELECT 1 FROM message_reactions WHERE seq=?',(e['seq'],)).fetchone())

    def test_idle_input_still_starts_normally(self):
        s=self.bot();e=self.followup(s)
        self.assertEqual(e['state'],'queued')
        self.assertEqual(self.state.claim('agent',s['id'])['seq'],e['seq'])
        self.assertIsNone(self.state.db.execute('SELECT 1 FROM steered_inputs WHERE seq=?',(e['seq'],)).fetchone())

    def test_owner_mid_is_idempotent_and_never_steered_twice(self):
        s,_=self.busy();e=self.followup(s);worker,calls,_=self.worker(s)
        worker.scan();worker.scan()
        self.assertEqual(self.relay.receive(self.event(e['text'],s['chat_id'],'om_followup')),'duplicate')
        worker.scan();self.assertEqual(sum(m=='turn/steer' for m,_ in calls),1)
        self.assertEqual(self.state.db.execute("SELECT COUNT(*) FROM recovery_notices WHERE id=?",('steer-'+str(e['seq'])+':accepted',)).fetchone()[0],1)

    def test_disconnect_after_send_is_held_and_not_retried(self):
        s,_=self.busy();e=self.followup(s);worker,calls,_=self.worker(s,error=BridgeError('disconnected'))
        worker.scan();worker.scan();worker.scan()
        self.assertEqual(self.row(e['seq'])['state'],'review_needed')
        self.assertEqual(sum(m=='turn/steer' for m,_ in calls),1)
        self.assertEqual(self.state.db.execute('SELECT state FROM events WHERE seq=?',(e['seq'],)).fetchone()[0],'uncertain_execution')

    def test_exact_later_transcript_can_confirm_unknown_send_without_replay(self):
        s,_=self.busy();e=self.followup(s);worker,calls,path=self.worker(s,error=BridgeError('disconnected'))
        worker.scan()
        path.write_text(json.dumps(dict(type='response_item',payload=dict(type='message',role='user',content=[dict(type='input_text',text=e['text'])])))+'\n')
        worker.scan();self.assertEqual(self.row(e['seq'])['state'],'accepted')
        self.assertEqual(sum(m=='turn/steer' for m,_ in calls),1)

    def test_old_matching_transcript_never_confirms_unknown_send(self):
        s,_=self.busy();e=self.followup(s);path=self.state.root/'old-rollout.jsonl'
        path.write_text(json.dumps(dict(type='response_item',payload=dict(type='message',role='user',content=[dict(type='input_text',text=e['text'])])))+'\n')
        worker,calls,_=self.worker(s,error=BridgeError('disconnected'),rollout=path);worker.scan();worker.scan()
        self.assertEqual(self.row(e['seq'])['state'],'review_needed')
        self.assertEqual(sum(m=='turn/steer' for m,_ in calls),1)

    def test_explicit_rejection_restores_same_original_request(self):
        s,parent=self.busy();e=self.followup(s);worker,calls,_=self.worker(s,error=RequestRejected('turn ended'))
        worker.scan();self.assertEqual(self.row(e['seq'])['state'],'fallback')
        self.assertEqual(self.state.db.execute('SELECT state,text FROM events WHERE seq=?',(e['seq'],)).fetchone()['state'],'queued')
        worker.scan();self.assertEqual(sum(m=='turn/steer' for m,_ in calls),1)
        self.state.transition(parent['seq'],'complete')
        self.assertEqual(self.state.claim('agent',s['id'])['seq'],e['seq'])

    def test_idle_race_falls_back_without_calling_steer(self):
        s,_=self.busy();e=self.followup(s);worker,calls,_=self.worker(s,active=False);worker.scan()
        self.assertEqual(self.row(e['seq'])['state'],'fallback')
        self.assertFalse(any(m=='turn/steer' for m,_ in calls))

    def test_background_and_forced_queue_messages_never_steer(self):
        s,_=self.busy()
        for mid in ['research:fixture:1','peer:fixture:request','auto-resume:fixture','queued:om_fixture']:
            self.state.db.execute('''INSERT INTO events(app_id,message_id,chat_id,sender,role,workspace,session,text,state)
                VALUES('cli_test',?,?,'ou_owner','agent','project',?,'Background reference','queued')''',(mid,s['chat_id'],s['id']))
        reserve_waiting(self.state)
        self.assertEqual(self.state.db.execute('SELECT COUNT(*) FROM steered_inputs').fetchone()[0],0)

    def test_queue_command_explicitly_creates_a_separate_turn(self):
        s,_=self.busy();self.relay.receive(self.event('/queue A separate task',s['chat_id'],'om_queue'))
        item=dict(self.state.db.execute("SELECT * FROM events WHERE message_id='om_queue'").fetchone())
        self.assertIn('separate',self.relay.control(item))
        queued=self.state.db.execute("SELECT * FROM events WHERE message_id='queued:om_queue'").fetchone()
        self.assertEqual(queued['text'],'A separate task');self.assertEqual(queued['state'],'queued')
        reserve_waiting(self.state)
        self.assertIsNone(self.state.db.execute('SELECT 1 FROM steered_inputs WHERE seq=?',(queued['seq'],)).fetchone())

    def test_attachment_keeps_normal_file_dispatch_without_losing_request(self):
        s,_=self.busy();e=self.followup(s)
        self.state.db.execute("INSERT INTO attachments VALUES(?,0,'fixture-key','evidence.pdf','file',NULL)",(e['seq'],))
        worker,calls,_=self.worker(s);worker.scan()
        self.assertEqual(self.row(e['seq'])['state'],'fallback');self.assertEqual(calls,[])

    def test_stop_prevents_not_yet_sent_update(self):
        s,_=self.busy();e=self.followup(s);self.state.ledger.stop(s)
        worker,calls,_=self.worker(s);worker.scan()
        self.assertEqual(self.row(e['seq'])['state'],'cancelled');self.assertEqual(calls,[])
        self.assertEqual(self.state.db.execute('SELECT state FROM events WHERE seq=?',(e['seq'],)).fetchone()[0],'cancelled')

    def test_explicit_queue_works_with_an_existing_receiver_and_once_only(self):
        s,_=self.busy();self.relay.receive(self.event('/queue Separate work',s['chat_id'],'om_queue_bridge'))
        self.assertIsNone(self.state.claim('control'))
        worker,calls,_=self.worker(s);worker.scan();worker.scan()
        command=self.state.db.execute("SELECT * FROM events WHERE message_id='om_queue_bridge'").fetchone()
        self.assertEqual(command['state'],'result_ready');self.assertIn('separate',command['result'])
        self.assertEqual(self.state.db.execute("SELECT COUNT(*) FROM events WHERE message_id='queued:om_queue_bridge'").fetchone()[0],1)
        self.assertEqual(calls,[])

    def test_unclaimed_legacy_message_can_join_current_manual_work(self):
        s=self.bot();e=self.followup(s)
        self.assertEqual(e['state'],'queued');self.busy(s,manual=True)
        worker,calls,_=self.worker(s);worker.scan()
        self.assertEqual(self.row(e['seq'])['state'],'accepted')
        self.assertIsNone(self.row(e['seq'])['parent_seq'])
        self.assertEqual(sum(m=='turn/steer' for m,_ in calls),1)

    def test_a_later_identical_input_cannot_certify_an_earlier_unknown_send(self):
        s,_=self.busy();first=self.followup(s);worker,calls,path=self.worker(s,error=BridgeError('disconnected'));worker.scan()
        second=self.followup(s,first['text'],mid='om_distinct_followup')
        # Reconcile an already uncertain legacy pair; new dispatch ordering
        # prevents this second send until the first is confirmed.
        self.state.db.execute("UPDATE steered_inputs SET state='review_needed',cursor=0,inode=? WHERE seq=?",(path.stat().st_ino,second['seq']))
        path.write_text(json.dumps(dict(type='response_item',payload=dict(type='message',role='user',content=[dict(type='input_text',text=first['text'])])))+'\n')
        worker.scan()
        self.assertEqual(self.row(first['seq'])['state'],'review_needed')
        self.assertEqual(self.row(second['seq'])['state'],'review_needed')

    def test_followups_keep_arrival_order_without_waiting_for_turn_completion(self):
        s,_=self.busy();first=self.followup(s,'First correction',mid='om_correction_first')
        second=self.followup(s,'Second correction',mid='om_correction_second');worker,calls,_=self.worker(s)
        worker.scan();self.assertEqual(self.row(first['seq'])['state'],'accepted')
        self.assertEqual(self.row(second['seq'])['state'],'pending')
        worker.scan();self.assertEqual(self.row(second['seq'])['state'],'accepted')
        self.assertEqual([p['input'][0]['text'] for m,p in calls if m=='turn/steer'],['First correction','Second correction'])

    def test_unknown_first_send_holds_only_its_own_later_followup(self):
        s,_=self.busy();first=self.followup(s,mid='om_unknown_first');second=self.followup(s,mid='om_unknown_second')
        worker,calls,_=self.worker(s,error=BridgeError('Fixture transport unavailable'));worker.scan();worker.scan()
        self.assertEqual(self.row(first['seq'])['state'],'review_needed')
        self.assertEqual(self.row(second['seq'])['state'],'pending')
        self.assertEqual(sum(m=='turn/steer' for m,_ in calls),1)

    def test_reloaded_state_preserves_accepted_input_without_another_send(self):
        s,_=self.busy();e=self.followup(s);worker,calls,_=self.worker(s);worker.scan()
        self.state.close();self.state.recover();worker.scan()
        self.assertEqual(self.row(e['seq'])['state'],'accepted')
        self.assertEqual(sum(m=='turn/steer' for m,_ in calls),1)

    def test_one_unreachable_console_does_not_block_another_bots_followup(self):
        a,_=self.busy();b,_=self.busy();first=self.followup(a,mid='om_unreachable');second=self.followup(b,mid='om_reachable')
        worker,calls,_=self.worker(b);original=worker.host.connect
        def connect(**kwargs):
            if kwargs['session']['id']==a['id']:raise OSError('Fixture disconnected')
            return original(**kwargs)
        worker.host.connect=connect;worker.scan()
        self.assertEqual(self.row(first['seq'])['state'],'pending')
        self.assertEqual(self.row(second['seq'])['state'],'accepted')

    def test_accepted_receipt_survives_a_later_ledger_failure(self):
        s,_=self.busy();e=self.followup(s);worker,calls,_=self.worker(s)
        with patch.object(self.state.ledger,'update',side_effect=RuntimeError('Fixture ledger failure')):
            worker.scan()
        self.assertEqual(self.row(e['seq'])['state'],'accepted')
        self.assertTrue(self.state.db.execute('SELECT 1 FROM recovery_notices WHERE id=?',('steer-'+str(e['seq'])+':accepted',)).fetchone())
        worker.scan();self.assertEqual(sum(m=='turn/steer' for m,_ in calls),1)

    def test_linked_history_follows_later_parent_git_reconciliation(self):
        s,parent=self.busy();e=self.followup(s);worker,calls,_=self.worker(s);worker.scan()
        self.state.transition(parent['seq'],'record_blocked',result='Saved answer',receipt='om_parent_final')
        self.state.ledger.update(parent,'record_blocked',activity='completed',sync='blocked',completed_at=time.time())
        self.state.db.execute("UPDATE codex_outcomes SET status='completed' WHERE session=?",(s['id'],))
        self.state.db.execute("UPDATE recovery_notices SET receipt='om_ack',state='delivered' WHERE id=?",('steer-'+str(e['seq'])+':accepted',))
        worker.scan();self.assertEqual(self.state.db.execute('SELECT state FROM events WHERE seq=?',(e['seq'],)).fetchone()[0],'record_blocked')
        self.state.transition(parent['seq'],'complete')
        self.state.ledger.update(parent,'complete',activity='completed',sync='synced',completed_at=time.time(),code_end='1'*40)
        worker.scan();child=self.state.db.execute('SELECT phase,sync,code_end FROM turn_records WHERE seq=?',(e['seq'],)).fetchone()
        self.assertEqual(dict(child),dict(phase='complete',sync='synced',code_end='1'*40))

    def test_delivered_manual_completion_releases_followup_before_git(self):
        s,_=self.busy(manual=True);e=self.followup(s);worker,calls,_=self.worker(s);worker.scan()
        self.state.db.execute('''INSERT INTO events(app_id,message_id,chat_id,sender,role,workspace,session,text,state,result,receipt)
            VALUES(?,?,?,?,'agent',?,?,?,'result_ready','Saved answer','om_final')''',
            (e['app_id'],'local_terminal_fixture',e['chat_id'],e['sender'],e['workspace'],s['id'],'Monitoring'))
        parent=dict(self.state.db.execute("SELECT * FROM events WHERE message_id='local_terminal_fixture'").fetchone())
        self.state.ledger.ensure_turn(parent,time.time(),'terminal')
        self.state.ledger.update(parent,activity='completed')
        self.state.db.execute('INSERT INTO terminal_turns VALUES(?,?,?,?,?)',(s['id'],'turn-'+s['id'],parent['seq'],'Saved answer','task_complete'))
        self.state.db.execute("UPDATE recovery_notices SET receipt='om_ack',state='delivered' WHERE id=?",('steer-'+str(e['seq'])+':accepted',))
        worker.scan();self.assertEqual(self.row(e['seq'])['state'],'completed')
        child=self.state.db.execute('SELECT * FROM turn_records WHERE seq=?',(e['seq'],)).fetchone()
        self.assertEqual((child['phase'],child['activity'],child['sync']),('reply_ready','completed','pending'))
        # No child retry or separate baseline/publication is fabricated.
        self.state.schedule_sync_retries()
        self.assertIsNone(self.state.db.execute('SELECT 1 FROM sync_retries WHERE seq=?',(e['seq'],)).fetchone())
        self.assertEqual(self.state.claim('agent',s['id'])['seq'],parent['seq'])
        self.state.transition(parent['seq'],'complete')
        self.state.ledger.update(parent,'complete',activity='completed',sync='synced',code_end='1'*40)
        worker.scan();child=self.state.db.execute('SELECT phase,sync,code_end FROM turn_records WHERE seq=?',(e['seq'],)).fetchone()
        self.assertEqual(tuple(child),('complete','synced','1'*40))
        self.assertEqual(sum(m=='turn/steer' for m,_ in calls),1)

    def test_shared_completion_waits_for_parent_ledger_sync_race(self):
        s,parent=self.busy();e=self.followup(s);worker,_,_=self.worker(s);worker.scan()
        self.state.ledger.update(parent,'result_ready',activity='completed',sync='pending')
        self.state.transition(parent['seq'],'complete',result='Delivered answer',receipt='om_final')
        self.state.db.execute("UPDATE codex_outcomes SET status='completed' WHERE session=?",(s['id'],))
        self.state.db.execute("UPDATE recovery_notices SET receipt='om_ack',state='delivered' WHERE id=?",('steer-'+str(e['seq'])+':accepted',))
        worker.scan()
        child=self.state.db.execute('SELECT phase,sync FROM turn_records WHERE seq=?',(e['seq'],)).fetchone()
        self.assertEqual(tuple(child),('reply_ready','pending'))
        self.state.ledger.update(parent,'complete',activity='completed',sync='synced')
        worker.scan()
        child=self.state.db.execute('SELECT phase,sync FROM turn_records WHERE seq=?',(e['seq'],)).fetchone()
        self.assertEqual(tuple(child),('complete','synced'))

    def test_shared_completion_preserves_a_followups_captured_baseline(self):
        s,parent=self.busy();e=self.followup(s);worker,calls,_=self.worker(s);worker.scan()
        baseline=json.dumps(dict(head='a'*40,dirty={},staged=[]))
        self.state.ledger.update(e,code_start='a'*40,baseline=baseline)
        self.state.transition(parent['seq'],'sync_pending',result='Saved answer',receipt='om_final')
        self.state.ledger.update(parent,'sync_pending',activity='completed',sync='pending',code_start='b'*40)
        self.state.db.execute("UPDATE codex_outcomes SET status='completed' WHERE session=?",(s['id'],))
        self.state.db.execute("UPDATE recovery_notices SET receipt='om_ack',state='delivered' WHERE id=?",('steer-'+str(e['seq'])+':accepted',))
        worker.scan();child=self.state.db.execute('SELECT code_start,baseline FROM turn_records WHERE seq=?',(e['seq'],)).fetchone()
        self.assertEqual(tuple(child),('a'*40,baseline))
        self.assertEqual(sum(m=='turn/steer' for m,_ in calls),1)

    def test_legacy_cleared_followup_head_recovers_only_from_its_own_saved_baseline(self):
        s,parent=self.busy();e=self.followup(s);worker,calls,_=self.worker(s);worker.scan()
        baseline=json.dumps(dict(head='a'*40,dirty={},staged=[]))
        self.state.ledger.update(e,baseline=baseline)
        self.state.transition(parent['seq'],'sync_pending',result='Saved answer',receipt='om_final')
        self.state.ledger.update(parent,'sync_pending',activity='completed',sync='pending')
        self.state.db.execute("UPDATE codex_outcomes SET status='completed' WHERE session=?",(s['id'],))
        self.state.db.execute("UPDATE recovery_notices SET receipt='om_ack',state='delivered' WHERE id=?",('steer-'+str(e['seq'])+':accepted',))
        self.state.transition(e['seq'],'reply_ready',result='Shared answer',receipt='om_ack')
        self.state.ledger.update(e,'reply_ready',activity='completed',sync='pending')
        self.state.db.execute("UPDATE steered_inputs SET state='completed' WHERE seq=?",(e['seq'],))
        worker.scan();child=self.state.db.execute('SELECT code_start,baseline FROM turn_records WHERE seq=?',(e['seq'],)).fetchone()
        self.assertEqual(tuple(child),('a'*40,baseline))
        worker.scan();self.assertEqual(sum(m=='turn/steer' for m,_ in calls),1)

    def test_named_task_can_accept_the_latest_shared_followup_delivery(self):
        from intern_personal.task_branches import command
        s=self.bot();self.state.ledger.task(s,'start','Fixture shared delivery')
        s,parent=self.busy(s);e=self.followup(s);worker,_,_=self.worker(s);worker.scan()
        self.state.ledger.evidence(parent['seq'],'Codex observed turn/completed status completed')
        self.state.transition(parent['seq'],'complete',result='Shared answer',receipt='om_final')
        self.state.ledger.update(parent,'complete',activity='completed',sync='synced',completed_at=time.time(),code_end='1'*40)
        self.state.db.execute("UPDATE codex_outcomes SET status='completed' WHERE session=?",(s['id'],))
        self.state.db.execute("UPDATE recovery_notices SET receipt='om_ack',state='delivered' WHERE id=?",('steer-'+str(e['seq'])+':accepted',))
        worker.scan();self.state.db.execute("UPDATE console_views SET snapshot='{}' WHERE session=?",(s['id'],))
        tracker=SimpleNamespace(state=self.state,code_directory=lambda session:'fixture',dirty=lambda directory:{},
            intern=lambda session:dict(metadata_mode='bot_branch',code_branch='fixture'),clean=lambda value:value)
        tracker.git=lambda directory,*args,**kwargs: '1'*40 if args[0]=='rev-parse' else '1'*40+' refs/heads/fixture' if args[0]=='ls-remote' else ''
        with patch('intern_personal.task_branches.switch'):
            self.assertIn('Task:',command(tracker,s,'finish','Owner accepts shared delivery'))
        self.assertIsNone(self.state.ledger.selection(s['id']))

    def test_one_bot_cannot_steer_another_bots_turn(self):
        a,_=self.busy();b,_=self.busy();e=self.followup(a);worker,calls,_=self.worker(a);worker.scan()
        self.assertEqual(next(p for m,p in calls if m=='turn/steer')['threadId'],a['thread_id'])
        self.assertNotEqual(self.row(e['seq'])['thread_id'],b['thread_id'])

    def test_native_enter_requires_transcript_acceptance(self):
        s,parent=self.busy();worker,calls,path=self.worker(s)
        config=worker.terminal.config(s)|{'pane':'fixture-pane'};worker.terminal.config=lambda session:config
        self.state.db.execute('INSERT INTO terminal_attachments(session,config_path,cursor,snapshot) VALUES(?,?,0,?)',
            (s['id'],'fixture-config',json.dumps(dict(active='turn-'+s['id'],event_seq=parent['seq']))))
        e=self.followup(s);keys=[]
        def tmux(config,*args,**kwargs):
            keys.append(args)
            if args[0]=='send-keys':
                path.write_text(json.dumps(dict(type='response_item',payload=dict(type='message',role='user',content=[dict(type='input_text',text=e['text'])])))+'\n')
        with patch('intern_personal.terminal_bridge.verify_pane'),patch('intern_personal.terminal_bridge.empty_prompt'),patch('intern_personal.terminal_bridge.tmux',side_effect=tmux):
            worker.scan();self.assertEqual(self.row(e['seq'])['state'],'sending');worker.scan()
        self.assertEqual(self.row(e['seq'])['state'],'accepted');self.assertEqual(calls,[])
        self.assertIn(('send-keys','-t','fixture-pane','Enter'),keys)
        self.assertFalse(any('Escape' in k or 'C-c' in k for k in keys))

    def test_native_draft_is_preserved(self):
        s,parent=self.busy();worker,calls,path=self.worker(s)
        config=worker.terminal.config(s)|{'pane':'fixture-pane'};worker.terminal.config=lambda session:config
        self.state.db.execute('INSERT INTO terminal_attachments(session,config_path,cursor,snapshot) VALUES(?,?,0,?)',
            (s['id'],'fixture-config',json.dumps(dict(active='turn-'+s['id'],event_seq=parent['seq']))))
        e=self.followup(s)
        with patch('intern_personal.terminal_bridge.verify_pane'),patch('intern_personal.terminal_bridge.empty_prompt',side_effect=Invalid('User draft')),patch('intern_personal.terminal_bridge.tmux') as tmux:
            worker.scan();tmux.assert_not_called()
        self.assertEqual(self.row(e['seq'])['state'],'fallback')

    def test_older_completed_turn_does_not_complete_a_newer_followup(self):
        s,parent=self.busy();e=self.followup(s);worker,_,_=self.worker(s);worker.scan()
        self.state.transition(parent['seq'],'complete',result='An older response',receipt='om_old')
        self.state.db.execute("UPDATE recovery_notices SET receipt='om_ack',state='delivered' WHERE id=?",('steer-'+str(e['seq'])+':accepted',))
        worker.scan();self.assertEqual(self.row(e['seq'])['state'],'accepted')

    def test_completion_shares_parent_result_without_another_model_or_final_reply(self):
        s,parent=self.busy();e=self.followup(s);worker,calls,_=self.worker(s);worker.scan()
        self.state.transition(parent['seq'],'complete',result='Shared final answer',receipt='om_final')
        self.state.ledger.update(parent,'complete',activity='completed',sync='synced',completed_at=time.time())
        self.state.db.execute("UPDATE codex_outcomes SET status='completed' WHERE session=?",(s['id'],))
        self.state.db.execute("UPDATE recovery_notices SET receipt='om_ack',state='delivered' WHERE id=?",('steer-'+str(e['seq'])+':accepted',))
        worker.scan();worker.scan()
        self.assertEqual(self.row(e['seq'])['state'],'completed')
        child=self.state.db.execute('SELECT * FROM events WHERE seq=?',(e['seq'],)).fetchone()
        self.assertEqual(child['state'],'complete');self.assertEqual(child['receipt'],'om_ack')
        self.assertEqual(sum(m=='turn/steer' for m,_ in calls),1)
        self.assertIn('turn '+str(parent['seq']),child['result'])
        self.assertNotEqual(child['result'],'Shared final answer')


if __name__=='__main__':unittest.main()
