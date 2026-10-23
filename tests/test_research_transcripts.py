"""Exercise real published research references through both transcript adapters."""
import hashlib
import json
import time
import unittest
from unittest.mock import patch

import test_terminal as fixture
from test_live import FakeAPI, FakeHost
from intern_personal.auto_recovery import observe
from intern_personal.console_observer import ConsoleObserver
from intern_personal.core import Invalid
from intern_personal.delivery import pump
from intern_personal.feishu_live import Relay
from intern_personal.prompt_context import publish
from intern_personal.research import Research, start


class ResearchTranscriptTests(unittest.TestCase):
    setUp = fixture.TerminalTests.setUp
    event = fixture.TerminalTests.event
    message = fixture.TerminalTests.message
    attach = fixture.TerminalTests.attach
    reference = fixture.TerminalTests.reference
    environment = fixture.TerminalTests.environment
    idle_attachment = fixture.TerminalTests.idle_attachment

    def prepare_watch(self, managed):
        self.idle_attachment()
        self.state.db.execute("UPDATE events SET state='complete',receipt='om_prior'")
        self.state.db.execute("UPDATE turn_records SET phase='complete',activity='completed',sync='synced'")
        if managed:
            self.state.db.execute('INSERT INTO console_views SELECT * FROM terminal_attachments')
            self.state.db.execute('DELETE FROM terminal_attachments')
            self.bridge = ConsoleObserver(self.state, self.tracker)
        self.state.db.execute('''INSERT INTO tracked_interns(session,intern_name,code_path,code_branch,home_branch,published,metadata_mode)
            VALUES(?,?,?,?,?,1,'bot_branch')''', (self.session['id'], 'intern_fixture',
                self.tracker.code_directory(self.session), 'intern-agent/fixture', 'intern-agent/fixture'))
        self.watch = start(self.state, self.session, '10h', 'Inspect existing experiment and saved artifacts', key='transcript-check')
        self.state.db.execute('UPDATE research_watches SET due_at=?', (time.time() - 1,))
        host = FakeHost(self.state)
        self.api = FakeAPI()
        self.relay = Relay(self.state, self.api, host, self.tracker)
        Research(self.relay).scan()
        self.item = self.state.claim('agent')
        self.assertIsNotNone(self.item)
        self.assertIn('Scheduled research check', self.item['text'])
        self.refs = publish(self.tracker, self.session, 'An existing owner attachment remains reference data.')
        self.assertIn('intern-agent.research', self.refs)
        row = self.bridge.binding(self.session)
        snapshot = json.loads(row['snapshot'])
        snapshot['pending'] = dict(seq=self.item['seq'], prompt_sha256=hashlib.sha256(self.item['text'].encode()).hexdigest(), cursor=row['cursor'])
        self.state.db.execute('UPDATE ' + self.bridge.table + ' SET snapshot=? WHERE session=?', (json.dumps(snapshot), self.session['id']))
        self.event('task_started', turn_id='research-check')

    def delivered_check(self, managed):
        self.prepare_watch(managed)
        self.environment()
        # These are the producer's actual fragments, not a separately maintained
        # list or a manually stubbed acknowledgement of the expected prompt.
        for key, value in self.refs.items():
            self.reference(key, '<external_' + key + '>\n' + value['value'] + '\n</external_' + key + '>')
        snapshot = self.bridge.poll(self.session)
        self.assertEqual(snapshot['pending']['seq'], self.item['seq'])
        self.assertIsNone(snapshot['event_seq'])
        self.assertEqual(snapshot['request'], '')
        self.message(self.item['text'])
        answer = 'Existing jobs inspected. Saved results retained; next check scheduled.'
        self.event('task_complete', turn_id='research-check', last_agent_message=answer)
        # Reproduce the incident: RPC already saved a completed answer, while
        # the independent console acknowledgement still held reply delivery.
        self.state.transition(self.item['seq'], 'record_blocked', result=answer)
        self.state.ledger.update(self.item, 'record_blocked', activity='completed')
        observe(self.state, self.session, 'research-check', 'completed', seq=self.item['seq'])
        pump(self.relay)
        self.assertEqual(self.api.messages, [])
        self.bridge.poll(self.session)
        self.bridge.poll(self.session)
        row = self.state.db.execute('SELECT * FROM events WHERE seq=?', (self.item['seq'],)).fetchone()
        self.assertEqual(row['text'], self.item['text'])
        self.assertEqual(row['result'], answer)
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM terminal_turns WHERE event_seq=?', (self.item['seq'],)).fetchone()[0], 1)
        pump(self.relay)
        self.assertEqual(self.api.messages, [])  # Console completion is not Git completion.
        self.state.resolve(self.item['seq'],'retry')
        self.relay.retry_sync()
        self.assertEqual(self.relay.process_once('agent'), 'complete')
        pump(self.relay)
        pump(self.relay)
        self.assertEqual(len(self.api.messages), 1)
        self.assertEqual(self.api.messages[0][0], self.session['chat_id'])
        self.assertIn(answer, self.api.messages[0][1])
        self.assertEqual(self.relay.host.calls, [])
        Research(self.relay).scan()
        self.assertEqual(self.state.db.execute('SELECT state FROM research_watches').fetchone()[0], 'waiting')
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM research_runs').fetchone()[0], 1)

    def test_native_research_context_releases_saved_reply_without_prompt_replay(self):
        self.delivered_check(False)

    def test_managed_research_context_releases_saved_reply_without_prompt_replay(self):
        self.delivered_check(True)

    def test_unregistered_producer_context_fails_before_publishing_hook_file(self):
        self.prepare_watch(True)
        path = self.state.runtime / 'prompt-context' / (self.session['id'] + '.json')
        original = path.read_bytes()
        with patch('intern_personal.prompt_context.CONTEXT_KEYS', frozenset({'intern-agent.workspace'})):
            with self.assertRaisesRegex(Invalid, 'Unregistered relay reference context'):
                publish(self.tracker, self.session)
        self.assertEqual(path.read_bytes(), original)

    def test_owner_research_reference_lookalike_still_requires_exact_prompt(self):
        self.prepare_watch(True)
        before = self.bridge.binding(self.session)['cursor']
        self.message('<external_intern-agent.research>Owner supplied changed text</external_intern-agent.research>')
        with self.assertRaisesRegex(Invalid, 'differed from the dispatched message'):
            self.bridge.poll(self.session)
        self.assertEqual(self.bridge.binding(self.session)['cursor'], before)
        self.assertIsNotNone(json.loads(self.bridge.binding(self.session)['snapshot'])['pending'])


if __name__ == '__main__':
    unittest.main()
