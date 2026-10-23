import json
import os
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import test_live as fixtures
from intern_personal import peer_mail as peer
from intern_personal.auto_recovery import observe
from intern_personal.auto_recovery import Recovery
from intern_personal.codex_bridge import CodexHost
from intern_personal.core import Invalid
from intern_personal.live_state import LiveState,write_private
from intern_personal.prompt_context import publish
from intern_personal.workspace_tracking import WorkspaceTracking


class PeerTests(unittest.TestCase):
    setUp=fixtures.LiveTests.setUp
    event=fixtures.LiveTests.event

    def bots(self,same=False):
        a=self.relay.open_session('alpha',new=True);b=self.relay.open_session('alpha' if same else 'beta',new=True)
        for s in [a,b]:
            path=self.state.root/'code'/s['id'];path.mkdir(parents=True)
            self.state.db.execute("INSERT INTO tracked_interns(session,intern_name,code_path,code_branch,home_branch,published,metadata_mode) VALUES(?,?,?,?,?,1,'bot_branch')",
                (s['id'],'fixture_'+s['id'],str(path),'intern-agent/'+s['id'],'intern-agent/'+s['id']))
        self.tracker.clean=lambda body:WorkspaceTracking.clean(self.tracker,body)
        self.tracker.code_directory=lambda s:self.state.db.execute('SELECT code_path FROM tracked_interns WHERE session=?',(s['id'],)).fetchone()[0]
        self.state.db.execute('UPDATE peer_policy SET enabled=1')
        self.api.messages.clear()
        return a,b

    def send(self,a,b,**kwargs):return peer.send(self.state,self.tracker,a,b,'What is the saved result?',**kwargs)

    def execute(self,answer='Saved result is ready'):
        def run(s,text,**kwargs):
            e=self.state.db.execute("SELECT * FROM events WHERE session=? AND state='running' ORDER BY seq DESC LIMIT 1",(s['id'],)).fetchone()
            turn='turn_'+str(e['seq']);observe(self.state,s,turn,'inProgress',seq=e['seq'])
            self.host.calls.append((s,text));self.state.ledger.evidence(e['seq'],'Codex observed turn/completed status completed')
            observe(self.state,s,turn,'completed',seq=e['seq'])
            return answer
        with patch.object(self.host,'run',side_effect=run):return self.relay.process_once('agent')

    def research_alarm(self,session):
        from intern_personal.research import Research,start
        watch=start(self.state,session,'1h','Inspect existing experiment results',key='experiment')
        self.state.db.execute('UPDATE research_watches SET due_at=? WHERE id=?',(time.time()-1,watch['id']))
        Research(self.relay).scan()
        return dict(self.state.db.execute('SELECT * FROM research_runs WHERE watch=?',(watch['id'],)).fetchone())

    def test_peer_question_bypasses_waiting_alarm_and_later_delivered_git_review(self):
        a,b=self.bots();alarm=self.research_alarm(b)
        self.relay.receive(self.event('Owner request',b['chat_id'],'om_owner'))
        self.assertEqual(self.execute('Saved owner answer'),'complete')
        owner=dict(self.state.db.execute("SELECT * FROM events WHERE message_id='om_owner'").fetchone())
        self.state.transition(owner['seq'],'record_blocked')
        before=dict(self.state.db.execute('SELECT * FROM research_watches').fetchone())
        message=self.send(a,b,key='question');peer.poll(self.relay)
        request=peer.owned(self.state,a,message['id'])
        self.assertEqual(request['state'],'queued')
        self.assertIsNotNone(request['event_seq'])
        for _ in range(3):peer.poll(self.relay)
        self.assertEqual(peer.owned(self.state,a,message['id'])['event_seq'],request['event_seq'])
        self.assertEqual(self.execute('Information answer'),'complete');peer.poll(self.relay)
        saved=peer.owned(self.state,a,message['id'])
        self.assertEqual(saved['reply'],'Information answer')
        self.assertEqual(saved['state'],'replied')
        self.assertEqual(self.state.db.execute('SELECT state FROM events WHERE seq=?',(alarm['seq'],)).fetchone()[0],'queued')
        self.assertEqual(dict(self.state.db.execute('SELECT * FROM research_watches').fetchone()),before)
        self.assertEqual(dict(self.state.db.execute('SELECT * FROM events WHERE seq=?',(owner['seq'],)).fetchone()),dict(owner,state='record_blocked'))
        # The older alarm must also become dispatchable after the information
        # answer; the newer delivered review is not unfinished execution.
        claimed=self.state.claim('agent',b['id'])
        self.assertIsNotNone(claimed);self.assertEqual(claimed['seq'],alarm['seq'])
        self.assertEqual(len(self.host.calls),2)

    def test_peer_question_never_interrupts_an_active_research_turn(self):
        a,b=self.bots();alarm=self.research_alarm(b)
        item=self.state.claim('agent',b['id']);self.assertEqual(item['seq'],alarm['seq'])
        self.host.active[b['id']]=(b['thread_id'],'research-turn')
        message=self.send(a,b);peer.poll(self.relay)
        self.assertEqual(peer.owned(self.state,a,message['id'])['state'],'stored')
        self.assertIsNone(peer.owned(self.state,a,message['id'])['event_seq'])
        self.assertEqual(self.host.active[b['id']][1],'research-turn')
        self.assertEqual(self.host.calls,[])

    def control_review(self,session,workspace=None):
        message='om_control_review_'+session['id']
        self.relay.receive(self.event('/fast status',session['chat_id'],message))
        row=self.state.db.execute('SELECT * FROM events WHERE message_id=?',(message,)).fetchone()
        self.state.db.execute("UPDATE events SET state='uncertain_execution',workspace=? WHERE seq=?",
                              (workspace or session['workspace'],row['seq']))
        return dict(self.state.db.execute('SELECT * FROM events WHERE seq=?',(row['seq'],)).fetchone())

    def test_historical_control_review_does_not_hold_an_idle_peer_question(self):
        a,b=self.bots();old=self.control_review(b,workspace=a['workspace'])
        message=self.send(a,b);peer.poll(self.relay)
        self.assertEqual(peer.owned(self.state,a,message['id'])['state'],'queued')
        self.assertEqual(dict(self.state.db.execute('SELECT * FROM events WHERE seq=?',(old['seq'],)).fetchone()),old)
        self.assertEqual(self.execute('Confirmed saved information'),'complete');peer.poll(self.relay)
        self.assertEqual(peer.owned(self.state,a,message['id'])['reply'],'Confirmed saved information')

    def test_shared_claim_ignores_control_uncertainty_without_erasing_the_review(self):
        a,b=self.bots();old=self.control_review(b)
        self.relay.receive(self.event('New owner question',b['chat_id'],'om_new_owner_question'))
        item=self.state.claim('agent',b['id'])
        self.assertIsNotNone(item);self.assertEqual(item['text'],'New owner question')
        self.assertEqual(dict(self.state.db.execute('SELECT * FROM events WHERE seq=?',(old['seq'],)).fetchone()),old)

    def test_genuine_uncertain_agent_blocks_even_with_legacy_workspace_mismatch(self):
        a,b=self.bots()
        self.relay.receive(self.event('Original uncertain request',b['chat_id'],'om_uncertain_agent'))
        old=self.state.claim('agent',b['id'])
        self.state.db.execute("UPDATE events SET state='uncertain_execution',workspace=? WHERE seq=?",(a['workspace'],old['seq']))
        self.relay.receive(self.event('New owner request',b['chat_id'],'om_new_owner_question'))
        message=self.send(a,b);peer.poll(self.relay)
        self.assertEqual(peer.owned(self.state,a,message['id'])['state'],'stored')
        self.assertIsNone(self.state.claim('agent',b['id']))
        self.assertEqual(self.host.calls,[])
        self.assertEqual(peer.owned(self.state,a,message['id'])['state'],'stored')
        self.assertEqual(peer.public(self.state,peer.owned(self.state,a,message['id']))['waiting_reason'],'execution_review')

    def test_control_review_does_not_block_requester_followup_or_research_alarm(self):
        a,b=self.bots();self.control_review(a);self.control_review(b)
        alarm=self.research_alarm(b)
        message=self.send(a,b);peer.poll(self.relay)
        self.assertEqual(self.execute('Saved answer'),'complete');peer.poll(self.relay)
        reply=peer.owned(self.state,a,message['id'])
        self.assertIsNotNone(reply['reply_seq'])
        claimed=self.state.claim('agent',a['id']);self.assertEqual(claimed['seq'],reply['reply_seq'])
        research=self.state.claim('agent',b['id']);self.assertEqual(research['seq'],alarm['seq'])
        self.assertEqual(len(self.host.calls),1)

    def test_control_review_does_not_block_owner_requested_note_processing(self):
        a,b=self.bots();self.control_review(b)
        message=self.send(a,b,kind='note')
        self.assertEqual(peer.public(self.state,peer.owned(self.state,a,message['id']))['waiting_reason'],'passive_note')
        self.relay.receive(self.event('/mail process '+message['id'],b['chat_id'],'om_process_note'))
        self.assertEqual(self.relay.process_once('control'),'complete')
        peer.poll(self.relay)
        self.assertIsNotNone(peer.owned(self.state,b,message['id'])['event_seq'])
        self.assertEqual(self.execute('Note inspected; no experiment started'),'complete');peer.poll(self.relay)
        self.assertEqual(peer.owned(self.state,b,message['id'])['state'],'delivered')

    def test_active_control_and_actual_codex_work_still_defer_peer_input(self):
        a,b=self.bots();old=self.control_review(b)
        self.state.transition(old['seq'],'running')
        message=self.send(a,b);peer.poll(self.relay)
        self.assertIsNone(peer.owned(self.state,a,message['id'])['event_seq'])
        self.state.transition(old['seq'],'uncertain_execution')
        self.host.active[b['id']]=(b['thread_id'],'still-active')
        peer.poll(self.relay)
        self.assertIsNone(peer.owned(self.state,a,message['id'])['event_seq'])
        self.assertEqual(self.host.active[b['id']][1],'still-active')
        self.assertEqual(self.host.calls,[])

    def test_one_observation_failure_cannot_starve_unrelated_mail(self):
        a,b=self.bots();bad=self.send(a,b);peer.poll(self.relay)
        c,d=self.bots();good=self.send(c,d)
        original=peer.observe_one
        def fail(relay,row):
            if row['id']==bad['id']:raise ValueError('Private diagnostic fixture must not leak')
            return original(relay,row)
        with patch.object(peer,'observe_one',side_effect=fail):
            peer.poll(self.relay,isolate=True)
            first=peer.owned(self.state,c,good['id'])['event_seq']
            self.assertIsNotNone(first)
            peer.poll(self.relay,isolate=True)
            self.assertEqual(peer.owned(self.state,c,good['id'])['event_seq'],first)
        self.assertEqual(self.relay.peer_health['errors'],1)
        self.assertEqual(self.relay.peer_health['error_types'],['ValueError'])
        self.assertNotIn('Private diagnostic',json.dumps(self.relay.peer_health))
        self.assertEqual(peer.owned(self.state,a,bad['id'])['state'],'queued')
        self.assertIsNone(peer.owned(self.state,a,bad['id'])['reply'])
        peer.poll(self.relay,isolate=True)
        self.assertEqual(self.relay.peer_health['errors'],0)

    def test_peer_worker_health_is_reported_without_message_contents(self):
        a,b=self.bots();self.send(a,b);peer.poll(self.relay,isolate=True)
        self.relay.receiver=SimpleNamespace(_conn=SimpleNamespace(open=True))
        self.relay.peer_thread=SimpleNamespace(is_alive=lambda:True)
        written=[]
        def capture(path,value):
            written.append(value);self.relay.stop.set()
        with patch('intern_personal.feishu_live.write_private',side_effect=capture):self.relay.heartbeat()
        self.assertTrue(written[0]['peer_worker_alive'])
        self.assertEqual(written[0]['peer_health']['errors'],0)
        self.assertNotIn('What is the saved result?',json.dumps(written))

    def test_doctor_exposes_peer_worker_errors_as_an_informational_check(self):
        from intern_personal.operations import diagnose
        self.bots()
        for errors in (0,1):
            write_private(self.state.root/'health.json',dict(updated_at=time.time(),receiver_connected=True,
                peer_worker_alive=True,peer_health=dict(updated_at=time.time(),errors=errors,
                    error_types=['ValueError'] if errors else [],phases=['observe'] if errors else [])))
            check=next(c for c in diagnose(self.state)['checks'] if c['id']=='peer_delivery')
            self.assertEqual(check['passed'],not errors)
            self.assertFalse(check['blocking'])

    def test_stored_question_wins_startup_race_before_alarm_dispatch(self):
        a,b=self.bots();alarm=self.research_alarm(b)
        message=self.send(a,b)
        item=self.state.claim('agent',b['id']);self.assertEqual(item['seq'],alarm['seq'])
        # The input worker won the claim race before the mailbox created its
        # event. The final preflight guard must still yield without dispatch.
        self.assertEqual(self.relay.process_item(item,'agent'),'deferred')
        self.assertEqual(self.host.calls,[])
        self.assertEqual(self.state.db.execute('SELECT state FROM events WHERE seq=?',(alarm['seq'],)).fetchone()[0],'queued')
        peer.poll(self.relay)
        self.assertEqual(self.execute('Saved information'),'complete');peer.poll(self.relay)
        self.assertEqual(peer.owned(self.state,a,message['id'])['reply'],'Saved information')

    def test_manual_inbox_or_changed_thread_does_not_starve_research(self):
        a,b=self.bots();self.send(a,b)
        self.assertTrue(peer.pending_information(self.state,b))
        self.state.db.execute("INSERT INTO terminal_attachments VALUES(?, '/fixture/manual.json',0,'{}')",(b['id'],))
        self.assertFalse(peer.pending_information(self.state,b))
        self.state.db.execute('DELETE FROM terminal_attachments WHERE session=?',(b['id'],))
        self.state.set_session(b['id'],thread_id='replacement-thread')
        self.assertFalse(peer.pending_information(self.state,self.state.session(b['id'])))

    def test_busy_recipient_keeps_owner_text_and_no_receipt_is_invented(self):
        a,b=self.bots();self.relay.receive(self.event('Original owner request',b['chat_id'],'om_original'))
        original=self.state.claim('agent');self.host.active[b['id']]=(b['thread_id'],'owner_turn')
        message=self.send(a,b);peer.poll(self.relay);peer.poll(self.relay)
        row=peer.owned(self.state,a,message['id'])
        self.assertEqual(row['state'],'stored');self.assertIsNone(row['event_seq'])
        self.assertEqual([r['stage'] for r in peer.listing(self.state,a)[0]['receipts']],['stored'])
        self.assertEqual(self.state.db.execute('SELECT text FROM events WHERE seq=?',(original['seq'],)).fetchone()[0],'Original owner request')
        self.assertEqual(self.host.calls,[]);self.assertEqual(self.host.active[b['id']][1],'owner_turn')

    def test_saved_reply_is_available_while_requester_busy_without_injection(self):
        a,b=self.bots();self.relay.receive(self.event('Continue original work',a['chat_id'],'om_work'));self.state.claim('agent')
        self.host.active[a['id']]=(a['thread_id'],'original');message=self.send(a,b)
        peer.poll(self.relay);self.assertEqual(self.execute(),'complete');peer.poll(self.relay)
        row=peer.owned(self.state,a,message['id']);self.assertEqual(row['state'],'replied');self.assertEqual(row['reply_state'],'available');self.assertIsNone(row['reply_seq'])
        data=peer.read(self.state,a,message['id']);self.assertEqual(data['reply'],'Saved result is ready')
        self.assertNotIn('reply_handled',[r['stage'] for r in data['receipts']])
        peer.read(self.state,a,message['id'],ack=True);peer.poll(self.relay)
        self.assertEqual(peer.owned(self.state,a,message['id'])['reply_state'],'handled');self.assertEqual(len(self.host.calls),1)

    def test_one_idle_followup_and_no_recursive_reply_loop(self):
        a,b=self.bots();message=self.send(a,b);peer.poll(self.relay)
        self.assertEqual(self.execute(),'complete');peer.poll(self.relay)
        row=peer.owned(self.state,a,message['id']);seq=row['reply_seq'];self.assertIsNotNone(seq)
        for _ in range(4):peer.poll(self.relay)
        self.assertEqual(peer.owned(self.state,a,message['id'])['reply_seq'],seq)
        self.assertEqual(self.execute('Reply processed'),'complete');peer.poll(self.relay);peer.poll(self.relay)
        self.assertEqual(peer.owned(self.state,a,message['id'])['reply_state'],'handled')
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM peer_mail').fetchone()[0],1);self.assertEqual(len(self.host.calls),2)

    def test_same_workspace_information_request_can_run_while_other_checkout_busy(self):
        a,b=self.bots(same=True);self.relay.receive(self.event('Owner work',a['chat_id'],'om_owner'));self.state.claim('agent')
        self.send(a,b);peer.poll(self.relay);queued=self.state.claim('agent')
        self.assertIsNotNone(queued);self.assertEqual(queued['session'],b['id'])
        with self.assertRaises(Invalid):self.send(b,a)

    def test_shared_checkout_does_not_bypass_workspace_serialization(self):
        a,b=self.bots(same=True);self.relay.receive(self.event('Owner work',a['chat_id'],'om_owner'));self.state.claim('agent')
        self.state.db.execute('UPDATE tracked_interns SET code_path=? WHERE session=?',(self.tracker.code_directory(a),b['id']))
        self.send(a,b);peer.poll(self.relay);self.assertIsNone(self.state.claim('agent'))

    def test_duplicate_key_and_restart_do_not_create_another_turn(self):
        a,b=self.bots();one=self.send(a,b,key='stable');two=self.send(a,b,key='stable');self.assertEqual(one['id'],two['id'])
        with self.assertRaises(Invalid):peer.send(self.state,self.tracker,a,b,'Changed question',key='stable')
        peer.poll(self.relay);seq=peer.owned(self.state,a,one['id'])['event_seq']
        other=LiveState(self.state.root);other.close();peer.poll(self.relay)
        self.assertEqual(peer.owned(self.state,a,one['id'])['event_seq'],seq);self.assertEqual(self.state.db.execute('SELECT count(*) FROM events').fetchone()[0],1)

    def test_ambiguous_execution_is_reviewed_without_replay(self):
        a,b=self.bots();m=self.send(a,b);peer.poll(self.relay);e=self.state.claim('agent')
        self.state.transition(e['seq'],'uncertain_execution');peer.poll(self.relay);peer.poll(self.relay)
        row=peer.owned(self.state,a,m['id']);self.assertEqual(row['state'],'review_needed')
        self.assertEqual(row['event_seq'],e['seq']);self.assertEqual(self.host.calls,[])

    def test_confirmed_failed_recipient_is_not_left_looking_unfinished(self):
        a,b=self.bots();m=self.send(a,b);peer.poll(self.relay);e=self.state.claim('agent')
        observe(self.state,b,'failed','failed',{'codexErrorInfo':'serverOverloaded'},e['seq'])
        self.state.transition(e['seq'],'complete',result='Failure',receipt='om_notice')
        self.state.ledger.update(e,activity='failed');peer.poll(self.relay)
        row=peer.owned(self.state,a,m['id']);self.assertEqual(row['state'],'review_needed');self.assertEqual(row['error'],'codex_failed');self.assertIsNone(row['reply'])

    def test_newer_outcome_cannot_hide_the_exact_completed_answer(self):
        a,b=self.bots();m=self.send(a,b);peer.poll(self.relay);self.execute()
        observe(self.state,b,'later-turn','inProgress')
        peer.poll(self.relay);self.assertEqual(peer.owned(self.state,a,m['id'])['state'],'replied')

    def test_old_reply_does_not_run_in_a_new_thread_or_task(self):
        for change in ['thread','task']:
            with self.subTest(change=change):
                a,b=self.bots();m=self.send(a,b);peer.poll(self.relay);self.execute()
                if change=='thread':self.state.set_session(a['id'],thread_id='new-thread')
                else:self.state.ledger.task(a,'start','Different work')
                peer.poll(self.relay);row=peer.owned(self.state,a,m['id']);self.assertIsNone(row['reply_seq'])
                self.assertEqual(row['reply_state'],'available')
                self.state.db.execute('DELETE FROM events');self.state.db.execute('DELETE FROM peer_mail')

    def test_manual_debug_is_inbox_only_and_can_reply_explicitly(self):
        a,b=self.bots();self.state.db.execute("INSERT INTO terminal_attachments VALUES(?, '/fixture/manual.json',0,'{}')",(b['id'],))
        m=self.send(a,b);peer.poll(self.relay);self.assertIsNone(peer.owned(self.state,b,m['id'])['event_seq'])
        peer.reply(self.state,self.tracker,b,m['id'],'Manual answer');peer.reply(self.state,self.tracker,b,m['id'],'Manual answer')
        with self.assertRaises(Invalid):peer.reply(self.state,self.tracker,a,m['id'],'Forged answer')
        with self.assertRaises(Invalid):peer.reply(self.state,self.tracker,b,m['id'],'Changed answer')
        self.assertEqual(peer.read(self.state,a,m['id'])['reply'],'Manual answer')

    def test_owner_can_process_a_stored_note_once_without_waking_the_sender(self):
        a,b=self.bots();m=self.send(a,b,kind='note',key='status-note')
        peer.poll(self.relay);self.assertIsNone(peer.owned(self.state,b,m['id'])['event_seq'])
        self.relay.receive(self.event('/mail process '+m['id'],b['chat_id'],'om_owner_note_review'))
        self.assertEqual(self.relay.process_once('control'),'complete')
        self.api.messages.clear();peer.poll(self.relay)
        first=peer.owned(self.state,b,m['id'])['event_seq'];self.assertIsNotNone(first)
        self.assertEqual(self.execute('Read the status note; the prior survey and jobs are preserved.'),'complete')
        peer.poll(self.relay);peer.poll(self.relay)
        row=peer.owned(self.state,b,m['id'])
        self.assertEqual(row['state'],'delivered');self.assertEqual(row['reply_state'],'none');self.assertIsNone(row['reply_seq'])
        self.assertIsNotNone(row['received_at']);self.assertIsNotNone(row['read_at'])
        self.assertEqual(sum(r['stage']=='note_handled' for r in peer.public(self.state,row)['receipts']),1)
        peer.request_note(self.state,dict(self.state.db.execute("SELECT * FROM events WHERE message_id='om_owner_note_review'").fetchone()),m['id'])
        peer.poll(self.relay);self.assertEqual(peer.owned(self.state,b,m['id'])['event_seq'],first)
        self.assertEqual(len(self.host.calls),1)

    def test_requested_note_waits_for_busy_recipient_and_preserves_manual_debug(self):
        a,b=self.bots();m=self.send(a,b,kind='note')
        self.relay.receive(self.event('/mail process '+m['id'],b['chat_id'],'om_request_note'))
        self.assertEqual(self.relay.process_once('control'),'complete')
        self.relay.receive(self.event('Owner is working',b['chat_id'],'om_busy_owner'));active=self.state.claim('agent')
        peer.poll(self.relay);self.assertIsNone(peer.owned(self.state,b,m['id'])['event_seq'])
        self.assertEqual(self.host.calls,[])
        self.state.transition(active['seq'],'complete',result='Done',receipt='om_done')
        self.state.ledger.update(active,'complete',activity='completed')
        peer.poll(self.relay);self.assertIsNotNone(peer.owned(self.state,b,m['id'])['event_seq'])
        self.state.db.execute("INSERT INTO terminal_attachments VALUES(?, '/fixture/manual.json',0,'{}')",(b['id'],))
        other=self.send(a,b,kind='note',key='another-note')
        owner=dict(self.state.db.execute("SELECT * FROM events WHERE message_id='om_request_note'").fetchone())
        with self.assertRaisesRegex(Invalid,'manual/native'):peer.request_note(self.state,owner,other['id'])

    def test_peer_generated_event_cannot_authorize_processing_another_note(self):
        a,b=self.bots();m=self.send(a,b);peer.poll(self.relay);peer_item=self.state.claim('agent')
        note=self.send(a,b,kind='note',key='untrusted-note')
        with self.assertRaisesRegex(Invalid,'owner request'):peer.request_note(self.state,peer_item,note['id'])

    def test_new_owner_message_in_same_task_suppresses_old_reply_followup(self):
        a,b=self.bots();m=self.send(a,b);peer.poll(self.relay);self.execute()
        self.relay.receive(self.event('New owner objective',a['chat_id'],'om_new_objective'))
        self.assertEqual(self.execute('New objective done'),'complete')
        peer.poll(self.relay);row=peer.owned(self.state,a,m['id'])
        self.assertIsNone(row['reply_seq']);self.assertEqual(row['reply_state'],'available')

    def test_owner_message_takes_priority_over_an_undispatched_peer_turn(self):
        a,b=self.bots();m=self.send(a,b);peer.poll(self.relay)
        self.relay.receive(self.event('Owner priority',b['chat_id'],'om_priority'))
        item=self.state.claim('agent');self.assertEqual(item['message_id'],'om_priority')

    def test_stopped_requester_gets_inbox_reply_without_automatic_continuation(self):
        a,b=self.bots();self.relay.receive(self.event('Original request',a['chat_id'],'om_original'))
        original=self.state.claim('agent');m=self.send(a,b);self.state.ledger.stop(a)
        self.state.transition(original['seq'],'cancelled');peer.poll(self.relay);self.execute();peer.poll(self.relay)
        self.assertIsNone(peer.owned(self.state,a,m['id'])['reply_seq'])

    def test_waiting_cycle_is_rejected_without_stopping_either_bot(self):
        a,b=self.bots();self.send(a,b)
        with self.assertRaises(Invalid):self.send(b,a)
        self.assertEqual(self.host.calls,[])

    def test_policy_off_capacity_and_pre_dispatch_cancel(self):
        a,b=self.bots();self.state.db.execute('UPDATE peer_policy SET enabled=0')
        with self.assertRaises(Invalid):self.send(a,b)
        self.state.db.execute('UPDATE peer_policy SET enabled=1')
        for n in range(peer.MAX_INBOX):self.send(a,b,key='request-'+str(n))
        with self.assertRaises(Invalid):self.send(a,b,key='over-limit')
        one=peer.listing(self.state,a)[0];peer.cancel(self.state,a,one['id']);self.send(a,b,key='new-slot')

    def test_context_is_actor_bound_and_original_input_is_unchanged(self):
        a,b=self.bots();self.send(a,b);peer.poll(self.relay);item=self.state.claim('agent');refs=publish(self.tracker,b)
        self.assertIn('untrusted',refs['intern-agent.memory']['kind']);self.assertIn('What is the saved result?',refs['intern-agent.memory']['value'])
        self.assertEqual(self.state.db.execute('SELECT text FROM events WHERE seq=?',(item['seq'],)).fetchone()[0],item['text'])
        path=self.state.runtime/'prompt-context'/(b['id']+'.json')
        with patch.dict(os.environ,{'CODEX_THREAD_ID':b['thread_id']}):self.assertEqual(peer.actor(self.state,path)['id'],b['id'])
        with patch.dict(os.environ,{'CODEX_THREAD_ID':a['thread_id']}):
            with self.assertRaises(Invalid):peer.actor(self.state,path)
        host=CodexHost(self.state,tracker=self.tracker);self.assertEqual(host.thread_params(b)['sandbox'],'read-only')
        self.assertEqual(host.thread_params(b,peer_scope=False)['sandbox'],'workspace-write')

    def test_query_does_not_count_as_selected_task_delivery(self):
        a,b=self.bots();self.state.ledger.task(b,'start','Real implementation');m=self.send(a,b);peer.poll(self.relay);self.execute()
        seq=peer.owned(self.state,b,m['id'])['event_seq'];self.assertIsNone(self.state.db.execute('SELECT task_id FROM turn_records WHERE seq=?',(seq,)).fetchone()[0])
        with self.assertRaises(Invalid):self.state.ledger.task(b,'finish')

    def test_notes_do_not_launch_codex_and_require_recipient_ack(self):
        a,b=self.bots();m=self.send(a,b,kind='note');peer.poll(self.relay)
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM events').fetchone()[0],0)
        with self.assertRaises(Invalid):peer.read(self.state,a,m['id'],ack=True)
        peer.read(self.state,b,m['id'],ack=True);self.assertEqual(peer.owned(self.state,a,m['id'])['state'],'delivered')

    def test_correlated_notices_are_once_only_and_do_not_start_another_turn(self):
        a,b=self.bots();m=self.send(a,b,key='notice');self.send(a,b,key='notice')
        recovery=Recovery(self.relay);recovery.notifications();recovery.notifications()
        self.assertEqual(len(self.api.messages),1);self.assertIn(m['id'],self.api.messages[0][1]);self.assertEqual(self.host.calls,[])
        peer.poll(self.relay);self.execute();peer.poll(self.relay);peer.poll(self.relay)
        recovery.notifications();recovery.notifications()
        self.assertEqual(sum(m['id'] in text and 'Peer reply' in text for chat,text,key in self.api.messages),1)

    def test_reply_receipt_and_notice_commit_atomically_after_sender_failure(self):
        a,b=self.bots();m=self.send(a,b);peer.poll(self.relay);self.execute()
        with patch.object(peer,'announce',side_effect=RuntimeError('Fixture notice persistence failure')):
            with self.assertRaises(RuntimeError):peer.poll(self.relay)
        row=peer.owned(self.state,a,m['id']);self.assertIsNone(row['reply'])
        self.assertNotIn('replied',[r['stage'] for r in peer.public(self.state,row)['receipts']])
        peer.poll(self.relay);self.assertEqual(peer.owned(self.state,a,m['id'])['reply'],'Saved result is ready')

    def test_handling_ack_rolls_back_with_its_receipt_on_failure(self):
        a,b=self.bots();m=self.send(a,b);peer.reply(self.state,self.tracker,b,m['id'],'Answer')
        with patch.object(peer,'receipt',side_effect=RuntimeError('Fixture receipt failure')):
            with self.assertRaises(RuntimeError):peer.read(self.state,a,m['id'],ack=True)
        self.assertEqual(peer.owned(self.state,a,m['id'])['reply_state'],'available')

    def test_team_handoff_mirrors_and_acknowledges_without_executing(self):
        a,b=self.bots();self.state.db.execute("INSERT INTO team_mail VALUES('mail_fixture','team_fixture',?,?,NULL,'Saved team result','unread',?,1)",(a['id'],b['id'],1.0))
        peer.poll(self.relay);peer.poll(self.relay)
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM peer_mail').fetchone()[0],1)
        m=peer.listing(self.state,b)[0];peer.read(self.state,b,m['id'],ack=True)
        self.assertEqual(self.state.db.execute("SELECT state FROM team_mail WHERE id='mail_fixture'").fetchone()[0],'read')
        self.assertEqual(self.host.calls,[])

    def test_markdown_correspondence_preserves_identity_reply_and_receipts(self):
        from intern_personal.records import render,validate_document,validate_evolution
        a,b=self.bots();m=self.send(a,b);before=peer.public(self.state,peer.owned(self.state,a,m['id']),True)
        peer.reply(self.state,self.tracker,b,m['id'],'Answer');after=peer.public(self.state,peer.owned(self.state,a,m['id']),True)
        def doc(v,n):return render('Correspondence',dict(kind='knowledge',workspace='alpha',revision=n,peer=v),[('Body','Saved')])
        validate_document('peer.md',doc(before,1));validate_evolution(doc(before,1),doc(after,2))
        changed=dict(after,body='Forged question')
        with self.assertRaises(Invalid):validate_evolution(doc(after,2),doc(changed,3))
        changed=dict(after,receipts=[])
        with self.assertRaises(Invalid):validate_evolution(doc(after,2),doc(changed,3))

    def test_concurrent_same_key_has_one_committed_message(self):
        a,b=self.bots();results=[];errors=[]
        def worker():
            try:results.append(self.send(a,b,key='concurrent')['id'])
            except Exception as error:errors.append(error)
            finally:self.state.close()
        ts=[threading.Thread(target=worker) for _ in range(2)]
        for t in ts:t.start()
        for t in ts:t.join(10)
        self.assertEqual(errors,[]);self.assertEqual(len(set(results)),1)
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM peer_mail').fetchone()[0],1)


if __name__=='__main__':unittest.main()
