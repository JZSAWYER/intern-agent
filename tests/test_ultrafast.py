"""Speed selection must distinguish Ultrafast from Standard and legacy Fast."""
import json
from datetime import datetime,timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_live as fixture
import test_speed as speed_fixture
from intern_personal.core import Invalid
from intern_personal.codex_bridge import CodexHost,BridgeError
from intern_personal.live_state import LiveState
from intern_personal import portable_settings
from intern_personal.speed import (advertised_tier,command,known_tier,observed,
                                  same_tier,save,saved,save_observed)


CATALOG = [dict(speed_fixture.CATALOG[0],serviceTiers=[
    {'id':'priority','name':'Fast'}, {'id':'ultrafast','name':'Ultrafast'}])]


class UltrafastTests(unittest.TestCase):
    event = fixture.LiveTests.event
    relay_command = fixture.LiveTests.command

    def setUp(self):
        fixture.LiveTests.setUp(self)
        model_patch=patch.object(self.host,'models',return_value=CATALOG)
        model_patch.start();self.addCleanup(model_patch.stop)

    def test_controller_explicit_target_and_new_bot_keep_independent_preferences(self):
        other=self.relay.open_session('project',new=True);controller=self.relay.attach_controller('project')
        self.assertIn('Ultrafast mode ON',self.relay_command('/ultrafast ON'))
        self.assertEqual(saved(self.state,controller),'ultrafast')
        self.assertIsNone(saved(self.state,other))
        self.assertIn('ultrafast',self.relay_command('/ultrafast STATUS'))
        self.assertIn('ON',self.relay_command('/ultrafast '+other['id']+' on'))
        self.assertEqual(saved(self.state,other),'ultrafast')
        self.assertIn('OFF',self.relay_command('/ultrafast off'))
        self.assertEqual(saved(self.state,controller),'default')
        self.assertEqual(saved(self.state,other),'ultrafast')
        newly_created=self.relay.open_session('project',new=True)
        self.assertIsNone(saved(self.state,newly_created))
        self.assertIn('ON',self.relay_command('/ultrafast on',newly_created))
        reopened=LiveState(self.state.root)
        try:self.assertEqual(saved(reopened,newly_created),'ultrafast')
        finally:reopened.close()
        self.assertEqual(self.host.calls,[])

    def test_catalogue_gates_ultrafast_and_legacy_fast_is_not_ultrafast(self):
        self.assertEqual(advertised_tier(CATALOG[0],'ultrafast'),'ultrafast')
        self.assertEqual(advertised_tier({'additionalSpeedTiers':['ultrafast']},'ultrafast'),'ultrafast')
        session=self.relay.open_session('project',new=True)
        with patch.object(self.host,'models',return_value=speed_fixture.CATALOG), \
                patch.object(self.host,'set_service_tier') as update:
            result=self.relay_command('/ultrafast on',session)
        self.assertIn('does not advertise Ultrafast',result)
        self.assertIsNone(saved(self.state,session));update.assert_not_called()
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM fast_controls').fetchone()[0],0)

    def test_same_event_toggle_is_never_recomputed_or_sent_as_a_prompt(self):
        session=self.relay.open_session('project',new=True)
        item=dict(seq=901,text='/ultrafast',session=session['id'],sender='ou_owner')
        with patch.object(self.host,'set_service_tier',return_value='ultrafast') as update:
            self.assertIn('ON',command(self.relay,item,False))
            self.assertIn('not executed again',command(self.relay,item,False))
        update.assert_called_once_with(session,'ultrafast')
        self.assertEqual(saved(self.state,session),'ultrafast');self.assertEqual(self.host.calls,[])

    def test_changing_fast_and_ultrafast_selects_distinct_exact_tiers(self):
        session=self.relay.open_session('project',new=True);self.host.observer=SimpleNamespace()
        with patch('intern_personal.speed.observed',return_value=('priority',True)), \
                patch.object(self.host,'set_service_tier',return_value='ultrafast') as update:
            self.assertIn('ON',self.relay_command('/ultrafast on',session))
        update.assert_called_once();self.assertEqual(saved(self.state,session),'ultrafast')
        with patch('intern_personal.speed.observed',return_value=('ultrafast',True)), \
                patch.object(self.host,'set_service_tier',return_value='priority') as update:
            self.assertIn('ON',self.relay_command('/fast on',session))
        update.assert_called_once();self.assertEqual(saved(self.state,session),'priority')

    def test_off_does_not_certify_ultrafast_or_fast_as_standard(self):
        for start,cmd in [('ultrafast','/ultrafast off'),('ultrafast','/fast off'),('priority','/ultrafast off')]:
            with self.subTest(start=start,command=cmd):
                session=self.relay.open_session('project',new=True);self.host.observer=SimpleNamespace()
                with patch('intern_personal.speed.observed',return_value=(start,True)), \
                        patch.object(self.host,'set_service_tier',return_value='default') as update:
                    self.assertIn('OFF',self.relay_command(cmd,session))
                update.assert_called_once();self.assertEqual(saved(self.state,session),'default')

    def test_unknown_or_wrong_ack_never_saves_ultrafast_success(self):
        for actual in ['priority','default',None,'unknown',{'tier':'ultrafast'}]:
            with self.subTest(ack=actual):
                session=self.relay.open_session('project',new=True)
                with patch.object(self.host,'set_service_tier',return_value=actual):
                    self.assertIn('did not match',self.relay_command('/ultrafast on',session))
                self.assertIsNone(saved(self.state,session))
                self.assertEqual(self.host.calls,[])

    def test_lost_ack_requires_matching_ultrafast_recorded_settings(self):
        for actual,success in [('ultrafast',True),('priority',False),('default',False),(None,False)]:
            with self.subTest(observed=actual):
                session=self.relay.open_session('project',new=True);self.host.observer=SimpleNamespace()
                with patch('intern_personal.speed.observed',side_effect=[('default',True),(actual,True)]), \
                        patch.object(self.host,'set_service_tier',side_effect=TimeoutError('private')):
                    result=self.relay_command('/ultrafast on',session)
                self.assertIn('confirmed from' if success else 'not confirmed',result)
                self.assertEqual(saved(self.state,session),'ultrafast' if success else None)
                self.assertNotIn('private',result)

    def test_already_on_uses_observation_without_catalogue_or_settings_update(self):
        session=self.relay.open_session('project',new=True);self.host.observer=SimpleNamespace()
        with patch('intern_personal.speed.observed',return_value=('ultrafast',True)), \
                patch.object(self.host,'models') as models,patch.object(self.host,'set_service_tier') as update:
            self.assertIn('already ON',self.relay_command('/ultrafast on',session))
        models.assert_not_called();update.assert_not_called()

    def test_viewer_can_inspect_but_not_enable_ultrafast(self):
        session=self.relay.open_session('project',new=True)
        self.state.db.execute("INSERT INTO workspace_access VALUES('ou_viewer','project','viewer')")
        for i,text in enumerate(['/ultrafast','/ultrafast on','/ultrafast off']):
            self.assertEqual(self.relay.receive(self.event(text,session['chat_id'],'om_denied_'+str(i),sender='ou_viewer')),'rejected_sender')
        self.assertEqual(self.relay.receive(self.event('/ultrafast STATUS',session['chat_id'],'om_read',sender='ou_viewer')),'accepted')

    def test_settings_replies_and_status_do_not_wait_for_git_publication(self):
        session=self.relay.open_session('project',new=True)
        for text in ['/ultrafast on','/ultrafast status','/ultrafast off','/fast on','/fast status']:
            with self.subTest(command=text),patch.object(self.tracker,'queue_record',side_effect=AssertionError('Synchronous Git')):
                reply=self.relay_command(text,session)
                self.assertNotIn('Synchronous Git',reply)
                event=self.state.db.execute('SELECT * FROM events ORDER BY seq DESC LIMIT 1').fetchone()
                self.assertEqual(event['state'],'complete')
                self.assertEqual(self.state.db.execute('SELECT phase FROM turn_records WHERE seq=?',(event['seq'],)).fetchone()[0],'complete')
        self.assertEqual(self.host.calls,[])

    def test_model_change_rejects_losing_selected_ultrafast(self):
        session=self.relay.open_session('project',new=True);save(self.state,session,'ultrafast')
        self.state.set_model(session['id'],'example-model','medium')
        with patch.object(self.host,'models',return_value=speed_fixture.CATALOG):
            self.assertIn('does not advertise Ultrafast',self.relay_command('/reasoning high',session))
        self.assertEqual(self.state.session(session['id'])['effort'],'medium')
        self.assertEqual(saved(self.state,session),'ultrafast')

    def test_portable_settings_roundtrip_retains_ultrafast(self):
        session=self.relay.open_session('project',new=True);save(self.state,session,'ultrafast')
        snapshot=portable_settings.snapshot(self.state,'project')
        portable_settings.validate(snapshot,{session['id']})
        self.assertEqual(snapshot['conversations'][0]['fast']['tier'],'ultrafast')
        self.state.db.execute('DELETE FROM session_service_tiers')
        portable_settings.apply(self.state,snapshot)
        self.assertEqual(saved(self.state,session),'ultrafast')

    def test_ultrafast_observation_respects_newer_preference_and_normalization(self):
        session=self.relay.open_session('project',new=True);save(self.state,session,'priority')
        point=self.state.db.execute('SELECT updated_at FROM session_service_tiers').fetchone()[0]
        save_observed(self.state,session,'ultrafast',datetime.fromtimestamp(point-1,timezone.utc).isoformat())
        self.assertEqual(saved(self.state,session),'priority')
        save_observed(self.state,session,'ultrafast',datetime.fromtimestamp(point+1,timezone.utc).isoformat())
        self.assertEqual(saved(self.state,session),'ultrafast')
        for left,right,expected in [('priority','fast',True),(None,'default',True),('ultrafast','default',False),('ultrafast','priority',False),('ultrafast','ultrafast',True),('unknown','unknown',False)]:
            self.assertEqual(same_tier(left,right),expected)
        self.assertFalse(known_tier({}));self.assertFalse(same_tier({},'default'))

    def test_ultrafast_flows_to_real_bridge_without_rewriting_owner_prompt(self):
        session=self.relay.open_session('project',new=True);host=CodexHost(self.state,tracker=self.tracker)
        save(self.state,session,'ultrafast')
        self.assertEqual(host.thread_params(session)['serviceTier'],'ultrafast')
        requests=[]
        class Wire:
            def __init__(inner):inner.count=0
            def request(inner,method,params):
                requests.append((method,params));return {'turn':{'id':'fixture-turn'}}
            def notification(inner,timeout):
                inner.count+=1
                if inner.count==1:return {'method':'item/completed','params':{'item':{'type':'agentMessage','id':'answer','text':'Done','phase':'final_answer'}}}
                return {'method':'turn/completed','params':{'turn':{'id':'fixture-turn','status':'completed'}}}
        text='  Original owner words\nunchanged  '
        host._turn(Wire(),session,session['thread_id'],text)
        self.assertEqual(requests[0][1]['serviceTier'],'ultrafast')
        self.assertEqual(requests[0][1]['input'],[{'type':'text','text':text}])

    def test_settings_api_requires_exact_ultrafast_ack_and_always_closes(self):
        session=self.relay.open_session('project',new=True);host=CodexHost(self.state,tracker=self.tracker)
        for actual in ['ultrafast','priority','default',None,{}]:
            with self.subTest(actual=actual):
                wire=SimpleNamespace(request=lambda *a,**k:{},close=lambda:None,notification=lambda *a:{
                    'method':'thread/settings/updated','params':{'threadId':session['thread_id'],'threadSettings':{'serviceTier':actual}}})
                with patch.object(host,'ensure_started'),patch.object(host,'connect',return_value=wire),patch.object(wire,'close') as close:
                    if actual=='ultrafast':self.assertEqual(host.set_service_tier(session,'ultrafast'),'ultrafast')
                    else:
                        with self.assertRaises(BridgeError):host.set_service_tier(session,'ultrafast')
                    close.assert_called_once()


class NativeUltrafastTests(unittest.TestCase):
    setUp = speed_fixture.NativeFastTests.setUp
    event = speed_fixture.NativeFastTests.event
    message = speed_fixture.NativeFastTests.message
    attach = speed_fixture.NativeFastTests.attach
    prepare = speed_fixture.NativeFastTests.prepare

    def test_native_ultrafast_is_observed_but_not_toggled_with_fast_keys(self):
        item=self.prepare('ultrafast')
        self.assertEqual(observed(self.bridge,self.session),('ultrafast',True))
        with patch('intern_personal.terminal_bridge.verify_pane'),patch('intern_personal.terminal_bridge.tmux') as keys:
            result=command(self.relay,dict(item,text='/ultrafast on'),False)
            self.assertIn('already ON',result)
            self.assertIn('Ultrafast mode: ON',command(self.relay,dict(item,text='/ultrafast status'),False))
        keys.assert_not_called()
        with patch('intern_personal.terminal_bridge.empty_prompt'),patch('intern_personal.terminal_bridge.tmux') as keys:
            with self.assertRaisesRegex(Invalid,'no verified Ultrafast'):
                command(self.relay,dict(item,seq=902,text='/ultrafast off'),False)
        keys.assert_not_called()

    def test_no_invented_native_ultrafast_command_is_ever_pasted(self):
        item=self.prepare('default');self.relay.host.models=lambda session=None:CATALOG
        with patch('intern_personal.terminal_bridge.empty_prompt'),patch('intern_personal.terminal_bridge.tmux') as keys:
            with self.assertRaisesRegex(Invalid,'no verified Ultrafast'):
                command(self.relay,dict(item,text='/ultrafast on'),False)
        keys.assert_not_called()

    def test_busy_native_ultrafast_preserves_the_turn(self):
        item=self.prepare('default',active=True);self.relay.host.models=lambda session=None:CATALOG
        with patch('intern_personal.terminal_bridge.tmux') as keys:
            with self.assertRaisesRegex(Invalid,'busy'):
                command(self.relay,dict(item,text='/ultrafast on'),False)
        keys.assert_not_called()


if __name__=='__main__':unittest.main()
