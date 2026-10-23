import json
import threading
import time
import unittest
from unittest.mock import patch

import test_live as fixtures
import test_delivery_reliability as delivery_fixtures
from intern_personal.auto_recovery import Recovery,settings,schema as recovery_schema
from intern_personal.core import Invalid
from intern_personal.delivery import pump,send
from intern_personal.flow_watchdog import scan
from intern_personal.live_state import write_private
from intern_personal.workspace_tracking import WorkspaceTracking


class OutboxTests(unittest.TestCase):
    setUp=fixtures.LiveTests.setUp
    event=fixtures.LiveTests.event
    project=delivery_fixtures.DeliveryReliabilityTests.project
    def saved(self,mid='om_saved',state='sync_pending',uploaded=True):
        session,item=delivery_fixtures.DeliveryReliabilityTests.saved(self,mid,state)
        if uploaded:
            # These transport tests begin after the offline publisher's ack.
            self.state.db.execute("INSERT INTO turn_uploads VALUES(?,?,'offline','uploaded','{}',NULL,NULL,?,?,0)",
                (item['seq'],session['id'],time.time(),time.time()))
        return session,item

    def test_completed_answer_waits_for_git_publisher_before_delivery(self):
        session=self.project();self.relay.receive(self.event('Original owner message',session['chat_id'],'om_owner'))
        entered=threading.Event();release=threading.Event();errors=[];result=[]
        def publish(*args):
            entered.set()
            if not release.wait(10):raise RuntimeError('Fixture publication timeout')
        def worker():
            try:result.append(self.relay.process_once('agent'))
            except Exception as error:errors.append(error)
            finally:self.state.close()
        with patch.object(self.tracker,'publish_code',side_effect=publish):
            thread=threading.Thread(target=worker);thread.start()
            try:
                self.assertTrue(entered.wait(5));pump(self.relay)
                self.assertEqual(len(self.api.messages),0)
                row=self.state.db.execute('SELECT state,receipt FROM events').fetchone()
                self.assertEqual(row['state'],'preparing_reply');self.assertIsNone(row['receipt'])
                self.relay.cleanup_typing();self.assertEqual(self.api.removals,[])
            finally:release.set();thread.join(10)
        self.assertFalse(thread.is_alive());self.assertEqual(errors,[]);self.assertEqual(result,['complete'])
        pump(self.relay);self.assertEqual(len(self.api.messages),1);self.assertEqual(len(self.host.calls),1)

    def test_concurrent_courier_and_git_completion_share_one_send_lease(self):
        session,item=self.saved(state='preparing_reply')
        entered=threading.Event();release=threading.Event();errors=[];original=self.api.send
        def sending(*args):
            entered.set()
            if not release.wait(10):raise RuntimeError('Fixture delivery timeout')
            return original(*args)
        def courier():
            try:send(self.relay,item)
            except Exception as error:errors.append(error)
            finally:self.state.close()
        with patch.object(self.api,'send',side_effect=sending):
            thread=threading.Thread(target=courier);thread.start()
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(self.relay.deliver_saved(item,'complete'),'preparing_reply')
                pump(self.relay)
            finally:release.set();thread.join(10)
        self.assertEqual(errors,[]);self.assertEqual(len(self.api.messages),1)
        self.assertEqual(self.state.db.execute('SELECT state FROM events').fetchone()[0],'complete')
        self.assertEqual(self.state.db.execute('SELECT phase FROM turn_records').fetchone()[0],'complete')

    def test_receipt_survives_a_completion_callback_failure_and_repairs_without_resend(self):
        session,item=self.saved(state='preparing_reply')
        with patch.object(self.state.ledger,'observe',side_effect=RuntimeError('Fixture callback failure')):
            with self.assertRaises(RuntimeError):send(self.relay,item,'complete')
        self.assertEqual(len(self.api.messages),1)
        self.assertTrue(self.state.db.execute('SELECT receipt FROM events').fetchone()[0])
        pump(self.relay);pump(self.relay)
        self.assertEqual(self.state.db.execute('SELECT phase FROM turn_records').fetchone()[0],'complete')
        self.assertEqual(len(self.api.messages),1)

    def test_send_failure_and_restart_preserve_payload_without_blind_resend(self):
        session,item=self.saved(state='preparing_reply');self.api.fail=True
        self.assertEqual(self.relay.deliver_saved(item,'complete'),'uncertain_delivery')
        payload=self.state.db.execute('SELECT payload FROM reply_outbox').fetchone()[0]
        self.state.recover();self.api.fail=False;pump(self.relay);self.assertEqual(self.api.messages,[])
        self.state.resolve(item['seq'],'deliver');self.assertEqual(self.relay.process_once('agent'),'complete')
        self.assertEqual(self.api.messages[0][1],payload);self.assertEqual(self.host.calls,[])

    def test_restart_during_send_requires_confirmation_but_pending_send_survives(self):
        session,item=self.saved(state='preparing_reply')
        self.state.db.execute("INSERT INTO reply_outbox VALUES(?,'Frozen answer','sending','complete',NULL,NULL,?)",(item['seq'],time.time()))
        self.state.transition(item['seq'],'sending');self.state.recover();pump(self.relay)
        self.assertEqual(self.api.messages,[])
        self.assertEqual(self.state.db.execute('SELECT state FROM reply_outbox').fetchone()[0],'uncertain')
        self.state.resolve(item['seq'],'deliver');pump(self.relay)
        self.assertEqual(self.api.messages[0][1],'Frozen answer')

    def test_courier_never_delivers_a_result_from_an_active_or_unknown_turn(self):
        session,item=self.saved(state='preparing_reply')
        self.state.ledger.update(item,activity='running');pump(self.relay)
        self.assertEqual(self.api.messages,[])
        self.state.ledger.update(item,activity='unknown');pump(self.relay)
        self.assertEqual(self.api.messages,[])

    def test_legacy_callback_cannot_reopen_an_uploaded_historical_turn(self):
        session,item=self.saved(state='complete')
        self.state.db.execute("UPDATE events SET receipt='om_prior' WHERE seq=?",(item['seq'],))
        self.state.ledger.update(item,'complete',activity='completed',sync='synced')
        self.state.db.execute("INSERT INTO reply_outbox VALUES(?,'Saved result','delivered','sync_pending','om_prior',NULL,?)",(item['seq'],time.time()))
        pump(self.relay);pump(self.relay)
        self.assertEqual(self.state.db.execute('SELECT state FROM events WHERE seq=?',(item['seq'],)).fetchone()[0],'complete')
        self.assertEqual(self.state.db.execute('SELECT after_state FROM reply_outbox WHERE seq=?',(item['seq'],)).fetchone()[0],'complete')
        self.assertEqual(self.api.messages,[]);self.assertEqual(self.host.calls,[])

    def test_old_delivery_callback_waits_for_its_actual_git_acknowledgement(self):
        session,item=self.saved(state='preparing_reply',uploaded=False)
        self.state.db.execute("UPDATE events SET receipt='om_prior' WHERE seq=?",(item['seq'],))
        self.state.db.execute("INSERT INTO reply_outbox VALUES(?,'Saved result','delivered','sync_pending','om_prior',NULL,?)",(item['seq'],time.time()))
        pump(self.relay)
        self.assertEqual(self.state.db.execute('SELECT state FROM events WHERE seq=?',(item['seq'],)).fetchone()[0],'preparing_reply')
        self.assertFalse(self.state.db.execute('SELECT 1 FROM turn_uploads').fetchone())
        self.assertEqual(self.api.messages,[]);self.assertEqual(self.host.calls,[])

    def test_later_manual_work_does_not_hide_an_earlier_completed_answer(self):
        session,item=self.saved(state='sync_pending')
        self.host.active[session['id']]=(session['thread_id'],'newer-manual-turn')
        pump(self.relay)
        self.assertEqual(len(self.api.messages),1);self.assertEqual(self.host.calls,[])

    def test_undelivered_git_review_holds_next_request_until_saved_result_is_repaired(self):
        session=self.project()
        with patch.object(self.tracker,'publish_code',side_effect=Invalid('Research logs changed')), \
             patch.object(self.tracker,'begin_turn',create=True) as baseline:
            self.relay.receive(self.event('Owner request 0',session['chat_id'],'om_request_0'))
            self.assertEqual(self.relay.process_once('agent'),'record_blocked')
            self.relay.receive(self.event('Owner request 1',session['chat_id'],'om_request_1'))
            self.assertEqual(self.relay.process_once('agent'),'idle')
            self.assertEqual(self.api.messages,[]);self.assertEqual(baseline.call_count,1)
        self.assertEqual([text for s,text in self.host.calls],['Owner request 0'])
        seq=self.state.db.execute("SELECT seq FROM events WHERE state='record_blocked'").fetchone()[0]
        self.state.resolve(seq,'retry')
        self.relay.retry_sync()
        self.assertEqual(self.relay.process_once('agent'),'complete')
        self.assertEqual(self.relay.process_once('agent'),'complete')
        self.assertEqual([text for s,text in self.host.calls],['Owner request 0','Owner request 1'])
        self.assertEqual(len(self.api.messages),2)

    def setup_debug(self):
        debug=self.relay.open_session('debug');config=self.state.config()
        config['auto_recovery']={'enabled':True,'debug_session':debug['id']}
        write_private(self.state.root/'live.json',config)
        self.tracker.clean=lambda value:WorkspaceTracking.clean(self.tracker,value)
        return debug,Recovery(self.relay)

    def test_preflight_failure_gets_a_durable_diagnostic_and_debug_incident(self):
        debug,recovery=self.setup_debug();session=self.project()
        self.relay.receive(self.event('Never reached Codex',session['chat_id'],'om_preflight'))
        with patch.object(self.tracker,'begin_turn',create=True,side_effect=Invalid('Unpublished commit')), \
             patch.object(self.tracker,'queue_record',return_value=False):
            self.assertEqual(self.relay.process_once('agent'),'record_blocked')
        self.assertEqual(self.host.calls,[]);recovery.notifications();recovery.mailbox(settings(self.state))
        self.assertTrue(any('has not reached Codex' in text for chat,text,key in self.api.messages))
        message=self.state.db.execute('SELECT * FROM bot_mailbox').fetchone()
        self.assertEqual(message['recipient'],debug['id']);self.assertEqual(message['state'],'queued')
        self.assertEqual(self.relay.process_once('agent'),'complete');recovery.mailbox(settings(self.state));recovery.notifications()
        self.assertTrue(any('Recovery Sentinel update' in text for chat,text,key in self.api.messages))

    def test_diagnostic_and_incident_are_committed_atomically(self):
        debug,recovery=self.setup_debug();session,item=self.saved(state='record_blocked')
        from intern_personal.flow_watchdog import alert
        with patch('intern_personal.auto_recovery.notice',side_effect=RuntimeError('Fixture notice failure')):
            with self.assertRaises(RuntimeError):alert(self.relay,item,'fixture_blocker','Diagnostic',debug=True)
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM flow_incidents').fetchone()[0],0)
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM bot_mailbox').fetchone()[0],0)
        alert(self.relay,item,'fixture_blocker','Diagnostic',debug=True)
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM flow_incidents').fetchone()[0],1)
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM bot_mailbox').fetchone()[0],1)

    def test_watchdog_explains_waiting_without_replaying_or_interrupting(self):
        debug,recovery=self.setup_debug();session=self.project()
        self.relay.receive(self.event('Queued request',session['chat_id'],'om_waiting'))
        age=time.time()+301
        scan(self.relay,age);scan(self.relay,age+16);scan(self.relay,age+17)
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM flow_incidents').fetchone()[0],1)
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM bot_mailbox').fetchone()[0],1)
        recovery.notifications();self.assertTrue(any('has not reached Codex' in text for chat,text,key in self.api.messages))
        self.assertEqual(self.host.calls,[])
        self.relay.process_once('agent');scan(self.relay,age+20)
        self.assertEqual(self.state.db.execute('SELECT state FROM bot_mailbox').fetchone()[0],'resolved')

    def test_long_healthy_work_stays_quiet_without_auto_retry(self):
        session,item=self.saved(state='running');self.state.ledger.update(item,activity='running')
        for elapsed in [301,302,601,3601,14401]:scan(self.relay,time.time()+elapsed)
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM recovery_notices').fetchone()[0],0)
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM recovery_runs').fetchone()[0],0)
        self.assertEqual(self.host.calls,[])

    def test_old_receiver_heartbeat_is_blocked_without_suppressing_real_notices(self):
        session,item=self.saved(state='running');self.state.ledger.update(item,activity='running')
        self.state.db.execute("INSERT INTO recovery_notices VALUES(?,?,'Old heartbeat','pending',NULL,?)",
                              ('heartbeat_'+str(item['seq'])+'_123:heartbeat',session['id'],time.time()))
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM recovery_notices').fetchone()[0],0)
        self.state.db.execute("INSERT INTO recovery_notices VALUES('recovery_fixture:scheduled',?,'Actual failure','pending',NULL,?)",(session['id'],time.time()))
        Recovery(self.relay).notifications()
        self.assertEqual([text for chat,text,key in self.api.messages],['Actual failure'])
        self.assertEqual(self.state.db.execute("SELECT state FROM recovery_notices WHERE id='recovery_fixture:scheduled'").fetchone()[0],'delivered')

    def test_pending_legacy_heartbeat_is_retained_but_not_sent_after_migration(self):
        session,item=self.saved(state='running');self.state.ledger.update(item,activity='running')
        self.state.db.execute('DROP TRIGGER suppress_flow_heartbeats')
        self.state.db.execute("INSERT INTO recovery_notices VALUES('heartbeat_7_123:heartbeat',?,'Old pending heartbeat','pending',NULL,?)",(session['id'],time.time()))
        self.state.db.execute("INSERT INTO recovery_notices VALUES('heartbeat_7_122:heartbeat',?,'Old delivered heartbeat','delivered','om_fixture',?)",(session['id'],time.time()))
        recovery_schema(self.state.db);recovery_schema(self.state.db)
        Recovery(self.relay).notifications()
        rows=self.state.db.execute('SELECT state,text,receipt FROM recovery_notices ORDER BY id').fetchall()
        self.assertEqual([r['state'] for r in rows],['delivered','suppressed'])
        self.assertEqual(rows[0]['receipt'],'om_fixture');self.assertEqual(rows[1]['text'],'Old pending heartbeat')
        self.assertEqual(self.api.messages,[]);self.assertEqual(self.host.calls,[])

    def test_uncertain_delivery_is_reported_without_resending_the_answer(self):
        debug,recovery=self.setup_debug();session,item=self.saved(state='preparing_reply')
        self.api.fail=True;self.relay.deliver_saved(item,'complete');self.api.fail=False
        scan(self.relay,time.time()+61);recovery.notifications();pump(self.relay)
        self.assertTrue(any('could not be confirmed' in text for chat,text,key in self.api.messages))
        self.assertFalse(any(text.startswith('Saved result') for chat,text,key in self.api.messages))


if __name__=='__main__':unittest.main()
