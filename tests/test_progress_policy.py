"""Saved publication retries must not flood a research conversation."""
import time
import unittest
from unittest.mock import patch

import test_live as fixture
from intern_personal import progress,progress_policy
from intern_personal.feishu_live import Relay
from intern_personal.live_state import LiveState


class ProgressPolicyTests(unittest.TestCase):
    setUp=fixture.LiveTests.setUp
    event=fixture.LiveTests.event

    def bot(self):
        session=self.relay.open_session('project'+str(self.state.db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]))
        progress.set_mode(self.state,session['id'],True,'messages')
        self.notices=getattr(self,'notices',[])
        self.api.progress=lambda chat,text,key:self.notices.append((chat,text,key)) or 'om_progress'
        return session

    def saved(self,session,*,delivered=False):
        count=self.state.db.execute('SELECT COUNT(*) FROM events').fetchone()[0]
        self.relay.receive(self.event('Original work',session['chat_id'],'om_work_'+str(count)))
        item=dict(self.state.db.execute('SELECT * FROM events ORDER BY seq DESC LIMIT 1').fetchone())
        self.state.transition(item['seq'],'sync_pending',result='Saved research result',receipt='om_answer' if delivered else None)
        self.state.ledger.update(item,'sync_pending',activity='completed',sync='pending')
        self.state.db.execute('INSERT OR REPLACE INTO sync_retries VALUES(?,?)',(item['seq'],time.time()+30))
        return item

    def legacy_mark(self,item,stage):
        # Exact old-receiver UPSERT; protection cannot require its restart.
        self.state.db.execute("INSERT INTO progress_notices VALUES(?,?,'pending',?) ON CONFLICT(seq) DO UPDATE SET stage=excluded.stage,state='pending',created_at=excluded.created_at WHERE progress_notices.stage!=excluded.stage",(item['seq'],stage,time.time()))

    def complete(self,item):
        self.state.transition(item['seq'],'complete')
        self.state.ledger.update(item,'complete',activity='completed',sync='synced')

    def test_many_delivered_results_retry_silently_without_changing_work(self):
        session=self.bot();items=[self.saved(session,delivered=True) for _ in range(24)]
        before=[tuple(r) for r in self.state.db.execute('SELECT seq,text,state,result,receipt FROM events')]
        retries=[tuple(r) for r in self.state.db.execute('SELECT * FROM sync_retries')]
        for _ in range(3):
            for item in items:
                for stage in ['uploading','sync_pending']:
                    self.legacy_mark(item,stage);progress.deliver(self.relay)
        self.assertEqual(self.notices,[])
        self.assertEqual([tuple(r) for r in self.state.db.execute('SELECT seq,text,state,result,receipt FROM events')],before)
        self.assertEqual([tuple(r) for r in self.state.db.execute('SELECT * FROM sync_retries')],retries)

    def test_uploading_then_one_failure_not_repeated_by_alternating_stages(self):
        item=self.saved(self.bot())
        for stage in ['uploading','sync_pending']*5:
            progress.mark(self.relay,item,stage);progress.deliver(self.relay)
        self.assertEqual([r[1] for r in self.notices],[progress.LABELS['uploading'],progress.LABELS['sync_pending']])

    def test_one_alert_for_backlog_and_no_uploading_after_a_suppressed_failure(self):
        session=self.bot();items=[self.saved(session) for _ in range(12)]
        for item in items:
            self.legacy_mark(item,'sync_pending');progress.deliver(self.relay)
            self.legacy_mark(item,'uploading');progress.deliver(self.relay)
        self.assertEqual(len(self.notices),1)
        self.assertEqual(self.notices[0][1],progress.LABELS['sync_pending'])

    def test_only_actual_backlog_recovery_rearms_a_new_incident(self):
        session=self.bot();first=self.saved(session);second=self.saved(session)
        progress.mark(self.relay,first,'sync_pending');progress.deliver(self.relay)
        self.complete(first)
        progress.mark(self.relay,second,'sync_pending');progress.deliver(self.relay)
        self.assertEqual(len(self.notices),1)
        self.complete(second)
        self.assertEqual(self.state.db.execute('SELECT COUNT(*) FROM progress_sync_incidents').fetchone()[0],0)
        third=self.saved(session);progress.mark(self.relay,third,'sync_pending');progress.deliver(self.relay)
        self.assertEqual(len(self.notices),2)

    def test_restart_keeps_dedup_and_does_not_reopen_resolved_old_incidents(self):
        session=self.bot();item=self.saved(session)
        progress.mark(self.relay,item,'sync_pending');progress.deliver(self.relay)
        reopened=LiveState(self.temp.name);self.addCleanup(reopened.close)
        relay=Relay(reopened,self.api,self.host,self.tracker)
        progress.mark(relay,item,'uploading');progress.mark(relay,item,'sync_pending');progress.deliver(relay)
        self.assertEqual(len(self.notices),1)
        self.complete(item)
        progress_policy.schema(reopened.db)
        self.assertEqual(reopened.db.execute('SELECT COUNT(*) FROM progress_sync_incidents').fetchone()[0],0)
        new=self.saved(session);progress.mark(relay,new,'sync_pending');progress.deliver(relay)
        self.assertEqual(len(self.notices),2)

    def test_public_commentary_and_other_bots_are_independent_of_git_notices(self):
        session=self.bot();saved=self.saved(session,delivered=True)
        self.legacy_mark(saved,'sync_pending')
        self.relay.receive(self.event('Current work',session['chat_id'],'om_current'))
        item=dict(self.state.db.execute('SELECT * FROM events ORDER BY seq DESC LIMIT 1').fetchone())
        self.state.transition(item['seq'],'running')
        progress.enqueue(self.state,session,'current','paragraph','Training reached the next checkpoint.',source='managed',event_seq=item['seq'])
        with patch('builtins.print'):self.assertTrue(progress.deliver(self.relay))
        self.assertEqual(self.notices[0][1],'Training reached the next checkpoint.')
        other=self.bot();new=self.saved(other);progress.mark(self.relay,new,'sync_pending');progress.deliver(self.relay)
        self.assertEqual(self.notices[-1][0],other['chat_id']);self.assertEqual(len(self.notices),2)

    def test_ambiguous_optional_alert_is_not_retried(self):
        item=self.saved(self.bot())
        progress.mark(self.relay,item,'sync_pending')
        with patch.object(self.api,'progress',side_effect=RuntimeError('unknown send')):progress.deliver(self.relay)
        for stage in ['uploading','sync_pending']*3:self.legacy_mark(item,stage);progress.deliver(self.relay)
        self.assertEqual(self.notices,[])

    def test_legacy_pending_notices_are_adopted_without_touching_reviews(self):
        session=self.bot();item=self.saved(session,delivered=True)
        for name in ['bound_git_notice_insert','bound_git_notice_update']:
            self.state.db.execute('DROP TRIGGER '+name)
        self.state.db.execute("INSERT INTO progress_notices VALUES(?,'sync_pending','pending',?)",(item['seq'],time.time()))
        self.state.transition(item['seq'],'record_blocked')
        before=tuple(self.state.db.execute('SELECT * FROM events WHERE seq=?',(item['seq'],)).fetchone())
        progress_policy.schema(self.state.db);progress.deliver(self.relay)
        self.assertEqual(self.notices,[])
        self.assertEqual(tuple(self.state.db.execute('SELECT * FROM events WHERE seq=?',(item['seq'],)).fetchone()),before)
        progress.mark(self.relay,item,'uncertain_execution');progress.deliver(self.relay)
        self.assertEqual(self.notices[0][1],progress.LABELS['uncertain_execution'])

    def test_legacy_attempted_warning_supplies_the_existing_session_incident(self):
        session=self.bot();first=self.saved(session);second=self.saved(session)
        for name in ['bound_git_notice_insert','bound_git_notice_update']:self.state.db.execute('DROP TRIGGER '+name)
        self.state.db.execute("INSERT INTO progress_notices VALUES(?,'sync_pending','claimed',?)",(first['seq'],time.time()))
        progress_policy.schema(self.state.db)
        self.legacy_mark(second,'sync_pending');progress.deliver(self.relay)
        self.assertEqual(self.notices,[])
        self.assertEqual(self.state.db.execute('SELECT seq FROM progress_sync_incidents WHERE session=?',(session['id'],)).fetchone()[0],first['seq'])

    def test_receipt_alone_does_not_silence_an_unconfirmed_result(self):
        session=self.bot();item=self.saved(session,delivered=True)
        self.state.ledger.update(item,'uncertain_execution',activity='unknown',sync='blocked')
        progress.mark(self.relay,item,'sync_pending');progress.deliver(self.relay)
        self.assertEqual([n[1] for n in self.notices],[progress.LABELS['sync_pending']])


if __name__=='__main__':unittest.main()
