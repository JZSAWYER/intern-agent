import json
import time
import unittest
from unittest.mock import patch

from intern_personal.codex_bridge import CodexHost
import test_live as fixtures


class DeliveryReliabilityTests(unittest.TestCase):
    setUp=fixtures.LiveTests.setUp
    event=fixtures.LiveTests.event

    def project(self):
        session=self.relay.open_session('project')
        self.api.messages.clear()
        return session

    def saved(self,mid='om_saved',state='sync_pending'):
        session=self.project()
        self.relay.receive(self.event('Original request',session['chat_id'],mid))
        item=self.state.claim('agent')
        self.state.transition(item['seq'],state,result='Saved result')
        self.state.ledger.update(item,'result_ready',activity='completed')
        return session,item

    def test_preparation_restart_resumes_saved_result_without_replaying_codex(self):
        session,item=self.saved(state='result_ready')
        claimed=self.state.claim('agent')
        self.assertEqual(claimed['seq'],item['seq'])
        self.assertEqual(self.state.db.execute('SELECT state FROM events WHERE seq=?',(item['seq'],)).fetchone()[0],'preparing_reply')
        self.state.recover()
        self.assertEqual(self.state.db.execute('SELECT state FROM events WHERE seq=?',(item['seq'],)).fetchone()[0],'result_ready')
        self.assertEqual(self.relay.process_once('agent'),'complete')
        self.assertEqual(self.host.calls,[])
        self.assertIn('Saved result',self.api.messages[-1][1])

    def test_preparation_without_saved_result_fails_closed(self):
        session,item=self.saved(state='preparing_reply')
        self.state.db.execute('UPDATE events SET result=NULL WHERE seq=?',(item['seq'],))
        self.state.recover()
        self.assertEqual(self.relay.process_once('agent'),'idle')
        self.assertEqual(self.state.db.execute('SELECT state FROM events').fetchone()[0],'record_blocked')
        self.assertEqual(self.host.calls,[])

    def test_orphan_sync_retry_is_repaired_and_delivery_uses_normal_claim(self):
        session,item=self.saved()
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM sync_retries').fetchone()[0],0)
        with patch.object(self.relay,'process_item',side_effect=AssertionError('Cleanup must not deliver')):
            self.relay.retry_sync()
        self.assertEqual(self.relay.process_once('agent'),'complete')
        self.assertEqual(self.relay.process_once('agent'),'idle')
        self.assertEqual(self.host.calls,[])
        self.assertEqual(len(self.api.messages),1)

    def test_retry_keeps_saved_results_in_fifo_order(self):
        session,first=self.saved()
        self.relay.receive(self.event('Second request',session['chat_id'],'om_later'))
        second=self.state.db.execute("SELECT seq FROM events WHERE message_id='om_later'").fetchone()[0]
        self.state.transition(second,'result_ready',result='Second saved result')
        self.assertIsNone(self.state.claim('agent'))
        self.relay.retry_sync()
        self.assertEqual(self.relay.process_once('agent'),'complete')
        self.assertIn('Saved result',self.api.messages[0][1])
        self.assertEqual(self.relay.process_once('agent'),'complete')
        self.assertIn('Second saved result',self.api.messages[1][1])
        self.assertEqual(self.host.calls,[])

    def test_preparation_reservation_blocks_a_second_worker(self):
        session=self.project()
        self.relay.receive(self.event('Work',session['chat_id'],'om_race'))
        def preparing(*args):
            self.assertEqual(self.state.db.execute('SELECT state FROM events').fetchone()[0],'preparing_reply')
            self.assertIsNone(self.state.claim('agent'))
        with patch.object(self.tracker,'publish_code',side_effect=preparing):
            self.assertEqual(self.relay.process_once('agent'),'complete')
        self.assertEqual(len(self.host.calls),1);self.assertEqual(len(self.api.messages),1)

    def test_uncertain_network_send_stays_blocked_and_is_never_auto_retried(self):
        session,item=self.saved(state='result_ready')
        self.api.fail=True
        self.assertEqual(self.relay.process_once('agent'),'uncertain_delivery')
        self.api.fail=False;self.state.recover();self.relay.retry_sync()
        self.assertEqual(self.relay.process_once('agent'),'idle')
        self.assertEqual(self.host.calls,[]);self.assertEqual(self.api.messages,[])

    def test_active_manual_turn_defers_git_without_waiting_or_interrupting(self):
        session,item=self.saved(state='result_ready')
        host=CodexHost(self.state,tracker=self.tracker);self.relay.host=host
        with patch.object(host.observer,'binding',return_value={'snapshot':'{"active":"manual-turn"}'}), \
             patch.object(host.observer,'poll',return_value={'active':'manual-turn'}), \
             patch.object(host.observer.stop,'wait',side_effect=AssertionError('Do not block worker')), \
             patch.object(host,'interrupt',side_effect=AssertionError('Do not interrupt')):
            self.assertEqual(self.relay.process_once('agent'),'sync_pending')
        self.assertEqual(self.api.messages,[])
        self.assertTrue(self.state.db.execute('SELECT 1 FROM sync_retries').fetchone())

    def test_observed_failed_managed_turn_is_reported_without_interrupt_or_replay(self):
        session=self.project()
        self.relay.receive(self.event('Research',session['chat_id'],'om_failure'))
        calls=[]
        class Wire:
            def request(self,method,params,timeout=60):
                calls.append(method)
                return {'turn':{'id':'fixture-turn'}}
            def notification(self,timeout):
                return {'method':'turn/completed','params':{'threadId':session['thread_id'],
                    'turn':{'id':'fixture-turn','status':'failed','error':{'message':'network disconnected private-details'}}}}
            def close(self):pass
        host=CodexHost(self.state,tracker=self.tracker);self.relay.host=host
        with patch.object(host,'ensure_started'),patch.object(host,'ensure_terminal'), \
             patch.object(host,'connect',return_value=Wire()),patch.object(host,'interrupt') as interrupt:
            self.assertEqual(self.relay.process_once('agent'),'complete')
        interrupt.assert_not_called()
        self.assertEqual(calls.count('turn/start'),1)
        self.assertIn('connection error',self.api.messages[-1][1]);self.assertNotIn('private-details',self.api.messages[-1][1])
        self.assertEqual(self.state.db.execute('SELECT activity FROM turn_records').fetchone()[0],'failed')
        self.assertEqual(self.state.db.execute('SELECT kind FROM event_failures').fetchone()[0],'execution_failed')

    def test_sync_failure_and_retry_deadline_are_saved_together(self):
        session,item=self.saved(state='preparing_reply')
        self.assertEqual(self.relay.defer_sync(item,RuntimeError('private details')),'sync_pending')
        retry=self.state.db.execute('SELECT retry_at FROM sync_retries').fetchone()[0]
        self.assertGreater(retry,time.time())
        self.relay.retry_sync()
        self.assertEqual(self.state.db.execute('SELECT state FROM events').fetchone()[0],'sync_pending')

    def test_status_includes_waiting_saved_replies(self):
        session,item=self.saved(state='preparing_reply')
        status=self.state.turn_status(session['id'])
        self.assertIn('preparing_reply',status);self.assertIn('agent completed',status)

    def test_completed_answer_waits_for_git_confirmation_and_is_not_sent_twice(self):
        session,item=self.saved(state='result_ready')
        self.relay.begin_typing(item)
        with patch.object(self.tracker,'publish_code',side_effect=RuntimeError('Git unavailable')):
            self.assertEqual(self.relay.process_once('agent'),'sync_pending')
        saved=self.state.db.execute('SELECT * FROM events WHERE seq=?',(item['seq'],)).fetchone()
        self.assertFalse(saved['receipt']);self.assertEqual(saved['state'],'sync_pending')
        self.assertEqual(len(self.api.messages),0)
        self.assertIn('Git unavailable',self.state.turn_status(session['id']))
        self.assertFalse(self.api.removals)
        self.state.db.execute('UPDATE sync_retries SET retry_at=0')
        self.relay.retry_sync();self.assertEqual(self.relay.process_once('agent'),'complete')
        self.assertEqual(len(self.api.messages),1);self.assertEqual(self.host.calls,[])

    def test_blocked_native_upload_holds_answer_and_retains_blocker(self):
        from intern_personal.core import Invalid
        session,item=self.saved(state='result_ready')
        with patch.object(self.tracker,'publish_code',side_effect=Invalid('Review native changes')):
            self.assertEqual(self.relay.process_once('agent'),'record_blocked')
        self.assertEqual(len(self.api.messages),0)
        self.assertIn('Review native changes',self.state.turn_status(session['id']))
        self.state.resolve(item['seq'],'retry');self.relay.retry_sync()
        self.assertEqual(self.relay.process_once('agent'),'complete')
        self.assertEqual(len(self.api.messages),1)

    def test_unknown_send_during_git_failure_is_not_automatically_repeated(self):
        session,item=self.saved(state='result_ready');self.api.fail=True
        with patch.object(self.tracker,'publish_code',side_effect=RuntimeError('Git unavailable')):
            self.assertEqual(self.relay.process_once('agent'),'sync_pending')
        self.state.db.execute('UPDATE sync_retries SET retry_at=0');self.relay.retry_sync()
        self.assertEqual(self.relay.process_once('agent'),'uncertain_delivery')
        self.api.fail=False;self.relay.retry_sync()
        self.assertEqual(self.relay.process_once('agent'),'idle')
        self.assertEqual(self.api.messages,[])

    def test_failed_recording_before_codex_dispatch_sends_no_task_answer(self):
        session=self.project()
        self.relay.receive(self.event('Task',session['chat_id'],'om_before_run'))
        with patch.object(self.tracker,'record',side_effect=RuntimeError('Git unavailable')), \
             patch.object(self.tracker,'queue_record',return_value=False):
            self.assertEqual(self.relay.process_once('agent'),'sync_pending')
        self.assertEqual(self.api.messages,[]);self.assertEqual(self.host.calls,[])

    def test_legacy_delivered_reviews_allow_guarded_new_execution_without_premature_new_reply(self):
        from intern_personal.core import Invalid
        session,first=self.saved(state='result_ready')
        self.relay.receive(self.event('Second',session['chat_id'],'om_second'))
        second=self.state.db.execute("SELECT seq FROM events WHERE message_id='om_second'").fetchone()[0]
        self.state.transition(second,'result_ready',result='Second saved result')
        self.state.ledger.update(dict(self.state.db.execute('SELECT * FROM events WHERE seq=?',(second,)).fetchone()),'result_ready',activity='completed')
        self.relay.receive(self.event('New work',session['chat_id'],'om_queued'))
        with patch.object(self.tracker,'publish_code',side_effect=Invalid('Review native changes')):
            self.assertEqual(self.relay.process_once('agent'),'record_blocked')
            # Simulate receipts produced by the old, pre-Git reply policy.
            self.state.transition(first['seq'],'record_blocked',receipt='om_legacy_first')
            self.api.send(session['chat_id'],'Saved result','legacy-first')
            self.assertEqual(self.relay.process_once('agent'),'record_blocked')
            self.state.transition(second,'record_blocked',receipt='om_legacy_second')
            self.api.send(session['chat_id'],'Second saved result','legacy-second')
            self.assertEqual(self.relay.process_once('agent'),'record_blocked')
        self.assertEqual(len(self.api.messages),2);self.assertEqual([text for session,text in self.host.calls],['New work'])

    def blocked_and_verified(self,*,proof=True,failed=False,receipt=True,other_session=False):
        from intern_personal.core import Invalid
        session,first=self.saved(state='result_ready')
        with patch.object(self.tracker,'publish_code',side_effect=Invalid('Review old native changes')):
            self.assertEqual(self.relay.process_once('agent'),'record_blocked')
        # This helper models an already delivered historical review, whose
        # receipt remains valid after adopting strict final-response ordering.
        self.state.transition(first['seq'],'record_blocked',receipt='om_legacy_review')
        target=self.relay.open_session('other') if other_session else session
        self.relay.receive(self.event('Later tracked continuation',target['chat_id'],'om_verified'))
        later=dict(self.state.db.execute("SELECT * FROM events WHERE message_id='om_verified'").fetchone())
        start='a'*40;end='b'*40
        self.state.transition(later['seq'],'complete',result='Verified later result',receipt='om_verified_receipt' if receipt else None)
        self.state.ledger.update(later,'complete',activity='failed' if failed else 'completed',sync='synced',
            code_start=start,code_end=end,baseline=json.dumps(dict(head=start,dirty={},staged=[])) if proof else None)
        self.relay.receive(self.event('Fresh owner request',session['chat_id'],'om_new_request'))
        queued=dict(self.state.db.execute("SELECT * FROM events WHERE message_id='om_new_request'").fetchone())
        return session,first,later,queued

    def test_verified_later_completion_releases_new_owner_message_once(self):
        session,old,later,queued=self.blocked_and_verified()
        self.assertEqual(self.relay.process_once('agent'),'complete')
        self.assertEqual(self.relay.process_once('agent'),'idle')
        self.assertEqual([text for session,text in self.host.calls],['Fresh owner request'])
        previous=self.state.db.execute('SELECT state,receipt FROM events WHERE seq=?',(old['seq'],)).fetchone()
        self.assertEqual(previous['state'],'record_blocked');self.assertTrue(previous['receipt'])
        self.assertEqual(self.state.session(session['id'])['thread_id'],session['thread_id'])

    def test_new_dispatch_does_not_depend_on_a_later_successful_turn(self):
        for options in [dict(proof=False),dict(failed=True),dict(receipt=False),dict(other_session=True)]:
            with self.subTest(options=options):
                # Independent ledgers avoid a previous scenario's completion
                # providing unintended proof for the next one.
                temporary=DeliveryReliabilityTests();temporary.setUp()
                try:
                    session,old,later,queued=temporary.blocked_and_verified(**options)
                    claimed=temporary.state.claim('agent')
                    self.assertEqual(claimed['seq'],queued['seq'])
                    self.assertEqual(claimed['text'],'Fresh owner request')
                    self.assertEqual(temporary.host.calls,[])
                finally:temporary.doCleanups()

    def test_new_dispatch_takes_its_own_baseline_instead_of_borrowing_old_proof(self):
        session,old,later,queued=self.blocked_and_verified()
        self.state.db.execute("UPDATE turn_records SET baseline='invalid old JSON' WHERE seq=?",(later['seq'],))
        fresh=json.dumps(dict(head='c'*40,dirty={},staged=[]))
        def capture(session,item):
            self.state.ledger.update(item,code_start='c'*40,baseline=fresh)
        with patch.object(self.tracker,'begin_turn',create=True,side_effect=capture):
            self.assertEqual(self.relay.process_once('agent'),'complete')
        self.assertEqual(self.state.db.execute('SELECT baseline FROM turn_records WHERE seq=?',(queued['seq'],)).fetchone()[0],fresh)
        self.assertEqual(self.state.db.execute('SELECT state FROM events WHERE seq=?',(old['seq'],)).fetchone()[0],'record_blocked')

    def test_unresolved_execution_or_delivery_remains_blocked_after_later_proof(self):
        session,old,later,queued=self.blocked_and_verified()
        for phase in ['uncertain_execution','uncertain_delivery']:
            self.state.transition(old['seq'],phase)
            self.assertIsNone(self.state.claim('agent'))
        self.assertEqual(self.host.calls,[])

    def test_delivered_review_with_unknown_execution_still_blocks_new_work(self):
        session,old,later,queued=self.blocked_and_verified()
        self.state.transition(queued['seq'],'record_blocked',result='Newer review',receipt='om_newer_review')
        self.state.ledger.update(queued,'record_blocked',activity='unknown',sync='blocked')
        self.relay.receive(self.event('Next request',session['chat_id'],'om_after_newer'))
        self.assertIsNone(self.state.claim('agent'));self.assertEqual(self.host.calls,[])

    def test_new_dispatch_still_requires_a_published_head_and_new_baseline(self):
        from intern_personal.core import Invalid
        session,old,later,queued=self.blocked_and_verified()
        with patch.object(self.tracker,'begin_turn',create=True,side_effect=Invalid('Unpublished head')) as preflight, \
             patch.object(self.tracker,'queue_record',return_value=False):
            self.assertEqual(self.relay.process_once('agent'),'record_blocked')
        preflight.assert_called_once();self.assertEqual(self.host.calls,[])

    def test_status_reports_a_queued_owner_message_as_not_dispatched(self):
        session,old,later,queued=self.blocked_and_verified(proof=False)
        report=self.state.turn_status(session['id'])
        self.assertIn('Turn '+str(queued['seq'])+': queued',report)
        self.assertIn('has not reached Codex',report)
        self.assertIn('Do not resend',report)


if __name__=='__main__':unittest.main()
