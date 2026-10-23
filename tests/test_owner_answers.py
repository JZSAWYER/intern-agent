"""An answer must not wait for a long-lived monitoring turn to end."""
import json
import time
import unittest

import test_live as fixture
import test_steering as steering_fixture
from intern_personal.core import Invalid
from intern_personal.delivery import finalize
from intern_personal.owner_answers import collect


class OwnerAnswerTests(unittest.TestCase):
    setUp=fixture.LiveTests.setUp
    event=fixture.LiveTests.event
    bot=steering_fixture.SteeringTests.bot
    busy=steering_fixture.SteeringTests.busy
    followup=steering_fixture.SteeringTests.followup
    worker=steering_fixture.SteeringTests.worker
    row=steering_fixture.SteeringTests.row

    def ready(self):
        s,_=self.busy(manual=True);e=self.followup(s,'Show the training and validation table')
        worker,calls,path=self.worker(s);worker.scan()
        self.api.messages.clear()  # Exclude the workspace's initial welcome.
        return s,e,worker,calls,path

    def append(self,path,*items):
        with path.open('a') as stream:
            for item in items:stream.write(json.dumps(item)+'\n')

    def user(self,text):
        return dict(type='response_item',payload=dict(type='message',role='user',content=[dict(type='input_text',text=text)]))

    def answer(self,text='| Split | Score |\n|---|---|\n| Validation | 75/80 |',phase='final_answer',role='assistant'):
        return dict(type='response_item',payload=dict(type='message',role=role,phase=phase,content=[dict(type='output_text',text=text)]))

    def notices(self):
        return self.state.db.execute("SELECT * FROM recovery_notices WHERE id LIKE 'owner-answer-%' ORDER BY created_at").fetchall()

    def delivered(self):
        row=self.notices()[0];s=self.state.session(row['session'])
        receipt=self.api.send(s['chat_id'],row['text'],'recovery-notice:'+row['id'])
        self.state.db.execute("UPDATE recovery_notices SET state='delivered',receipt=? WHERE id=?",(receipt,row['id']))
        return receipt

    def terminal(self,s,result,turn=None):
        # Reproduce the console observer's manual-turn completion transaction.
        self.state.db.execute('BEGIN IMMEDIATE')
        owner=self.state.identity()
        seq=self.state.db.execute("""INSERT INTO events(app_id,message_id,chat_id,sender,role,workspace,session,text,state,result)
            VALUES(?,?,?,?,'agent',?,?,?,'result_ready',?)""",
            (owner['app_id'],'local_terminal_fixture',s['chat_id'],owner['owner'],s['workspace'],s['id'],'Monitor checkpoint',result)).lastrowid
        self.state.db.execute('INSERT INTO terminal_turns VALUES(?,?,?,?,?)',
            (s['id'],turn or 'turn-'+s['id'],seq,result,'task_complete'))
        self.state.db.execute('COMMIT')
        event=dict(self.state.db.execute('SELECT * FROM events WHERE seq=?',(seq,)).fetchone())
        self.state.ledger.ensure_turn(event,time.time(),'terminal')
        self.state.ledger.update(event,activity='completed')
        # Exercise answer deduplication after the offline publication ack.
        self.state.db.execute("INSERT INTO turn_uploads VALUES(?,?,'offline','uploaded','{}',NULL,NULL,?,?,0)",
            (seq,s['id'],time.time(),time.time()))
        return event

    def test_answer_is_durable_before_monitoring_finishes(self):
        s,e,w,calls,path=self.ready();self.append(path,self.user(e['text']),self.answer())
        w.scan();w.scan()
        self.assertEqual(len(self.notices()),1)
        self.assertIn('| Validation |',self.notices()[0]['text'])
        self.assertIn('Quota',self.notices()[0]['text'])
        self.assertEqual(self.row(e['seq'])['state'],'accepted')
        saved=self.state.db.execute('SELECT state,receipt FROM events WHERE seq=?',(e['seq'],)).fetchone()
        self.assertEqual(tuple(saved),('awaiting_terminal',None))
        self.assertTrue(self.state.db.execute('SELECT 1 FROM message_reactions WHERE seq=?',(e['seq'],)).fetchone())
        self.assertEqual(sum(m=='turn/steer' for m,_ in calls),1)

    def test_no_prose_is_forwarded_without_the_exact_post_dispatch_user_input(self):
        s,e,w,_,path=self.ready();self.append(path,self.user('A different message'),self.answer())
        w.scan();self.assertEqual(self.notices(),[])

    def test_no_history_tools_private_analysis_or_commentary_are_answers(self):
        s,e,w,_,path=self.ready()
        self.append(path,self.user(e['text']),self.answer('Private reasoning','analysis'),
                    self.answer('Public progress','commentary'),self.answer('Developer instructions',role='developer'),
                    dict(type='response_item',payload=dict(type='function_call_output',output='Private tool output')))
        w.scan();self.assertEqual(self.notices(),[])

    def test_answer_deduplication_survives_collector_restart(self):
        s,e,w,_,path=self.ready();self.append(path,self.user(e['text']),self.answer());w.scan()
        self.state.db.execute('DELETE FROM steering_answer_scans')
        collect(w,self.row(e['seq']));self.assertEqual(len(self.notices()),1)

    def test_newer_distinct_answers_in_the_same_turn_are_not_lost(self):
        s,e,w,_,path=self.ready();self.append(path,self.user(e['text']),self.answer());w.scan()
        self.append(path,self.answer('Checkpoint 40: validation 76/80'));w.scan()
        self.assertEqual(len(self.notices()),2)

    def test_new_turn_output_is_not_attributed_to_old_followup(self):
        s,e,w,_,path=self.ready();self.append(path,self.user(e['text']),
            dict(type='event_msg',payload=dict(type='task_started',turn_id='another-turn')),self.answer())
        w.scan();self.assertEqual(self.notices(),[])

    def test_delivered_early_answer_is_not_sent_again_at_terminal_completion(self):
        s,e,w,_,path=self.ready();text='Already written answer'
        self.append(path,self.user(e['text']),self.answer(text));w.scan();receipt=self.delivered()
        parent=self.terminal(s,text)
        self.assertEqual(self.relay.deliver_saved(parent,'complete'),'complete')
        self.assertEqual(len(self.api.messages),1)
        self.assertEqual(self.state.db.execute('SELECT receipt FROM events WHERE seq=?',(parent['seq'],)).fetchone()[0],receipt)

    def test_completion_racing_pending_notice_waits_for_the_same_delivery(self):
        s,e,w,_,path=self.ready();text='One answer, one transport'
        self.append(path,self.user(e['text']),self.answer(text));w.scan();parent=self.terminal(s,text)
        self.assertEqual(self.relay.deliver_saved(parent,'complete'),'preparing_reply')
        self.assertEqual(self.api.messages,[])
        self.delivered();finalize(self.relay,parent['seq'])
        self.assertEqual(self.state.db.execute('SELECT state FROM events WHERE seq=?',(parent['seq'],)).fetchone()[0],'complete')
        self.assertEqual(len(self.api.messages),1)

    def test_final_line_endings_and_whitespace_do_not_duplicate_delivery(self):
        s,e,w,_,path=self.ready();self.append(path,self.user(e['text']),self.answer('Table\r\n75/80\r\n'));w.scan();self.delivered()
        parent=self.terminal(s,'  Table\n75/80\n')
        self.assertEqual(self.relay.deliver_saved(parent,'complete'),'complete')
        self.assertEqual(len(self.api.messages),1)

    def test_two_followups_share_one_answer_delivery(self):
        s,e,w,_,path=self.ready();other=self.followup(s,'Also include the overlap splits','om_second_followup');w.scan()
        self.append(path,self.user(e['text']),self.user(other['text']),self.answer());w.scan()
        self.assertEqual(len(self.notices()),1)

    def test_ambiguous_early_delivery_is_not_replayed_by_terminal_completion(self):
        s,e,w,_,path=self.ready();text='An uncertain delivery'
        self.append(path,self.user(e['text']),self.answer(text));w.scan();notice=self.notices()[0]
        self.state.db.execute("UPDATE recovery_notices SET state='uncertain' WHERE id=?",(notice['id'],))
        parent=self.terminal(s,text)
        self.assertEqual(self.relay.deliver_saved(parent,'complete'),'uncertain_delivery')
        self.assertEqual(self.api.messages,[])

    def test_notice_becoming_uncertain_after_completion_stays_guarded(self):
        s,e,w,_,path=self.ready();text='In-flight answer'
        self.append(path,self.user(e['text']),self.answer(text));w.scan();parent=self.terminal(s,text)
        notice=self.notices()[0];self.state.db.execute("UPDATE recovery_notices SET state='uncertain' WHERE id=?",(notice['id'],))
        self.assertEqual(self.relay.deliver_saved(parent,'complete'),'uncertain_delivery')
        self.assertEqual(self.api.messages,[])

    def test_different_final_completion_still_uses_the_normal_reply(self):
        s,e,w,_,path=self.ready();self.append(path,self.user(e['text']),self.answer('Earlier table'));w.scan();self.delivered()
        parent=self.terminal(s,'New checkpoint results')
        self.assertEqual(self.relay.deliver_saved(parent,'complete'),'complete')
        self.assertEqual(len(self.api.messages),2)
        self.assertIn('New checkpoint results',self.api.messages[-1][1])

    def test_already_completed_turn_uses_the_existing_outbox(self):
        s,e,w,_,path=self.ready();self.append(path,self.user(e['text']),self.answer('Ordinary completion'))
        self.terminal(s,'Ordinary completion');w.scan();self.assertEqual(self.notices(),[])

    def test_partial_records_are_revisited_without_losing_an_answer(self):
        s,e,w,_,path=self.ready();self.append(path,self.user(e['text']))
        line=json.dumps(self.answer());path.write_text(path.read_text()+line[:20]);w.scan()
        self.assertEqual(self.notices(),[])
        with path.open('a') as stream:stream.write(line[20:]+'\n')
        w.scan();self.assertEqual(len(self.notices()),1)

    def test_another_sessions_completion_cannot_reuse_this_answer(self):
        s,e,w,_,path=self.ready();self.append(path,self.user(e['text']),self.answer('Private to one bot'));w.scan();self.delivered()
        other=self.bot();parent=self.terminal(other,'Private to one bot',turn='turn-'+s['id'])
        self.assertIsNone(self.state.db.execute('SELECT receipt FROM events WHERE seq=?',(parent['seq'],)).fetchone()[0])


if __name__=='__main__':unittest.main()
