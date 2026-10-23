"""Shared features must stay usable and isolated across conversation roles."""
import hashlib
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import test_features as features
import test_tracking as tracking
from intern_personal import attachments, file_transfer, tutorial_workflow
from intern_personal.core import Invalid
from intern_personal.live_state import write_private,private_json
from intern_personal.prompt_context import native_output, publish, refresh
from intern_personal.record_quality import ARTIFACT_POLICY


PDF = b'%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\n%%EOF\n'


class SessionCapabilitiesTests(unittest.TestCase):
    setUp = tracking.TrackingTests.setUp
    raw = tracking.TrackingTests.raw
    attach = tracking.TrackingTests.attach
    conversation = features.FeaturesTests.conversation
    relay = features.FeaturesTests.relay
    event = features.FeaturesTests.event

    def roles(self):
        debug = self.conversation()
        relay = self.relay()
        controller = relay.attach_controller('project')
        project = self.conversation()
        operator = self.conversation()
        self.state.db.execute('INSERT INTO terminal_attachments VALUES(?,?,0,?)',
                              (debug['id'], 'fixture-native-config', '{}'))
        config = self.state.config()
        config['auto_recovery'] = dict(enabled=True, incident_session=operator['id'])
        write_private(self.state.root / 'live.json', config)
        self.state.db.execute('UPDATE peer_policy SET enabled=1 WHERE singleton=1')
        tutorial_workflow.configure(self.state, [debug['id'], controller['id']])
        return relay, [debug, controller, project, operator]

    def test_existing_roles_and_future_bots_inherit_workflows_without_tutorial_leakage(self):
        relay, sessions = self.roles()
        for session in sessions:
            publish(self.tracker, session)
            current_context=private_json(self.state.runtime/'prompt-context'/(session['id']+'.json'))
            self.assertIn(' research --context ',current_context['fragments']['intern-agent.workspace']['value'])
            self.assertIn('persistent wake-ups',current_context['fragments']['intern-agent.workspace']['value'])
        sessions.append(self.conversation())
        before = [tuple(row) for row in self.state.db.execute('SELECT * FROM events')]
        for index, session in enumerate(sessions):
            with self.subTest(role=index):
                refs = publish(self.tracker, session)
                application = refs['intern-agent.workspace']['value']
                self.assertIn(ARTIFACT_POLICY,application)
                self.assertIn(str(Path(__file__).resolve().parents[1]/'docs/RECORD_QUALITY.md'),application)
                for command in ['file-send --context', 'file-status --context',
                                'file-permissions --context', 'intern-agent.peer.v1',' research --context ']:
                    self.assertIn(command, application)
                self.assertIn(str(session['id']) + '.json', application)
                self.assertEqual(tutorial_workflow.REMINDER in application, index < 2)
                self.assertEqual('intern-agent.controller' in refs, index == 1)
                self.assertEqual('You are Recovery Sentinel' in application, index == 3)
                context = self.state.runtime / 'prompt-context' / (session['id'] + '.json')
                with patch.dict(os.environ, {'CODEX_THREAD_ID': session['thread_id']}):
                    self.assertEqual(file_transfer.context_session(self.state, context)['id'], session['id'])
                with patch.dict(os.environ, {'CODEX_THREAD_ID': sessions[(index + 1) % len(sessions)]['thread_id']}):
                    with self.assertRaises(Invalid):
                        file_transfer.context_session(self.state, context)
        self.assertEqual(before, [tuple(row) for row in self.state.db.execute('SELECT * FROM events')])
        self.assertFalse(relay.host.calls)

    def test_each_roles_pdf_returns_only_to_its_own_chat(self):
        relay, sessions = self.roles()
        sessions.append(self.conversation())
        uploads, sends, expected = {}, [], {}

        def upload(name, data):
            key = 'file_' + hashlib.sha256(data).hexdigest()
            uploads[key] = data
            return key

        def send(chat, key, identity):
            sends.append((chat, uploads[key]))
            return 'om_receipt_' + str(len(sends))

        relay.api.upload_file, relay.api.send_file = upload, send
        for index, session in enumerate(sessions):
            directory = Path(self.tracker.code_directory(session))
            data = PDF + ('% role ' + str(index) + '\n').encode()
            (directory / 'report.pdf').write_bytes(data)
            publish(self.tracker, session)
            context = self.state.runtime / 'prompt-context' / (session['id'] + '.json')
            with patch.dict(os.environ, {'CODEX_THREAD_ID': session['thread_id']}):
                bound = file_transfer.context_session(self.state, context)
                file_transfer.enqueue(self.state, self.tracker, bound, 'report.pdf')
            expected[session['chat_id']] = data
        file_transfer.pump(relay)
        file_transfer.pump(relay)
        self.assertEqual(dict(sends), expected)
        self.assertEqual(len(sends), len(sessions))
        self.assertTrue(all(row[0] == 'delivered' for row in self.state.db.execute('SELECT state FROM file_transfers')))
        self.assertFalse(relay.host.calls)
        self.assertEqual(self.raw(self.remote, 'rev-parse', 'main'), self.original)

    def test_runtime_refresh_updates_all_roles_and_new_bots_without_turns_or_preference_changes(self):
        relay,sessions=self.roles()
        sessions.append(relay.open_session('project',new=True))
        from intern_personal import progress,speed
        self.state.set_model(sessions[2]['id'],'fixture-custom-model','medium')
        speed.save(self.state,sessions[2],'priority')
        progress.set_mode(self.state,sessions[3]['id'],False)
        before='\n'.join(self.state.db.iterdump())
        contexts=[]
        for session in sessions:
            publish(self.tracker,session,'private-attachment-reference-'+session['id'])
            file=self.state.runtime/'prompt-context'/(session['id']+'.json')
            value=private_json(file)
            value['fragments']['intern-agent.workspace']['value']='obsolete relay reference'
            write_private(file,value);contexts.append(file)
        result=refresh(self.tracker)
        self.assertEqual(result,dict(refreshed=len(sessions),skipped=0,failed=0,error_types=[]))
        for index,(session,file) in enumerate(zip(sessions,contexts)):
            context=private_json(file);app=context['fragments']['intern-agent.workspace']['value']
            self.assertNotIn('obsolete relay reference',app)
            self.assertIn(' file-send --context ',app)
            self.assertIn(ARTIFACT_POLICY,app)
            self.assertEqual(context['fragments']['intern-agent.attachments']['value'],'private-attachment-reference-'+session['id'])
            self.assertEqual(tutorial_workflow.REMINDER in app,index<2)
        operator=private_json(contexts[3])['fragments']['intern-agent.workspace']['value']
        expected=str(Path(__file__).resolve().parents[1]/'docs/RECOVERY_OPERATOR.md')
        self.assertIn('Read '+expected+' and the workspace AGENTS.md',operator)
        self.assertEqual(before,'\n'.join(self.state.db.iterdump()))
        self.assertFalse(relay.host.calls)
        self.assertEqual(self.raw(self.remote,'rev-parse','main'),self.original)

    def test_refresh_preserves_paused_context_and_does_not_copy_a_foreign_threads_files(self):
        _,sessions=self.roles();paused,foreign=sessions[:2]
        for session in (paused,foreign):publish(self.tracker,session,'private-current-files')
        paused_file=self.state.runtime/'prompt-context'/(paused['id']+'.json');before=paused_file.read_bytes()
        self.state.set_session(paused['id'],state='paused')
        foreign_file=self.state.runtime/'prompt-context'/(foreign['id']+'.json')
        value=private_json(foreign_file);value['thread_id']='fixture-previous-thread'
        write_private(foreign_file,value)
        result=refresh(self.tracker)
        self.assertEqual(result,dict(refreshed=3,skipped=1,failed=0,error_types=[]))
        self.assertEqual(paused_file.read_bytes(),before)
        current=private_json(foreign_file)
        self.assertEqual(current['thread_id'],foreign['thread_id'])
        self.assertNotIn('intern-agent.attachments',current['fragments'])

    def test_one_malformed_context_is_reported_without_overwriting_it_or_blocking_other_bots(self):
        _,sessions=self.roles()
        for session in sessions:publish(self.tracker,session,'preserved-private-file-reference')
        file=self.state.runtime/'prompt-context'/(sessions[0]['id']+'.json')
        file.write_text('malformed-private-context-sentinel')
        result=refresh(self.tracker)
        self.assertEqual(result,dict(refreshed=3,skipped=0,failed=1,error_types=['JSONDecodeError']))
        self.assertNotIn('malformed-private-context-sentinel',str(result))
        self.assertEqual(file.read_text(),'malformed-private-context-sentinel')
        for session in sessions[1:]:
            value=private_json(self.state.runtime/'prompt-context'/(session['id']+'.json'))
            self.assertEqual(value['fragments']['intern-agent.attachments']['value'],'preserved-private-file-reference')

    def test_normal_receiver_start_refreshes_context_before_workers_or_network_reception(self):
        relay,sessions=self.roles()
        for session in sessions:publish(self.tracker,session,'private-startup-reference')
        fake=Mock();fake.LogLevel.ERROR=1
        observed=[]
        def check():
            observed.append(relay.context_refresh)
            self.assertEqual(relay.context_refresh['refreshed'],len(sessions))
            for session in sessions:
                value=private_json(self.state.runtime/'prompt-context'/(session['id']+'.json'))
                self.assertEqual(value['thread_id'],session['thread_id'])
                self.assertEqual(value['fragments']['intern-agent.attachments']['value'],'private-startup-reference')
        fake.ws.Client.return_value.start.side_effect=check
        with patch.dict('sys.modules',{'lark_oapi':fake}),patch('intern_personal.feishu_live.threading.Thread') as worker:
            worker.return_value.start.side_effect=check
            relay.start(self.state.credentials())
        self.assertTrue(observed)
        self.assertFalse(relay.host.calls)

    def test_doctor_reports_feature_context_refresh_failure_without_prompt_replay(self):
        self.roles()
        from intern_personal.operations import diagnose
        import time
        for failed in (0,1):
            write_private(self.state.root/'health.json',dict(receiver_connected=True,updated_at=time.time(),
                context_refresh=dict(refreshed=3,skipped=0,failed=failed,error_types=['Invalid'] if failed else [])))
            check=next(row for row in diagnose(self.state)['checks'] if row['id']=='feature_context')
            self.assertEqual(check['passed'],failed==0)
            self.assertFalse(check['blocking'])

    def test_incoming_files_stay_private_and_prompts_unchanged_for_every_role(self):
        relay, sessions = self.roles()
        sessions.append(self.conversation())
        payloads = {'file_resource_' + str(i): PDF + ('% input ' + str(i) + '\n').encode()
                    for i in range(len(sessions))}
        relay.api.download = lambda message, key, kind: payloads[key]
        paths = set()
        for index, session in enumerate(sessions):
            key = 'file_resource_' + str(index)
            self.assertEqual(self.state.ingest(self.event(session, mid='om_input_' + str(index),
                kind='file', content=dict(file_name='paper.pdf', file_key=key))), 'accepted')
            self.assertEqual(self.state.ingest(self.event(session, text='Compare this paper',
                mid='om_caption_'+str(index))), 'accepted')
            item = dict(self.state.db.execute('SELECT * FROM events WHERE message_id=?',
                                              ('om_caption_' + str(index),)).fetchone())
            text, images, resources = attachments.prepare(relay, item)
            self.assertEqual(text, item['text'])
            refs = publish(self.tracker, session, resources)
            self.assertEqual(refs['intern-agent.attachments']['kind'], 'untrusted')
            path = self.state.runtime / 'attachments' / str(item['seq']) / '0-paper.pdf'
            paths.add(path)
            self.assertEqual(path.read_bytes(), payloads[key])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertFalse((Path(self.tracker.code_directory(session)) / 'paper.pdf').exists())
            context = self.state.runtime / 'prompt-context' / (session['id'] + '.json')
            original = dict(hook_event_name='UserPromptSubmit', session_id=session['thread_id'], prompt=text)
            output = native_output(context, original)
            self.assertIn(str(path), output['hookSpecificOutput']['additionalContext'])
            self.assertIn(ARTIFACT_POLICY,output['hookSpecificOutput']['additionalContext'])
            self.assertEqual(original['prompt'], text)
        self.assertEqual(len(paths), len(sessions))
        self.assertFalse(relay.host.calls)


if __name__ == '__main__':
    unittest.main()
