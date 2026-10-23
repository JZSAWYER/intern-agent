import json
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import test_live as fixture
import test_terminal as terminal_fixture
from intern_personal.codex_bridge import BridgeError, CodexHost
from intern_personal.feishu_live import Relay
from intern_personal.live_state import LiveState
from intern_personal.quota import (
    MAX_AGE_SECONDS, UNAVAILABLE, format_line, normalize, observe, reply, snapshot,
)


def reported(used=25, duration=300, reset=None):
    window = {'usedPercent': used, 'windowDurationMins': duration}
    if reset is not None:
        window['resetsAt'] = reset
    return {'rateLimits': {'primary': window}}


class QuotaFormatTests(unittest.TestCase):
    def test_reports_each_window_and_percentage_left_without_combining_quotas(self):
        data = reported(33.36)
        data['rateLimits']['secondary'] = {'usedPercent': 80, 'windowDurationMins': 10080}
        self.assertEqual(format_line((normalize(data), 100), now=101),
                         'Quota (account): 5h: 33.4% used, 66.6% left | 7d: 80% used, 20% left')

    def test_single_weekly_window_zero_and_exhausted_are_not_missing_data(self):
        self.assertEqual(format_line((normalize(reported(0, 10080)), 100), now=100),
                         'Quota (account): 7d: 0% used, 100% left')
        self.assertIn('100% used, 0% left', format_line((normalize(reported(100)), 100), now=100))

    def test_expired_stale_future_and_unknown_quotas_are_unavailable(self):
        limits = normalize(reported(reset=101))
        for observed, now in [((limits, 100), 101), ((limits, 100), 100 + MAX_AGE_SECONDS + 1),
                              ((limits, 102), 101), (({}, 100), 100)]:
            self.assertEqual(format_line(observed, now=now), UNAVAILABLE)

    def test_native_shape_is_sanitized_and_bad_numeric_values_are_not_estimates(self):
        data = {'rate_limits': {'primary': {'used_percent': 2.0, 'window_minutes': 10080},
                               'credits': {'balance': 'private-credit-detail'}, 'plan_type': 'private-plan'}}
        clean = normalize(data)
        self.assertEqual(clean, {'primary': {'usedPercent': 2.0, 'windowDurationMins': 10080}})
        for invalid in [None, True, '25', -1, 101, float('nan'), float('inf'), 10 ** 400]:
            self.assertEqual(normalize(reported(invalid)), {})
        self.assertEqual(normalize({'rateLimits': None}), {})
        self.assertIsNone(normalize({'tokenUsage': {'totalTokens': 4000}}))

    def test_multi_bucket_fallback_uses_codex_without_guessing_between_other_buckets(self):
        quota = reported(10)['rateLimits']
        self.assertEqual(normalize({'rateLimitsByLimitId': {'codex': quota, 'other': reported(90)['rateLimits']}}),
                         normalize(reported(10)))
        self.assertEqual(normalize({'rateLimitsByLimitId': {'one': quota, 'two': quota}}), {})


class QuotaDeliveryTests(unittest.TestCase):
    setUp = fixture.LiveTests.setUp
    event = fixture.LiveTests.event

    def test_completed_reply_gets_one_footer_without_changing_owner_text_or_saved_result(self):
        session = self.relay.open_session('project')
        observe(self.state, session, reported(20))
        before = len(self.api.messages)
        owner = '  Original owner words\nwith whitespace  '
        self.relay.receive(self.event(owner, session['chat_id'], 'om_quota'))
        self.assertEqual(self.relay.process_once('agent'), 'complete')
        item = dict(self.state.db.execute("SELECT * FROM events WHERE message_id='om_quota'").fetchone())
        self.assertEqual(item['text'], owner)
        self.assertEqual(item['result'], 'Result: ' + owner)
        self.assertEqual(self.host.calls[0][1], owner)
        self.assertEqual(self.api.messages[-1][1], item['result'].rstrip() +
                         '\n\nQuota (account): 5h: 20% used, 80% left')
        self.assertEqual(self.api.messages[-1][1].count('Quota (account):'), 1)
        self.assertEqual(len(self.api.messages) - before, 1)
        self.assertEqual(len(self.api.removals), 1)

    def test_delivery_retry_reuses_frozen_footer_after_restart_and_does_not_rerun_codex(self):
        session = self.relay.open_session('project')
        observe(self.state, session, reported(20))
        self.relay.receive(self.event('Finish my task', session['chat_id'], 'om_retry_quota'))
        attempts = []
        def fail(chat, text, key):
            attempts.append((text, key))
            raise TimeoutError('Ambiguous send')
        with patch.object(self.api, 'send', side_effect=fail):
            self.assertEqual(self.relay.process_once('agent'), 'uncertain_delivery')
        seq = self.state.db.execute("SELECT seq FROM events WHERE message_id='om_retry_quota'").fetchone()[0]
        root = self.state.root
        self.state.close()
        self.state = LiveState(root)
        self.addCleanup(self.state.close)
        restarted = Relay(self.state, self.api, self.host, fixture.FakeTracker(self.state))
        observe(self.state, session, reported(75))
        self.state.resolve(seq, 'deliver')
        self.assertEqual(restarted.process_once('agent'), 'complete')
        self.assertEqual(self.api.messages[-1][1:], attempts[0])
        self.assertEqual(len(self.host.calls), 1)

    def test_missing_quota_does_not_block_completion_or_invent_usage(self):
        session = self.relay.open_session('project')
        self.relay.receive(self.event('My task', session['chat_id'], 'om_missing_quota'))
        self.assertEqual(self.relay.process_once('agent'), 'complete')
        self.assertTrue(self.api.messages[-1][1].endswith(UNAVAILABLE))
        self.assertEqual(len(self.host.calls), 1)

    def test_legacy_inflight_delivery_keeps_its_original_body_and_key_after_upgrade(self):
        session = self.relay.open_session('project')
        self.relay.receive(self.event('Old request', session['chat_id'], 'om_legacy_quota'))
        seq = self.state.db.execute("SELECT seq FROM events WHERE message_id='om_legacy_quota'").fetchone()[0]
        original = 'Already finished\n\n'
        self.state.transition(seq, 'sending', result=original)
        observe(self.state, session, reported(20))
        self.state.recover()
        self.state.resolve(seq, 'deliver')
        self.assertEqual(self.relay.process_once('agent'), 'complete')
        self.assertEqual(self.api.messages[-1][1:], (original, 'cli_test:om_legacy_quota'))
        self.assertEqual(self.host.calls, [])

    def test_management_commands_do_not_get_a_codex_completion_footer(self):
        self.relay.receive(self.event('/status'))
        self.assertEqual(self.relay.process_once('control'), 'complete')
        item = dict(self.state.db.execute('SELECT * FROM events').fetchone())
        self.assertEqual(self.api.messages[-1][1], item['result'])
        self.assertEqual(self.state.db.execute('SELECT COUNT(*) FROM reply_quota').fetchone()[0], 0)

    def test_quota_cache_is_scoped_to_the_reporting_session(self):
        first = self.relay.open_session('first')
        second = self.relay.open_session('second')
        observe(self.state, first, reported(12))
        self.assertEqual(snapshot(self.state, second), ({}, 0))
        self.assertNotIn('12% used', reply(self.state, {'seq': 500, 'session': second['id'],
                                                     'role': 'agent', 'result': 'Done'}))

    def test_actual_managed_turn_refreshes_quota_with_bounded_read_before_final_delivery(self):
        session = self.relay.open_session('project')
        self.relay.receive(self.event('Exactly my words', session['chat_id'], 'om_managed_quota'))
        calls = []
        events = iter([
            {'method': 'account/rateLimits/updated', 'params': dict(reported(99), threadId='foreign-thread')},
            {'method': 'account/rateLimits/updated', 'params': reported(21)},
            {'method': 'item/completed', 'params': {'threadId': session['thread_id'], 'turnId': 'fixture_turn',
                'item': {'type': 'agentMessage', 'phase': 'final_answer', 'text': 'Verified result'}}},
            {'method': 'turn/completed', 'params': {'threadId': session['thread_id'],
                'turn': {'id': 'fixture_turn', 'status': 'completed'}}},
        ])
        class Wire:
            def request(self, method, params, timeout=60):
                calls.append((method, params, timeout))
                if method == 'turn/start':
                    return {'turn': {'id': 'fixture_turn'}}
                return reported(22) if method == 'account/rateLimits/read' else {}
            def notification(self, timeout):
                return next(events)
            def close(self):
                pass
        host = CodexHost(self.state, tracker=self.tracker)
        self.relay.host = host
        with patch.object(host, 'ensure_started'), patch.object(host, 'ensure_terminal'), \
             patch.object(host, 'connect', return_value=Wire()):
            self.assertEqual(self.relay.process_once('agent'), 'complete')
        self.assertEqual(calls[-1], ('account/rateLimits/read', {}, 2))
        self.assertIn('22% used, 78% left', self.api.messages[-1][1])
        self.assertEqual(next(c[1]['input'] for c in calls if c[0] == 'turn/start'),
                         [{'type': 'text', 'text': 'Exactly my words'}])

    def test_quota_read_failure_preserves_observed_notification_and_completed_turn(self):
        session = self.relay.open_session('project')
        events = iter([
            {'method': 'account/rateLimits/updated', 'params': reported(30)},
            {'method': 'item/completed', 'params': {'item': {'type': 'agentMessage', 'text': 'Done'}}},
            {'method': 'turn/completed', 'params': {'turn': {'id': 'fixture_turn', 'status': 'completed'}}},
        ])
        reads = []
        class Wire:
            def request(self, method, params, timeout=60):
                if method == 'account/rateLimits/read':
                    reads.append(timeout)
                    raise TimeoutError('Quota endpoint unavailable')
                return {'turn': {'id': 'fixture_turn'}}
            def notification(self, timeout):
                return next(events)
        host = CodexHost(self.state, tracker=self.tracker)
        self.assertEqual(host._turn(Wire(), session, session['thread_id'], 'Owner text'), 'Done')
        self.assertEqual(reads, [2])
        self.assertIn('30% used, 70% left', format_line(snapshot(self.state, session)))

    def test_failed_turn_does_not_freeze_a_quota_for_a_later_owner_retry(self):
        session = self.relay.open_session('project')
        self.relay.receive(self.event('My task', session['chat_id'], 'om_failed_quota'))
        self.state.claim('agent')
        reads = []
        class Wire:
            def request(self, method, params, timeout=60):
                reads.append(method)
                return {'turn': {'id': 'fixture_turn'}}
            def notification(self, timeout):
                return {'method': 'turn/completed', 'params': {
                    'turn': {'id': 'fixture_turn', 'status': 'failed'}}}
        host = CodexHost(self.state, tracker=self.tracker)
        with self.assertRaises(BridgeError):
            host._turn(Wire(), session, session['thread_id'], 'My task')
        self.assertNotIn('account/rateLimits/read', reads)
        self.assertEqual(self.state.db.execute('SELECT COUNT(*) FROM reply_quota').fetchone()[0], 0)


class NativeQuotaTests(unittest.TestCase):
    setUp = terminal_fixture.TerminalTests.setUp
    event = terminal_fixture.TerminalTests.event
    message = terminal_fixture.TerminalTests.message
    attach = terminal_fixture.TerminalTests.attach

    def quota_event(self, used, at=None):
        at = time.time() if at is None else at
        value = {'type': 'event_msg', 'timestamp': datetime.fromtimestamp(at, timezone.utc).isoformat(),
                 'payload': {'type': 'token_count', 'info': {'total_token_usage': {'total_tokens': 123}},
                             'rate_limits': {'primary': {'used_percent': used, 'window_minutes': 10080},
                                             'credits': {'balance': 'private-credit-details'}}}}
        with self.rollout.open('a') as stream:
            stream.write(json.dumps(value) + '\n')

    def test_native_turns_in_same_poll_keep_their_own_completion_quota_and_final_result(self):
        self.event('task_started', turn_id='one')
        self.message('First owner request')
        self.attach()
        self.quota_event(10)
        self.event('task_complete', turn_id='one', last_agent_message='First result')
        self.event('task_started', turn_id='two')
        self.message('Second owner request')
        self.quota_event(12)
        self.event('task_complete', turn_id='two', last_agent_message='Second result')
        self.bridge.poll(self.session)
        self.bridge.poll(self.session)
        rows = [dict(r) for r in self.state.db.execute('SELECT * FROM events ORDER BY seq')]
        self.assertEqual([r['result'] for r in rows], ['First result', 'Second result'])
        self.assertIn('10% used, 90% left', reply(self.state, rows[0]))
        self.assertIn('12% used, 88% left', reply(self.state, rows[1]))
        stored = self.state.db.execute('SELECT limits FROM conversation_quota').fetchone()[0]
        self.assertNotIn('private-credit-details', stored)
        self.assertNotIn('123', stored)

    def test_native_stale_quota_is_not_shown_as_current(self):
        self.event('task_started', turn_id='current')
        self.message('Owner request')
        self.attach()
        self.quota_event(10, time.time() - MAX_AGE_SECONDS - 10)
        self.event('task_complete', turn_id='current', last_agent_message='Result')
        self.bridge.poll(self.session)
        row = dict(self.state.db.execute('SELECT * FROM events').fetchone())
        self.assertTrue(reply(self.state, row).endswith(UNAVAILABLE))

    def test_native_interruption_has_a_footer_without_new_execution(self):
        self.event('task_started', turn_id='current')
        self.message('Owner request')
        self.attach()
        self.quota_event(15)
        self.event('turn_aborted', turn_id='current')
        self.bridge.poll(self.session)
        row = dict(self.state.db.execute('SELECT * FROM events').fetchone())
        self.assertEqual(row['result'], 'The terminal turn was interrupted.')
        self.assertIn('15% used, 85% left', reply(self.state, row))

    def test_late_native_observation_does_not_replace_newer_quota(self):
        self.event('task_started', turn_id='current')
        self.message('Owner request')
        self.attach()
        observe(self.state, self.session, reported(30), observed_at=time.time())
        self.quota_event(20, time.time() - 10)
        self.event('task_complete', turn_id='current', last_agent_message='Result')
        self.bridge.poll(self.session)
        row = dict(self.state.db.execute('SELECT * FROM events').fetchone())
        self.assertIn('30% used, 70% left', reply(self.state, row))
