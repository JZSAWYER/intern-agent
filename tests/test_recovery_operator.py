from pathlib import Path
import unittest

from intern_personal.auto_recovery import validate
from intern_personal.core import Invalid
from intern_personal.flow_watchdog import alert
from intern_personal.live_state import write_private
from intern_personal.prompt_context import publish,native_output
from intern_personal.tutorial_workflow import REMINDER,configure
from intern_personal.workspace_tracking import WorkspaceTracking
import test_auto_recovery as recovery_fixtures
import test_tracking as tracking_fixtures


class OperatorRoutingTests(unittest.TestCase):
    setUp=recovery_fixtures.RecoveryTests.setUp
    event=recovery_fixtures.RecoveryTests.event
    setup_policy=recovery_fixtures.RecoveryTests.setup_policy
    failure=recovery_fixtures.RecoveryTests.failure
    due=recovery_fixtures.RecoveryTests.due
    run_continuation=recovery_fixtures.RecoveryTests.run_continuation

    def policy(self,manual,operator):
        self.setup_policy(manual)
        config=self.state.config();config['auto_recovery']['incident_session']=operator['id']
        write_private(self.state.root/'live.json',config)
        self.state.db.execute('INSERT INTO recovery_session_settings VALUES(?,0)',(manual['id'],))

    def test_new_target_overrides_legacy_debug_without_fallback(self):
        self.assertEqual(validate({'auto_recovery':{'debug_session':'s_manual','incident_session':'s_operator'}})['incident_session'],'s_operator')
        self.assertIsNone(validate({'auto_recovery':{'debug_session':'s_manual','incident_session':None}})['incident_session'])
        self.assertEqual(validate({'auto_recovery':{'debug_session':'s_legacy'}})['incident_session'],'s_legacy')
        with self.assertRaises(Invalid):validate({'auto_recovery':{'incident_session':'unknown'}})

    def test_exhaustion_and_flow_incidents_go_only_to_operator_while_manual_busy(self):
        manual=self.relay.open_session('manual');operator=self.relay.open_session('recovery-sentinel');self.policy(manual,operator)
        self.host.active[manual['id']]=(manual['thread_id'],'owner-work')
        session,item,episode=self.failure()
        for _ in range(3):self.due();self.run_continuation();self.recovery.scan()
        mail=self.state.db.execute("SELECT * FROM bot_mailbox WHERE kind='incident'").fetchone()
        self.assertEqual(mail['recipient'],operator['id']);self.assertEqual(mail['state'],'queued')
        self.assertEqual(len(self.host.calls),3)
        self.assertEqual(self.run_continuation(success=True),'complete');self.recovery.scan()
        self.assertTrue(any('Recovery Sentinel update' in text and chat==session['chat_id'] for chat,text,key in self.api.messages))
        alert(self.relay,item,'test_delivery','Inspect delivery',debug=True)
        self.assertEqual(self.state.db.execute("SELECT recipient FROM bot_mailbox WHERE episode LIKE 'flow_%'").fetchone()[0],operator['id'])
        self.assertFalse(self.state.db.execute('SELECT 1 FROM bot_mailbox WHERE recipient=?',(manual['id'],)).fetchone())
        self.assertEqual(self.host.active[manual['id']][1],'owner-work')

    def test_manual_debug_failure_never_gets_automatic_continuation(self):
        manual=self.relay.open_session('manual');operator=self.relay.open_session('recovery-sentinel');self.policy(manual,operator)
        session,item,episode=self.failure(manual)
        self.assertIsNone(episode);self.recovery.scan()
        self.assertFalse(self.state.db.execute('SELECT 1 FROM recovery_runs').fetchone())
        self.assertEqual(self.host.calls,[])

    def test_role_is_scoped_reference_context_without_rewriting_owner_input(self):
        manual=self.relay.open_session('manual');operator=self.relay.open_session('recovery-sentinel');self.policy(manual,operator)
        controller=self.relay.attach_controller('manual');configure(self.state,[manual['id'],controller['id']])
        before=[tuple(row) for row in self.state.db.execute('SELECT * FROM events')]
        for s in [manual,controller]:
            self.assertNotIn('You are Recovery Sentinel',publish(self.tracker,s)['intern-agent.workspace']['value'])
        refs=publish(self.tracker,operator)
        self.assertIn('docs/RECOVERY_OPERATOR.md',refs['intern-agent.workspace']['value'])
        self.assertEqual(refs['intern-agent.workspace']['kind'],'application')
        self.assertNotIn(REMINDER,refs['intern-agent.workspace']['value'])
        event=dict(hook_event_name='UserPromptSubmit',session_id=operator['thread_id'],prompt='  unchanged owner input\n')
        original=dict(event)
        output=native_output(self.state.runtime/'prompt-context'/(operator['id']+'.json'),event)
        self.assertEqual(event,original)
        self.assertNotIn(event['prompt'],output['hookSpecificOutput']['additionalContext'])
        self.assertEqual(before,[tuple(row) for row in self.state.db.execute('SELECT * FROM events')])

    def test_operator_exhaustion_never_falls_back_to_manual_or_itself(self):
        manual=self.relay.open_session('manual');operator=self.relay.open_session('recovery-sentinel');self.policy(manual,operator)
        session,item,episode=self.failure(operator)
        for _ in range(3):self.due();self.run_continuation();self.recovery.scan()
        row=self.state.db.execute("SELECT * FROM bot_mailbox WHERE kind='incident'").fetchone()
        self.assertEqual(row['state'],'owner_review');self.assertIsNone(row['event_seq'])
        self.assertEqual(len(self.host.calls),3)
        self.assertFalse(self.state.db.execute('SELECT 1 FROM events WHERE session=?',(manual['id'],)).fetchone())


class OperatorWorkspaceTests(unittest.TestCase):
    setUp=tracking_fixtures.TrackingTests.setUp
    raw=tracking_fixtures.TrackingTests.raw
    attach=tracking_fixtures.TrackingTests.attach

    def test_new_operator_can_fork_published_relay_code_on_one_own_branch(self):
        self.raw(self.source,'checkout','-qb','feature/relay')
        (self.source/'relay.py').write_text('ready = True\n')
        self.raw(self.source,'add','relay.py');self.raw(self.source,'commit','-qm','Published relay code')
        self.raw(self.source,'push','-q',str(self.remote),'feature/relay')
        self.raw(self.source,'update-ref','refs/remotes/origin/feature/relay','HEAD')
        self.attach();session,_=self.state.reserve_session('project')
        intern=WorkspaceTracking.prepare_session(self.tracker,session,mode='bot_branch',base_ref='refs/remotes/origin/feature/relay')
        self.assertEqual(intern['code_branch'],intern['home_branch']);self.assertEqual(intern['metadata_mode'],'bot_branch')
        self.assertEqual((Path(intern['code_path'])/'relay.py').read_text(),'ready = True\n')
        self.assertEqual(self.raw(self.remote,'rev-parse','main'),self.original)

    def test_unpublished_operator_baseline_cannot_create_a_branch(self):
        self.raw(self.source,'checkout','-qb','feature/local')
        (self.source/'relay.py').write_text('unpublished = True\n')
        self.raw(self.source,'add','relay.py');self.raw(self.source,'commit','-qm','Local code only')
        self.raw(self.source,'update-ref','refs/remotes/origin/feature/local','HEAD')
        self.attach();session,_=self.state.reserve_session('project')
        with self.assertRaises(Invalid):
            WorkspaceTracking.prepare_session(self.tracker,session,mode='bot_branch',base_ref='refs/remotes/origin/feature/local')
        self.assertIsNone(self.tracker.intern(session))
        self.assertEqual(self.raw(self.remote,'rev-parse','main'),self.original)
