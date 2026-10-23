"""A background turn starting during preflight must not swallow owner input."""
import json
import time
import unittest
from unittest.mock import patch

import test_live as fixture
import test_steering as steering_fixture
from intern_personal.busy_inputs import LEGACY_BUSY,defer,recover
from intern_personal.codex_bridge import TurnBusy
from intern_personal.core import Invalid


class BusyInputTests(unittest.TestCase):
    setUp=fixture.LiveTests.setUp
    event=fixture.LiveTests.event
    bot=steering_fixture.SteeringTests.bot
    worker=steering_fixture.SteeringTests.worker
    row=steering_fixture.SteeringTests.row

    def request(self):
        s=self.bot();self.relay.receive(self.event('  Explain the missing disk usage  ',s['chat_id'],'om_owner'))
        e=dict(self.state.db.execute("SELECT * FROM events WHERE message_id='om_owner'").fetchone())
        self.api.messages.clear();return s,e

    def active(self,s):
        turn='goal-'+s['id']
        self.state.db.execute('INSERT OR REPLACE INTO codex_outcomes VALUES(?,?,?,?,NULL,NULL,?)',
            (s['id'],s['thread_id'],turn,'inProgress',time.time()))
        self.state.db.execute('INSERT OR REPLACE INTO console_views VALUES(?,?,0,?)',
            (s['id'],'fixture-config',json.dumps(dict(active=turn,event_seq=None))))
        return turn

    def test_automatic_turn_start_during_preflight_steers_the_original_input_once(self):
        s,e=self.request()
        def race(*args,**kwargs):raise TurnBusy(self.active(s))
        with patch.object(self.host,'run',side_effect=race):
            self.assertEqual(self.relay.process_once('agent',s['id']),'awaiting_terminal')
        saved=self.state.db.execute('SELECT * FROM events WHERE seq=?',(e['seq'],)).fetchone()
        self.assertEqual((saved['text'],saved['state'],saved['result'],saved['receipt']),(e['text'],'awaiting_terminal',None,None))
        self.assertEqual(self.api.messages,[])
        worker,calls,_=self.worker(s);worker.scan();worker.scan()
        sends=[p for m,p in calls if m=='turn/steer']
        self.assertEqual(len(sends),1);self.assertEqual(sends[0]['input'][0]['text'],e['text'])
        self.assertEqual(sends[0]['expectedTurnId'],'goal-'+s['id'])

    def test_unmatched_busy_turn_keeps_unstarted_input_queued_with_a_fresh_baseline(self):
        s,e=self.request();baseline=json.dumps(dict(head='1'*40,dirty={},staged=[]))
        def race(*args,**kwargs):
            self.state.ledger.update(e,code_start='1'*40,baseline=baseline)
            self.active(s);raise TurnBusy('different-turn')
        with patch.object(self.host,'run',side_effect=race):
            self.assertEqual(self.relay.process_once('agent',s['id']),'queued')
        record=self.state.db.execute('SELECT phase,activity,baseline,code_start FROM turn_records WHERE seq=?',(e['seq'],)).fetchone()
        self.assertEqual(tuple(record),('waiting','waiting',None,None))
        self.assertEqual(self.state.db.execute('SELECT baseline FROM busy_input_deferrals WHERE seq=?',(e['seq'],)).fetchone()[0],baseline)
        self.assertEqual(self.api.messages,[])

    def legacy(self,*,newer=True,seen=False):
        s,e=self.request();claimed=self.state.claim('agent',s['id'])
        from intern_personal.dispatch import start
        start(self.state,claimed)
        self.state.transition(e['seq'],'sync_pending',result=LEGACY_BUSY,receipt='om_busy_notice')
        self.state.ledger.update(e,'sync_pending',activity='completed',sync='pending')
        self.state.db.execute("INSERT INTO reply_outbox VALUES(?,?,'delivered',NULL,?,NULL,?)",(e['seq'],LEGACY_BUSY,'om_busy_notice',time.time()))
        self.active(s)
        if newer:
            owner=self.state.identity()
            seq=self.state.db.execute("""INSERT INTO events(app_id,message_id,chat_id,sender,role,workspace,session,text,state)
                VALUES(?,?,?,?,'agent',?,?,?,'running')""",
                (owner['app_id'],'om_newer',s['chat_id'],owner['owner'],s['workspace'],s['id'],'Next owner request')).lastrowid
            self.state.db.execute("INSERT INTO dispatch_attempts(seq,token,stage,updated_at)VALUES(?,'fixture-newer','preflight',?)",(seq,time.time()+1))
        w,calls,path=self.worker(s)
        if seen:path.write_text(json.dumps(dict(type='response_item',payload=dict(type='message',role='user',content=[dict(type='input_text',text=e['text'])])))+'\n')
        return s,e,w,calls,path

    def test_legacy_false_completion_is_archived_before_original_input_is_restored(self):
        s,e,w,calls,_=self.legacy();recover(w)
        saved=self.state.db.execute('SELECT * FROM events WHERE seq=?',(e['seq'],)).fetchone()
        self.assertEqual((saved['state'],saved['result'],saved['receipt']),('awaiting_terminal',None,None))
        audit=self.state.db.execute('SELECT * FROM busy_input_deferrals WHERE seq=?',(e['seq'],)).fetchone()
        self.assertEqual(audit['result'],LEGACY_BUSY);self.assertEqual(audit['receipt'],'om_busy_notice')
        self.assertEqual(json.loads(audit['outbox'])['payload'],LEGACY_BUSY)
        self.assertIsNone(self.state.db.execute('SELECT 1 FROM reply_outbox WHERE seq=?',(e['seq'],)).fetchone())
        w.scan();w.scan();self.assertEqual(sum(m=='turn/steer'for m,_ in calls),1)
        self.assertEqual(self.row(e['seq'])['state'],'accepted')

    def test_any_observed_identical_input_blocks_automatic_legacy_replay(self):
        s,e,w,calls,_=self.legacy(seen=True);recover(w)
        self.assertIsNone(self.state.db.execute('SELECT 1 FROM busy_input_deferrals').fetchone())
        self.assertEqual(self.state.db.execute('SELECT result FROM events WHERE seq=?',(e['seq'],)).fetchone()[0],LEGACY_BUSY)
        self.assertEqual(calls,[])

    def test_current_legacy_worker_must_move_on_before_reconciliation(self):
        s,e,w,calls,_=self.legacy(newer=False);recover(w)
        self.assertIsNone(self.state.db.execute('SELECT 1 FROM busy_input_deferrals').fetchone())
        self.assertEqual(calls,[])

    def test_published_postprocessing_proves_the_legacy_worker_has_finished(self):
        s,e,w,_,_=self.legacy(newer=False)
        self.state.db.execute('UPDATE turn_records SET projected_revision=revision WHERE seq=?',(e['seq'],))
        recover(w)
        self.assertEqual(self.state.db.execute('SELECT state FROM events WHERE seq=?',(e['seq'],)).fetchone()[0],'awaiting_terminal')

    def test_existing_review_is_never_reopened(self):
        s,e,w,calls,_=self.legacy();self.state.transition(e['seq'],'record_blocked')
        before=dict(self.state.db.execute('SELECT * FROM events WHERE seq=?',(e['seq'],)).fetchone());recover(w)
        self.assertEqual(dict(self.state.db.execute('SELECT * FROM events WHERE seq=?',(e['seq'],)).fetchone()),before)

    def test_completion_evidence_prevents_prestart_replay_even_if_outcome_was_pruned(self):
        s,e,w,_,_=self.legacy();self.state.ledger.evidence(e['seq'],'Observed bound Codex transcript task_complete')
        with self.assertRaises(Invalid):defer(self.state,e,legacy=True)
        self.assertIsNone(self.state.db.execute('SELECT 1 FROM busy_input_deferrals').fetchone())


if __name__=='__main__':unittest.main()
