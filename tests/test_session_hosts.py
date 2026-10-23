import hashlib
import json
import os
from pathlib import Path
import shlex
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from intern_personal.core import Invalid
from intern_personal.live_state import LiveState, write_private
from intern_personal.session_hosts import SessionHosts, console
from intern_personal.codex_bridge import CodexHost, BridgeError
from intern_personal.console_observer import ConsoleObserver
from intern_personal.terminal_bridge import TerminalBridge


class SessionHostTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='ia-host-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.root.chmod(0o700)
        auth = self.root / 'login'
        auth.mkdir(mode=0o700)
        (auth / 'auth.json').write_text('{"fixture":true}')
        config = dict(schema='intern-agent.live.v1', github_owner='example-user', model='fixture-model',
                      sandbox='workspace-write',codex_command=['/fixture/codex'],
                      codex_auth_home=str(auth),launcher_home=str(self.root))
        write_private(self.root / 'live.json', config)
        self.state = LiveState(self.root)
        self.addCleanup(self.state.close)
        cwd = self.root / 'project'
        cwd.mkdir()
        self.state.register_workspace('project',cwd)
        self.session,_ = self.state.reserve_session('project')
        self.state.set_session(self.session['id'],thread_id='fixture-thread',state='ready',chat_id='oc_fixture')
        self.session = self.state.session(self.session['id'])
        self.hosts = SessionHosts(self.state,config)
        self.tracker = SimpleNamespace(code_directory=lambda s:str(cwd),clean=lambda t:t)
        self.state.db.execute("UPDATE identity SET app_id='cli_fixture',owner='ou_fixture' WHERE singleton=1")

    def legacy(self,thread='fixture-thread'):
        path=self.state.runtime/'codex-home/sessions/2026/10/11'/('rollout-test-'+thread+'.jsonl')
        path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        path.write_text('{"type":"session_meta","payload":{"id":"'+thread+'"}}\n')
        return path

    def prepared(self):
        self.legacy()
        spec=self.hosts.prepare(self.session)
        self.hosts.remember(self.session,spec)
        return spec

    def emit(self,path,value):
        with path.open('a') as f:f.write(json.dumps(value)+'\n')

    def event(self,path,kind,**kw):
        self.emit(path,dict(type='event_msg',payload=dict(type=kind,**kw)))

    def message(self,path,text):
        self.emit(path,dict(type='response_item',payload=dict(type='message',role='user',content=[dict(type='input_text',text=text)])))

    def observe(self):
        spec=self.prepared();observer=ConsoleObserver(self.state,self.tracker)
        observer.register(self.session,spec)
        path=next((spec['codex_home']/'sessions').glob('**/*.jsonl'))
        return observer,path,spec

    def test_same_workspace_conversations_have_distinct_process_addresses_and_homes(self):
        other,_=self.state.reserve_session('project',new=True)
        a,b=self.hosts.spec(self.session),self.hosts.spec(other)
        for key in ['name','socket','home','codex_home','tmp']:
            self.assertNotEqual(a[key],b[key])
        self.assertEqual(a['tmux_socket'],Path('/tmp')/('tmux-'+str(os.getuid()))/'default')
        self.assertEqual(a,self.hosts.spec(self.session))
        self.assertNotEqual(a['socket'],self.hosts.spec()['socket'])

    def test_imports_only_bound_history_and_reuses_login_without_copying_it(self):
        source=self.legacy();unrelated=self.legacy('another-thread')
        before=hashlib.sha256(source.read_bytes()).hexdigest()
        spec=self.hosts.prepare(self.session)
        files=list((spec['codex_home']/'sessions').glob('**/*.jsonl'))
        self.assertEqual(len(files),1);self.assertEqual(files[0].read_bytes(),source.read_bytes())
        self.assertEqual(files[0].stat().st_mode&0o777,0o600)
        self.assertEqual(spec['codex_home'].stat().st_mode&0o777,0o700)
        self.assertTrue((spec['codex_home']/'auth.json').is_symlink())
        self.assertEqual(before,hashlib.sha256(source.read_bytes()).hexdigest())
        self.assertTrue(unrelated.exists())
        files[0].write_text('local new history\n')
        self.hosts.prepare(self.session)
        self.assertEqual(files[0].read_text(),'local new history\n')

    def test_missing_or_ambiguous_history_fails_without_replacement_thread(self):
        with self.assertRaises(Invalid):self.hosts.prepare(self.session)
        self.legacy();self.legacy().with_name('rollout-other-fixture-thread.jsonl').write_text('{}\n')
        with self.assertRaises(Invalid):self.hosts.prepare(self.session)
        self.assertEqual(self.state.session(self.session['id'])['thread_id'],'fixture-thread')

    def test_native_attachment_never_receives_another_host(self):
        self.state.db.execute('INSERT INTO terminal_attachments(session,config_path) VALUES(?,?)',(self.session['id'],'/fixture'))
        with self.assertRaises(Invalid):self.hosts.spec(self.session)

    def test_linked_or_public_home_and_foreign_auth_fail_closed(self):
        self.legacy();spec=self.hosts.spec(self.session)
        spec['base'].mkdir(parents=True,mode=0o700)
        spec['home'].symlink_to(self.root/'project')
        with self.assertRaises(Invalid):self.hosts.prepare(self.session)
        spec['home'].unlink();spec['home'].mkdir(mode=0o755)
        with self.assertRaises(Invalid):self.hosts.prepare(self.session)
        spec['home'].chmod(0o700);spec['codex_home'].mkdir(mode=0o700,exist_ok=True)
        (spec['codex_home']/'auth.json').write_text('{}')
        with self.assertRaises(Invalid):self.hosts.prepare(self.session)

    def test_console_launch_resumes_exact_thread_without_a_prompt_or_second_backend(self):
        spec=self.prepared();calls=[]
        def tmux(s,*args,**kw):
            calls.append(args)
            return SimpleNamespace(returncode=1 if args[0]=='list-panes' else 0,stdout='')
        with patch.object(self.hosts,'tmux',side_effect=tmux):
            self.hosts.terminal(self.session,self.root/'project','fixture-model','high')
        create=next(a for a in calls if a[0]=='new-window')
        command=shlex.split(create[-1])
        self.assertIn('--remote',command);self.assertIn('unix://'+str(spec['socket']),command)
        self.assertEqual(command[-2:],['resume','fixture-thread'])
        self.assertFalse(any(a[0]=='new-session' for a in calls))
        self.assertEqual(console(self.state,self.session)['name'],spec['name'])

    def test_unreachable_existing_host_is_never_replaced(self):
        spec=self.prepared()
        with patch.object(self.hosts,'tmux',return_value=SimpleNamespace(returncode=0,stdout='')) as tm:
            with self.assertRaises(Invalid):self.hosts.start_backend(spec,self.session)
        self.assertEqual(tm.call_count,1)

    def test_catalog_and_session_connection_route_to_separate_sockets(self):
        host=CodexHost(self.state,tracker=self.tracker)
        with patch('intern_personal.codex_bridge.Connection') as connection:
            host.connect(session=self.session);host.connect()
        self.assertNotEqual(connection.call_args_list[0].args[0],connection.call_args_list[1].args[0])

    def test_manual_console_turn_is_recorded_once_without_becoming_native_transport(self):
        observer,path,spec=self.observe()
        self.assertIsNone(TerminalBridge(self.state,self.tracker).binding(self.session))
        self.event(path,'task_started',turn_id='manual')
        self.message(path,'Exact locally typed prompt')
        self.event(path,'task_complete',turn_id='manual',last_agent_message='Done')
        observer.poll(self.session);observer.poll(self.session)
        rows=self.state.db.execute('SELECT * FROM events').fetchall()
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['text'],'Exact locally typed prompt')
        self.assertEqual(rows[0]['state'],'result_ready');self.assertEqual(rows[0]['result'],'Done')
        self.assertEqual(self.state.db.execute('SELECT source FROM turn_records').fetchone()[0],'terminal')

    def test_api_turn_is_associated_once_and_recovered_without_replay(self):
        observer,path,spec=self.observe()
        self.state.db.execute("INSERT INTO events(app_id,message_id,chat_id,sender,role,workspace,session,text,state) VALUES('cli_fixture','om_api','oc_fixture','ou_fixture','agent','project',?,'Exact API prompt','uncertain_execution')",(self.session['id'],))
        seq=self.state.db.execute('SELECT seq FROM events').fetchone()[0]
        observer.prepare(self.session,'Exact API prompt',seq)
        self.event(path,'task_started',turn_id='api');self.message(path,'Exact API prompt')
        self.event(path,'task_complete',turn_id='api',last_agent_message='Saved response')
        observer.poll(self.session)
        rows=self.state.db.execute('SELECT * FROM events').fetchall()
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['state'],'uncertain_delivery')
        self.assertEqual(rows[0]['result'],'Saved response')
        self.assertEqual(self.state.db.execute('SELECT event_seq FROM terminal_turns').fetchone()[0],seq)

    def test_live_manual_turn_blocks_competing_feishu_submission(self):
        observer,path,spec=self.observe()
        self.event(path,'task_started',turn_id='manual');self.message(path,'Still working')
        with self.assertRaises(Invalid):observer.prepare(self.session,'Another request',42)
        self.assertTrue(self.state.ledger.busy(self.session['id']))
        with self.assertRaises(Invalid):self.state.ledger.task(self.session,'start','Change branch during work')
        self.event(path,'task_complete',turn_id='manual',last_agent_message='Done');observer.poll(self.session)
        self.assertFalse(self.state.ledger.busy(self.session['id']))

    def test_git_snapshot_waits_for_the_console_turn(self):
        observer,path,spec=self.observe();host=CodexHost(self.state,tracker=self.tracker)
        self.event(path,'task_started',turn_id='manual');self.message(path,'Work')
        with patch.object(host.observer.stop,'wait') as waiting:
            with self.assertRaises(BridgeError):host.wait_idle(self.session)
        waiting.assert_not_called()
        self.event(path,'task_complete',turn_id='manual',last_agent_message='Done')
        host.wait_idle(self.session)

    def test_stop_manual_turn_uses_only_its_own_backend(self):
        observer,path,spec=self.observe()
        self.event(path,'task_started',turn_id='manual');self.message(path,'Work');observer.poll(self.session)
        host=CodexHost(self.state,tracker=self.tracker);calls=[]
        class Wire:
            def request(self,*args,**kw):calls.append(args)
            def close(self):pass
        with patch.object(host,'connect',return_value=Wire()) as connect,patch.object(host,'ensure_started',side_effect=AssertionError('No startup on stop')):
            host.interrupt(self.session['id'])
        self.assertEqual(connect.call_args.kwargs['session']['id'],self.session['id'])
        self.assertEqual(calls,[('turn/interrupt',dict(threadId='fixture-thread',turnId='manual'))])

    def test_import_and_console_do_not_unpause_untracked_pilot(self):
        self.state.set_session(self.session['id'],state='paused')
        session=self.state.session(self.session['id']);self.legacy();spec=self.hosts.prepare(session)
        with patch.object(self.hosts,'tmux',return_value=SimpleNamespace(returncode=1,stdout='')):
            self.hosts.terminal(session,self.root/'project','fixture-model','high')
        self.assertEqual(self.state.session(session['id'])['state'],'paused')
        self.assertEqual(self.state.db.execute('SELECT COUNT(*) FROM tracked_interns').fetchone()[0],0)

    def test_remote_ui_never_passes_unsupported_permission_overrides(self):
        spec=self.prepared();calls=[]
        def tmux(s,*args,**kw):
            calls.append(args)
            return SimpleNamespace(returncode=1 if args[0]=='list-panes' else 0,stdout='')
        with patch.object(self.hosts,'tmux',side_effect=tmux):
            self.hosts.terminal(self.session,self.root/'project','fixture-model','high')
        command=shlex.split(next(a for a in calls if a[0]=='new-window')[-1])
        self.assertNotIn('-s',command);self.assertNotIn('-a',command)

    def test_fresh_console_notice_never_approves_tools_or_injects_a_prompt(self):
        self.prepared();calls=[];screens=iter(['› 1. Trust and continue\n','› \n'])
        def tmux(s,*args,**kw):
            calls.append(args)
            if args[0]=='list-panes':return SimpleNamespace(stdout='0')
            return SimpleNamespace(stdout=next(screens) if args[0]=='capture-pane' else '')
        with patch.object(self.hosts,'tmux',side_effect=tmux),patch('intern_personal.session_hosts.time.sleep'):
            self.hosts.wait_terminal(self.session)
        sends=[a for a in calls if a[0]=='send-keys']
        self.assertEqual(len(sends),1);self.assertEqual(sends[0][-1],'Enter')
        with patch.object(self.hosts,'tmux',return_value=SimpleNamespace(stdout='1')) as tm:
            with self.assertRaises(Invalid):self.hosts.wait_terminal(self.session)
        self.assertFalse(any(a.args[1]=='send-keys' for a in tm.call_args_list))

    def test_saved_console_reopens_exact_history_without_inference(self):
        self.legacy();host=CodexHost(self.state,tracker=self.tracker)
        self.state.set_session(self.session['id'],state='paused');session=self.state.session(self.session['id'])
        spec=self.hosts.prepare(session);calls=[]
        class Wire:
            def request(self,m,p):calls.append((m,p));return dict(thread=dict(id='fixture-thread',status=dict(type='idle')))
            def close(self):pass
        with patch.object(host,'ensure_started'),patch.object(host,'connect',return_value=Wire()), \
             patch.object(host.hosts,'terminal',return_value=False):
            host.open_saved_console(session)
        self.assertEqual([m for m,p in calls],['thread/resume'])
        self.assertEqual(calls[0][1]['sandbox'],'read-only')
        self.assertEqual(self.state.session(session['id'])['state'],'paused')


if __name__=='__main__':unittest.main()
