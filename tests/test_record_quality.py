"""Durable knowledge stays useful without losing the original conversation."""
import json
from pathlib import Path
import time
import unittest

import test_tracking as tracking
import test_features as features
import test_recovery_memory as upgrades
from intern_personal import memory,record_projection,recovery
from intern_personal.record_quality import distinct_notes,turn_index
from intern_personal.records import message_section,parse,render,quote


class RecordQualityTests(unittest.TestCase):
    setUp = tracking.TrackingTests.setUp
    raw = tracking.TrackingTests.raw
    attach = tracking.TrackingTests.attach
    conversation = features.FeaturesTests.conversation
    fresh = upgrades.WorkspaceUpgradeTests.fresh

    def saved(self, session, text, result, *, role='agent', activity='completed'):
        cursor = self.state.db.execute("""INSERT INTO events
            (app_id,message_id,chat_id,sender,role,workspace,session,text,state,result)
            VALUES('cli_test',?,'oc_workspace','ou_owner',?,'project',?,?,'running',?)""",
            ('om_' + str(time.time_ns()),role,session['id'],text,result))
        item = dict(self.state.db.execute('SELECT * FROM events WHERE seq=?',(cursor.lastrowid,)).fetchone())
        self.state.ledger.ensure_turn(item,time.time(),'local')
        self.state.ledger.bind_turn(item)
        self.state.ledger.evidence(item['seq'],'Codex observed turn/completed status ' +
            {'completed':'completed','failed':'failed','stopped':'interrupted'}[activity])
        self.state.transition(item['seq'],'complete')
        self.tracker.record(session,item,'complete')
        return item

    def record_root(self):
        return Path(self.tracker.binding('project')['metadata_path'])

    def test_indexes_link_to_work_without_copying_responses_or_promoting_commands(self):
        session=self.conversation()
        result='Negative result: method A reduced validation accuracy to 48%; see results.csv.'
        work=self.saved(session,'Compare methods\nwith held-out evaluation.',result)
        control=self.saved(session,'/status','Operational status response sentinel',role='control')
        failed=self.saved(session,'Continue analysis','Transport failure sentinel',activity='failed')
        prefix=self.record_root()/'.intern_workspace/conversations'/session['id']
        knowledge=(prefix/'knowledge.md').read_text()
        self.assertIn('Compare methods with held-out evaluation.',knowledge)
        self.assertIn('turns/turn_'+str(work['seq'])+'.md#response',knowledge)
        self.assertIn('complete / failed',knowledge)
        self.assertNotIn('turns/turn_'+str(control['seq'])+'.md#response',knowledge)
        for turn in (work,control,failed):
            document=(prefix/'turns'/('turn_'+str(turn['seq'])+'.md')).read_text()
            self.assertEqual(message_section(document,'Request'),turn['text'])
            self.assertEqual(message_section(document,'Response'),turn['result'])
            self.assertNotIn(turn['result'],knowledge)
        self.assertIn('not verified knowledge',knowledge)
        context=memory.context(self.tracker,session)
        self.assertIn(result,context)  # Useful negative research results are not transport failures.
        self.assertIn('Source: .intern_workspace/',context)
        self.assertNotIn('Operational status response sentinel',context)
        self.assertNotIn('Transport failure sentinel',context)

    def test_task_memory_does_not_mix_other_named_tasks_or_idle_chat(self):
        session=self.conversation()
        idle=self.saved(session,'Idle discussion','Idle answer sentinel')
        self.state.ledger.task(session,'start','First named task')
        first=self.saved(session,'First task question','First task answer sentinel')
        first_task=self.state.ledger.selection(session['id'])
        memory.remember(self.tracker,session,'task','First task owner knowledge')
        self.state.ledger.task(session,'pause')
        self.state.ledger.task(session,'start','Second named task')
        second=self.saved(session,'Second task question','Second task answer sentinel')
        memory.remember(self.tracker,session,'task','Second task owner knowledge')
        context=memory.context(self.tracker,session)
        self.assertIn(second['result'],context)
        self.assertIn('Second task owner knowledge',context)
        for text in (idle['result'],first['result'],'First task owner knowledge'):
            self.assertNotIn(text,context)
        self.state.ledger.task(session,'pause')
        self.assertIn(idle['result'],memory.context(self.tracker,session))
        self.assertNotIn(second['result'],memory.context(self.tracker,session))
        self.state.ledger.task(session,'use',first_task['id'])
        context=memory.context(self.tracker,session)
        self.assertIn(first['result'],context)
        self.assertIn('First task owner knowledge',context)
        self.assertNotIn(second['result'],context)
        record_projection.project(self.tracker,self.tracker.binding('project'))
        text=(self.record_root()/'.intern_workspace/tasks'/first_task['id']/'task_knowledge.md').read_text()
        self.assertIn('../../conversations/'+session['id']+'/turns/turn_'+str(first['seq'])+'.md#response',text)
        self.assertIn('First task owner knowledge',text)
        self.assertNotIn(first['result'],text)

    def test_identical_owner_note_retries_create_no_revision_commit_or_duplicate(self):
        session=self.conversation()
        memory.remember(self.tracker,session,'rule','GitHub only')  # Short constraints remain valid.
        before=(self.state.db.execute('SELECT count(*) FROM record_events').fetchone()[0],
                self.raw(self.record_root(),'rev-parse','HEAD'),len(self.tracker.pushes))
        result=memory.remember(self.tracker,session,'rule','  GitHub only\n')
        self.assertIn('already recorded',result)
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM workspace_memory').fetchone()[0],1)
        self.assertEqual(before,(self.state.db.execute('SELECT count(*) FROM record_events').fetchone()[0],
                               self.raw(self.record_root(),'rev-parse','HEAD'),len(self.tracker.pushes)))
        memory.remember(self.tracker,session,'note','private-test-sentinel-123 is redacted')
        memory.remember(self.tracker,session,'note','[redacted] is redacted')
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM workspace_memory').fetchone()[0],2)

    def test_deduplication_preserves_kind_task_scope_and_different_evidence(self):
        session=self.conversation()
        memory.remember(self.tracker,session,'rule','GitHub only')
        memory.remember(self.tracker,session,'note','GitHub only')
        self.state.ledger.task(session,'start','Task A')
        memory.remember(self.tracker,session,'task','GitHub only')
        memory.remember(self.tracker,session,'task','Validation: 48%')
        memory.remember(self.tracker,session,'task','Validation: 49%')
        self.state.ledger.task(session,'pause')
        self.state.ledger.task(session,'start','Task B')
        memory.remember(self.tracker,session,'task','GitHub only')
        notes=[dict(row) for row in self.state.db.execute('SELECT * FROM workspace_memory')]
        self.assertEqual(len(notes),6)
        self.assertEqual(len(list(distinct_notes(notes))),6)

    def test_legacy_duplicate_notes_are_hidden_in_views_but_remain_restorable(self):
        session=self.conversation()
        for _ in range(15):
            revision=self.state.ledger.revision('project','memory','memory','memory',conversation=session['id'])
            self.state.db.execute('INSERT INTO workspace_memory(workspace,kind,body,at,revision) VALUES(?,?,?,?,?)',
                                  ('project','rule','GitHub only',time.time(),revision))
        self.tracker.record(session,phase='Ready')
        self.assertEqual((self.record_root()/'.intern_workspace/memory.md').read_text().count('GitHub only'),1)
        self.assertEqual(memory.context(self.tracker,session).count('GitHub only'),1)
        data=json.loads((self.record_root()/'.intern_workspace/recovery.json').read_text())
        self.assertEqual(len(data['notes']),15)
        clone,state,tracker=self.fresh()
        recovery.restore(tracker,clone,self.tracker.binding('project')['repo_url'],'intern-management/test',True)
        self.assertEqual(state.db.execute('SELECT count(*) FROM workspace_memory').fetchone()[0],15)

    def test_reprojection_preserves_completed_turns_history_and_git_idempotency(self):
        session=self.conversation()
        self.saved(session,'What did we learn?','Use the measured result and preserve its limitation.')
        self.saved(session,'/status','Status information remains recorded',role='control')
        root=self.record_root()/'.intern_workspace'
        before={str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}
        head=self.raw(self.record_root(),'rev-parse','HEAD')
        self.tracker.record(session,phase='Ready')
        after={str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}
        self.assertEqual(before,after)
        self.assertEqual(head,self.raw(self.record_root(),'rev-parse','HEAD'))
        clone,state,tracker=self.fresh()
        recovery.restore(tracker,clone,self.tracker.binding('project')['repo_url'],'intern-management/test',True)
        actual=[tuple(row) for row in state.db.execute('SELECT text,result,role FROM events ORDER BY seq')]
        expected=[tuple(row) for row in self.state.db.execute('SELECT text,result,role FROM events ORDER BY seq')]
        self.assertEqual(actual,expected)

    def test_old_copying_views_upgrade_without_rewriting_completed_turns_or_history(self):
        session=self.conversation()
        item=self.saved(session,'Explain the measured result','Original reported result and its limitation.')
        prefix=self.record_root()/'.intern_workspace/conversations'/session['id']
        turn=prefix/'turns'/('turn_'+str(item['seq'])+'.md')
        history=prefix/'history_log.md'
        original=(turn.read_bytes(),history.read_bytes())
        knowledge=prefix/'knowledge.md'
        knowledge.write_text(render('Conversation knowledge',parse(knowledge.read_text()),
            [('Recent outcomes',quote(item['result']))]))
        record_projection.project(self.tracker,self.tracker.binding('project'))
        self.assertNotIn(item['result'],knowledge.read_text())
        self.assertIn('#response',knowledge.read_text())
        self.assertEqual(original,(turn.read_bytes(),history.read_bytes()))

    def test_large_work_history_has_a_bounded_readable_index(self):
        turns=[dict(seq=i,role='agent',result='Measured result',phase='complete',activity='completed',
                    sync='synced',task_id=None,text='Long question ' * 1000) for i in range(250)]
        index=turn_index(turns,lambda t:'turns/turn_'+str(t['seq'])+'.md',str)
        self.assertLess(len(index),35000)
        self.assertEqual(index.count('#response)'),100)
        self.assertNotIn('turns/turn_0.md',index)
        self.assertIn('turns/turn_249.md#response',index)
        self.assertIn('Older source records remain',index)
        self.assertEqual(len(turns),250)

    def test_values_are_sanitized_before_index_and_context_truncation(self):
        session=self.conversation()
        self.saved(session,'Q'*145+'private-test-sentinel-123 explanation','A measured result')
        knowledge=(self.record_root()/'.intern_workspace/conversations'/session['id']/'knowledge.md').read_text()
        self.assertNotIn('private-test',knowledge)
        self.saved(session,'Q'*590+'private-test-sentinel-123 explanation',
                   'R'*1792+'private-test-sentinel-123 result')
        context=memory.context(self.tracker,session)
        self.assertNotIn('private-',context)
        self.assertIn('[redacted]',context)


if __name__=='__main__':
    unittest.main()
