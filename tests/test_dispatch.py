import sqlite3
import time
import unittest
from unittest.mock import patch

import test_live as fixtures
from intern_personal import dispatch
from intern_personal.core import Invalid
from intern_personal.research import Research, start as research_start


class DispatchTests(unittest.TestCase):
    setUp = fixtures.LiveTests.setUp
    event = fixtures.LiveTests.event

    def request(self, text='Send the paper and next research direction'):
        session = self.relay.open_session('project')
        self.relay.receive(self.event(text, session['chat_id']))
        return session

    def ready(self):
        self.state.db.execute('UPDATE dispatch_attempts SET retry_at=0')

    def test_preflight_and_failed_error_reporting_cannot_orphan_claim(self):
        session = self.request()
        before = len(self.api.messages)
        with patch('intern_personal.research.guard', side_effect=RuntimeError('private database detail')), \
                patch('intern_personal.flow_watchdog.preflight', side_effect=sqlite3.OperationalError('private diagnostic detail')):
            self.assertEqual(self.relay.process_once('agent'), 'queued')
        event = dict(self.state.db.execute('SELECT * FROM events').fetchone())
        proof = dict(self.state.db.execute('SELECT * FROM dispatch_attempts').fetchone())
        self.assertEqual(event['state'], 'queued')
        self.assertEqual(event['text'], 'Send the paper and next research direction')
        self.assertEqual(self.host.calls, [])
        self.assertEqual(proof['stage'], 'preflight')
        self.assertEqual(proof['error_type'], 'OperationalError')
        self.assertNotIn('private', str(proof))
        self.assertFalse(self.state.ledger.busy(session['id']))
        self.assertEqual(self.relay.process_once('agent'), 'idle')
        self.ready()
        self.assertEqual(self.relay.process_once('agent'), 'complete')
        self.assertEqual([text for _, text in self.host.calls], [event['text']])
        self.assertEqual(len(self.api.messages), before + 1)

    def test_database_lock_during_retry_persistence_is_repaired_by_same_worker(self):
        self.request()
        with patch.object(self.relay, 'process_item', side_effect=sqlite3.OperationalError('database locked')), \
                patch('intern_personal.dispatch.retry', side_effect=sqlite3.OperationalError('still locked')):
            with self.assertRaises(sqlite3.OperationalError): self.relay.process_once('agent')
        self.assertEqual(self.host.calls, [])
        self.assertEqual(self.state.db.execute('SELECT state FROM events').fetchone()[0], 'running')
        self.assertEqual(self.relay.process_once('agent'), 'queued')
        self.ready()
        self.assertEqual(self.relay.process_once('agent'), 'complete')
        self.assertEqual(len(self.host.calls), 1)

    def test_failed_retry_rolls_back_old_transaction_before_recovering(self):
        self.request()
        def failure(item, role):
            self.state.db.execute('BEGIN IMMEDIATE')
            raise sqlite3.OperationalError('private transaction failure')
        with patch.object(self.relay, 'process_item', side_effect=failure):
            self.assertEqual(self.relay.process_once('agent'), 'queued')
        self.assertFalse(self.state.db.in_transaction)
        self.ready()
        self.assertEqual(self.relay.process_once('agent'), 'complete')
        self.assertEqual(len(self.host.calls), 1)

    def test_preflight_retries_are_bounded_and_retain_review(self):
        self.request()
        with patch.object(self.relay, 'process_item', side_effect=RuntimeError('private worker failure')):
            for expected in ['queued', 'queued', 'queued', 'record_blocked']:
                self.ready()
                self.assertEqual(self.relay.process_once('agent'), expected)
        self.ready()
        self.assertEqual(self.relay.process_once('agent'), 'idle')
        self.assertEqual(self.host.calls, [])
        self.assertEqual(self.state.db.execute('SELECT failures FROM dispatch_attempts').fetchone()[0], 4)
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM flow_incidents').fetchone()[0], 1)

    def test_restart_requeues_only_proven_unstarted_claims(self):
        self.request()
        first = self.state.claim('agent')
        self.state.recover()
        self.assertEqual(self.state.db.execute('SELECT state FROM events').fetchone()[0], 'queued')
        self.ready()
        second = self.state.claim('agent')
        self.assertNotEqual(first['_dispatch_token'], second['_dispatch_token'])
        with self.assertRaises(Invalid): dispatch.start(self.state, first)
        dispatch.start(self.state, second)
        self.state.recover()
        self.assertEqual(self.state.db.execute('SELECT state FROM events').fetchone()[0], 'uncertain_execution')
        self.assertEqual(self.host.calls, [])

    def test_backend_attempt_is_never_replayed(self):
        self.request()
        self.host.fail = True
        self.assertEqual(self.relay.process_once('agent'), 'uncertain_execution')
        self.state.recover()
        self.assertEqual(self.relay.process_once('agent'), 'idle')
        proof = self.state.db.execute('SELECT * FROM dispatch_attempts').fetchone()
        self.assertEqual(proof['stage'], 'dispatching')
        self.assertEqual(proof['failures'], 0)
        self.assertEqual(len(self.host.calls), 1)

    def test_legacy_running_event_without_proof_is_never_guessed_safe(self):
        self.request()
        item = self.state.claim('agent')
        self.state.db.execute('DELETE FROM dispatch_attempts')
        self.state.recover()
        self.assertEqual(self.state.db.execute('SELECT state FROM events').fetchone()[0], 'uncertain_execution')
        self.assertIsNone(dispatch.retry(self.state, item, RuntimeError()))
        self.assertEqual(self.host.calls, [])

    def test_failed_alarm_yields_to_owner_without_new_check_or_duplicate(self):
        session = self.relay.open_session('project')
        self.state.db.execute('''INSERT INTO tracked_interns(session,intern_name,code_path,code_branch,published)
            VALUES(?, 'intern_project', ?, 'intern-agent/project', 1)''',
            (session['id'], self.tracker.code_directory(session)))
        watch = research_start(self.state, session, '1h', 'Inspect existing jobs', key='existing')
        self.state.db.execute('UPDATE research_watches SET due_at=?', (time.time()-1,))
        Research(self.relay).scan()
        with patch.object(self.relay, 'process_item', side_effect=sqlite3.OperationalError('database locked')):
            self.assertEqual(self.relay.process_once('agent'), 'queued')
        self.relay.receive(self.event('Send the paper', session['chat_id']))
        self.assertEqual(self.relay.process_once('agent'), 'complete')
        self.assertEqual(self.host.calls[0][1], 'Send the paper')
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM research_runs').fetchone()[0], 1)
        self.assertEqual(self.state.db.execute('SELECT checks FROM research_watches WHERE id=?', (watch['id'],)).fetchone()[0], 1)


if __name__ == '__main__': unittest.main()
