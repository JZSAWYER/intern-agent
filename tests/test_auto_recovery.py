import json
import time
import unittest
from unittest.mock import patch

import test_live as fixtures
import test_terminal as native_fixtures
from intern_personal.auto_recovery import CONTINUE, Recovery, cancel, classify, command, observe, status
from intern_personal.codex_bridge import BridgeError, CodexHost, TurnFailed
from intern_personal.core import Invalid
from intern_personal.live_state import write_private


class RecoveryTests(unittest.TestCase):
    setUp=fixtures.LiveTests.setUp
    event=fixtures.LiveTests.event

    def setup_policy(self,debug=None,**changes):
        config=self.state.config()
        config['auto_recovery']={'enabled':True,'max_attempts':3,'backoff_seconds':[30,120,300],**changes}
        if debug:config['auto_recovery']['debug_session']=debug['id']
        write_private(self.state.root/'live.json',config)
        self.recovery=Recovery(self.relay)
        from intern_personal.workspace_tracking import WorkspaceTracking
        self.tracker.clean=lambda text:WorkspaceTracking.clean(self.tracker,text)
        def turn_state(session):
            row=self.state.db.execute('SELECT * FROM codex_outcomes WHERE session=?',(session['id'],)).fetchone()
            return dict(row) if row else None
        self.host.turn_state=turn_state

    def failure(self,session=None,receipt='om_receipt',error=None):
        session=session or self.relay.open_session('project')
        self.relay.receive(self.event('Original owner prompt',session['chat_id'],'om_original_'+session['id']))
        item=self.state.claim('agent')
        self.state.ledger.evidence(item['seq'],'Codex observed turn/completed status failed')
        self.state.transition(item['seq'],'complete',result='Observed failure',receipt=receipt)
        self.state.ledger.update(item,'complete',activity='failed',sync='synced')
        observe(self.state,session,'failed-root','failed',error or {'codexErrorInfo':'serverOverloaded','message':'private details'},item['seq'])
        episode=self.state.db.execute('SELECT * FROM recovery_episodes WHERE session=?',(session['id'],)).fetchone()
        return session,item,dict(episode) if episode else None

    def due(self):
        self.state.db.execute('UPDATE recovery_episodes SET due_at=0')
        self.recovery.scan()

    def run_continuation(self,success=False):
        # This fixture emits the real adapter's authoritative boundaries. It
        # never makes a subprocess, network request or replacement thread.
        def execute(session,text,**kwargs):
            item=dict(self.state.db.execute("SELECT * FROM events WHERE session=? AND state='running' ORDER BY seq DESC LIMIT 1",(session['id'],)).fetchone())
            turn='continued-'+str(item['seq'])
            self.host.calls.append((session,text))
            observe(self.state,session,turn,'inProgress',seq=item['seq'])
            final='completed' if success else 'failed'
            self.state.ledger.evidence(item['seq'],'Codex observed turn/completed status '+final)
            observe(self.state,session,turn,final,{'codexErrorInfo':'serverOverloaded'},item['seq'])
            if not success:raise TurnFailed({'codexErrorInfo':'serverOverloaded'})
            return 'Continued from saved work'
        with patch.object(self.host,'run',side_effect=execute):
            return self.relay.process_once('agent')

    def test_classifier_only_transient_authoritative_errors(self):
        cases=[('serverOverloaded','capacity'),('rateLimitExceeded','capacity'),('flexUnavailable','capacity'),
            ('internalServerError','service'),('responseStreamDisconnected','network'),
            ('responseTooManyFailedAttempts','network'),('usageLimitExceeded',None),('contextWindowExceeded',None),
            ('unauthorized',None),('sandboxError',None),('badRequest',None),('cyberPolicy',None)]
        for kind,expected in cases:
            with self.subTest(kind=kind):self.assertEqual(classify({'codexErrorInfo':kind,'message':'connection reset'}),expected)
        for code,expected in [(401,None),(403,None),(400,None),(429,'capacity'),(502,'service')]:
            self.assertEqual(classify({'codexErrorInfo':{'httpConnectionFailed':{'httpStatusCode':code}}}),expected)
        self.assertEqual(classify({'codexErrorInfo':'other','message':'error decoding response body'}),'network')
        self.assertIsNone(classify({'codexErrorInfo':'other','message':'quota exceeded; network unavailable'}))
        self.assertIsNone(classify('ordinary model response'))

    def test_default_is_opt_in_and_configuration_is_bounded(self):
        session,item,ep=self.failure()
        self.assertIsNone(ep)
        for policy in [dict(enabled='yes'),dict(enabled=True,max_attempts=0),dict(enabled=True,backoff_seconds=[0]),dict(enabled=True,debug_session='unknown')]:
            config=self.state.config();config['auto_recovery']=policy
            write_private(self.state.root/'live.json',config)
            with self.assertRaises(Invalid):self.state.config()
            config.pop('auto_recovery');write_private(self.state.root/'live.json',config)

    def test_continuation_keeps_original_prompt_thread_task_branch_and_model(self):
        self.setup_policy();session=self.relay.open_session('project')
        self.state.ledger.task(session,'start','Keep researching')
        task=self.state.ledger.selection(session['id'])
        session,item,ep=self.failure(session)
        self.assertEqual(ep['task_id'],task['id'])
        self.assertGreater(ep['due_at'],time.time()+28)
        self.recovery.scan()
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM recovery_runs').fetchone()[0],0)
        self.due();self.assertEqual(self.run_continuation(success=True),'complete');self.recovery.scan()
        self.assertEqual(self.host.calls[0][1],CONTINUE)
        self.assertEqual(self.host.calls[0][0]['thread_id'],session['thread_id'])
        self.assertEqual(self.state.session(session['id']),session)
        self.assertEqual(self.state.db.execute('SELECT text FROM events WHERE seq=?',(item['seq'],)).fetchone()[0],'Original owner prompt')
        self.assertEqual(self.state.ledger.selection(session['id'])['id'],task['id'])
        self.assertEqual(status(self.state,session['id'])['episodes'][0]['state'],'resolved')
        self.assertEqual(self.state.db.execute('SELECT source FROM turn_records ORDER BY seq DESC LIMIT 1').fetchone()[0],'local')

    def test_three_failures_escalate_once_and_debug_reply_returns_to_source(self):
        debug=self.relay.open_session('debug');self.setup_policy(debug)
        session,item,ep=self.failure()
        for attempt,delay in [(1,120),(2,300),(3,None)]:
            self.due();self.assertEqual(self.run_continuation(),'complete');self.recovery.scan()
            episode=self.state.db.execute('SELECT * FROM recovery_episodes WHERE id=?',(ep['id'],)).fetchone()
            self.assertEqual(episode['attempts'],attempt)
            if delay:self.assertGreater(episode['due_at'],time.time()+delay-2)
        self.assertEqual(episode['state'],'escalated')
        self.recovery.scan();self.recovery.scan()
        self.assertEqual(self.state.db.execute("SELECT count(*) FROM bot_mailbox WHERE kind='incident'").fetchone()[0],1)
        self.assertEqual(len(self.host.calls),3)
        self.assertEqual(self.run_continuation(success=True),'complete');self.recovery.scan()
        reply=self.state.db.execute("SELECT * FROM bot_mailbox WHERE kind='reply'").fetchone()
        self.assertEqual(reply['recipient'],session['id']);self.assertEqual(reply['state'],'delivered')
        self.assertTrue(any(chat==session['chat_id'] and 'Recovery Sentinel update' in text for chat,text,key in self.api.messages))
        self.assertEqual(self.state.session(session['id'])['thread_id'],session['thread_id'])

    def test_restart_keeps_attempt_count_and_does_not_duplicate_queued_work(self):
        self.setup_policy();session,item,ep=self.failure();self.due()
        self.state.recover();self.recovery=Recovery(self.relay)
        self.due();self.due()
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM recovery_runs').fetchone()[0],1)
        self.assertEqual(self.run_continuation(success=True),'complete');self.recovery.scan()
        self.assertEqual(len(self.host.calls),1)
        self.state.recover();self.recovery=Recovery(self.relay);self.due()
        self.assertEqual(len(self.host.calls),1)

    def test_active_turn_and_waiting_approval_never_get_a_continuation(self):
        self.setup_policy();session,item,ep=self.failure()
        self.host.active[session['id']]=(session['thread_id'],'manual-active')
        self.due();self.assertEqual(self.state.db.execute('SELECT count(*) FROM recovery_runs').fetchone()[0],0)
        self.host.active.clear()
        observe(self.state,session,'manual-active','inProgress',seq=999)
        self.due()
        self.assertEqual(status(self.state,session['id'])['episodes'][0]['state'],'superseded')
        self.assertEqual(self.host.calls,[])

    def test_new_owner_prompt_supersedes_waiting_recovery(self):
        self.setup_policy();session,item,ep=self.failure()
        self.relay.receive(self.event('New owner direction',session['chat_id'],'om_new'))
        self.due()
        self.assertEqual(status(self.state,session['id'])['episodes'][0]['state'],'superseded')
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM recovery_runs').fetchone()[0],0)

    def test_stop_cancels_waiting_and_already_queued_recovery(self):
        for queued in (False,True):
            session=self.relay.open_session('queued-'+str(queued));self.setup_policy()
            session,item,ep=self.failure(session)
            if queued:self.due()
            control=dict(sender='ou_owner',chat_id=session['chat_id'],session=session['id'],text='/stop')
            with patch.object(self.host,'interrupt',create=True,side_effect=Invalid('No active turn')):
                self.assertIn('Cancellation',self.relay.control(control))
            self.due();self.assertEqual(self.relay.process_once('agent'),'idle')
            self.assertEqual(status(self.state,session['id'])['episodes'][0]['state'],'cancelled')
            self.assertEqual(self.host.calls,[])

    def test_task_change_or_backend_race_cancels_before_dispatch(self):
        for changed in ('task','backend'):
            session=self.relay.open_session('race-'+changed);self.setup_policy()
            session,item,ep=self.failure(session);self.due()
            if changed=='task':self.state.ledger.task(session,'start','Other task')
            else:self.host.turn_state=lambda s:dict(turn_id='manual-new',status='inProgress')
            self.assertEqual(self.relay.process_once('agent'),'cancelled')
            self.assertEqual(status(self.state,session['id'])['episodes'][0]['state'],'superseded')
            self.assertEqual(self.host.calls,[])

    def test_delivered_git_review_does_not_block_guarded_recovery(self):
        self.setup_policy();session,item,ep=self.failure()
        self.state.transition(item['seq'],'record_blocked')
        self.due();self.assertEqual(self.run_continuation(success=True),'complete')
        self.assertEqual(len(self.host.calls),1)
        self.assertEqual(self.state.db.execute('SELECT state FROM events WHERE seq=?',(item['seq'],)).fetchone()[0],'record_blocked')

    def test_uncertain_execution_or_delivery_is_never_replayed(self):
        self.setup_policy();session,item,ep=self.failure(receipt=None)
        self.state.transition(item['seq'],'uncertain_execution')
        self.due();self.assertEqual(self.host.calls,[])
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM recovery_runs').fetchone()[0],0)
        self.state.transition(item['seq'],'uncertain_delivery',receipt='ambiguous-receipt')
        self.due();self.assertEqual(self.state.db.execute('SELECT count(*) FROM recovery_runs').fetchone()[0],0)

    def test_failed_preflight_escalates_without_running_codex(self):
        self.setup_policy();session,item,ep=self.failure();self.due()
        with patch.object(self.tracker,'record',side_effect=Invalid('Git baseline requires review')),patch.object(self.tracker,'queue_record',return_value=False):
            self.assertEqual(self.relay.process_once('agent'),'record_blocked')
        self.recovery.scan()
        self.assertEqual(status(self.state,session['id'])['episodes'][0]['state'],'escalated')
        self.assertEqual(self.host.calls,[])

    def test_typing_stays_through_backoff_and_clears_after_success(self):
        self.setup_policy();session,item,ep=self.failure()
        self.relay.begin_typing(item);self.relay.cleanup_typing()
        self.assertEqual(self.api.removals,[])
        self.due();self.assertEqual(self.run_continuation(success=True),'complete');self.recovery.scan();self.relay.cleanup_typing()
        self.assertEqual(self.api.removals,self.api.reactions)

    def test_debug_busy_waits_and_self_escalation_does_not_loop(self):
        debug=self.relay.open_session('debug');self.setup_policy(debug)
        session,item,ep=self.failure();self.recovery.escalate(session,ep)
        self.host.active[debug['id']]=('thread-debug','active')
        self.recovery.scan()
        self.assertEqual(self.state.db.execute('SELECT state FROM bot_mailbox').fetchone()[0],'pending')
        self.host.active.clear();self.recovery.scan()
        self.assertEqual(self.state.db.execute('SELECT state FROM bot_mailbox').fetchone()[0],'queued')
        # Cancel this queued fixture and then fail Debug itself.
        self.state.db.execute("UPDATE events SET state='cancelled' WHERE state='queued'")
        debug,item,own=self.failure(debug);self.recovery.escalate(debug,own);self.recovery.scan()
        self.assertEqual(self.state.db.execute('SELECT state FROM bot_mailbox WHERE episode=?',(own['id'],)).fetchone()[0],'owner_review')

    def test_manual_rearm_keeps_monotonic_attempts_and_a_distinct_mailbox_cycle(self):
        debug=self.relay.open_session('debug');self.setup_policy(debug)
        session,item,ep=self.failure()
        for _ in range(3):self.due();self.run_continuation();self.recovery.scan()
        self.run_continuation(success=True);self.recovery.scan()
        control=dict(sender='ou_owner',chat_id=debug['chat_id'],session=debug['id'])
        self.assertIn('bounded',command(self.relay,control,['retry',ep['id']]))
        for _ in range(3):self.due();self.run_continuation();self.recovery.scan()
        self.assertEqual(status(self.state,session['id'])['episodes'][0]['attempts'],6)
        self.assertEqual(self.state.db.execute("SELECT count(*) FROM bot_mailbox WHERE kind='incident' AND episode=?",(ep['id'],)).fetchone()[0],2)

    def test_recovered_debug_completion_is_forwarded_to_original_bot(self):
        debug=self.relay.open_session('debug');self.setup_policy(debug)
        session,item,ep=self.failure();self.recovery.escalate(session,ep);self.recovery.scan()
        self.assertEqual(self.run_continuation(),'complete');self.recovery.scan()
        self.due();self.assertEqual(self.run_continuation(success=True),'complete');self.recovery.scan()
        self.assertTrue(self.state.db.execute("SELECT 1 FROM bot_mailbox WHERE episode=? AND kind='reply' AND state='delivered'",(ep['id'],)).fetchone())

    def test_notice_send_uncertainty_is_retained_across_restart(self):
        self.setup_policy();session,item,ep=self.failure()
        self.api.fail=True;self.recovery.scan();self.api.fail=False;self.state.recover();self.recovery.scan()
        self.assertEqual(self.state.db.execute('SELECT state FROM recovery_notices').fetchone()[0],'uncertain')
        self.assertFalse(any('Same-conversation recovery will try' in text for chat,text,key in self.api.messages))

    def test_privacy_and_recovery_controls_are_scoped(self):
        self.setup_policy();session,item,ep=self.failure(error={'message':'network reset with sensitive marker','codexErrorInfo':'other'})
        self.recovery.scan()
        self.assertNotIn('sensitive marker',json.dumps(status(self.state)))
        self.assertFalse(any('sensitive marker' in text for chat,text,key in self.api.messages))
        other=self.relay.open_session('other')
        with self.assertRaises(Invalid):command(self.relay,dict(sender='ou_owner',chat_id=session['chat_id'],session=session['id']),[other['id'],'off'])
        with self.assertRaises(Invalid):command(self.relay,dict(sender='ou_guest',chat_id=session['chat_id'],session=session['id']),['off'])
        command(self.relay,dict(sender='ou_owner',chat_id=session['chat_id'],session=session['id']),['off'])
        self.assertFalse(status(self.state,session['id'])['enabled']);self.due();self.assertEqual(self.host.calls,[])

    def test_bootstrap_inspects_existing_failed_thread_without_resuming_it(self):
        session=self.relay.open_session('project');session,item,ep=self.failure(session)
        self.setup_policy()
        self.state.db.execute('INSERT INTO terminal_turns VALUES(?,?,?,?,?)',(session['id'],'failed-root',item['seq'],'Observed failure','task_failed'))
        self.state.db.execute('DELETE FROM codex_outcomes')
        self.host.turn_state=lambda s:dict(turn_id='failed-root',status='failed',error={'codexErrorInfo':'serverOverloaded'})
        self.recovery.scan();self.recovery.scan()
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM recovery_episodes').fetchone()[0],1)
        self.assertEqual(self.host.calls,[])

    def test_new_manual_turn_does_not_inherit_the_previous_event_sequence(self):
        self.setup_policy();session,item,ep=self.failure()
        observe(self.state,session,'new-manual','inProgress',at=time.time()+2)
        observe(self.state,session,'old-turn','failed',{'message':'network'},item['seq'],at=1)
        outcome=self.state.db.execute('SELECT * FROM codex_outcomes').fetchone()
        self.assertEqual(outcome['turn_id'],'new-manual');self.assertIsNone(outcome['event_seq'])

    def test_pause_and_new_thread_cancel_waiting_recovery(self):
        for changed in ('paused','thread'):
            session=self.relay.open_session('change-'+changed);self.setup_policy()
            session,item,ep=self.failure(session)
            self.state.set_session(session['id'],**(dict(state='paused') if changed=='paused' else dict(thread_id='different-thread')))
            self.due()
            self.assertIn(status(self.state,session['id'])['episodes'][0]['state'],{'cancelled','superseded'})
            self.assertEqual(self.host.calls,[])

    def test_branch_changed_since_failure_prevents_dispatch(self):
        self.setup_policy();session,item,ep=self.failure();self.due()
        self.tracker.intern=lambda session:dict(code_branch='different-branch')
        self.assertEqual(self.relay.process_once('agent'),'cancelled')
        self.assertEqual(self.host.calls,[])

    def test_future_sessions_inherit_enabled_global_policy(self):
        self.setup_policy();self.relay.open_session('earlier')
        later=self.relay.open_session('future');session,item,ep=self.failure(later)
        self.assertEqual(ep['session'],later['id']);self.due()
        self.assertEqual(self.run_continuation(success=True),'complete')
        self.assertEqual(self.host.calls[0][0]['id'],later['id'])

    def test_interrupted_or_permanent_failures_do_not_start_recovery(self):
        self.setup_policy()
        session,item,ep=self.failure(error={'codexErrorInfo':'unauthorized','message':'network error'})
        self.assertIsNone(ep)
        observe(self.state,session,'aborted','interrupted',seq=item['seq']);self.due()
        self.assertEqual(self.host.calls,[])

    def test_notice_interrupted_mid_send_is_not_resent(self):
        self.setup_policy();session,item,ep=self.failure()
        self.state.db.execute("UPDATE recovery_notices SET state='sending'")
        self.state.recover();self.recovery.scan()
        self.assertEqual(status(self.state)['notices'],{'uncertain':1})

    def test_owner_change_prevents_incident_delivery_to_debug(self):
        debug=self.relay.open_session('debug');self.setup_policy(debug)
        session,item,ep=self.failure();self.recovery.escalate(session,ep)
        self.state.db.execute("UPDATE identity SET owner='ou_different'")
        self.recovery.scan()
        self.assertEqual(self.state.db.execute('SELECT state FROM bot_mailbox').fetchone()[0],'owner_review')
        self.assertEqual(self.host.calls,[])


class NativeRecoveryTests(unittest.TestCase):
    setUp=native_fixtures.TerminalTests.setUp
    event=native_fixtures.TerminalTests.event
    message=native_fixtures.TerminalTests.message
    attach=native_fixtures.TerminalTests.attach

    def test_native_failed_completion_enrols_same_thread_and_deduplicates(self):
        config=self.state.config();config['auto_recovery']={'enabled':True}
        write_private(self.state.root/'live.json',config)
        self.event('task_started',turn_id='native-failed');self.message('Exact owner words');self.attach()
        self.event('task_complete',turn_id='native-failed',last_agent_message='',error={'message':'connection reset'})
        self.bridge.poll(self.session);self.bridge.poll(self.session)
        row=self.state.db.execute('SELECT * FROM recovery_episodes').fetchone()
        self.assertEqual(row['root_turn'],'native-failed');self.assertEqual(row['thread_id'],self.session['thread_id'])
        self.assertEqual(self.state.db.execute('SELECT text FROM events').fetchone()[0],'Exact owner words')
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM recovery_episodes').fetchone()[0],1)

    def test_old_completion_does_not_overwrite_newer_active_turn_in_one_poll(self):
        self.event('task_started',turn_id='old');self.message('Old request');self.attach()
        self.event('task_complete',turn_id='old',last_agent_message='Old result')
        self.event('task_started',turn_id='new');self.message('New request')
        self.bridge.poll(self.session)
        self.assertEqual(self.state.db.execute('SELECT turn_id,status FROM codex_outcomes').fetchone()[:],('new','inProgress'))


class TransportRecoveryTests(unittest.TestCase):
    setUp=fixtures.LiveTests.setUp

    def test_disconnected_observer_does_not_interrupt_a_still_running_backend(self):
        session=self.relay.open_session('project');host=CodexHost(self.state,tracker=self.tracker)
        class Wire:
            def request(self,method,params):return {'turn':{'id':'still-running'}}
            def notification(self,timeout):raise BridgeError('transport disconnected')
        with patch.object(host,'interrupt') as interrupt:
            with self.assertRaises(BridgeError):host._turn(Wire(),session,session['thread_id'],'Owner prompt')
        interrupt.assert_not_called()
        self.assertEqual(self.state.db.execute('SELECT status FROM codex_outcomes').fetchone()[0],'inProgress')
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM recovery_episodes').fetchone()[0],0)

    def test_latest_turn_query_reads_without_start_or_resume(self):
        session=self.relay.open_session('project');host=CodexHost(self.state,tracker=self.tracker);calls=[]
        class Wire:
            def request(self,method,params,timeout=5):
                calls.append((method,params))
                return {'thread':{'id':session['thread_id'],'status':{'type':'systemError'}}} if method=='thread/read' else {'data':[{'id':'failed','status':'failed','error':{'codexErrorInfo':'serverOverloaded'}}]}
            def close(self):pass
        with patch.object(host,'connect',return_value=Wire()):result=host.turn_state(session)
        self.assertEqual(result['status'],'failed')
        self.assertEqual([method for method,params in calls],['thread/read','thread/turns/list'])
        self.assertFalse(calls[0][1]['includeTurns']);self.assertEqual(calls[1][1]['itemsView'],'notLoaded')


if __name__=='__main__':unittest.main()
