"""A saved answer's Git retry must not hold a newer owner question hostage."""
import time
import unittest

import test_live as fixture
import test_steering as steering_fixture
from intern_personal.publication import yield_to_owner


class PublicationPriorityTests(unittest.TestCase):
    setUp=fixture.LiveTests.setUp
    event=fixture.LiveTests.event
    bot=steering_fixture.SteeringTests.bot

    def saved(self,s,mid='om_old',phase='result_ready',receipt=True,activity='completed'):
        self.relay.receive(self.event('Old work',s['chat_id'],mid))
        row=dict(self.state.db.execute('SELECT * FROM events WHERE message_id=?',(mid,)).fetchone())
        self.state.transition(row['seq'],phase,result='Saved answer',receipt='om_old_reply' if receipt else None)
        self.state.ledger.update(row,phase,activity=activity,sync='pending')
        return row

    def owner(self,s,mid='om_owner'):
        self.relay.receive(self.event('New disk-space question',s['chat_id'],mid))
        return dict(self.state.db.execute('SELECT * FROM events WHERE message_id=?',(mid,)).fetchone())

    def test_delivered_reports_yield_before_an_idle_owner_message_is_claimed(self):
        s=self.bot();old=[self.saved(s,'om_old_'+str(i))for i in range(5)]
        new=self.owner(s);yield_to_owner(self.state)
        self.assertEqual(self.state.claim('agent',s['id'])['seq'],new['seq'])
        for row in old:
            saved=self.state.db.execute('SELECT state,result,receipt FROM events WHERE seq=?',(row['seq'],)).fetchone()
            self.assertEqual(tuple(saved),('sync_pending','Saved answer','om_old_reply'))
            record=self.state.db.execute('SELECT activity,sync FROM turn_records WHERE seq=?',(row['seq'],)).fetchone()
            self.assertEqual(tuple(record),('completed','pending'))

    def test_old_receiver_retry_loop_cannot_reintroduce_the_backlog(self):
        s=self.bot();old=self.saved(s);new=self.owner(s);yield_to_owner(self.state)
        for _ in range(3):
            self.state.schedule_sync_retries()
            self.state.db.execute('UPDATE sync_retries SET retry_at=0')
            self.relay.retry_sync()
            self.assertEqual(self.state.db.execute('SELECT state FROM events WHERE seq=?',(old['seq'],)).fetchone()[0],'sync_pending')
        self.assertEqual(self.state.claim('agent',s['id'])['seq'],new['seq'])

    def test_git_retry_rejoins_normally_after_owner_work_finishes(self):
        s=self.bot();old=self.saved(s);new=self.owner(s);yield_to_owner(self.state)
        claimed=self.state.claim('agent',s['id']);self.assertEqual(claimed['seq'],new['seq'])
        self.state.transition(new['seq'],'complete',result='New reply',receipt='om_new_reply')
        self.state.ledger.update(new,'complete',activity='completed',sync='synced')
        self.state.schedule_sync_retries();self.state.db.execute('UPDATE sync_retries SET retry_at=0');self.relay.retry_sync()
        self.assertEqual(self.state.claim('agent',s['id'])['seq'],old['seq'])

    def test_a_current_git_publisher_is_not_reassigned_underneath_its_worker(self):
        s=self.bot();old=self.saved(s,phase='preparing_reply');new=self.owner(s);yield_to_owner(self.state)
        self.assertEqual(self.state.db.execute('SELECT state FROM events WHERE seq=?',(old['seq'],)).fetchone()[0],'preparing_reply')
        self.assertIsNone(self.state.claim('agent',s['id']))
        # Once that existing publisher finishes, the original message proceeds.
        self.state.transition(old['seq'],'complete');self.state.ledger.update(old,'complete',activity='completed',sync='synced')
        self.assertEqual(self.state.claim('agent',s['id'])['seq'],new['seq'])

    def test_undelivered_or_unconfirmed_work_still_blocks_dispatch(self):
        for phase,receipt,activity in [('result_ready',False,'completed'),('uncertain_execution',False,'unknown'),('running',False,'running')]:
            with self.subTest(phase=phase):
                s=self.bot();old=self.saved(s,'om_old_'+phase,phase,receipt,activity);self.owner(s,'om_new_'+phase);yield_to_owner(self.state)
                self.assertEqual(self.state.db.execute('SELECT state FROM events WHERE seq=?',(old['seq'],)).fetchone()[0],phase)
                candidate=self.state.claim('agent',s['id'])
                self.assertTrue(candidate is None or candidate['seq']==old['seq'])

    def test_existing_review_is_preserved(self):
        s=self.bot();old=self.saved(s,phase='record_blocked');before=dict(self.state.db.execute('SELECT * FROM turn_records WHERE seq=?',(old['seq'],)).fetchone())
        new=self.owner(s);yield_to_owner(self.state)
        self.assertEqual(dict(self.state.db.execute('SELECT * FROM turn_records WHERE seq=?',(old['seq'],)).fetchone()),before)
        self.assertEqual(self.state.db.execute('SELECT state FROM events WHERE seq=?',(old['seq'],)).fetchone()[0],'record_blocked')
        self.assertEqual(self.state.claim('agent',s['id'])['seq'],new['seq'])

    def test_another_bots_owner_message_does_not_change_this_bots_git_queue(self):
        a=self.bot();b=self.bot();old=self.saved(a);self.owner(b);yield_to_owner(self.state)
        self.assertEqual(self.state.db.execute('SELECT state FROM events WHERE seq=?',(old['seq'],)).fetchone()[0],'result_ready')

    def test_peer_and_timer_input_do_not_acquire_owner_priority(self):
        s=self.bot();old=self.saved(s)
        for mid in ['peer:fixture','research:fixture','local_terminal_fixture']:
            self.state.db.execute('''INSERT INTO events(app_id,message_id,chat_id,sender,role,workspace,session,text,state)
                VALUES(?,?,?,?,'agent',?,?,?,'queued')''',
                (old['app_id'],mid,old['chat_id'],old['sender'],old['workspace'],s['id'],'Internal reference'))
        yield_to_owner(self.state)
        self.assertEqual(self.state.db.execute('SELECT state FROM events WHERE seq=?',(old['seq'],)).fetchone()[0],'result_ready')

    def test_poll_adopts_a_pre_upgrade_owner_request_without_replaying_it(self):
        s=self.bot();old=self.saved(s)
        self.state.db.execute('DROP TRIGGER yield_delivered_publication')
        new=self.owner(s);self.assertEqual(self.state.db.execute('SELECT state FROM events WHERE seq=?',(old['seq'],)).fetchone()[0],'result_ready')
        yield_to_owner(self.state);yield_to_owner(self.state)
        self.assertEqual(self.state.claim('agent',s['id'])['seq'],new['seq'])
        self.assertIsNone(self.state.db.execute('SELECT 1 FROM dispatch_attempts WHERE seq=?',(old['seq'],)).fetchone())


if __name__=='__main__':unittest.main()
