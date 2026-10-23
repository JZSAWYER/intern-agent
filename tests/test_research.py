import json
import os
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch

import test_live as fixtures
import test_features as feature_fixtures
from test_tracking import LocalTracking
from intern_personal.auto_recovery import Recovery, observe
from intern_personal.core import Invalid
from intern_personal.feature_state import validate as validate_features
from intern_personal.live_state import LiveState, write_private
from intern_personal.prompt_context import publish
from intern_personal.research import (Research, actor, change, duration, execute, guard,
                                     listing, record, start, validate_record)


class ResearchTests(unittest.TestCase):
    setUp = fixtures.LiveTests.setUp
    event = fixtures.LiveTests.event

    def session(self, name='research'):
        s = self.relay.open_session(name)
        self.state.db.execute('''INSERT INTO tracked_interns(session,intern_name,code_path,code_branch,home_branch,metadata_mode,published)
            VALUES(?,?,?,?,?,'bot_branch',1)''', (s['id'], 'intern_' + s['id'], self.tracker.code_directory(s),
                                                'intern-agent/' + name, 'intern-agent/' + name))
        return self.state.session(s['id'])

    def watch(self, session=None, **options):
        s = session or self.session()
        return s, start(self.state, s, '10h', 'Inspect the existing experiment and saved results',
                       evidence=['runs/experiment/status.json'], key='experiment', **options)

    def make_due(self, watch):
        self.state.db.execute('UPDATE research_watches SET due_at=? WHERE id=?', (time.time() - 1, watch['id']))
        Research(self.relay).scan()
        return self.state.db.execute('SELECT seq FROM research_runs WHERE watch=? ORDER BY number DESC LIMIT 1', (watch['id'],)).fetchone()

    def finish_turn(self, session, *, status='completed', action=None):
        def run(s, text, **kwargs):
            item = dict(self.state.db.execute("SELECT * FROM events WHERE session=? AND state='running' AND role='agent' ORDER BY seq DESC LIMIT 1", (s['id'],)).fetchone())
            observe(self.state, s, 'research-turn-' + str(item['seq']), 'inProgress', seq=item['seq'])
            self.host.calls.append((s, text))
            if action:
                action(item)
            self.state.ledger.evidence(item['seq'], 'Codex observed turn/completed status ' + status)
            observe(self.state, s, 'research-turn-' + str(item['seq']), status, seq=item['seq'])
            return 'Existing experiment inspected; no new job launched.'
        with patch.object(self.host, 'run', side_effect=run):
            return self.relay.process_once('agent')

    def test_ten_hour_alarm_is_quiet_until_due_and_survives_reopen(self):
        s, w = self.watch()
        messages_before = list(self.api.messages)
        due = self.state.db.execute('SELECT due_at FROM research_watches').fetchone()[0]
        Research(self.relay).scan(now=due - 1)
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM research_runs').fetchone()[0], 0)
        self.assertEqual(self.host.calls, [])
        self.assertEqual(self.api.messages, messages_before)
        self.state.close()
        recovered = LiveState(self.state.root)
        try:
            self.assertEqual(listing(recovered, s)[0]['id'], w['id'])
        finally:
            recovered.close()
        Research(self.relay).scan(now=due)
        Research(self.relay).scan(now=due + 1000)
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM research_runs').fetchone()[0], 1)
        with patch('intern_personal.research.time.time', return_value=due + 1001):
            self.assertEqual(self.finish_turn(s), 'complete')
        self.assertEqual(self.host.calls[0][0]['thread_id'], s['thread_id'])
        self.assertIn('Scheduled research check', self.host.calls[0][1])
        self.assertTrue(any(chat == s['chat_id'] for chat, text, key in self.api.messages))

    def test_busy_thread_waits_and_other_conversation_stays_independent(self):
        s, w = self.watch()
        other = self.session('independent')
        self.host.active[s['id']] = (s['thread_id'], 'running')
        self.assertIsNone(self.make_due(w))
        self.relay.receive(self.event('Owner prompt unchanged', other['chat_id'], 'om_other'))
        self.assertEqual(self.relay.process_once('agent'), 'complete')
        self.assertEqual(self.host.calls[-1][1], 'Owner prompt unchanged')
        self.host.active.clear()
        self.assertIsNotNone(self.make_due(w))

    def test_authoritative_backend_busy_prevents_a_second_turn(self):
        s, w = self.watch()
        self.host.turn_state = lambda session: {'status':'inProgress','turn_id':None}
        self.assertIsNone(self.make_due(w))
        self.host.turn_state = lambda session: {'status':'completed','turn_id':'ended'}
        self.assertIsNotNone(self.make_due(w))

    def test_owner_message_has_priority_over_an_already_queued_alarm(self):
        s, w = self.watch()
        run = self.make_due(w)
        self.relay.receive(self.event('New owner instruction', s['chat_id'], 'om_new_owner'))
        claimed = self.state.claim('agent')
        self.assertNotEqual(claimed['seq'], run['seq'])
        self.assertEqual(claimed['text'], 'New owner instruction')

    def test_older_alarm_can_follow_newer_ended_delivered_review(self):
        s,w=self.watch();run=self.make_due(w)
        self.relay.receive(self.event('Owner priority',s['chat_id'],'om_owner_priority'))
        self.assertEqual(self.finish_turn(s),'complete')
        owner=dict(self.state.db.execute("SELECT * FROM events WHERE message_id='om_owner_priority'").fetchone())
        for phase in ['record_blocked','sync_pending']:
            with self.subTest(phase=phase):
                self.state.transition(owner['seq'],phase)
                self.state.db.execute('UPDATE turn_records SET activity=? WHERE seq=?',('unknown',owner['seq']))
                self.assertIsNone(self.state.claim('agent',s['id']))
                self.state.db.execute('UPDATE turn_records SET activity=? WHERE seq=?',('completed',owner['seq']))
                claimed=self.state.claim('agent',s['id'])
                self.assertIsNotNone(claimed);self.assertEqual(claimed['seq'],run['seq'])
                self.state.transition(run['seq'],'queued')
        self.assertEqual(self.state.db.execute('SELECT state FROM events WHERE seq=?',(owner['seq'],)).fetchone()[0],'sync_pending')

    def test_older_alarm_keeps_uncertain_or_undelivered_review_blocked(self):
        s,w=self.watch();run=self.make_due(w)
        self.relay.receive(self.event('Owner priority',s['chat_id'],'om_owner_priority'))
        self.assertEqual(self.finish_turn(s),'complete')
        owner=dict(self.state.db.execute("SELECT * FROM events WHERE message_id='om_owner_priority'").fetchone())
        self.state.db.execute('UPDATE events SET state=?,receipt=NULL WHERE seq=?',('record_blocked',owner['seq']))
        self.assertIsNone(self.state.claim('agent',s['id']))
        self.state.db.execute('UPDATE events SET state=?,receipt=? WHERE seq=?',('uncertain_execution',owner['receipt'],owner['seq']))
        self.assertIsNone(self.state.claim('agent',s['id']))
        self.assertEqual(self.state.db.execute('SELECT state FROM events WHERE seq=?',(run['seq'],)).fetchone()[0],'queued')

    def test_busy_dispatch_race_defers_without_cancellation_or_prompt(self):
        s, w = self.watch()
        run = self.make_due(w)
        self.host.active[s['id']] = (s['thread_id'], 'manual-turn')
        self.assertEqual(self.relay.process_once('agent'), 'deferred')
        self.assertEqual(self.host.calls, [])
        self.assertEqual(self.state.db.execute('SELECT state FROM events WHERE seq=?', (run['seq'],)).fetchone()[0], 'queued')
        self.assertIsNone(self.state.claim('agent'))

    def test_stop_during_busy_guard_does_not_resurrect_queued_alarm(self):
        s,w=self.watch();run=self.make_due(w)
        def raced(relay,session):
            change(self.state,s,w['id'],'cancel',source=dict(seq=0,sender='ou_owner',role='control',session=s['id']))
            return True
        with patch('intern_personal.research.physical_busy',side_effect=raced):
            self.assertEqual(self.relay.process_once('agent'),'cancelled')
        self.assertEqual(listing(self.state,s)[0]['state'],'cancelled')
        self.assertEqual(self.state.db.execute('SELECT state FROM events WHERE seq=?',(run['seq'],)).fetchone()[0],'cancelled')
        self.assertEqual(self.host.calls,[])

    def test_clock_rollback_does_not_allow_early_dispatch(self):
        s,w=self.watch();run=self.make_due(w)
        self.state.db.execute('UPDATE research_watches SET due_at=?',(time.time()+3600,))
        self.assertEqual(self.relay.process_once('agent'),'deferred')
        self.assertEqual(self.host.calls,[])

    def test_checks_recur_from_completion_without_catch_up_or_chat_heartbeats(self):
        s, w = self.watch(checks=2)
        self.make_due(w)
        self.finish_turn(s)
        Research(self.relay).scan()
        updated = listing(self.state, s)[0]
        self.assertEqual(updated['state'], 'waiting')
        self.assertGreater(self.state.db.execute('SELECT due_at FROM research_watches').fetchone()[0], time.time() + 3590)
        for _ in range(3): Research(self.relay).scan()
        self.assertEqual(len(self.host.calls), 1)
        self.make_due(w)
        self.finish_turn(s)
        Research(self.relay).scan()
        self.assertEqual(listing(self.state, s)[0]['state'], 'exhausted')
        self.assertEqual(len(self.host.calls), 2)
        for _ in range(3): Research(self.relay).scan()
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM recovery_notices').fetchone()[0], 1)

    def test_check_can_finish_or_defer_its_own_watch_and_never_extend_budget(self):
        s, w = self.watch()
        self.make_due(w)
        self.finish_turn(s, action=lambda item: change(self.state, s, w['id'], 'defer', after='2h', note='Remaining job still active'))
        Research(self.relay).scan()
        row = self.state.db.execute('SELECT * FROM research_watches').fetchone()
        self.assertGreater(row['due_at'], time.time() + 7190)
        self.make_due(w)
        self.finish_turn(s, action=lambda item: change(self.state, s, w['id'], 'finish', note='Validated results saved'))
        Research(self.relay).scan()
        self.assertEqual(listing(self.state, s)[0]['state'], 'completed')
        with self.assertRaises(Invalid): change(self.state, s, w['id'], 'resume')

    def test_scheduled_checks_cannot_spawn_replacement_watches_or_manage_another_watch(self):
        s, w = self.watch()
        second = start(self.state, s, '2h', 'Another existing experiment', key='second')
        self.make_due(w)
        item = self.state.claim('agent')
        guard(self.relay, item)
        for operation in [lambda: start(self.state, s, '1h', 'Another loop'),
                          lambda: change(self.state, s, second['id'], 'finish'),
                          lambda: change(self.state, s, w['id'], 'resume')]:
            with self.assertRaises(Invalid): operation()

    def test_stop_cancels_waiting_and_queued_alarms_without_stopping_background_jobs(self):
        s, w = self.watch()
        run = self.make_due(w)
        with patch.object(self.host, 'interrupt', create=True):
            self.relay.control(dict(sender='ou_owner', chat_id=s['chat_id'], session=s['id'], text='/stop'))
        self.assertEqual(listing(self.state, s)[0]['state'], 'cancelled')
        self.assertEqual(self.state.db.execute('SELECT state FROM events WHERE seq=?', (run['seq'],)).fetchone()[0], 'cancelled')
        self.assertEqual(self.host.calls, [])

    def test_task_branch_or_thread_change_never_redirects_the_alarm(self):
        for mode in ['task','branch','thread']:
            s, w = self.watch(self.session(mode))
            if mode == 'task': self.state.ledger.task(s, 'start', 'Different owner task')
            if mode == 'branch': self.state.db.execute("UPDATE tracked_interns SET code_branch='other-branch' WHERE session=?", (s['id'],))
            if mode == 'thread': self.state.set_session(s['id'], thread_id='replacement-thread')
            self.make_due(w)
            self.assertEqual(listing(self.state, s)[0]['state'], 'superseded' if mode == 'thread' else 'paused')
        self.assertEqual(self.host.calls, [])

    def test_unknown_execution_is_held_for_review_never_replayed(self):
        s, w = self.watch()
        run = self.make_due(w)
        self.state.transition(run['seq'], 'uncertain_execution')
        Research(self.relay).scan()
        self.state.recover()
        Research(self.relay).scan()
        self.assertEqual(listing(self.state, s)[0]['state'], 'review_needed')
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM research_runs').fetchone()[0], 1)
        self.assertEqual(self.host.calls, [])

    def test_restart_after_dispatch_keeps_exact_event_and_does_not_replay(self):
        s, w = self.watch()
        run = self.make_due(w)
        item = self.state.claim('agent')
        from intern_personal.dispatch import start as begin_dispatch
        begin_dispatch(self.state,item)
        self.state.recover()
        Research(self.relay).scan()
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM research_runs').fetchone()[0], 1)
        self.assertEqual(self.state.db.execute('SELECT state FROM events WHERE seq=?', (run['seq'],)).fetchone()[0], 'uncertain_execution')

    def test_matching_recovery_chain_is_followed_without_a_competing_alarm(self):
        s, w = self.watch()
        run = self.make_due(w)
        self.state.transition(run['seq'], 'complete', result='Failed network', receipt='om_failure')
        observe(self.state, s, 'failed-turn', 'failed', seq=run['seq'])
        self.state.db.execute("INSERT INTO recovery_episodes VALUES('episode',?,?,?, ?,NULL,?,'waiting',0,?,?,?, ?,?)",
            (s['id'], s['thread_id'], 'failed-turn', run['seq'], 'intern-agent/research', time.time(), 'failed-turn', 'network', time.time(), time.time()))
        Research(self.relay).scan()
        self.assertEqual(listing(self.state, s)[0]['state'], 'queued')
        Recovery(self.relay).queue_run(s, dict(self.state.db.execute('SELECT * FROM recovery_episodes').fetchone()))
        continuation = self.state.db.execute('SELECT seq FROM recovery_runs').fetchone()[0]
        observe(self.state, s, 'recovered-turn', 'completed', seq=continuation)
        self.state.transition(continuation, 'complete', result='Recovered', receipt='om_recovered')
        Research(self.relay).scan()
        self.assertEqual(listing(self.state, s)[0]['state'], 'waiting')
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM research_runs').fetchone()[0], 1)

    def test_idempotency_bounds_paths_and_permissions(self):
        s, w = self.watch()
        self.assertEqual(start(self.state, s, '10h', 'Inspect the existing experiment and saved results', evidence=['runs/experiment/status.json'], key='experiment')['id'], w['id'])
        with self.assertRaises(Invalid): start(self.state, s, '1h', 'Different plan', key='experiment')
        for value in ['0h','1s','1.5h','-10h','31d','tomorrow']:
            with self.assertRaises(Invalid): duration(value)
        for options in [dict(checks=0), dict(checks=49), dict(horizon='1h'), dict(evidence=['../credentials.json']), dict(evidence=['/tmp/status']), dict(evidence=['.codex/auth.json'])]:
            with self.assertRaises(Invalid): start(self.state, s, '10h', 'Existing experiment', **options)
        with self.assertRaises(Invalid): start(self.state, s, '1h', 'app_secret=example-local-fixture-only')
        with self.assertRaises(Invalid): start(self.state, s, '1h', 'Experiment', source=dict(seq=99, sender='ou_other', role='agent', session=s['id']))
        untracked = self.relay.open_session('untracked')
        with self.assertRaises(Invalid): start(self.state, untracked, '1h', 'Experiment')

    def test_context_commands_and_portable_history_do_not_export_live_routing(self):
        s, w = self.watch()
        context = self.state.runtime / 'prompt-context'
        context.mkdir(mode=0o700)
        path = context / (s['id'] + '.json')
        write_private(path, dict(thread_id=s['thread_id']))
        with patch.dict(os.environ, {'CODEX_THREAD_ID':s['thread_id']}):
            self.assertEqual(actor(self.state, path)['id'], s['id'])
            self.assertEqual(execute(self.state, s, ['status'])[0]['id'], w['id'])
        with patch.dict(os.environ, {'CODEX_THREAD_ID':'foreign'}):
            with self.assertRaises(Invalid): actor(self.state, path)
        feature = dict(self.state.db.execute("SELECT * FROM feature_documents WHERE kind='research_watch'").fetchone())
        feature['data'] = json.loads(feature['data'])
        validate_features([feature], s['workspace'], {s['id']}, set())
        for private in [s['thread_id'], s['chat_id'], 'ou_owner', 'cli_test']:
            self.assertNotIn(private, json.dumps(feature))
        change(self.state, s, w['id'], 'pause', note='Owner temporarily paused checks')
        after = json.loads(self.state.db.execute('SELECT data FROM feature_documents').fetchone()[0])
        self.assertEqual(after['history'][:len(feature['data']['history'])], feature['data']['history'])

    def test_feishu_help_and_start_commands_use_same_pipeline(self):
        s = self.session()
        self.relay.receive(self.event('/research start 10h "Inspect saved experiment" --every 2h --checks 3 --for 48h', s['chat_id']))
        self.assertEqual(self.relay.process_once('control'), 'complete')
        self.assertEqual(listing(self.state, s)[0]['max_checks'], 3)
        self.assertEqual(self.host.calls, [])
        self.assertIn('survive relay restarts', self.relay.control(dict(sender='ou_owner', chat_id=s['chat_id'], session=s['id'], text='/help research')))

    def test_observation_errors_pause_once_instead_of_creating_duplicate_jobs(self):
        s, w = self.watch()
        self.host.turn_state = lambda session: (_ for _ in ()).throw(OSError('Private connection details'))
        self.make_due(w)
        for _ in range(2):
            self.state.db.execute('UPDATE research_watches SET retry_at=0')
            Research(self.relay).scan()
        self.assertEqual(listing(self.state, s)[0]['state'], 'review_needed')
        self.assertEqual(self.host.calls, [])
        notices = self.state.db.execute('SELECT text FROM recovery_notices').fetchall()
        self.assertEqual(len(notices), 1)
        self.assertNotIn('Private connection details', notices[0][0])

    def test_stale_tick_cannot_overwrite_owner_completion(self):
        from intern_personal.research import end
        s,w=self.watch()
        stale=dict(self.state.db.execute('SELECT * FROM research_watches').fetchone())
        change(self.state,s,w['id'],'finish',note='Owner confirmed completion')
        end(self.state,stale,'review_needed','Stale observer error')
        self.assertEqual(listing(self.state,s)[0]['state'],'completed')
        self.assertEqual(listing(self.state,s)[0]['note'],'Owner confirmed completion')

    def test_concurrent_ticks_reserve_one_event_transactionally(self):
        s, w = self.watch()
        self.state.db.execute('UPDATE research_watches SET due_at=?', (time.time() - 1,))
        errors = []
        def tick():
            try: Research(self.relay).scan()
            except Exception as error: errors.append(type(error).__name__)
            finally: self.state.close()
        threads = [threading.Thread(target=tick) for _ in range(3)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM research_runs').fetchone()[0], 1)


class ResearchArchiveTests(unittest.TestCase):
    setUp=feature_fixtures.FeaturesTests.setUp
    raw=feature_fixtures.FeaturesTests.raw
    attach=feature_fixtures.FeaturesTests.attach
    conversation=feature_fixtures.FeaturesTests.conversation

    def test_real_git_markdown_roundtrip_restores_reference_without_executable_alarms(self):
        from intern_personal import recovery
        from intern_personal.live_state import private_json
        from intern_personal.records import validate_tree
        s=self.conversation()
        watch=start(self.state,s,'10h','Inspect existing experiment',key='archive-test',evidence=['runs/status.json'])
        self.tracker.record(s)
        change(self.state,s,watch['id'],'pause',note='Owner paused checks')
        self.tracker.record(s)
        binding=self.tracker.record_binding(s);directory=Path(binding['metadata_path'])
        record_path=directory/'.intern_workspace/features/research_watch'/(watch['id']+'.md')
        self.assertTrue(record_path.is_file());validate_tree(directory/'.intern_workspace')
        archive=json.loads((directory/'.intern_workspace/recovery.json').read_text())
        documents=[(str(p.relative_to(directory)),p.read_text()) for p in (directory/'.intern_workspace').rglob('*.md')]
        recovery.validate(archive,documents)
        portable=next(f for f in archive['feature_state'] if f['kind']=='research_watch')
        self.assertEqual(len(portable['data']['history']),2)
        self.assertNotIn(s['thread_id'],json.dumps(portable))
        clone=self.root/'recovered-source'
        self.raw(self.root,'clone','-q','--branch','main',str(self.remote),str(clone))
        self.raw(clone,'remote','set-url','origin',binding['repo_url'])
        fresh=LiveState(self.root/'research-fresh');self.addCleanup(fresh.close)
        write_private(fresh.root/'credentials.json',private_json(self.state.root/'credentials.json'))
        write_private(fresh.root/'live.json',self.state.config())
        result=recovery.restore(LocalTracking(fresh,self.remote),clone,binding['repo_url'],binding['metadata_branch'],apply=True)
        self.assertTrue(result['applied'])
        self.assertEqual(fresh.db.execute('SELECT count(*) FROM research_watches').fetchone()[0],0)
        self.assertEqual(fresh.db.execute('SELECT count(*) FROM research_runs').fetchone()[0],0)
        self.assertEqual(fresh.db.execute("SELECT count(*) FROM feature_documents WHERE kind='research_watch'").fetchone()[0],1)
        self.assertIsNone(fresh.identity()['owner'])
        self.assertEqual(self.raw(self.remote,'rev-parse','main'),self.original)


if __name__ == '__main__':
    unittest.main()
