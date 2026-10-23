"""Owner model choices survive stale observers, busy turns and reconnects."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import test_live as fixture
import test_session_hosts as hosts_fixture
from intern_personal import model_preferences as preferences
from intern_personal.codex_bridge import CodexHost,BridgeError
from intern_personal.live_state import LiveState,write_private
from intern_personal.steering import Steering


class ModelPreferenceTests(unittest.TestCase):
    setUp=fixture.LiveTests.setUp
    event=fixture.LiveTests.event

    def worker(self):return Steering(self.state,host=self.host,terminal=self.relay.terminal,api=self.api)

    def request(self,session,text,mid='om_model',chat=None):
        self.relay.receive(self.event(text,chat or session['chat_id'],mid))
        return dict(self.state.db.execute('SELECT * FROM events WHERE message_id=?',(mid,)).fetchone())

    def test_legacy_observer_cannot_overwrite_an_explicit_preference(self):
        session=self.relay.open_session('project')
        preferences.save(self.state,session['id'],'alternate-model','medium',10)
        for _ in range(4):self.state.set_model(session['id'],'example-model','high')
        self.assertEqual(self.state.session(session['id'])['effort'],'medium')
        self.assertEqual(self.state.session(session['id'])['model'],'alternate-model')
        preferences.save(self.state,session['id'],'example-model','high',11)
        self.assertEqual(self.state.session(session['id'])['model'],'example-model')
        preferences.save(self.state,session['id'],'alternate-model','medium',9)
        self.assertEqual(self.state.session(session['id'])['model'],'example-model')

    def test_reopen_keeps_owner_preference_and_native_terminal_remains_authoritative(self):
        session=self.relay.open_session('project');preferences.save(self.state,session['id'],'alternate-model','medium',10)
        reopened=LiveState(self.temp.name);self.addCleanup(reopened.close)
        reopened.set_model(session['id'],'example-model','high')
        self.assertEqual(reopened.session(session['id'])['effort'],'medium')
        self.state.db.execute('INSERT INTO terminal_attachments(session,config_path) VALUES(?,?)',(session['id'],'native-fixture'))
        self.state.set_model(session['id'],'example-model','high')
        self.assertEqual(self.state.session(session['id'])['effort'],'high')

    def test_busy_conversation_gets_settings_without_cancelling_or_running_an_agent(self):
        session=self.relay.open_session('project');worker=self.worker();preferences.poll(worker)
        work=self.request(session,'Keep researching','om_research');self.state.transition(work['seq'],'running')
        command=self.request(session,'/model alternate-model medium')
        self.assertEqual(command['state'],'awaiting_terminal')
        self.assertIsNone(self.state.claim('control'))
        preferences.poll(worker)
        event=self.state.db.execute('SELECT * FROM events WHERE seq=?',(command['seq'],)).fetchone()
        self.assertEqual(event['state'],'complete');self.assertIn('Model selected: alternate-model',event['result'])
        self.assertEqual(self.state.db.execute('SELECT state FROM events WHERE seq=?',(work['seq'],)).fetchone()[0],'running')
        self.assertEqual(self.host.calls,[])
        sent=len(self.api.messages);preferences.poll(worker);self.assertEqual(len(self.api.messages),sent)

    def test_adopts_only_unclaimed_old_commands_and_preserves_existing_reviews(self):
        session=self.relay.open_session('project');command=self.request(session,'/reasoning medium')
        review=self.request(session,'/model alternate-model medium','om_review')
        self.state.transition(review['seq'],'uncertain_delivery',result='Saved review')
        before=tuple(self.state.db.execute('SELECT * FROM events WHERE seq=?',(review['seq'],)).fetchone())
        preferences.poll(self.worker())
        self.assertEqual(self.state.db.execute('SELECT state FROM events WHERE seq=?',(command['seq'],)).fetchone()[0],'complete')
        self.assertEqual(tuple(self.state.db.execute('SELECT * FROM events WHERE seq=?',(review['seq'],)).fetchone()),before)

    def test_management_target_and_second_bot_keep_separate_settings(self):
        first=self.relay.open_session('project');second=self.relay.open_session('other');worker=self.worker();preferences.poll(worker)
        self.request(first,'/model '+second['id']+' alternate-model medium',chat='oc_manager')
        preferences.poll(worker)
        self.assertIsNone(self.state.session(first['id'])['model'])
        self.assertEqual(self.state.session(second['id'])['effort'],'medium')

    def test_invalid_model_or_revoked_owner_cannot_change_settings(self):
        session=self.relay.open_session('project');worker=self.worker();preferences.poll(worker)
        item=self.request(session,'/model missing medium');preferences.poll(worker)
        self.assertIsNone(self.state.session(session['id'])['model'])
        self.assertNotIn('Model selected:',self.state.db.execute('SELECT result FROM events WHERE seq=?',(item['seq'],)).fetchone()[0])
        revoked=self.request(session,'/model alternate-model medium','om_revoked')
        self.state.db.execute("UPDATE identity SET owner='ou_changed'")
        preferences.poll(worker)
        self.assertIsNone(self.state.session(session['id'])['model'])
        self.assertEqual(self.state.db.execute('SELECT state FROM events WHERE seq=?',(revoked['seq'],)).fetchone()[0],'cancelled_policy_changed')

    def test_uncertain_acknowledgement_is_not_resent_or_reexecuted(self):
        session=self.relay.open_session('project');worker=self.worker();preferences.poll(worker)
        item=self.request(session,'/model alternate-model medium');self.api.fail=True
        preferences.poll(worker);self.api.fail=False;preferences.poll(worker)
        self.assertEqual(self.state.session(session['id'])['effort'],'medium')
        self.assertEqual(self.state.db.execute('SELECT state FROM events WHERE seq=?',(item['seq'],)).fetchone()[0],'uncertain_delivery')
        self.assertEqual(self.host.calls,[])

    def test_history_adoption_uses_successful_choices_not_pending_or_failed_requests(self):
        session=self.relay.open_session('project')
        success=self.request(session,'/model alternate-model medium')
        self.state.transition(success['seq'],'complete',result='Model selected: alternate-model\nReasoning effort: medium\nSaved.',receipt='om_success')
        self.request(session,'/model example-model high','om_pending')
        preferences.adopt(self.state)
        self.assertEqual(self.state.session(session['id'])['effort'],'medium')
        preferences.adopt(self.state);self.state.set_model(session['id'],'example-model','high')
        self.assertEqual(self.state.session(session['id'])['model'],'alternate-model')

    def test_stale_worker_selection_refreshes_model_without_replacing_the_thread(self):
        stale=self.relay.open_session('project');host=CodexHost(self.state,tracker=self.tracker)
        preferences.save(self.state,stale['id'],'alternate-model','medium',10)
        self.assertEqual(host.model_selection(stale),dict(model='alternate-model',effort='medium'))
        self.assertEqual(stale['thread_id'],self.state.session(stale['id'])['thread_id'])
        self.assertEqual(host.thread_params(stale)['config']['model_reasoning_effort'],'medium')

    def test_an_older_pending_command_cannot_reset_a_newer_saved_choice_or_backend(self):
        session=self.relay.open_session('project');worker=self.worker()
        old=self.request(session,'/model alternate-model medium')
        newer=self.request(session,'/model example-model high','om_newer_model')
        preferences.save(self.state,session['id'],'example-model','high',newer['seq'])
        self.state.transition(newer['seq'],'complete',result='Model selected: example-model\nReasoning effort: high\nSaved.',receipt='om_newer')
        with patch.object(self.host,'set_model_settings',create=True) as update:
            preferences.poll(worker)
        update.assert_not_called()
        self.assertEqual(self.state.session(session['id'])['model'],'example-model')
        self.assertIn('superseded',self.state.db.execute('SELECT result FROM events WHERE seq=?',(old['seq'],)).fetchone()[0])

    def test_backend_setting_confirmation_is_fenced_to_thread_and_never_starts_a_turn(self):
        session=self.relay.open_session('project');host=CodexHost(self.state,tracker=self.tracker);calls=[]
        notices=iter([{'method':'thread/settings/updated','params':{'threadId':'wrong','threadSettings':dict(model='alternate-model',effort='medium')}},
                      {'method':'thread/settings/updated','params':{'threadId':session['thread_id'],'threadSettings':dict(model='alternate-model',effort='medium')}}])
        class Wire:
            def request(self,method,params,**kwargs):calls.append((method,params));return {}
            def notification(self,*args):return next(notices)
            def close(self):pass
        with patch.object(host,'ensure_started'),patch.object(host,'connect',return_value=Wire()):
            self.assertTrue(host.set_model_settings(session,'alternate-model','medium'))
        self.assertEqual([m for m,_ in calls],['thread/resume','thread/settings/update'])
        self.assertEqual(calls[-1][1],dict(threadId=session['thread_id'],model='alternate-model',effort='medium'))

    def test_new_session_defaults_do_not_replace_existing_explicit_choices(self):
        old=self.relay.open_session('project');preferences.save(self.state,old['id'],'alternate-model','medium',10)
        config=self.state.config();config.update(model='example-model',reasoning_effort='xhigh')
        write_private(self.state.root/'live.json',config);preferences.configure_defaults(self.state)
        new=self.relay.open_session('other')
        selected=self.state.session(new['id'])
        self.assertEqual((selected['model'],selected['effort']),('example-model','xhigh'))
        self.assertEqual(self.state.session(old['id'])['effort'],'medium')

    def test_cached_host_and_new_thread_params_use_the_current_global_defaults(self):
        host=CodexHost(self.state,tracker=self.tracker)
        config=self.state.config();config.update(model='alternate-model',reasoning_effort='medium')
        write_private(self.state.root/'live.json',config);preferences.configure_defaults(self.state)
        self.assertEqual(host.model_selection(),dict(model='alternate-model',effort='medium'))
        new=self.relay.open_session('project')
        params=host.thread_params(new)
        self.assertEqual((params['model'],params['config']['model_reasoning_effort']),('alternate-model','medium'))

    def test_default_seed_survives_reopening_without_rewriting_existing_sessions(self):
        old=self.relay.open_session('project')
        config=self.state.config();config['reasoning_effort']='xhigh'
        write_private(self.state.root/'live.json',config);preferences.configure_defaults(self.state)
        reopened=LiveState(self.temp.name);self.addCleanup(reopened.close)
        new,_=reopened.reserve_session('project',new=True)
        self.assertEqual(reopened.session(new['id'])['effort'],'xhigh')
        self.assertIsNone(reopened.session(old['id'])['effort'])


class ModelObserverTests(unittest.TestCase):
    setUp=hosts_fixture.SessionHostTests.setUp
    emit=hosts_fixture.SessionHostTests.emit
    legacy=hosts_fixture.SessionHostTests.legacy
    prepared=hosts_fixture.SessionHostTests.prepared
    observe=hosts_fixture.SessionHostTests.observe
    event=hosts_fixture.SessionHostTests.event

    def test_managed_observation_records_actual_settings_without_resetting_saved_selection(self):
        observer,path,_=self.observe()
        self.event(path,'thread_settings_applied',thread_id=self.session['thread_id'],thread_settings=dict(model='fixture-model',reasoning_effort='high'))
        observer.poll(self.session)
        self.state.set_model(self.session['id'],'fixture-model','xhigh')
        for _ in range(4):snapshot=observer.poll(self.session)
        self.assertEqual(snapshot['effort'],'high')
        self.assertEqual(self.state.session(self.session['id'])['effort'],'xhigh')


if __name__=='__main__':unittest.main()
