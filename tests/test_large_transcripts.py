import json
from pathlib import Path
import threading
from unittest.mock import patch
import unittest

import test_terminal as fixture
from intern_personal.attachments import MAX_BYTES
from intern_personal.console_observer import ConsoleObserver
from intern_personal.codex_bridge import Connection
from intern_personal.core import Invalid
from intern_personal.terminal_bridge import MAX_LINE


class LargeTranscriptTests(unittest.TestCase):
    setUp = fixture.TerminalTests.setUp
    event = fixture.TerminalTests.event
    message = fixture.TerminalTests.message
    attach = fixture.TerminalTests.attach

    def started(self, managed=False):
        self.event('task_started', turn_id='large-output')
        self.message('Keep the original owner request')
        self.attach()
        if managed:
            self.state.db.execute('INSERT INTO console_views SELECT * FROM terminal_attachments')
            self.state.db.execute('DELETE FROM terminal_attachments')
            self.bridge = ConsoleObserver(self.state, self.tracker)
        self.bridge.poll(self.session)

    def tool(self, newline=True):
        value = dict(type='response_item', payload=dict(type='custom_tool_call_output',
            call_id='fixture-call', output='PRIVATE_TOOL_OUTPUT_SENTINEL ' + 'x' * (5 * 1024 * 1024)))
        with self.rollout.open('a') as stream:
            stream.write(json.dumps(value) + ('\n' if newline else ''))

    def completion_after_large_tool(self, managed):
        self.started(managed)
        self.tool()
        with self.rollout.open('a') as stream:
            stream.write(json.dumps(dict(type='response_item', payload=dict(type='message', role='assistant',
                phase='commentary', content=[dict(type='output_text', text='Checking the result now.')]))) + '\n')
        self.bridge.poll(self.session)
        progress = self.state.db.execute('SELECT text FROM assistant_progress').fetchall()
        self.assertEqual([row[0] for row in progress], ['Checking the result now.'])
        self.event('task_complete', turn_id='large-output', last_agent_message='Final reply saved')
        self.bridge.poll(self.session)
        self.bridge.poll(self.session)
        rows = self.state.db.execute('SELECT text,result FROM events').fetchall()
        self.assertEqual([(row['text'], row['result']) for row in rows],
                         [('Keep the original owner request', 'Final reply saved')])
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM terminal_turns').fetchone()[0], 1)
        self.assertNotIn('PRIVATE_TOOL_OUTPUT_SENTINEL', str([tuple(row) for row in progress] + [tuple(row) for row in rows]))

    def test_native_large_tool_output_does_not_hide_progress_or_completion(self):
        self.completion_after_large_tool(False)

    def test_managed_large_tool_output_does_not_hide_progress_or_completion(self):
        self.completion_after_large_tool(True)

    def test_partial_large_record_waits_without_losing_its_prefix(self):
        self.started(True)
        before = self.bridge.binding(self.session)['cursor']
        self.tool(newline=False)
        self.bridge.poll(self.session)
        self.assertEqual(self.bridge.binding(self.session)['cursor'], before)
        with self.rollout.open('a') as stream:
            stream.write('\n')
        self.event('task_complete', turn_id='large-output', last_agent_message='Completed after newline')
        self.bridge.poll(self.session)
        self.assertEqual(self.state.db.execute('SELECT result FROM terminal_turns').fetchone()[0],
                         'Completed after newline')

    def test_read_bound_is_retained_and_does_not_advance_an_unreviewed_record(self):
        self.started(True)
        before = self.bridge.binding(self.session)['cursor']
        with self.rollout.open('a') as stream:
            stream.write(json.dumps(dict(type='response_item', payload=dict(type='custom_tool_call_output',
                output='x' * 8192))) + '\n')
        with patch('intern_personal.terminal_bridge.MAX_LINE', 4096):
            with self.assertRaisesRegex(Invalid, 'Transcript record exceeds supported size'):
                self.bridge.poll(self.session)
        self.assertEqual(self.bridge.binding(self.session)['cursor'], before)

    def test_documented_file_limit_fits_a_base64_transcript_record(self):
        self.assertLess(4 * ((MAX_BYTES + 2) // 3) + 65536, MAX_LINE)

    def test_rpc_receives_large_tool_notification_before_the_requested_result(self):
        from websockets.sync.server import unix_serve

        def handler(websocket):
            for raw in websocket:
                message = json.loads(raw)
                if 'id' not in message:
                    continue
                result = {}
                if message['method'] == 'thread/read':
                    websocket.send(json.dumps(dict(method='item/completed', params=dict(item=dict(
                        type='customToolCall', output='x' * (5 * 1024 * 1024))))))
                    result = dict(thread=dict(id='fixture-thread', status=dict(type='idle')))
                websocket.send(json.dumps(dict(id=message['id'], result=result)))

        path = Path(self.temp.name) / 'large-frame.sock'
        with unix_serve(handler, path=str(path)) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            connection = Connection(path)
            try:
                result = connection.request('thread/read', dict(threadId='fixture-thread'), timeout=10)
                self.assertEqual(result['thread']['status']['type'], 'idle')
                notice = connection.notification(timeout=1)
                self.assertEqual(notice['method'], 'item/completed')
                self.assertEqual(len(notice['params']['item']['output']), 5 * 1024 * 1024)
            finally:
                connection.close()
                server.shutdown()
                worker.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
