import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from intern_personal.core import Invalid
from intern_personal.git_hooks import code_credentials, install, run, validate, verified_path
from intern_personal.live_state import LiveState,write_private
from intern_personal.terminal_bridge import TerminalBridge,attach,prompt_is_empty
from intern_personal.feishu_live import Relay
from test_live import FakeAPI,FakeHost,FakeTracker


class TerminalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.state=LiveState(Path(self.temp.name)/'private');self.addCleanup(self.state.close)
        write_private(self.state.root/'credentials.json',{'app_id':'cli_test','app_secret':'example-test-value-123'})
        write_private(self.state.root/'live.json',{'schema':'intern-agent.live.v1','github_owner':'example-owner',
             'model':'example-model','sandbox':'workspace-write','codex_command':['/bin/false'],'turn_timeout_seconds':1})
        self.state.credentials();self.state.db.execute("UPDATE identity SET owner='ou_owner',management_chat='oc_manager'")
        project=Path(self.temp.name)/'project';project.mkdir();self.state.register_workspace('debug-bot',project)
        self.session,_=self.state.reserve_session('debug-bot')
        self.state.set_session(self.session['id'],thread_id='fixture-thread',chat_id='oc_debug',state='ready')
        self.session=self.state.session(self.session['id']);self.tracker=FakeTracker(self.state)
        self.rollout=Path(self.temp.name)/'rollout.jsonl';self.rollout.touch()
        self.bridge=TerminalBridge(self.state,self.tracker)

    def event(self,kind,**fields):
        with self.rollout.open('a') as f:f.write(json.dumps({'type':'event_msg','payload':{'type':kind,**fields}})+'\n')

    def message(self,text):
        with self.rollout.open('a') as f:f.write(json.dumps({'type':'response_item','payload':{'type':'message','role':'user','content':[{'type':'input_text','text':text}]}})+'\n')

    def environment(self,text='<environment_context>\n<current_date>2026-10-11</current_date>\n</environment_context>'):
        with self.rollout.open('a') as f:
            f.write(json.dumps({'type':'response_item','payload':{'type':'message','role':'user',
                'internal_chat_message_metadata_passthrough':{'content_item_kinds':['environments.environment_context']},
                'content':[{'type':'input_text','text':text}]}})+'\n')

    def reference(self,key='intern-agent.memory',text=None):
        text=text if text is not None else '<external_'+key+'>\nReference data only.\n</external_'+key+'>'
        with self.rollout.open('a') as f:
            f.write(json.dumps({'type':'response_item','payload':{'type':'message','role':'user',
                'internal_chat_message_metadata_passthrough':{'content_item_kinds':['additional_content.'+key]},
                'content':[{'type':'input_text','text':text}]}})+'\n')

    def pending(self,text,seq=42):
        import hashlib
        row=self.bridge.binding(self.session);snapshot=json.loads(row['snapshot'])
        snapshot['pending']={'seq':seq,'prompt_sha256':hashlib.sha256(text.encode()).hexdigest(),'cursor':row['cursor']}
        self.state.db.execute('UPDATE terminal_attachments SET snapshot=?',(json.dumps(snapshot),))

    def idle_attachment(self):
        self.event('task_started',turn_id='prior');self.attach()
        self.event('task_complete',turn_id='prior',last_agent_message='prior response');self.bridge.poll(self.session)

    def test_async_question_shape_and_ack_do_not_lose_the_owner_question(self):
        self.idle_attachment();self.event('task_started',turn_id='new-question');self.message('Choose a dataset')
        with self.rollout.open('a') as stream:
            stream.write(json.dumps(dict(type='response_item',payload=dict(type='function_call',name='functions.request_user_input_async',call_id='question-call',arguments=json.dumps({'questions':[{'title':'Which dataset?', 'options':['Small','Full']}]}))))+'\n')
            stream.write(json.dumps(dict(type='response_item',payload=dict(type='function_call_output',call_id='question-call',output='The question was presented; no answer yet.')))+'\n')
        self.bridge.poll(self.session);self.bridge.poll(self.session)
        row=self.state.db.execute('SELECT * FROM user_questions').fetchone()
        self.assertEqual(row['source'],'native_async');self.assertEqual(row['state'],'new');self.assertEqual(row['blocking'],0)
        self.assertEqual(json.loads(row['questions'])[0]['options'][1]['label'],'Full')

    def test_malformed_native_question_does_not_stall_later_completion(self):
        self.idle_attachment();self.event('task_started',turn_id='invalid-question');self.message('Owner request')
        with self.rollout.open('a') as stream:
            stream.write(json.dumps(dict(type='response_item',payload=dict(type='function_call',name='functions.request_user_input',call_id='bad-question',arguments='{"questions":[{"title":"No required ID"}]}')))+'\n')
        self.event('task_complete',turn_id='invalid-question',last_agent_message='Still observed')
        self.bridge.poll(self.session)
        self.assertEqual(self.state.db.execute("SELECT result FROM terminal_turns WHERE turn_id='invalid-question'").fetchone()[0],'Still observed')

    def test_generated_environment_update_before_prompt_does_not_block_completion(self):
        self.idle_attachment();self.pending('Original owner words')
        self.event('task_started',turn_id='incoming');self.environment();self.message('Original owner words')
        snapshot=self.bridge.poll(self.session)
        self.assertEqual(snapshot['event_seq'],42);self.assertIsNone(snapshot['pending'])
        self.assertEqual(snapshot['request'],'Original owner words')
        self.environment();self.event('task_complete',turn_id='incoming',last_agent_message='saved answer')
        restarted=TerminalBridge(self.state,self.tracker);restarted.poll(self.session);restarted.poll(self.session)
        rows=self.state.db.execute('SELECT result FROM terminal_turns WHERE event_seq=42').fetchall()
        self.assertEqual([r['result'] for r in rows],['saved answer'])

    def test_environment_does_not_acknowledge_a_prompt_or_lose_partial_records(self):
        self.idle_attachment();self.pending('Still expected')
        self.event('task_started',turn_id='incoming');self.environment()
        snapshot=self.bridge.poll(self.session)
        self.assertEqual(snapshot['pending']['seq'],42);self.assertIsNone(snapshot['event_seq'])
        self.assertEqual(snapshot['request'],'')
        self.message('Still expected');self.bridge.poll(self.session)
        self.assertEqual(json.loads(self.bridge.binding(self.session)['snapshot'])['event_seq'],42)

    def test_hook_references_do_not_acknowledge_or_replace_owner_text(self):
        self.idle_attachment();self.pending('Owner text unchanged')
        self.event('task_started',turn_id='incoming')
        for key in ['intern-agent.memory','intern-agent.workspace','intern-agent.controller','intern-agent.attachments','intern-agent.research']:
            self.reference(key)
        snapshot=self.bridge.poll(self.session)
        self.assertEqual(snapshot['pending']['seq'],42);self.assertIsNone(snapshot['event_seq'])
        self.assertEqual(snapshot['request'],'')
        self.message('Owner text unchanged');self.event('task_complete',turn_id='incoming',last_agent_message='saved answer')
        self.bridge.poll(self.session)
        row=self.state.db.execute('SELECT result FROM terminal_turns WHERE event_seq=42').fetchone()
        self.assertEqual(row[0],'saved answer')
        self.assertIsNone(json.loads(self.bridge.binding(self.session)['snapshot'])['pending'])

    def test_owner_reference_lookalike_still_fails_closed(self):
        self.idle_attachment();self.pending('Expected request');self.event('task_started',turn_id='incoming')
        self.message('<external_intern-agent.memory>Lookalike owner text</external_intern-agent.memory>')
        with self.assertRaises(Invalid):self.bridge.poll(self.session)

    def test_unknown_or_mixed_tagged_reference_is_not_skipped(self):
        from intern_personal.terminal_bridge import native_context_message
        for key,text in [('unknown.context','<external_unknown.context>data</external_unknown.context>'),
                         ('intern-agent.memory','<external_intern-agent.memory>data</external_intern-agent.memory>\nDifferent owner text'),
                         ('intern-agent.memory','<external_intern-agent.attachments>data</external_intern-agent.attachments>')]:
            payload={'internal_chat_message_metadata_passthrough':{'content_item_kinds':['additional_content.'+key]},
                     'content':[{'type':'input_text','text':text}]}
            self.assertFalse(native_context_message(payload))

    def test_explicit_owner_reference_literal_is_preserved(self):
        literal='<external_intern-agent.memory>Owner supplied literal</external_intern-agent.memory>'
        self.idle_attachment();self.pending(literal);self.event('task_started',turn_id='incoming');self.message(literal)
        snapshot=self.bridge.poll(self.session)
        self.assertEqual(snapshot['event_seq'],42);self.assertEqual(snapshot['request'],literal)

    def test_owner_environment_lookalike_still_fails_closed(self):
        self.idle_attachment();self.pending('Expected request')
        self.event('task_started',turn_id='incoming')
        self.message('<environment_context>\n<current_date>2026-10-11</current_date>\n</environment_context>')
        with self.assertRaises(Invalid):self.bridge.poll(self.session)
        self.assertEqual(json.loads(self.bridge.binding(self.session)['snapshot'])['pending']['seq'],42)

    def test_mixed_environment_and_owner_text_still_fails_closed(self):
        self.idle_attachment();self.pending('Expected request')
        self.event('task_started',turn_id='incoming')
        self.environment('<environment_context>generated</environment_context>\nDifferent words')
        with self.assertRaises(Invalid):self.bridge.poll(self.session)

    def test_explicit_owner_environment_text_is_preserved(self):
        literal='<environment_context>owner supplied literal</environment_context>'
        self.idle_attachment();self.pending(literal)
        self.event('task_started',turn_id='incoming');self.message(literal)
        snapshot=self.bridge.poll(self.session)
        self.assertEqual(snapshot['event_seq'],42);self.assertEqual(snapshot['request'],literal)

    def test_unqueued_native_history_excludes_generated_environment(self):
        self.event('task_started',turn_id='current');self.environment();self.message('Native owner request');self.attach()
        self.event('task_complete',turn_id='current',last_agent_message='Native result');self.bridge.poll(self.session)
        row=self.state.db.execute('SELECT text FROM events').fetchone()
        self.assertEqual(row['text'],'Native owner request')

    def test_recovered_completion_saves_result_without_replaying_execution(self):
        self.idle_attachment()
        self.state.db.execute("INSERT INTO events(app_id,message_id,chat_id,sender,role,workspace,session,text,state) VALUES('cli_test','om_recover','oc_debug','ou_owner','agent','debug-bot',?,'Exact owner request','uncertain_execution')",(self.session['id'],))
        seq=self.state.db.execute("SELECT seq FROM events WHERE message_id='om_recover'").fetchone()[0]
        self.pending('Exact owner request',seq)
        self.event('task_started',turn_id='recovered');self.environment();self.message('Exact owner request')
        self.event('task_complete',turn_id='recovered',last_agent_message='Already finished')
        self.bridge.poll(self.session);self.bridge.poll(self.session)
        row=self.state.db.execute('SELECT state,result FROM events WHERE seq=?',(seq,)).fetchone()
        self.assertEqual((row['state'],row['result']),('uncertain_delivery','Already finished'))
        self.state.resolve(seq,'deliver')
        relay=Relay(self.state,FakeAPI(),FakeHost(self.state),self.tracker)
        item=dict(self.state.db.execute('SELECT * FROM events WHERE seq=?',(seq,)).fetchone())
        with patch.object(relay.host,'run',side_effect=AssertionError('Must not execute again')):
            self.assertEqual(relay.process_item(item,'agent'),'complete')
        row=self.state.db.execute('SELECT state,receipt FROM events WHERE seq=?',(seq,)).fetchone()
        self.assertEqual(row['state'],'complete');self.assertTrue(row['receipt'])

    def attach(self):
        # FakeTracker is intentionally not a credential cleaner.
        self.tracker.clean=lambda x:x.replace('example-test-value-123','[redacted]')
        with patch('intern_personal.terminal_bridge.verify_pane',return_value=True):
            return attach(self.state,self.tracker,self.session,{'thread_id':'fixture-thread','rollout':str(self.rollout)})

    def test_native_completion_queues_saved_reply_once_and_skips_history(self):
        self.event('task_started',turn_id='old');self.message('historical private message')
        self.event('task_complete',turn_id='old',last_agent_message='old response')
        self.event('task_started',turn_id='current');self.message('current request example-test-value-123')
        self.attach();self.bridge.poll(self.session)
        self.assertEqual(self.state.db.execute('SELECT COUNT(*) FROM events').fetchone()[0],0)
        self.event('task_complete',turn_id='current',last_agent_message='new response')
        self.bridge.poll(self.session);self.bridge.poll(self.session)
        rows=self.state.db.execute('SELECT * FROM events').fetchall()
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['state'],'result_ready')
        self.assertNotIn('historical',rows[0]['text']);self.assertIn('[redacted]',rows[0]['text'])
        self.assertEqual(rows[0]['result'],'new response')

    def test_failed_completion_without_final_clears_activity_and_saves_one_notice(self):
        self.event('task_started',turn_id='failed-turn');self.message('Keep my work')
        self.attach()
        self.event('task_complete',turn_id='failed-turn',last_agent_message=None,
                   error={'message':'stream disconnected; private-api-detail','codexErrorInfo':'other'})
        snapshot=self.bridge.poll(self.session)
        self.bridge.poll(self.session)
        self.assertIsNone(snapshot['active'])
        rows=self.state.db.execute('SELECT * FROM events').fetchall()
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['state'],'result_ready')
        self.assertIn('connection error',rows[0]['result']);self.assertNotIn('private-api-detail',rows[0]['result'])
        self.assertEqual(self.state.db.execute('SELECT status FROM terminal_turns').fetchone()[0],'task_failed')
        self.assertEqual(self.state.db.execute('SELECT activity FROM turn_records').fetchone()[0],'failed')
        self.state.transition(rows[0]['seq'],'complete',receipt='om_notice')
        self.state.ledger.observe(dict(rows[0]),'complete')
        self.assertEqual(self.state.db.execute('SELECT activity FROM turn_records').fetchone()[0],'failed')

    def test_missing_final_without_failure_evidence_still_requires_review(self):
        self.event('task_started',turn_id='unknown');self.attach();self.bridge.poll(self.session)
        self.event('task_complete',turn_id='unknown',last_agent_message=None)
        with self.assertRaises(Invalid):self.bridge.poll(self.session)
        self.assertEqual(json.loads(self.bridge.binding(self.session)['snapshot'])['active'],'unknown')
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM events').fetchone()[0],0)

    def test_injected_turn_returns_to_existing_event_without_double_delivery(self):
        self.event('task_started',turn_id='prior');self.attach()
        self.event('task_complete',turn_id='prior',last_agent_message='prior final');self.bridge.poll(self.session)
        row=self.bridge.binding(self.session);snapshot=json.loads(row['snapshot']);snapshot['pending']={'seq':42,'tag':'fixture-tag'}
        self.state.db.execute('UPDATE terminal_attachments SET snapshot=?',(json.dumps(snapshot),))
        self.event('task_started',turn_id='incoming');self.message('fixture-tag\nrequest')
        self.event('task_complete',turn_id='incoming',last_agent_message='answer')
        self.bridge.poll(self.session)
        self.assertEqual(self.state.db.execute('SELECT COUNT(*) FROM events').fetchone()[0],1)
        self.assertEqual(self.state.db.execute('SELECT result FROM terminal_turns WHERE event_seq=42').fetchone()[0],'answer')

    def test_partial_line_restart_and_abort(self):
        self.event('task_started',turn_id='current');self.attach();self.bridge.poll(self.session)
        with self.rollout.open('a') as f:f.write('{"type":"event_msg",')
        cursor=self.bridge.binding(self.session)['cursor'];self.bridge.poll(self.session)
        self.assertEqual(self.bridge.binding(self.session)['cursor'],cursor)
        with self.rollout.open('a') as f:f.write('"payload":{"type":"turn_aborted","turn_id":"current"}}\n')
        restarted=TerminalBridge(self.state,self.tracker);restarted.poll(self.session)
        self.assertEqual(self.state.db.execute('SELECT status FROM terminal_turns').fetchone()[0],'turn_aborted')
        self.assertEqual(self.state.db.execute('SELECT activity FROM turn_records').fetchone()[0],'stopped')

    def test_wrong_completion_and_replaced_rollout_fail_closed(self):
        self.event('task_started',turn_id='current');self.attach();self.bridge.poll(self.session)
        self.event('task_complete',turn_id='other',last_agent_message='wrong')
        with self.assertRaises(Invalid):self.bridge.poll(self.session)
        self.rollout.unlink();self.rollout.write_text('')
        with self.assertRaises(Invalid):self.bridge.poll(self.session)

    def test_terminal_model_changes_cannot_fake_success(self):
        self.event('task_started',turn_id='current');self.attach()
        relay=Relay(self.state,FakeAPI(),FakeHost(self.state),self.tracker)
        item={'session':self.session['id'],'chat_id':'oc_debug','text':'/model alternate-model'}
        with self.assertRaises(Invalid):relay.control(item)
        self.assertIsNone(self.state.session(self.session['id'])['model'])
        item['text']='/help';self.assertIn('original pane',relay.control(item))

    def test_input_draft_and_unknown_menu_are_rejected(self):
        self.assertTrue(prompt_is_empty('› \n'))
        self.assertTrue(prompt_is_empty('\x1b[1m›\x1b[0m \x1b[2mExample hint\x1b[0m\n'))
        self.assertFalse(prompt_is_empty('› unsent personal draft\n'))
        self.assertFalse(prompt_is_empty('Approve this command?\n'))
        self.assertFalse(prompt_is_empty('› \x1b[2mHint\x1b[0mREAL TEXT\n'))

    def test_injection_uses_stdin_and_exact_pane_without_new_codex(self):
        self.event('task_started',turn_id='prior');self.attach()
        self.event('task_complete',turn_id='prior',last_agent_message='prior response');self.bridge.poll(self.session)
        config=Path(self.bridge.binding(self.session)['config_path'])
        value=json.loads(config.read_text());value.update(tmux_socket='/fixture/socket',pane='%fixture')
        write_private(config,value)
        calls=[]
        def transport(binding,*args,data=None,timeout=10):
            calls.append((args,data))
            if args[0]=='send-keys':
                prompt=next(d.decode() for a,d in calls if a[0]=='load-buffer')
                self.event('task_started',turn_id='injected');self.message(prompt.strip())
                self.event('task_complete',turn_id='injected',last_agent_message='EXACT_EXISTING_THREAD')
            return ''
        literal='  $(do-not-execute) `literal` ; private request\n\nKeep the original words.  '
        with patch('intern_personal.terminal_bridge.tmux',side_effect=transport), \
             patch('intern_personal.terminal_bridge.verify_pane',return_value=True), \
             patch('intern_personal.terminal_bridge.empty_prompt'), \
             patch('intern_personal.terminal_bridge.subprocess.run'):
            self.assertEqual(self.bridge.run(self.session,literal,42),'EXACT_EXISTING_THREAD')
        self.assertEqual([data for args,data in calls if args[0]=='load-buffer'],[literal.encode()])
        self.assertTrue(all(literal not in str(args) for args,_ in calls))
        self.assertTrue(all('%fixture' in args for args,_ in calls if args[0] in {'paste-buffer','send-keys'}))

    def test_failed_input_is_ambiguous_and_is_never_replayed(self):
        self.event('task_started',turn_id='prior');self.attach()
        self.event('task_complete',turn_id='prior',last_agent_message='prior response');self.bridge.poll(self.session)
        config=Path(self.bridge.binding(self.session)['config_path']);value=json.loads(config.read_text())
        value.update(tmux_socket='/fixture/socket',pane='%fixture');write_private(config,value)
        with patch('intern_personal.terminal_bridge.tmux',side_effect=Invalid('fixture failed after possible input')), \
             patch('intern_personal.terminal_bridge.verify_pane',return_value=True), \
             patch('intern_personal.terminal_bridge.empty_prompt'), \
             patch('intern_personal.terminal_bridge.subprocess.run'):
            with self.assertRaises(RuntimeError):self.bridge.run(self.session,'do not replay',42)
        pending=json.loads(self.bridge.binding(self.session)['snapshot'])['pending']
        self.assertEqual(pending['seq'],42)

    def test_raw_prompt_restart_duplicate_and_wrong_prompt_are_safe(self):
        import hashlib
        self.event('task_started',turn_id='old');self.message('Same words')
        self.event('task_complete',turn_id='old',last_agent_message='old answer');self.attach();self.bridge.poll(self.session)
        row=self.bridge.binding(self.session);snapshot=json.loads(row['snapshot'])
        snapshot['pending']={'seq':42,'prompt_sha256':hashlib.sha256(b'Same words').hexdigest(),'cursor':row['cursor']}
        self.state.db.execute('UPDATE terminal_attachments SET snapshot=?',(json.dumps(snapshot),))
        self.event('task_started',turn_id='new');self.message('Same words')
        self.event('task_complete',turn_id='new',last_agent_message='new answer')
        restarted=TerminalBridge(self.state,self.tracker);restarted.poll(self.session);restarted.poll(self.session)
        rows=self.state.db.execute('SELECT event_seq,result FROM terminal_turns WHERE event_seq=42').fetchall()
        self.assertEqual([(r['event_seq'],r['result']) for r in rows],[(42,'new answer')])
        row=self.bridge.binding(self.session);snapshot=json.loads(row['snapshot'])
        snapshot['pending']={'seq':43,'prompt_sha256':hashlib.sha256(b'expected').hexdigest(),'cursor':row['cursor']}
        self.state.db.execute('UPDATE terminal_attachments SET snapshot=?',(json.dumps(snapshot),))
        self.event('task_started',turn_id='wrong');self.message('Different manual words')
        with self.assertRaises(Invalid):restarted.poll(self.session)
        self.assertIsNotNone(json.loads(self.bridge.binding(self.session)['snapshot'])['pending'])

    def test_native_hook_is_scoped_and_keeps_input_out_of_context(self):
        from intern_personal.prompt_context import publish,native_output
        publish(self.tracker,self.session)
        file=self.state.runtime/'prompt-context'/(self.session['id']+'.json')
        self.assertEqual(file.stat().st_mode&0o777,0o600)
        self.assertEqual(native_output(file,dict(hook_event_name='UserPromptSubmit',session_id='other')), {})
        output=native_output(file,dict(hook_event_name='UserPromptSubmit',session_id='fixture-thread',prompt='UNMODIFIED_WORDS'))
        text=output['hookSpecificOutput']['additionalContext']
        self.assertIn(self.tracker.code_directory(self.session),text)
        self.assertNotIn('UNMODIFIED_WORDS',text)


class HookTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.state=LiveState(self.root/'state');self.addCleanup(self.state.close)
        self.repo=self.root/'repo';self.repo.mkdir()
        self.git('init','-q','-b','main');(self.repo/'README.md').write_text('# Seed\n')
        self.git('add','README.md');self.git('commit','-qm','seed')
        self.git('update-ref','refs/remotes/origin/main','HEAD');self.git('checkout','-qb','debug/work')
        self.config=install(self.state,self.repo,['debug/work'])

    def git(self,*args,ok=True):
        result=subprocess.run(['git','-c','user.name=Fixture','-c','user.email=fixture@example.invalid','-C',str(self.repo),*args],capture_output=True)
        if ok:self.assertEqual(result.returncode,0,result.stderr.decode())
        return result

    def test_real_precommit_validates_python_and_blocks_private_literals(self):
        (self.repo/'valid.py').write_text('answer = 42\n');self.git('add','valid.py');self.git('commit','-qm','valid')
        (self.repo/'bad.py').write_text("api_key = 'unapproved-real-value'\n");self.git('add','bad.py')
        self.assertNotEqual(self.git('commit','-qm','blocked',ok=False).returncode,0)
        (self.repo/'bad.py').write_text('if syntax is broken\n');self.git('add','bad.py')
        self.assertNotEqual(self.git('commit','-qm','blocked',ok=False).returncode,0)

    def test_papers_and_figures_pass_real_commit_and_push_guards(self):
        from test_binary_artifacts import PDF,PNG
        (self.repo/'paper.pdf').write_bytes(PDF);(self.repo/'plot.png').write_bytes(PNG)
        self.git('add','paper.pdf','plot.png');self.git('commit','-qm','research artifacts')
        remote=self.root/'artifacts.git';subprocess.run(['git','init','--bare','-q',str(remote)],check=True)
        self.git('push',str(remote),'debug/work:debug/work')
        restored=subprocess.check_output(['git','-C',str(remote),'show','debug/work:paper.pdf'])
        self.assertEqual(restored,PDF)

    def test_real_prepush_hook_and_default_branch_guard(self):
        remote=self.root/'remote.git';subprocess.run(['git','init','--bare','-q',str(remote)],check=True)
        self.git('push',str(remote),'debug/work:debug/work')
        self.assertNotEqual(self.git('push',str(remote),'debug/work:main',ok=False).returncode,0)
        self.assertNotEqual(self.git('push',str(remote),':debug/work',ok=False).returncode,0)

    def test_hook_tampering_rejects_operation(self):
        path=Path(self.config).parent/'pre-commit';path.write_text('#!/bin/sh\nexit 0\n')
        with self.assertRaises(Invalid):verified_path(self.config,self.repo)

    def test_metadata_format_and_branch_boundary(self):
        with self.assertRaises(Invalid):validate('.intern_workspace/interns/test/status.md',b'# Missing legacy marker\n',True)
        with self.assertRaises(Invalid):validate('other.md',b'# Title\n',True)
        with self.assertRaises(Invalid):validate('.intern_workspace/README.md',b'# Title\n',False)
        with self.assertRaises(Invalid):validate('personal.json',b'{}')

    def test_added_then_deleted_credentials_are_blocked_on_push(self):
        (self.repo/'old.py').write_text("api_key = 'unapproved-real-value'\n")
        self.git('-c','core.hooksPath=/dev/null','add','old.py');self.git('-c','core.hooksPath=/dev/null','commit','-qm','secret')
        self.git('rm','old.py');self.git('commit','-qm','removed')
        with self.assertRaises(Invalid):run(self.config,'pre-push',['refs/heads/debug/work '+'1'*40+' refs/heads/debug/work '+'0'*40])

    def test_safe_fixtures_and_actual_secret_detection(self):
        self.assertFalse(code_credentials("{'app_secret':'example-fixture-only'}"))
        self.assertTrue(code_credentials("{'app_secret':'unapproved-value'}"))
        self.assertTrue(code_credentials('ordinary copied actual-value', ['actual-value']))

    def test_worktree_hooks_inspect_their_own_index(self):
        management=self.root/'metadata'
        self.git('worktree','add','-b','intern_workspace/test',str(management),'main')
        config=install(self.state,management,['intern_workspace/test'],management=True)
        self.assertNotEqual(self.git('config','--get','core.hooksPath').stdout.strip(),
                            subprocess.check_output(['git','-C',str(management),'config','--get','core.hooksPath']).strip())
        (self.repo/'bad.py').write_text("api_key = 'unapproved-real-value'\n");self.git('add','bad.py')
        self.assertNotEqual(self.git('commit','-qm','must still block',ok=False).returncode,0)
        folder=management/'.intern_workspace';folder.mkdir();(folder/'README.md').write_text('# Correct management format\n')
        subprocess.run(['git','-C',str(management),'add','.intern_workspace'],check=True)
        run(config,'pre-commit')

    def test_atomic_management_and_updated_code_branches_pass_real_hooks(self):
        (self.repo/'work.py').write_text('answer = 42\n');self.git('add','work.py');self.git('commit','-qm','code work')
        management=self.root/'atomic-metadata'
        self.git('worktree','add','-b','intern_workspace/atomic',str(management),'main')
        install(self.state,management,['intern_workspace/atomic','debug/work'],management=True)
        folder=management/'.intern_workspace';folder.mkdir();(folder/'README.md').write_text('# Management records\n')
        remote=self.root/'atomic.git';subprocess.run(['git','init','--bare','-q',str(remote)],check=True)
        def local(*args):
            result=subprocess.run(['git','-c','user.name=Fixture','-c','user.email=fixture@example.invalid','-C',str(management),*args],capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr.decode())
            return result
        local('add','.intern_workspace');local('commit','-qm','records')
        local('push','--atomic',str(remote),'intern_workspace/atomic:intern_workspace/atomic','debug/work:debug/work')
        self.assertEqual(subprocess.check_output(['git','-C',str(remote),'show','debug/work:work.py']).decode(),'answer = 42\n')
