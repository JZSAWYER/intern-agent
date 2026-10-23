import json
import time
import unittest

import test_auto_recovery as fixture
from intern_personal.incident_lifecycle import reconcile
from intern_personal.flow_watchdog import alert


class IncidentLifecycleTests(unittest.TestCase):
    setUp=fixture.RecoveryTests.setUp
    event=fixture.RecoveryTests.event
    setup_policy=fixture.RecoveryTests.setup_policy

    def incident(self,queued=True):
        operator=self.relay.open_session('recovery-sentinel');self.setup_policy(operator)
        source=self.relay.open_session('project')
        self.relay.receive(self.event('Original request',source['chat_id']))
        root=dict(self.state.db.execute("SELECT * FROM events WHERE message_id='om_event'").fetchone())
        alert(self.relay,root,'answer_wait','Saved answer pending',debug=True)
        if queued:self.recovery.mailbox(self.state.config()['auto_recovery']|dict(incident_session=operator['id']))
        mail=dict(self.state.db.execute("SELECT * FROM bot_mailbox WHERE kind='incident'").fetchone())
        return operator,source,root,mail

    def resolved(self,root):
        self.state.transition(root['seq'],'complete',result='Original saved answer',receipt='om_original_delivered')
        self.state.ledger.update(root,'complete',activity='completed',sync='synced')

    def test_obsolete_queued_diagnostic_retires_once_and_releases_next_incident(self):
        operator,source,root,mail=self.incident();self.resolved(root)
        original=dict(self.state.db.execute('SELECT * FROM events WHERE seq=?',(mail['event_seq'],)).fetchone())
        reconcile(self.state);reconcile(self.state)
        event=self.state.db.execute('SELECT * FROM events WHERE seq=?',(mail['event_seq'],)).fetchone()
        self.assertEqual(event['state'],'cancelled');self.assertEqual(event['text'],original['text'])
        audit=self.state.db.execute('SELECT * FROM incident_resolutions').fetchone()
        self.assertEqual(json.loads(audit['original_event']),original)
        self.assertEqual(self.state.db.execute('SELECT COUNT(*) FROM incident_resolutions').fetchone()[0],1)
        self.assertEqual(self.host.calls,[])
        self.relay.receive(self.event('A real new operator request',operator['chat_id'],'om_operator'))
        self.assertEqual(self.state.claim('agent',session_id=operator['id'])['text'],'A real new operator request')

    def test_preflight_review_is_preserved_while_obsolete_execution_is_cancelled(self):
        operator,source,root,mail=self.incident();self.resolved(root)
        item=self.state.claim('agent',session_id=operator['id'])
        self.state.transition(item['seq'],'record_blocked')
        self.state.ledger.update(item,'record_blocked',activity='failed',sync='blocked',code_start='a'*40,baseline='{"head":"saved"}')
        self.state.ledger.evidence(item['seq'],'Original preflight failure')
        before=dict(self.state.db.execute('SELECT * FROM turn_records WHERE seq=?',(item['seq'],)).fetchone())
        reconcile(self.state)
        after=dict(self.state.db.execute('SELECT * FROM turn_records WHERE seq=?',(item['seq'],)).fetchone())
        for key in ['phase','activity','sync','code_start','baseline','code_end']:self.assertEqual(before[key],after[key])
        self.assertIn('Original preflight failure',json.loads(after['evidence']))
        audit=self.state.db.execute('SELECT original_turn FROM incident_resolutions').fetchone()
        self.assertEqual(json.loads(audit[0]),before)

    def test_legacy_review_without_a_dispatch_witness_needs_manual_audit(self):
        operator,source,root,mail=self.incident();self.resolved(root)
        self.state.transition(mail['event_seq'],'record_blocked')
        reconcile(self.state)
        self.assertEqual(self.state.db.execute('SELECT state FROM events WHERE seq=?',(mail['event_seq'],)).fetchone()[0],'record_blocked')
        self.assertFalse(self.state.db.execute('SELECT 1 FROM incident_resolutions').fetchone())

    def test_dispatching_or_ambiguous_diagnostics_are_never_retired(self):
        for state in ['running','queued','uncertain_execution','uncertain_delivery']:
            with self.subTest(state=state):
                if not hasattr(self,'operator'):
                    self.operator,self.source,self.root,self.mail=self.incident();self.resolved(self.root)
                    item=self.state.claim('agent',session_id=self.operator['id'])
                    from intern_personal.dispatch import start
                    start(self.state,item)
                self.state.transition(self.mail['event_seq'],state)
                reconcile(self.state)
                self.assertEqual(self.state.db.execute('SELECT state FROM events WHERE seq=?',(self.mail['event_seq'],)).fetchone()[0],state)
        self.assertFalse(self.state.db.execute('SELECT 1 FROM incident_resolutions').fetchone())

    def test_result_or_outbox_is_preserved_even_when_source_recovered(self):
        operator,source,root,mail=self.incident();self.resolved(root)
        self.state.transition(mail['event_seq'],'queued',result='Existing diagnostic result')
        reconcile(self.state)
        self.assertEqual(self.state.db.execute('SELECT result FROM events WHERE seq=?',(mail['event_seq'],)).fetchone()[0],'Existing diagnostic result')
        self.state.db.execute('UPDATE events SET result=NULL WHERE seq=?',(mail['event_seq'],))
        self.state.db.execute("INSERT INTO reply_outbox VALUES(?,?,'uncertain',NULL,NULL,NULL,?)",(mail['event_seq'],'Saved diagnosis',time.time()))
        reconcile(self.state)
        self.assertFalse(self.state.db.execute('SELECT 1 FROM incident_resolutions').fetchone())

    def test_pending_resolved_preflight_mail_never_creates_a_codex_request(self):
        operator,source,root,mail=self.incident(queued=False)
        self.state.db.execute("UPDATE flow_incidents SET cause='preflight_transport'")
        self.resolved(root);reconcile(self.state)
        self.assertEqual(self.state.db.execute('SELECT state FROM bot_mailbox').fetchone()[0],'resolved')
        self.assertIsNone(self.state.db.execute('SELECT event_seq FROM bot_mailbox').fetchone()[0])
        self.assertEqual(self.host.calls,[])

    def test_claimed_preflight_does_not_prove_queue_incident_recovered(self):
        operator,source,root,mail=self.incident(queued=False)
        self.state.db.execute("UPDATE flow_incidents SET cause='dispatch_wait'")
        self.state.claim('agent',session_id=source['id'])
        from intern_personal.flow_watchdog import scan
        scan(self.relay)
        self.assertEqual(self.state.db.execute('SELECT state FROM flow_incidents').fetchone()[0],'open')
        reconcile(self.state)
        self.assertEqual(self.state.db.execute('SELECT state FROM bot_mailbox').fetchone()[0],'pending')
        from intern_personal.auto_recovery import observe
        observe(self.state,source,'actually-accepted','inProgress',seq=root['seq'])
        scan(self.relay)
        self.assertEqual(self.state.db.execute('SELECT state FROM flow_incidents').fetchone()[0],'resolved')

    def test_prematurely_closed_mail_is_restored_without_replaying_source_or_duplicating_mail(self):
        operator,source,root,mail=self.incident(queued=False)
        self.state.db.execute("UPDATE flow_incidents SET cause='dispatch_wait',state='resolved'")
        self.state.db.execute("UPDATE bot_mailbox SET state='resolved'")
        reconcile(self.state);reconcile(self.state)
        self.assertEqual(self.state.db.execute('SELECT state FROM bot_mailbox').fetchone()[0],'pending')
        self.assertEqual(self.state.db.execute('SELECT COUNT(*) FROM bot_mailbox').fetchone()[0],1)
        self.assertEqual(self.state.db.execute('SELECT state FROM flow_incidents').fetchone()[0],'open')
        self.assertEqual(self.host.calls,[])

    def test_real_blocker_is_reported_once_without_recursive_debug_escalation(self):
        operator,source,root,mail=self.incident(queued=False)
        self.relay.receive(self.event('Old operator request',operator['chat_id'],'om_blocker'))
        self.state.db.execute("UPDATE events SET state='record_blocked' WHERE message_id='om_blocker'")
        reconcile(self.state);reconcile(self.state)
        notices=[dict(r) for r in self.state.db.execute("SELECT * FROM recovery_notices WHERE id LIKE '%operator-blocked%'")]
        self.assertEqual(len(notices),2)
        self.assertEqual({n['session'] for n in notices},{operator['id'],source['id']})
        self.assertTrue(all('has not reached its Codex' in n['text'] for n in notices))
        self.assertEqual(self.state.db.execute('SELECT COUNT(*) FROM bot_mailbox').fetchone()[0],1)

    def test_revoked_owner_or_changed_operator_does_not_retire_a_review(self):
        operator,source,root,mail=self.incident();self.resolved(root)
        self.state.db.execute("UPDATE identity SET owner='ou_replaced'")
        reconcile(self.state)
        self.assertEqual(self.state.db.execute('SELECT state FROM events WHERE seq=?',(mail['event_seq'],)).fetchone()[0],'queued')
        self.assertFalse(self.state.db.execute('SELECT 1 FROM incident_resolutions').fetchone())

    def test_legacy_mailbox_cannot_claim_a_resolved_incident_between_helper_checks(self):
        operator,source,root,mail=self.incident(queued=False)
        reconcile(self.state)  # Configure the guard while the source still needs delivery.
        self.resolved(root)
        claimed=self.state.db.execute("UPDATE bot_mailbox SET state='queued' WHERE id=? AND state='pending'",(mail['id'],))
        self.assertEqual(claimed.rowcount,0)
        self.assertEqual(self.state.db.execute('SELECT state FROM bot_mailbox').fetchone()[0],'pending')
        self.assertFalse(self.state.db.execute('SELECT 1 FROM events WHERE session=?',(operator['id'],)).fetchone())
        reconcile(self.state)
        self.assertEqual(self.state.db.execute('SELECT state FROM bot_mailbox').fetchone()[0],'resolved')

    def test_claim_race_guard_allows_real_preflight_incident_and_reconciles_only_unstarted_work(self):
        operator,source,root,mail=self.incident(queued=False)
        self.state.db.execute("UPDATE flow_incidents SET cause='dispatch_wait'")
        reconcile(self.state)
        item=self.state.claim('agent',session_id=source['id'])
        claimed=self.state.db.execute("UPDATE bot_mailbox SET state='queued' WHERE id=? AND state='pending'",(mail['id'],))
        self.assertEqual(claimed.rowcount,1)  # Claiming preflight has not delivered it to Codex.
        self.state.db.execute("UPDATE bot_mailbox SET state='pending' WHERE id=?",(mail['id'],))
        from intern_personal.auto_recovery import observe
        observe(self.state,source,'actually-accepted','inProgress',seq=root['seq'])
        claimed=self.state.db.execute("UPDATE bot_mailbox SET state='queued' WHERE id=? AND state='pending'",(mail['id'],))
        self.assertEqual(claimed.rowcount,0)
        self.assertEqual(self.state.db.execute('SELECT stage FROM dispatch_attempts WHERE seq=?',(item['seq'],)).fetchone()[0],'preflight')

    def test_old_receiver_cannot_close_incident_or_mail_while_claim_is_still_preflight(self):
        operator,source,root,mail=self.incident(queued=False)
        self.state.db.execute("UPDATE flow_incidents SET cause='dispatch_wait'")
        reconcile(self.state);self.state.claim('agent',session_id=source['id'])
        changed=self.state.db.execute("UPDATE flow_incidents SET state='resolved' WHERE id=?",(mail['episode'],))
        self.assertEqual(changed.rowcount,0)
        changed=self.state.db.execute("UPDATE bot_mailbox SET state='resolved' WHERE id=?",(mail['id'],))
        self.assertEqual(changed.rowcount,0)
        self.assertEqual(self.state.db.execute('SELECT state FROM bot_mailbox').fetchone()[0],'pending')
        self.assertEqual(self.state.db.execute('SELECT state FROM flow_incidents').fetchone()[0],'open')

    def test_claimed_but_never_submitted_source_reopens_its_original_mail(self):
        operator,source,root,mail=self.incident(queued=False)
        self.state.db.execute("UPDATE flow_incidents SET cause='dispatch_wait',state='resolved'")
        self.state.db.execute("UPDATE bot_mailbox SET state='resolved'")
        self.state.claim('agent',session_id=source['id'])
        reconcile(self.state)
        self.assertEqual(self.state.db.execute('SELECT state FROM bot_mailbox').fetchone()[0],'pending')
        self.assertEqual(self.state.db.execute('SELECT state FROM flow_incidents').fetchone()[0],'open')
        self.assertEqual(self.state.db.execute('SELECT COUNT(*) FROM bot_mailbox').fetchone()[0],1)


if __name__=='__main__':unittest.main()
