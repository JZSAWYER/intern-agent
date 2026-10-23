import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_live as fixture
import test_terminal as terminal_fixture
from intern_personal.core import Invalid
from intern_personal.codex_bridge import CodexHost,BridgeError
from intern_personal.live_state import LiveState,write_private
from intern_personal.speed import command,fast_tier,saved,save,save_observed,observed


CATALOG = [{'model':'example-model','serviceTiers':[{'id':'priority','name':'Fast'}],
            'supportedReasoningEfforts':[{'reasoningEffort':x} for x in ['low','medium','high']],
            'defaultReasoningEffort':'high'}]


class FastTests(unittest.TestCase):
    setUp = fixture.LiveTests.setUp
    event = fixture.LiveTests.event
    relay_command = fixture.LiveTests.command

    def setUp(self):
        fixture.LiveTests.setUp(self)
        self.model_patch=patch.object(self.host,'models',return_value=CATALOG)
        self.model_patch.start();self.addCleanup(self.model_patch.stop)

    def test_commands_route_to_controller_or_explicit_session_and_persist(self):
        other=self.relay.open_session('project');controller=self.relay.attach_controller('project')
        self.assertIn('ON',self.relay_command('/fast'))
        self.assertEqual(saved(self.state,controller),'priority')
        self.assertIsNone(saved(self.state,other))
        self.assertIn('ON',self.relay_command('/fast status'))
        self.assertIn('OFF',self.relay_command('/fast '+other['id']+' off'))
        self.assertEqual(saved(self.state,other),'default')
        self.assertEqual(saved(self.state,controller),'priority')
        self.assertIn('OFF',self.relay_command('/fast'))
        reopened=LiveState(self.state.root)
        try:self.assertEqual(saved(reopened,controller),'default')
        finally:reopened.close()

    def test_same_event_toggle_is_idempotent_and_owner_text_is_not_dispatched(self):
        session=self.relay.open_session('project')
        item=dict(seq=901,text='/fast',session=session['id'],sender='ou_owner')
        self.assertIn('ON',command(self.relay,item,False))
        self.assertIn('ON',command(self.relay,item,False))
        self.assertEqual(saved(self.state,session),'priority')
        save(self.state,session,'default')
        self.assertIn('not executed again',command(self.relay,item,False))
        self.assertEqual(saved(self.state,session),'default')
        self.assertEqual(self.host.calls,[])

    def test_explicit_uppercase_on_already_observed_does_not_call_settings_api(self):
        session=self.relay.open_session('project')
        self.host.observer=SimpleNamespace()
        with patch('intern_personal.speed.observed',return_value=('priority',True)), \
                patch.object(self.host,'set_service_tier',side_effect=BridgeError('No repeated notification')) as update:
            reply=self.relay_command('/fast ON',session)
        self.assertIn('already ON',reply)
        self.assertEqual(saved(self.state,session),'priority')
        self.assertEqual(self.state.db.execute('SELECT state FROM events').fetchone()[0],'complete')
        update.assert_not_called();self.assertEqual(self.host.calls,[])

    def test_lost_ack_uses_matching_typed_settings_without_inference_or_replay(self):
        session=self.relay.open_session('project')
        self.host.observer=SimpleNamespace()
        with patch('intern_personal.speed.observed',side_effect=[('default',True),('priority',True)]), \
                patch.object(self.host,'set_service_tier',side_effect=TimeoutError('Private transport detail')) as update:
            reply=self.relay_command('/fast ON',session)
        self.assertIn('confirmed from',reply)
        self.assertEqual(saved(self.state,session),'priority')
        self.assertEqual(self.state.db.execute('SELECT phase FROM fast_controls').fetchone()[0],'verified')
        self.assertEqual(self.state.db.execute('SELECT state FROM events').fetchone()[0],'complete')
        self.assertEqual(update.call_count,1);self.assertEqual(self.host.calls,[])

    def test_unknown_fast_ack_is_a_command_result_not_unknown_codex_execution(self):
        session=self.relay.open_session('project')
        with patch.object(self.host,'set_service_tier',side_effect=BridgeError('Private transport detail')):
            reply=self.relay_command('/fast ON',session)
        self.assertIn('not confirmed',reply)
        self.assertNotIn('Private transport detail',reply)
        self.assertEqual(self.state.db.execute('SELECT state FROM events').fetchone()[0],'complete')
        self.assertEqual(self.state.db.execute('SELECT phase FROM fast_controls').fetchone()[0],'dispatched')
        self.assertIsNone(saved(self.state,session));self.assertEqual(self.host.calls,[])

    def test_unmatched_or_unknown_observation_cannot_certify_a_lost_ack(self):
        for observed_value in [('default',True),('priority',False),('unrecognized',True),({'tier':'priority'},True)]:
            with self.subTest(observation=observed_value):
                session=self.relay.open_session('project');self.host.observer=SimpleNamespace()
                with patch('intern_personal.speed.observed',side_effect=[('default',True),observed_value]), \
                        patch.object(self.host,'set_service_tier',side_effect=TimeoutError):
                    reply=self.relay_command('/fast ON',session)
                self.assertIn('not confirmed',reply)
                self.assertIsNone(saved(self.state,session));self.assertEqual(self.host.calls,[])

    def test_catalogue_failure_returns_a_settings_error_without_an_execution_review(self):
        session=self.relay.open_session('project')
        with patch.object(self.host,'models',side_effect=BridgeError('Private transport detail')), \
                patch.object(self.host,'set_service_tier') as update:
            reply=self.relay_command('/fast ON',session)
        self.assertIn('no setting change was sent',reply)
        self.assertNotIn('Private transport detail',reply)
        self.assertEqual(self.state.db.execute('SELECT state FROM events').fetchone()[0],'complete')
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM fast_controls').fetchone()[0],0)
        update.assert_not_called();self.assertEqual(self.host.calls,[])

    def test_saved_preference_is_not_a_substitute_for_observed_settings(self):
        session=self.relay.open_session('project');self.host.observer=SimpleNamespace()
        save(self.state,session,'priority')
        with patch('intern_personal.speed.observed',return_value=('default',True)), \
                patch.object(self.host,'set_service_tier',return_value='priority') as update:
            self.relay_command('/fast ON',session)
        update.assert_called_once_with(session,'priority')
        save(self.state,session,'default')
        with patch('intern_personal.speed.observed',return_value=('priority',True)), \
                patch.object(self.host,'set_service_tier') as update:
            self.assertIn('already ON',self.relay_command('/fast ON',session))
        update.assert_not_called()

    def test_verified_control_review_remains_historical_without_a_false_doctor_block(self):
        from intern_personal.operations import diagnose,session_command
        session=self.relay.open_session('project');self.relay_command('/fast ON',session)
        event=self.state.db.execute('SELECT * FROM events').fetchone()
        self.state.transition(event['seq'],'uncertain_execution')
        before=tuple(self.state.db.execute('SELECT * FROM events').fetchone())
        check=next(c for c in diagnose(self.state)['checks'] if c['id']=='delivery_queue')
        self.assertTrue(check['passed'])
        self.assertEqual(before,tuple(self.state.db.execute('SELECT * FROM events').fetchone()))
        self.assertIn('archived',session_command(self.relay,session,'archive'))
        self.assertEqual(before,tuple(self.state.db.execute('SELECT * FROM events').fetchone()))

    def test_unknown_fast_review_still_protects_conversation_lifecycle(self):
        from intern_personal.operations import diagnose,session_command
        session=self.relay.open_session('project');self.relay_command('/fast ON',session)
        event=self.state.db.execute('SELECT * FROM events').fetchone()
        self.state.transition(event['seq'],'uncertain_execution')
        self.state.db.execute("UPDATE fast_controls SET phase='dispatched'")
        self.assertFalse(next(c for c in diagnose(self.state)['checks'] if c['id']=='delivery_queue')['passed'])
        with self.assertRaisesRegex(Invalid,'Resolve queued or uncertain'):
            session_command(self.relay,session,'archive')

    def test_verified_settings_do_not_hide_delivery_or_git_reviews(self):
        from intern_personal.operations import diagnose,session_command
        for pending in ['queued','uncertain_delivery','record_blocked','sync_pending']:
            with self.subTest(state=pending):
                session=self.relay.open_session('project');self.relay_command('/fast ON',session)
                event=self.state.db.execute('SELECT * FROM events WHERE session=?',(session['id'],)).fetchone()
                self.state.db.execute('UPDATE events SET state=? WHERE seq=?',(pending,event['seq']))
                with self.assertRaisesRegex(Invalid,'Resolve queued or uncertain'):
                    session_command(self.relay,session,'archive')
                if pending!='queued':
                    self.assertFalse(next(c for c in diagnose(self.state)['checks'] if c['id']=='delivery_queue')['passed'])

    def test_status_and_invalid_inputs_have_no_setting_side_effect(self):
        session=self.relay.open_session('project')
        before=self.state.db.execute('SELECT COUNT(*) FROM fast_controls').fetchone()[0]
        self.relay_command('/fast status',session)
        self.assertIsNone(saved(self.state,session))
        self.assertEqual(self.state.db.execute('SELECT COUNT(*) FROM fast_controls').fetchone()[0],before)
        for text in ['/fast turbo','/fast on off']:
            self.relay_command(text,session)
            self.assertIsNone(saved(self.state,session))

    def test_unsupported_models_and_non_owner_cannot_enable_fast(self):
        session=self.relay.open_session('project')
        with patch.object(self.host,'models',return_value=[{'model':'example-model'}]):
            self.assertIn('does not advertise',self.relay_command('/fast on',session))
        self.assertIsNone(saved(self.state,session))
        self.state.db.execute("INSERT INTO workspace_access VALUES('ou_viewer','project','viewer')")
        self.assertEqual(self.relay.receive(self.event('/fast on',session['chat_id'],'om_denied',sender='ou_viewer')),'rejected_sender')
        self.assertEqual(self.relay.receive(self.event('/fast',session['chat_id'],'om_denied_toggle',sender='ou_viewer')),'rejected_sender')
        self.assertEqual(self.relay.receive(self.event('/fast status',session['chat_id'],'om_allowed',sender='ou_viewer')),'accepted')

    def test_fast_tier_comes_from_catalogue_and_model_effort_are_preserved(self):
        self.assertEqual(fast_tier(CATALOG[0]),'priority')
        self.assertEqual(fast_tier({'additionalSpeedTiers':['fast']}),'fast')
        with self.assertRaises(Invalid):fast_tier({'serviceTiers':[{'id':'ultrafast'}]})
        session=self.relay.open_session('project')
        self.state.set_model(session['id'],'example-model','medium')
        self.relay_command('/fast on',session)
        current=self.state.session(session['id'])
        self.assertEqual((current['model'],current['effort']),('example-model','medium'))
        with patch.object(self.host,'models',return_value=[dict(CATALOG[0],serviceTiers=[])]):
            self.assertIn('does not advertise',self.relay_command('/reasoning high',session))
        self.assertEqual(self.state.session(session['id'])['effort'],'medium')

    def test_tier_is_sent_to_real_bridge_fields_without_changing_prompt(self):
        session=self.relay.open_session('project');host=CodexHost(self.state,tracker=self.tracker)
        self.assertNotIn('serviceTier',host.thread_params(session))
        save(self.state,session,'priority')
        self.assertEqual(host.thread_params(session)['serviceTier'],'priority')
        sent=[]
        class Connection:
            def __init__(inner):inner.count=0
            def request(inner,method,params):
                sent.append((method,params));return {'turn':{'id':'fixture-turn'}}
            def notification(inner,timeout):
                inner.count+=1
                if inner.count==1:
                    return {'method':'item/completed','params':{'item':{'type':'agentMessage','id':'answer','text':'Verified result','phase':'final_answer'}}}
                return {'method':'turn/completed','params':{'turn':{'id':'fixture-turn','status':'completed'}}}
        text='  Owner words\nremain exact  '
        host._turn(Connection(),session,session['thread_id'],text)
        params=sent[0][1]
        self.assertEqual(params['serviceTier'],'priority')
        self.assertEqual(params['input'],[{'type':'text','text':text}])
        save(self.state,session,'default')
        self.assertEqual(host.thread_params(session)['serviceTier'],'default')

    def test_old_console_settings_cannot_erase_new_preference_but_newer_changes_apply(self):
        from datetime import datetime,timezone
        session=self.relay.open_session('project')
        save(self.state,session,'priority')
        point=self.state.db.execute('SELECT updated_at FROM session_service_tiers').fetchone()[0]
        earlier=datetime.fromtimestamp(point-1,timezone.utc).isoformat()
        later=datetime.fromtimestamp(point+1,timezone.utc).isoformat()
        save_observed(self.state,session,'default',earlier)
        save_observed(self.state,session,'default',None)
        self.assertEqual(saved(self.state,session),'priority')
        save_observed(self.state,session,'default',later)
        self.assertEqual(saved(self.state,session),'default')

    def test_bridge_settings_update_requires_exact_thread_ack_and_closes_connection(self):
        session=self.relay.open_session('project');host=CodexHost(self.state,tracker=self.tracker)
        notices=iter([{'method':'thread/settings/updated','params':{'threadId':'other','threadSettings':{'serviceTier':'priority'}}},
                      {'method':'thread/settings/updated','params':{'threadId':session['thread_id'],'threadSettings':{'serviceTier':'priority'}}}])
        subscribed=[False]
        def request(method,params,timeout=None):
            if method=='thread/resume':subscribed[0]=params=={'threadId':session['thread_id'],'excludeTurns':True}
            return {}
        def notification(timeout):
            if not subscribed[0]:raise AssertionError('Settings client is not subscribed to its thread')
            return next(notices)
        wire=SimpleNamespace(request=request,notification=notification,close=lambda:None)
        with patch.object(host,'ensure_started'),patch.object(host,'connect',return_value=wire), \
             patch.object(wire,'request',wraps=wire.request) as request,patch.object(wire,'close') as close:
            self.assertEqual(host.set_service_tier(session,'priority'),'priority')
            self.assertEqual(request.call_args.args,('thread/settings/update',{'threadId':session['thread_id'],'serviceTier':'priority'}))
            self.assertEqual(request.call_args.kwargs['timeout'],5)
            close.assert_called_once()

    def test_failed_settings_ack_is_not_saved_as_success_and_is_retryable(self):
        session=self.relay.open_session('project');item=dict(seq=901,text='/fast',session=session['id'],sender='ou_owner')
        from intern_personal.codex_bridge import BridgeError
        with patch.object(self.host,'set_service_tier',side_effect=BridgeError('No acknowledgement')):
            with self.assertRaisesRegex(Invalid,'not confirmed'):command(self.relay,item,False)
        self.assertIsNone(saved(self.state,session))
        self.assertEqual(self.state.db.execute('SELECT phase FROM fast_controls').fetchone()[0],'dispatched')
        self.assertIn('verified in Codex',command(self.relay,item,False))
        self.assertEqual(saved(self.state,session),'priority')


class NativeFastTests(unittest.TestCase):
    setUp = terminal_fixture.TerminalTests.setUp
    event = terminal_fixture.TerminalTests.event
    message = terminal_fixture.TerminalTests.message
    attach = terminal_fixture.TerminalTests.attach

    def prepare(self,tier='default',active=False):
        self.event('thread_settings_applied',thread_id='fixture-thread',
                   thread_settings={'model':'example-model','reasoning_effort':'high','service_tier':tier})
        self.event('task_started',turn_id='old');self.message('Owner words')
        if not active:self.event('task_complete',turn_id='old',last_agent_message='Prior result')
        self.attach();self.bridge.poll(self.session)
        binding=self.bridge.binding(self.session);config=self.bridge.config(self.session)
        config['pane']='%fixture';write_private(binding['config_path'],config)
        host=fixture.FakeHost(self.state)
        host.models=lambda session=None:CATALOG
        self.relay=SimpleNamespace(state=self.state,terminal=self.bridge,host=host,tracker=self.tracker)
        return dict(seq=901,text='/fast on',session=self.session['id'],sender='ou_owner')

    def test_busy_native_never_sends_keys_or_claims_applied(self):
        item=self.prepare(active=True)
        with patch('intern_personal.terminal_bridge.tmux') as tmux:
            with self.assertRaisesRegex(Invalid,'busy'):command(self.relay,item,False)
        tmux.assert_not_called()
        self.assertEqual(self.state.db.execute('SELECT phase FROM fast_controls').fetchone()[0],'prepared')

    def test_native_uses_exact_console_toggle_and_requires_settings_ack(self):
        item=self.prepare()
        calls=[]
        def keys(config,*args,**kwargs):
            calls.append(args)
            if args[-1]=='Enter':
                self.event('thread_settings_applied',thread_id='fixture-thread',
                           thread_settings={'model':'example-model','reasoning_effort':'high','service_tier':'priority'})
            return ''
        with patch('intern_personal.terminal_bridge.verify_pane'),patch('intern_personal.terminal_bridge.empty_prompt'),patch('intern_personal.terminal_bridge.tmux',side_effect=keys):
            self.assertIn('verified',command(self.relay,item,False))
            self.assertIn('already verified',command(self.relay,item,False))
        self.assertEqual(len(calls),2)
        self.assertEqual(calls[0][-1],'/fast')
        self.assertEqual(self.state.db.execute('SELECT phase FROM fast_controls').fetchone()[0],'verified')
        self.assertEqual(observed(self.bridge,self.session),('priority',True))

    def test_native_ack_loss_never_replays_toggle_and_unknown_tier_is_rejected(self):
        item=self.prepare()
        self.state.db.execute("INSERT INTO fast_controls VALUES(901,?,'priority','dispatched')",(self.session['id'],))
        with patch('intern_personal.terminal_bridge.tmux') as tmux:
            with self.assertRaisesRegex(Invalid,'uncertain'):command(self.relay,item,False)
        tmux.assert_not_called()
        self.event('thread_settings_applied',thread_id='fixture-thread',
                   thread_settings={'model':'example-model','reasoning_effort':'high','service_tier':'priority'})
        with patch('intern_personal.terminal_bridge.verify_pane'):
            self.assertIn('already ON',command(self.relay,item,False))
        self.event('thread_settings_applied',thread_id='fixture-thread',
                   thread_settings={'model':'example-model','reasoning_effort':'high','service_tier':'ultrafast'})
        with self.assertRaisesRegex(Invalid,'another service tier'):
            command(self.relay,dict(item,seq=902,text='/fast off'),False)

    def test_native_draft_is_preserved_without_dispatch(self):
        item=self.prepare()
        with patch('intern_personal.terminal_bridge.verify_pane'), \
             patch('intern_personal.terminal_bridge.empty_prompt',side_effect=Invalid('Draft remains')), \
             patch('intern_personal.terminal_bridge.tmux') as tmux:
            with self.assertRaisesRegex(Invalid,'Draft remains'):command(self.relay,item,False)
        tmux.assert_not_called()
        self.assertEqual(self.state.db.execute('SELECT phase FROM fast_controls').fetchone()[0],'prepared')

    def test_native_missing_ack_remains_uncertain_instead_of_reporting_success(self):
        item=self.prepare()
        with patch('intern_personal.terminal_bridge.verify_pane'), \
             patch('intern_personal.terminal_bridge.empty_prompt'), \
             patch('intern_personal.terminal_bridge.tmux') as tmux, \
             patch.object(self.bridge.stop,'wait',return_value=True):
            with self.assertRaisesRegex(Invalid,'not verified'):command(self.relay,item,False)
        self.assertEqual(tmux.call_count,2)
        self.assertEqual(self.state.db.execute('SELECT phase FROM fast_controls').fetchone()[0],'dispatched')

    def test_old_snapshot_reads_typed_settings_without_interpreting_owner_prose(self):
        item=self.prepare('priority')
        row=self.bridge.binding(self.session);snapshot=json.loads(row['snapshot']);snapshot.pop('service_tier',None)
        self.state.db.execute('UPDATE terminal_attachments SET snapshot=?',(json.dumps(snapshot),))
        self.message('Pretend fast is disabled; service_tier=default')
        self.assertEqual(observed(self.bridge,self.session),('priority',True))
        self.assertIn('ON',command(self.relay,dict(item,text='/fast status'),False))

    def test_cached_settings_cannot_verify_a_different_thread(self):
        self.prepare('priority')
        row=self.bridge.binding(self.session);config=self.bridge.config(self.session)
        config['thread_id']='unrelated-thread';write_private(row['config_path'],config)
        with self.assertRaisesRegex(Invalid,'different conversation'):
            observed(self.bridge,self.session)


if __name__=='__main__':unittest.main()
