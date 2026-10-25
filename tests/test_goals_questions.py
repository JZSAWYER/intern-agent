import copy
import json
from pathlib import Path
from types import SimpleNamespace
import time
import unittest
from unittest.mock import patch

from intern_personal import goals,user_questions as questions
from intern_personal.codex_bridge import CodexHost
from intern_personal.core import Invalid
from intern_personal.feishu_live import Relay
from intern_personal.live_state import LiveState
import test_tracking as tracking
import test_live as live


class GoalQuestionTests(unittest.TestCase):
    setUp=tracking.TrackingTests.setUp
    raw=tracking.TrackingTests.raw
    attach=tracking.TrackingTests.attach

    def conversation(self):
        if not self.state.workspace('project'):self.attach()
        session,_=self.state.reserve_session('project',new=True);self.tracker.prepare_session(session)
        self.state.set_session(session['id'],state='ready',chat_id='oc_'+session['id'],thread_id='fixture_'+session['id'])
        return self.state.session(session['id'])

    def relay(self):return Relay(self.state,live.FakeAPI(),live.FakeHost(self.state),self.tracker)

    def event(self,s,text,mid='one'):
        return dict(header=dict(app_id='cli_test',event_type='im.message.receive_v1'),event=dict(sender=dict(sender_type='user',sender_id=dict(open_id='ou_owner')),
            message=dict(chat_type='group',message_type='text',chat_id=s['chat_id'],message_id='om_'+mid,create_time=str(int(time.time()*1000)),content=json.dumps(dict(text=text)))))

    def question(self,s,*,multi=False,blocking=True,source='rpc',request=11):
        qs=[dict(id='dataset',header='Dataset',question='Which dataset should be used?',options=[dict(label='Small',description='Quick check'),dict(label='Full',description='Full evaluation')],multiSelect=multi),
            dict(id='split',header='Split',question='Which split?',options=[dict(label='Dev',description='Development'),dict(label='Test',description='Held-out')])]
        return questions.register(self.state,s,dict(threadId=s['thread_id'],turnId='fixture-turn',itemId='fixture-call',questions=qs,isBlocking=blocking),request,source=source)

    def test_goal_waits_for_confirmed_git_and_reply_and_deduplicates(self):
        s=self.conversation();relay=self.relay();goal=goals.start(self.state,s,'Reduce parser cost, verified by benchmark',tracker=self.tracker)
        goals.poll(relay);goals.poll(relay)
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM goal_runs').fetchone()[0],1)
        item=self.state.claim('agent');self.assertIsNotNone(item)
        goals.poll(relay);self.assertEqual(self.state.db.execute('SELECT count(*) FROM goal_runs').fetchone()[0],1)
        self.assertEqual(relay.process_item(item,'agent'),'complete')
        from intern_personal.turn_uploads import ready
        self.assertTrue(ready(self.state,item['seq']))
        self.assertTrue(self.state.db.execute('SELECT receipt FROM events WHERE seq=?',(item['seq'],)).fetchone()[0])
        goals.poll(relay);self.assertEqual(self.state.db.execute('SELECT count(*) FROM goal_runs').fetchone()[0],2)
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM events WHERE state=\'queued\'').fetchone()[0],1)
        self.assertIn('Reduce parser cost',self.raw(self.remote,'show','intern-management/test:.intern_workspace/features/goal_plan/'+goal['id']+'.md'))

    def test_managed_goal_counts_actual_tools_not_its_input_or_compaction(self):
        s=self.conversation();relay=self.relay();goals.start(self.state,s,'Verify the parser',tracker=self.tracker);goals.poll(relay);item=self.state.claim('agent')
        notices=iter([dict(method='item/started',params=dict(item=dict(type=t))) for t in ['userMessage','contextCompaction','agentMessage','commandExecution']]+
            [dict(method='item/completed',params=dict(item=dict(type='agentMessage',text='Verified result'))),dict(method='turn/completed',params=dict(turn=dict(id='goal-turn',status='completed'))) ])
        class Wire:
            def request(inner,method,params,timeout=60):return dict(turn=dict(id='goal-turn')) if method=='turn/start' else {}
            def notification(inner,timeout):return next(notices)
        host=CodexHost(self.state,tracker=self.tracker)
        self.assertEqual(host._turn(Wire(),s,s['thread_id'],item['text']),'Verified result')
        self.assertEqual(self.state.db.execute('SELECT tool_calls FROM goal_runs WHERE seq=?',(item['seq'],)).fetchone()[0],1)

    def test_new_owner_input_has_priority_over_a_queued_goal(self):
        s=self.conversation();relay=self.relay();goals.start(self.state,s,'Keep optimizing the benchmark',tracker=self.tracker);goals.poll(relay)
        self.assertEqual(self.state.ingest(self.event(s,'Use a different benchmark','owner')),'accepted')
        item=self.state.claim('agent');self.assertEqual(item['text'],'Use a different benchmark')

    def test_semantic_selection_retries_and_owner_pause_keep_the_original_owner_boundary(self):
        s=self.conversation();self.state.ingest(self.event(s,'Optimize until the benchmark target passes','owner_goal'));item=self.state.claim('agent')
        first=goals.start(self.state,s,'Verify the benchmark target',auto=True,tracker=self.tracker)
        repeated=goals.start(self.state,s,'Verify the benchmark target',auto=True,tracker=self.tracker)
        self.assertEqual(first['id'],repeated['id']);self.assertEqual(goals.owner_turn(self.state,s)['seq'],item['seq'])
        goals.control(self.state,s,'pause');self.assertEqual(goals.current(self.state,s)['status'],'paused')

    def test_goal_pause_stop_and_restart_preserve_history_without_replay(self):
        s=self.conversation();relay=self.relay();goals.start(self.state,s,'Keep checking tests',tracker=self.tracker);goals.poll(relay)
        goals.control(self.state,s,'pause');count=self.state.db.execute('SELECT count(*) FROM events').fetchone()[0]
        reopened=LiveState(self.state.root);self.addCleanup(reopened.close)
        self.assertEqual(goals.current(reopened,s)['status'],'paused')
        goals.poll(SimpleNamespace(state=reopened,tracker=self.tracker));self.assertEqual(reopened.db.execute('SELECT count(*) FROM events').fetchone()[0],count)
        self.assertIsNone(reopened.claim('agent'))

    def test_goal_resume_creates_a_new_continuation_after_cancelling_an_unstarted_one(self):
        s=self.conversation();relay=self.relay();goals.start(self.state,s,'Keep checking tests',tracker=self.tracker);goals.poll(relay)
        old=self.state.db.execute('SELECT seq FROM goal_runs').fetchone()[0]
        goals.control(self.state,s,'pause');goals.control(self.state,s,'resume');goals.poll(relay);goals.poll(relay)
        row=self.state.claim('agent');self.assertNotEqual(row['seq'],old)
        self.assertEqual(self.state.db.execute('SELECT state FROM events WHERE seq=?',(old,)).fetchone()[0],'cancelled')
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM goal_runs').fetchone()[0],2)

    def test_resumed_goal_passes_verified_fast_review_without_replaying_or_editing_it(self):
        s=self.conversation();relay=self.relay()
        self.state.ingest(self.event(s,'/fast ON','old_fast'));old=self.state.claim('control')
        self.state.transition(old['seq'],'uncertain_execution')
        self.state.db.execute("INSERT INTO fast_controls VALUES(?,?,'priority','verified')",(old['seq'],s['id']))
        saved=dict(self.state.db.execute('SELECT * FROM events WHERE seq=?',(old['seq'],)).fetchone())
        goals.start(self.state,s,'Continue verified research without restarting jobs',tracker=self.tracker)
        goals.control(self.state,s,'pause');goals.control(self.state,s,'resume')
        goals.poll(relay);goals.poll(relay)
        self.assertEqual(dict(self.state.db.execute('SELECT * FROM events WHERE seq=?',(old['seq'],)).fetchone()),saved)
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM goal_runs').fetchone()[0],1)
        item=self.state.claim('agent');self.assertIsNotNone(item)
        self.assertNotEqual(item['seq'],old['seq']);self.assertNotEqual(item['text'],'/fast ON')
        self.assertEqual(relay.process_item(item,'agent'),'complete')
        self.assertEqual(len(relay.host.calls),1)

    def test_goal_still_waits_for_unverified_or_wrong_conversation_fast_reviews(self):
        s=self.conversation();other=self.conversation();relay=self.relay()
        self.state.ingest(self.event(s,'/fast ON','unknown_fast'));old=self.state.claim('control')
        self.state.transition(old['seq'],'uncertain_execution')
        goals.start(self.state,s,'Continue verified research',tracker=self.tracker)
        for receipt in (None,('dispatched',s['id']),('verified',other['id'])):
            with self.subTest(receipt=receipt):
                self.state.db.execute('DELETE FROM fast_controls WHERE seq=?',(old['seq'],))
                if receipt:self.state.db.execute("INSERT INTO fast_controls VALUES(?,?,'priority',?)",(old['seq'],receipt[1],receipt[0]))
                goals.poll(relay)
                self.assertEqual(self.state.db.execute('SELECT count(*) FROM goal_runs').fetchone()[0],0)

    def test_verified_settings_receipt_does_not_bypass_work_or_delivery_reviews(self):
        s=self.conversation();relay=self.relay()
        self.state.ingest(self.event(s,'/fast ON','busy_fast'));old=self.state.claim('control')
        self.state.db.execute("INSERT INTO fast_controls VALUES(?,?,'priority','verified')",(old['seq'],s['id']))
        goals.start(self.state,s,'Continue verified research',tracker=self.tracker)
        for role,phase in [('control',v) for v in ['queued','running','awaiting_terminal','preparing_reply','result_ready','sending','uncertain_delivery','record_blocked','sync_pending']]+[('agent','uncertain_execution')]:
            with self.subTest(role=role,phase=phase):
                self.state.db.execute('UPDATE events SET role=?,state=? WHERE seq=?',(role,phase,old['seq']))
                goals.poll(relay)
                self.assertEqual(self.state.db.execute('SELECT count(*) FROM goal_runs').fetchone()[0],0)

    def test_user_pause_cannot_be_undone_by_a_late_wait_checkpoint(self):
        s=self.conversation();goals.start(self.state,s,'Keep checking tests',tracker=self.tracker)
        goals.control(self.state,s,'pause');goals.checkpoint(self.state,s,'wait',note='Existing experiment still running',after='10h')
        self.assertEqual(goals.current(self.state,s)['status'],'paused')

    def test_real_progress_resets_the_consecutive_blocker_count(self):
        s=self.conversation();goals.start(self.state,s,'Improve a verified benchmark',tracker=self.tracker)
        for i in range(2):
            self.state.ingest(self.event(s,'Inspect failure','block_'+str(i)));item=self.state.claim('agent')
            goals.checkpoint(self.state,s,'blocked',note='Upstream temporarily unavailable')
            self.state.db.execute("UPDATE events SET state='complete' WHERE seq=?",(item['seq'],))
            self.state.db.execute('UPDATE turn_records SET activity=\'completed\' WHERE seq=?',(item['seq'],))
        self.assertEqual(self.state.db.execute('SELECT count FROM goal_blockers').fetchone()[0],2)
        goals.checkpoint(self.state,s,'progress',note='Recovered upstream and verified a new result',next_action='Check next candidate')
        self.state.ingest(self.event(s,'Inspect next candidate','block_new'));self.state.claim('agent')
        goals.checkpoint(self.state,s,'blocked',note='Upstream temporarily unavailable')
        self.assertEqual(self.state.db.execute('SELECT count FROM goal_blockers').fetchone()[0],1)
        self.assertEqual(goals.current(self.state,s)['status'],'active')

    def test_completed_goal_is_not_reactivated_by_stop(self):
        s=self.conversation();goals.start(self.state,s,'Deliver verified result',tracker=self.tracker);goals.checkpoint(self.state,s,'complete',note='Result file verified')
        goals.control(self.state,s,'pause');self.assertEqual(goals.current(self.state,s)['status'],'complete')
        goals.control(self.state,s,'clear');self.assertIsNone(goals.current(self.state,s));self.assertIsNone(goals.saved_status(self.state,s))

    def test_resume_yields_if_an_unrelated_native_goal_is_found(self):
        s=self.conversation();relay=self.relay();goals.start(self.state,s,'Verify parser cost',tracker=self.tracker)
        goals.control(self.state,s,'pause')
        with patch.object(goals,'sync_native',side_effect=Invalid('A different native goal exists')):
            result=goals.command(relay,s,['resume'])
        self.assertIn('needs attention',result);self.assertEqual(goals.current(self.state,s)['status'],'yielded')
        goals.poll(relay);self.assertEqual(self.state.db.execute('SELECT count(*) FROM goal_runs').fetchone()[0],0)

    def test_goal_budget_uses_its_frozen_end_counter_not_a_newer_turn(self):
        s=self.conversation();relay=self.relay();goals.usage(self.state,s,100)
        goals.start(self.state,s,'Keep checking tests',budget=50,tracker=self.tracker);goals.poll(relay);item=self.state.claim('agent')
        goals.usage(self.state,s,130);goals.ended(self.state,s,item['seq']);goals.usage(self.state,s,9999)
        self.assertEqual(relay.process_item(item,'agent'),'complete');goals.poll(relay)
        row=goals.current(self.state,s);self.assertEqual(row['tokens_used'],30);self.assertEqual(row['status'],'active')

    def test_budget_exhaustion_and_missing_measurement_never_mark_success(self):
        s=self.conversation();relay=self.relay();goals.usage(self.state,s,0)
        goals.start(self.state,s,'Keep checking tests',budget=100,tracker=self.tracker);goals.poll(relay);item=self.state.claim('agent')
        goals.ended(self.state,s,item['seq'],120);self.assertEqual(relay.process_item(item,'agent'),'complete');goals.poll(relay)
        self.assertEqual(goals.current(self.state,s)['status'],'budget_limited')
        with self.assertRaises(Invalid):goals.control(self.state,s,'resume')
        self.assertIsNone(self.state.claim('agent'))

    def test_goal_wait_is_rechecked_before_dispatch(self):
        s=self.conversation();relay=self.relay();goals.start(self.state,s,'Check the existing experiment',tracker=self.tracker);goals.poll(relay)
        goals.checkpoint(self.state,s,'wait',note='Training still running',after='10h')
        item=self.state.claim('agent');self.assertEqual(relay.process_item(item,'agent'),'deferred')
        self.assertEqual(relay.host.calls,[]);self.assertIsNone(self.state.claim('agent'))

    def test_native_manual_goal_adopts_only_the_current_observed_request(self):
        s=self.conversation();path=self.state.runtime/'fixture-terminal.json';path.write_text('{}')
        self.state.db.execute('INSERT INTO terminal_attachments VALUES(?,?,?,?)',(s['id'],str(path),0,json.dumps(dict(active='fixture-manual-turn',request='Keep checking until the evidence is complete',pending=None))))
        row=goals.start(self.state,s,'Evidence is complete',auto=True,tracker=self.tracker)
        event=self.state.db.execute('SELECT * FROM events').fetchone();self.assertEqual(event['state'],'awaiting_terminal')
        self.assertEqual(event['text'],'Keep checking until the evidence is complete');self.assertEqual(row['status'],'active')
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM dispatch_attempts').fetchone()[0],0)

    def test_switching_tasks_yields_instead_of_running_a_goal_in_another_branch(self):
        s=self.conversation();relay=self.relay();goals.start(self.state,s,'Improve the selected task',tracker=self.tracker)
        from intern_personal.task_branches import command
        command(self.tracker,s,'start','Another task')
        goals.poll(relay);self.assertEqual(goals.current(self.state,s)['status'],'yielded')
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM goal_runs').fetchone()[0],0)
        with self.assertRaises(Invalid):goals.control(self.state,s,'resume')

    def test_pending_question_blocks_goal_but_never_other_conversations(self):
        a=self.conversation();b=self.conversation();relay=self.relay()
        for s in [a,b]:goals.start(self.state,s,'Keep checking the benchmark',tracker=self.tracker)
        self.question(a);goals.poll(relay)
        item=self.state.claim('agent');self.assertEqual(item['session'],b['id'])

    def test_semantic_policy_uses_current_owner_turn_and_never_peer_or_continuation(self):
        s=self.conversation();self.state.ingest(self.event(s,'Keep optimizing until the benchmark passes'));item=self.state.claim('agent')
        row=goals.start(self.state,s,'Benchmark passes with tests green',auto=True,tracker=self.tracker)
        self.assertEqual(goals.current(self.state,s)['source_seq'],item['seq'])
        self.assertEqual(self.state.db.execute('SELECT goal FROM goal_runs WHERE seq=?',(item['seq'],)).fetchone()[0],row['id'])
        self.assertEqual(goals.owner_turn(self.state,s)['seq'],item['seq'])
        relay=self.relay();self.assertEqual(relay.process_item(item,'agent'),'complete');goals.poll(relay)
        self.assertIsNotNone(self.state.claim('agent'))
        with self.assertRaises(Invalid):goals.owner_turn(self.state,s)
        self.assertEqual(item['text'],'Keep optimizing until the benchmark passes')

    def test_completion_requires_evidence_and_repeated_blockers_use_distinct_turns(self):
        s=self.conversation();goals.start(self.state,s,'Get verified evidence',tracker=self.tracker)
        with self.assertRaises(Invalid):goals.checkpoint(self.state,s,'complete')
        self.state.ingest(self.event(s,'Continue work','first'));item=self.state.claim('agent')
        for _ in range(4):goals.checkpoint(self.state,s,'blocked',note='Missing dataset')
        self.assertEqual(goals.current(self.state,s)['status'],'active')
        self.state.transition(item['seq'],'complete')
        for name in ['second','third']:
            self.state.ingest(self.event(s,'Continue work',name));item=self.state.claim('agent');goals.checkpoint(self.state,s,'blocked',note='Missing dataset');self.state.transition(item['seq'],'complete')
        self.assertEqual(goals.current(self.state,s)['status'],'blocked')
        goals.checkpoint(self.state,s,'complete',note='Artifact exists and its verification passes')
        self.assertEqual(goals.current(self.state,s)['status'],'complete')

    def test_goal_reference_and_semantic_preference_round_trip(self):
        s=self.conversation();goals.start(self.state,s,'Keep a verified task record',tracker=self.tracker)
        self.state.db.execute('INSERT INTO goal_settings VALUES(?,0)',(s['id'],));self.tracker.queue_record(s)
        from intern_personal import feature_state,portable_settings
        rows=feature_state.snapshot(self.state,s['workspace']);self.assertTrue(any(r['kind']=='goal_plan' for r in rows))
        for r in rows:feature_state.validate_data(r['kind'],r['data'])
        profile=portable_settings.snapshot(self.state,s['workspace']);portable_settings.validate(profile,{s['id']})
        self.assertEqual(profile['conversations'][0]['goal_selection'],{'automatic':0})
        self.state.db.execute('DELETE FROM goal_plans');goals.restore_saved(self.state,s)
        self.assertEqual(goals.current(self.state,s)['status'],'paused')

    def test_question_card_includes_choices_descriptions_and_custom_fields(self):
        s=self.conversation();row=self.question(s,multi=True);card=questions.card(row)
        body=json.dumps(card);self.assertIn('multi_select_static',body);self.assertIn('select_static',body)
        self.assertIn('Full evaluation',body);self.assertIn('Custom answer',body);self.assertNotIn(s['thread_id'],body)

    def test_multi_question_answers_are_correlated_not_copied_to_every_question(self):
        s=self.conversation();row=self.question(s)
        self.assertIn('Next: Which split?',questions.answer(self.state,s,row['code'],'1'))
        saved=json.loads(self.state.db.execute('SELECT answers FROM user_questions').fetchone()[0]);self.assertEqual(saved,{'dataset':{'answers':['Small']}})
        questions.answer(self.state,s,row['code'],'Use a custom split')
        saved=self.state.db.execute('SELECT * FROM user_questions').fetchone();self.assertEqual(saved['state'],'answered')
        self.assertEqual(json.loads(saved['answers'])['split'],{'answers':['Use a custom split']})

    def test_multiple_selections_and_json_answers_preserve_labels(self):
        s=self.conversation();row=self.question(s,multi=True)
        questions.answer(self.state,s,row['code'],json.dumps({'dataset':['Small','Full'],'split':'Dev'}))
        saved=json.loads(self.state.db.execute('SELECT answers FROM user_questions').fetchone()[0]);self.assertEqual(saved['dataset']['answers'],['Small','Full'])
        with self.assertRaises(Invalid):questions.validate_answers(row,{'split':['Dev','Test']})

    def test_callback_checks_app_owner_chat_message_and_duplicate_events(self):
        s=self.conversation();row=self.question(s);relay=self.relay()
        self.state.db.execute("UPDATE user_questions SET state='pending',receipt='om_card' WHERE code=?",(row['code'],))
        event=dict(header=dict(app_id='cli_test',event_type='card.action.trigger',event_id='fixture-event'),event=dict(operator=dict(open_id='ou_owner'),context=dict(open_chat_id=s['chat_id'],open_message_id='om_card'),action=dict(value=dict(intern_agent_question=row['code']),form_value=dict(q0_select='1',q1_text='Custom test split'))))
        for key in ['owner','chat','message','app']:
            bad=copy.deepcopy(event)
            if key=='owner':bad['event']['operator']['open_id']='ou_other'
            elif key=='chat':bad['event']['context']['open_chat_id']='oc_other'
            elif key=='message':bad['event']['context']['open_message_id']='om_other'
            else:bad['header']['app_id']='cli_other'
            with self.assertRaises(Invalid):questions.callback(relay,bad)
        self.assertIn('saved',questions.callback(relay,event));self.assertIn('already',questions.callback(relay,event))
        answers=json.loads(self.state.db.execute('SELECT answers FROM user_questions').fetchone()[0]);self.assertEqual(answers['dataset']['answers'],['Full'])

    def test_native_and_rpc_observation_adopt_one_question_card(self):
        s=self.conversation();one=self.question(s,source='tui',request='fixture-call');two=self.question(s,source='rpc',request=11)
        self.assertEqual(one['code'],two['code']);self.assertEqual(two['source'],'rpc')
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM user_questions').fetchone()[0],1)
        questions.resolved(self.state,s,11)
        with self.assertRaises(Invalid):questions.answer(self.state,s,one['code'],'Dev')

    def test_ordinary_text_answers_only_a_blocking_question_request(self):
        s=self.conversation();row=self.question(s)
        self.state.ingest(self.event(s,'A custom answer','answer'));event=dict(self.state.db.execute('SELECT * FROM events').fetchone())
        self.assertEqual(event['role'],'control');self.assertEqual(event['text'],'/answer '+row['code']+' A custom answer')
        questions.cancel(self.state,s);self.question(s,blocking=False,request=12)
        self.state.ingest(self.event(s,'Change the plan','steer'));event=dict(self.state.db.execute('SELECT * FROM events ORDER BY seq DESC LIMIT 1').fetchone())
        self.assertEqual(event['role'],'agent');self.assertEqual(event['text'],'Change the plan')

    def test_question_text_shortcut_does_not_swallow_a_file_caption(self):
        s=self.conversation();self.question(s)
        upload=self.event(s,'','file_first');upload['event']['message'].update(message_type='file',content=json.dumps(dict(file_name='evidence.pdf',file_key='file_fixture')))
        self.assertEqual(self.state.ingest(upload),'accepted')
        self.assertEqual(self.state.ingest(self.event(s,'Use the attached evidence','file_caption')),'accepted')
        event=dict(self.state.db.execute("SELECT * FROM events WHERE message_id='om_file_caption'").fetchone())
        self.assertEqual(event['role'],'agent');self.assertEqual(event['text'],'Use the attached evidence')
        self.assertEqual(self.state.db.execute('SELECT name FROM attachments WHERE seq=?',(event['seq'],)).fetchone()[0],'evidence.pdf')
        self.assertEqual(questions.pending(self.state,s)[0]['state'],'new')

    def test_question_survives_receiver_state_reopen_but_never_answers_itself(self):
        s=self.conversation();row=self.question(s);reopened=LiveState(self.state.root);self.addCleanup(reopened.close)
        restored=questions.pending(reopened,s);self.assertEqual(restored[0]['code'],row['code']);self.assertEqual(restored[0]['answers'],'{}')
        questions.cancel(reopened,s)
        with self.assertRaises(Invalid):questions.answer(reopened,s,row['code'],'1')

    def test_yolo_question_response_waits_for_real_answers(self):
        s=self.conversation();host=CodexHost(self.state,tracker=self.tracker);sent=[];wire=SimpleNamespace(send=sent.append)
        params=dict(threadId=s['thread_id'],turnId='fixture-turn',itemId='fixture-call',questions=[dict(id='one',header='Choice',question='Which path?',options=[dict(label='A',description='First'),dict(label='B',description='Second')])])
        host._approval(s,wire,dict(id=91,method='item/tool/requestUserInput',params=params))
        self.assertEqual(sent,[]);code=next(iter(host.approvals))
        host.decide(code,'answer',s['id'],'B')
        self.assertEqual(sent,[{'id':91,'result':{'answers':{'one':{'answers':['B']}}}}])

    def test_normal_codex_mode_enables_questions_without_changing_execution_permissions(self):
        s=self.conversation();host=CodexHost(self.state,tracker=self.tracker)
        params=host.thread_params(s)
        self.assertTrue(params['config']['features.default_mode_request_user_input'])
        self.assertEqual(params['sandbox'],self.state.config()['sandbox'])

    def test_card_send_failure_preserves_a_visible_text_fallback(self):
        s=self.conversation();relay=self.relay();row=self.question(s)
        relay.api.send_question=lambda *args:(_ for _ in ()).throw(live.FeishuError('fixture_card',230001))
        questions.pump(relay)
        saved=self.state.db.execute('SELECT * FROM user_questions WHERE code=?',(row['code'],)).fetchone()
        self.assertEqual(saved['delivery_state'],'text_fallback');self.assertIn('Full evaluation',relay.api.messages[-1][1])
        self.assertIn('/answer '+row['code']+' YOUR ANSWER',relay.api.messages[-1][1])
        questions.pump(relay);self.assertEqual(len(relay.api.messages),1)

    def test_ambiguous_card_and_fallback_failure_are_visible_without_automatic_duplicate(self):
        s=self.conversation();relay=self.relay();row=self.question(s)
        relay.api.send_question=lambda *args:(_ for _ in ()).throw(TimeoutError())
        relay.api.send=lambda *args:(_ for _ in ()).throw(TimeoutError())
        questions.pump(relay);questions.pump(relay)
        self.assertIn('uncertain',questions.describe(self.state,s))
        self.assertEqual(self.state.db.execute('SELECT delivery_attempts FROM user_questions').fetchone()[0],1)

    def test_rpc_rejects_a_foreign_question_without_stalling_the_current_turn(self):
        s=self.conversation();host=CodexHost(self.state,tracker=self.tracker);sent=[]
        host._approval(s,SimpleNamespace(send=sent.append),dict(id=11,method='item/tool/requestUserInput',params=dict(threadId='foreign',questions=[])))
        self.assertIn('error',sent[0]);self.assertEqual(host.approvals,{})

    def test_secret_fields_are_not_sent_to_a_feishu_input(self):
        s=self.conversation();row=questions.register(self.state,s,dict(threadId=s['thread_id'],turnId='secret-turn',questions=[dict(id='secret',header='Local',question='Supply the credential locally',isSecret=True)]),88)
        self.assertNotIn('form_submit',json.dumps(questions.card(row)))
        self.assertNotIn('/answer ',json.dumps(questions.card(row)))
        with self.assertRaises(Invalid):questions.answer(self.state,s,row['code'],'a-private-value')

    def test_attached_client_question_helper_waits_for_exact_answers_without_terminal_keys(self):
        from concurrent.futures import ThreadPoolExecutor
        s=self.conversation();self.state.ingest(self.event(s,'Choose the dataset'));self.state.claim('agent')
        body=[dict(id='choice',header='Choice',question='Which dataset?',options=[dict(label='Small',description='Quick'),dict(label='Full',description='All')])]
        def work():
            try:return questions.ask(self.state,s,body,timeout=4)
            finally:self.state.close()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future=executor.submit(work);until=time.time()+3;pending=[]
            while time.time()<until:
                pending=questions.pending(self.state,s)
                if pending:break
                time.sleep(.02)
            self.assertTrue(pending);self.assertFalse(future.done())
            questions.answer(self.state,s,pending[0]['code'],'Full')
            self.assertEqual(future.result(timeout=3),{'answers':{'choice':{'answers':['Full']}}})
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM events').fetchone()[0],1)

    def test_async_question_answers_use_one_normal_followup_not_terminal_keys(self):
        s=self.conversation();relay=self.relay()
        params=questions.native_params({'questions':[{'title':'Which dataset?', 'options':['Small','Full']}]},True)
        row=questions.register(self.state,s,dict(params,threadId=s['thread_id'],turnId='fixture-turn',itemId='async-call'),'async-call',source='native_async')
        questions.resolved(self.state,s,'async-call')
        self.assertEqual(questions.pending(self.state,s)[0]['state'],'new')
        questions.answer(self.state,s,row['code'],'2');questions.pump(relay);questions.pump(relay)
        events=self.state.db.execute("SELECT text,session FROM events WHERE message_id=?",('om_question_'+row['code'],)).fetchall()
        self.assertEqual(len(events),1);self.assertEqual(events[0]['session'],s['id'])
        self.assertIn('Which dataset?\nAnswer: Full',events[0]['text'])
        self.assertEqual(self.state.db.execute('SELECT state FROM user_questions WHERE code=?',(row['code'],)).fetchone()[0],'submitted')

    def test_question_decisions_become_reusable_markdown_without_live_request_routes(self):
        s=self.conversation();relay=self.relay();row=self.question(s)
        questions.answer(self.state,s,row['code'],{'dataset':'Full','split':'Dev'})
        self.state.db.execute("UPDATE user_questions SET state='submitted' WHERE code=?",(row['code'],))
        saved=dict(self.state.db.execute('SELECT * FROM user_questions').fetchone());questions.save_decision(relay,saved)
        doc=self.state.db.execute("SELECT * FROM feature_documents WHERE kind='question_decision'").fetchone()
        data=json.loads(doc['data']);questions.validate_decision(data)
        self.assertNotIn(s['thread_id'],doc['data']);self.assertNotIn(row['code'],doc['data'])
        self.assertIn('Which dataset',self.raw(self.remote,'show','intern-management/test:.intern_workspace/features/question_decision/'+doc['key']+'.md'))

    def test_native_question_answer_never_types_into_an_unrecognized_prompt(self):
        from intern_personal import question_terminal
        s=self.conversation();row=self.question(s,source='tui');questions.answer(self.state,s,row['code'],{'dataset':'Small','split':'Dev'})
        row=dict(self.state.db.execute('SELECT * FROM user_questions WHERE code=?',(row['code'],)).fetchone())
        self.state.db.execute("UPDATE user_questions SET state='sending' WHERE code=?",(row['code'],))
        bridge=SimpleNamespace(binding=lambda s:{'snapshot':json.dumps({'active':row['turn']})},config=lambda s:{'pane':'fixture','tmux_socket':'fixture'})
        calls=[]
        def ui(config,*args,**kw):
            if args[0]=='capture-pane':return 'Normal console with an owner draft\n› do not erase this draft'
            calls.append(args);return ''
        with patch.object(question_terminal,'verify_pane'),patch.object(question_terminal,'tmux',side_effect=ui):
            with self.assertRaises(Invalid):question_terminal.submit(self.state,bridge,s,row)
        self.assertEqual(calls,[])

    def test_stop_during_native_answer_delivery_cannot_reopen_the_question(self):
        from intern_personal import question_terminal
        s=self.conversation();relay=self.relay();row=self.question(s,source='tui')
        questions.answer(self.state,s,row['code'],{'dataset':'Full','split':'Dev'})
        def stop(*args):
            questions.cancel(self.state,s)
            raise Invalid('The owner stopped this request')
        with patch.object(relay.terminal,'binding',return_value={'bound':True}),patch.object(question_terminal,'submit',side_effect=stop):questions.pump(relay)
        self.assertEqual(self.state.db.execute('SELECT state FROM user_questions WHERE code=?',(row['code'],)).fetchone()[0],'cancelled')
        self.assertEqual(questions.pending(self.state,s),[])

    def test_partial_native_answer_is_retained_for_review_without_replay(self):
        from intern_personal import question_terminal
        s=self.conversation();row=self.question(s,source='tui');questions.answer(self.state,s,row['code'],{'dataset':'Small','split':'Dev'})
        row=dict(self.state.db.execute('SELECT * FROM user_questions WHERE code=?',(row['code'],)).fetchone())
        self.state.db.execute("UPDATE user_questions SET state='sending' WHERE code=?",(row['code'],))
        bridge=SimpleNamespace(binding=lambda s:{'snapshot':json.dumps({'active':row['turn']})},config=lambda s:{'pane':'fixture','tmux_socket':'fixture'})
        calls=[]
        def ui(config,*args,**kw):
            if args[0]=='capture-pane':return 'Which dataset should be used?\n› 1. Small\n  2. Full\n  3. None of the above' if not calls else 'Unknown changed menu'
            calls.append(args);return ''
        with patch.object(question_terminal,'verify_pane'),patch.object(question_terminal,'tmux',side_effect=ui),patch.object(question_terminal.time,'sleep'):
            with self.assertRaises(Invalid):question_terminal.submit(self.state,bridge,s,row)
        self.assertEqual(len(calls),1);self.assertEqual(calls[0][-1],'Enter')
        self.assertIn('uncertain',questions.describe(self.state,s))
        with self.assertRaises(Invalid):questions.answer(self.state,s,row['code'],'1')


    def test_structured_card_and_decision_redaction_preserve_json_and_live_answer_code(self):
        s=self.conversation();relay=self.relay();row=self.question(s);values=json.loads(row['questions'])
        values[0]['question']='api_key = harmless-fixture-text'
        self.state.db.execute('UPDATE user_questions SET questions=? WHERE code=?',(json.dumps(values),row['code']))
        delivered=[]
        relay.api.send_question=lambda chat,card,key:(delivered.append(card) or 'om_fixture_card')
        questions.pump(relay)
        self.assertEqual(self.state.db.execute('SELECT delivery_state FROM user_questions').fetchone()[0],'delivered')
        body=json.dumps(delivered[0]);self.assertIn('[redacted credential field]',body);self.assertNotIn('harmless-fixture-text',body)
        self.assertIn('/answer '+row['code'],body);self.assertEqual(delivered[0]['elements'][-2]['elements'][-1]['value']['intern_agent_question'],row['code'])
        questions.answer(self.state,s,row['code'],{'dataset':'password = another-fixture-text','split':'Dev'})
        self.state.db.execute("UPDATE user_questions SET state='submitted' WHERE code=?",(row['code'],));questions.pump(relay)
        doc=self.state.db.execute("SELECT data FROM feature_documents WHERE kind='question_decision'").fetchone()[0]
        saved=json.loads(doc);questions.validate_decision(saved)
        self.assertEqual(saved['questions'][0],{'question':'[redacted credential field]','answers':['[redacted credential field]']})
        self.assertEqual(saved['questions'][1]['answers'],['Dev']);self.assertNotIn(row['code'],doc)

    def test_question_test_card_cannot_hold_real_goal_work(self):
        s=self.conversation();relay=self.relay();questions.demo(self.state,s)
        self.assertEqual(questions.pending(self.state,s),[])
        goals.start(self.state,s,'Verify the next candidate',tracker=self.tracker);goals.poll(relay)
        self.assertIsNotNone(self.state.claim('agent'))
        self.assertEqual(self.state.db.execute("SELECT count(*) FROM user_questions WHERE source='demo' AND state='new'").fetchone()[0],1)


    def test_configuration_placeholder_decision_remains_valid_json_and_publishes_once(self):
        s=self.conversation();relay=self.relay();row=self.question(s)
        values=json.loads(row['questions']);values[0]['question']='Choose api_key = PLACEHOLDER or environment configuration'
        self.state.db.execute('UPDATE user_questions SET questions=? WHERE code=?',(json.dumps(values),row['code']))
        answer='Use environment variable "API_KEY"\nKeep configuration outside Git. 配置'
        questions.answer(self.state,s,row['code'],{'dataset':'api_key = PLACEHOLDER','split':answer})
        self.state.db.execute("UPDATE user_questions SET state='submitted' WHERE code=?",(row['code'],))
        questions.pump(relay);questions.pump(relay)
        docs=self.state.db.execute("SELECT * FROM feature_documents WHERE kind='question_decision'").fetchall()
        self.assertEqual(len(docs),1);data=json.loads(docs[0]['data']);questions.validate_decision(data)
        self.assertEqual(data['questions'][0],{'question':'[redacted credential field]','answers':['[redacted credential field]']})
        self.assertEqual(data['questions'][1]['answers'],[answer])
        self.assertEqual(self.state.db.execute('SELECT decision_saved FROM user_questions WHERE code=?',(row['code'],)).fetchone()[0],1)
        published=self.raw(self.remote,'show','intern-management/test:.intern_workspace/features/question_decision/'+docs[0]['key']+'.md')
        self.assertIn('Keep configuration outside Git.',published);self.assertIn('[redacted credential field]',published)

    def test_persisted_question_decision_redacts_known_secret_in_each_text_field(self):
        s=self.conversation();relay=self.relay();row=self.question(s);secret=self.state.credentials()['app_secret']
        values=json.loads(row['questions']);values[0]['question']='Which label contains "'+secret+'"?'
        self.state.db.execute('UPDATE user_questions SET questions=? WHERE code=?',(json.dumps(values),row['code']))
        questions.answer(self.state,s,row['code'],{'dataset':'Use "'+secret+'" only locally','split':'Dev'})
        self.state.db.execute("UPDATE user_questions SET state='submitted' WHERE code=?",(row['code'],))
        questions.pump(relay)
        doc=self.state.db.execute("SELECT * FROM feature_documents WHERE kind='question_decision'").fetchone()
        data=json.loads(doc['data']);questions.validate_decision(data)
        self.assertEqual(data['questions'][0],{'question':'Which label contains "[redacted]"?','answers':['Use "[redacted]" only locally']})
        published=self.raw(self.remote,'show','intern-management/test:.intern_workspace/features/question_decision/'+doc['key']+'.md')
        self.assertNotIn(secret,doc['data']);self.assertNotIn(secret,published)
        self.assertNotIn(row['code'],doc['data']);self.assertNotIn(s['thread_id'],doc['data'])


if __name__=='__main__':unittest.main()
