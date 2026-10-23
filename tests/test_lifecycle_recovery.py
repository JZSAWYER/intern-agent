import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from intern_personal.core import Invalid
from intern_personal.live_state import write_private
from intern_personal.codex_bridge import BridgeError, CodexHost, Connection
from intern_personal import operations
import test_session_hosts as host_fixtures
import test_live as live_fixtures


class RecoveryTests(unittest.TestCase):
    setUp = host_fixtures.SessionHostTests.setUp
    legacy = host_fixtures.SessionHostTests.legacy
    prepared = host_fixtures.SessionHostTests.prepared
    observe = host_fixtures.SessionHostTests.observe
    emit = host_fixtures.SessionHostTests.emit
    event = host_fixtures.SessionHostTests.event

    def reset_fixture(self):
        observer, path, spec = self.observe()
        self.hosts.remember(self.session, spec, self.session['thread_id'])
        new = path.with_name('rollout-test-fixture-new.jsonl')
        new.write_text('{"type":"session_meta","payload":{"id":"fixture-new"}}\n')
        host = CodexHost(self.state, tracker=self.tracker)
        quiet=patch.object(host.hosts,'_view_quiet',return_value=True)
        quiet.start();self.addCleanup(quiet.stop)
        windows = {'Codex': 'old'}
        calls = []
        def tmux(spec, *args, **kwargs):
            calls.append(args)
            target = args[args.index('-t')+1].rsplit(':', 1)[-1] if '-t' in args else None
            if args[0] == 'list-panes':
                return SimpleNamespace(returncode=0 if target in windows else 1, stdout='0\n')
            if args[0] == 'capture-pane':
                return SimpleNamespace(returncode=0, stdout='› \n')
            if args[0] == 'new-window': windows[args[args.index('-n')+1]] = 'new'
            if args[0] == 'rename-window': windows[args[-1]] = windows.pop(target)
            if args[0] == 'kill-window': windows.pop(target)
            return SimpleNamespace(returncode=0, stdout='')
        class Wire:
            def request(self, method, params): return {'thread': {'id': 'fixture-new'}}
            def close(self): pass
        return host, windows, calls, tmux, Wire(), dict(observer.binding(self.session))

    def test_failed_reset_keeps_old_thread_view_and_observer(self):
        host, windows, calls, tmux, wire, binding = self.reset_fixture()
        with patch.object(host, 'ensure_started'), patch.object(host, 'connect', return_value=wire), \
             patch.object(host, '_turn', return_value='INTERN_AGENT_READY'), \
             patch.object(host.hosts, 'tmux', side_effect=tmux), \
             patch.object(host.hosts, 'wait_terminal', side_effect=Invalid('Fixture console failed')):
            with self.assertRaises(Invalid): host.create_thread(self.session)
        self.assertEqual(self.state.session(self.session['id'])['thread_id'], 'fixture-thread')
        self.assertEqual(windows['Codex'], 'old')
        self.assertEqual(dict(host.observer.binding(self.session)), binding)
        self.assertEqual(self.state.db.execute('SELECT view_thread FROM session_hosts').fetchone()[0], 'fixture-thread')

    def test_failed_observer_switch_restores_original_cursor_and_view(self):
        host, windows, calls, tmux, wire, binding = self.reset_fixture()
        register = host.observer.register
        def fail_after_registration(*args):
            register(*args)
            raise Invalid('Fixture observation failed')
        with patch.object(host, 'ensure_started'), patch.object(host, 'connect', return_value=wire), \
             patch.object(host, '_turn', return_value='INTERN_AGENT_READY'), \
             patch.object(host.hosts, 'tmux', side_effect=tmux), patch.object(host.hosts, 'wait_terminal'), \
             patch.object(host.observer, 'register', side_effect=fail_after_registration):
            with self.assertRaises(Invalid): host.create_thread(self.session)
        self.assertEqual(windows['Codex'], 'old')
        self.assertEqual(dict(host.observer.binding(self.session)), binding)
        self.assertEqual(self.state.session(self.session['id'])['thread_id'], 'fixture-thread')

    def test_successful_reset_switches_one_view_after_readiness(self):
        host, windows, calls, tmux, wire, binding = self.reset_fixture()
        def ready(*args, **kwargs): self.assertEqual(windows['Codex'], 'old')
        with patch.object(host, 'ensure_started'), patch.object(host, 'connect', return_value=wire), \
             patch.object(host, '_turn', return_value='INTERN_AGENT_READY'), \
             patch.object(host.hosts, 'tmux', side_effect=tmux), patch.object(host.hosts, 'wait_terminal', side_effect=ready):
            self.assertEqual(host.create_thread(self.session), 'fixture-new')
        self.assertEqual(windows, {'Codex': 'new'})
        self.assertEqual(host.observer.config_path(host.observer.binding(self.session))['thread_id'], 'fixture-new')
        self.assertEqual(self.state.db.execute('SELECT view_thread FROM session_hosts').fetchone()[0], 'fixture-new')

    def pending_fixture(self, reject):
        observer, path, spec = self.observe()
        host = CodexHost(self.state, tracker=self.tracker)
        self.state.db.execute("INSERT INTO events(app_id,message_id,chat_id,sender,role,workspace,session,text,state) VALUES('cli_fixture','om_api','oc_fixture','ou_fixture','agent','project',?,'Exact prompt','running')", (self.session['id'],))
        class WebSocket:
            def send(self, text): self.message = json.loads(text)
            def recv(self, timeout):
                if self.message['method'] == 'turn/start':
                    if reject: return json.dumps({'id': self.message['id'], 'error': {'code': -32602, 'message': 'private-server-detail'}})
                    raise TimeoutError('Fixture connection timed out')
                return json.dumps({'id': self.message['id'], 'result': {}})
            def close(self): pass
        wire = Connection('/fixture', connector=lambda *args, **kw: WebSocket())
        return host, wire

    def test_late_view_switch_failure_rolls_back_both_windows_and_registry(self):
        host, windows, calls, tmux, wire, binding = self.reset_fixture()
        def fail_selection(spec,*args,**kwargs):
            if args[0]=='select-window':raise Invalid('Fixture selection failed')
            return tmux(spec,*args,**kwargs)
        with patch.object(host,'ensure_started'),patch.object(host,'connect',return_value=wire), \
             patch.object(host,'_turn',return_value='INTERN_AGENT_READY'), \
             patch.object(host.hosts,'tmux',side_effect=fail_selection),patch.object(host.hosts,'wait_terminal'):
            with self.assertRaises(Invalid):host.create_thread(self.session)
        self.assertEqual(windows,{'Codex':'old'})
        self.assertEqual(dict(host.observer.binding(self.session)),binding)
        self.assertEqual(self.state.db.execute('SELECT view_thread FROM session_hosts').fetchone()[0],'fixture-thread')

    def test_failed_candidate_with_typed_draft_is_preserved_for_review(self):
        host, windows, calls, tmux, wire, binding = self.reset_fixture()
        def candidate_draft(spec,*args,**kwargs):
            if args[0]=='capture-pane' and 'pending' in args[args.index('-t')+1]:
                return SimpleNamespace(returncode=0,stdout='› Unsaved draft\n')
            return tmux(spec,*args,**kwargs)
        with patch.object(host,'ensure_started'),patch.object(host,'connect',return_value=wire), \
             patch.object(host,'_turn',return_value='INTERN_AGENT_READY'), \
             patch.object(host.hosts,'tmux',side_effect=candidate_draft), \
             patch.object(host.hosts,'wait_terminal',side_effect=Invalid('Fixture readiness failed')):
            with self.assertRaises(Invalid):host.create_thread(self.session)
        self.assertEqual(windows['Codex'],'old');self.assertEqual(len(windows),2)
        self.assertEqual(dict(host.observer.binding(self.session)),binding)

    def test_reset_checks_draft_again_after_candidate_becomes_ready(self):
        host, windows, calls, tmux, wire, binding = self.reset_fixture();captures=[]
        def changed_draft(spec,*args,**kwargs):
            if args[0]=='capture-pane' and args[args.index('-t')+1].endswith(':Codex'):
                captures.append(args)
                return SimpleNamespace(returncode=0,stdout='› \n' if len(captures)==1 else '› Owner draft\n')
            return tmux(spec,*args,**kwargs)
        with patch.object(host,'ensure_started'),patch.object(host,'connect',return_value=wire), \
             patch.object(host,'_turn',return_value='INTERN_AGENT_READY'), \
             patch.object(host.hosts,'tmux',side_effect=changed_draft),patch.object(host.hosts,'wait_terminal'):
            with self.assertRaisesRegex(Invalid,'original console changed'):host.create_thread(self.session)
        self.assertEqual(windows,{'Codex':'old'})
        self.assertEqual(dict(host.observer.binding(self.session)),binding)

    def test_explicit_start_rejection_clears_pending_without_interrupting(self):
        host, wire = self.pending_fixture(True)
        with patch.object(host, 'ensure_started'), patch.object(host, 'ensure_terminal'), \
             patch.object(host, 'connect', return_value=wire), patch.object(host, 'interrupt') as interrupt:
            with self.assertRaisesRegex(BridgeError, '-32602') as error: host.run(self.session, 'Exact prompt')
        self.assertNotIn('private-server-detail', str(error.exception))
        self.assertFalse(json.loads(host.observer.binding(self.session)['snapshot']).get('pending'))
        interrupt.assert_not_called()

    def test_unknown_start_timeout_preserves_pending_for_recovery(self):
        host, wire = self.pending_fixture(False)
        with patch.object(host, 'ensure_started'), patch.object(host, 'ensure_terminal'), \
             patch.object(host, 'connect', return_value=wire):
            with self.assertRaises(TimeoutError): host.run(self.session, 'Exact prompt')
        self.assertTrue(json.loads(host.observer.binding(self.session)['snapshot']).get('pending'))

    def test_stop_before_submission_clears_only_its_own_pending_marker(self):
        host,wire=self.pending_fixture(True)
        with patch.object(host,'ensure_started'),patch.object(host,'ensure_terminal'), \
             patch.object(host,'connect',return_value=wire), \
             patch.object(self.state.ledger,'session_stopped',side_effect=[False,True]):
            self.assertEqual(host.run(self.session,'Exact prompt'),'Cancelled before execution.')
        self.assertFalse(json.loads(host.observer.binding(self.session)['snapshot']).get('pending'))

    def test_one_healthy_console_does_not_hide_another_observer_error(self):
        observer,path,spec=self.observe()
        other,_=self.state.reserve_session('project',new=True)
        self.state.db.execute('INSERT INTO console_views VALUES(?,?,0,?)',(other['id'],'/fixture','{}'))
        with patch.object(observer,'poll',side_effect=[Invalid('private details'),{}]):observer.poll_all()
        self.assertEqual(observer.last_error,'Invalid')
        with patch.object(observer,'poll',return_value={}):observer.poll_all()
        self.assertIsNone(observer.last_error)

    def test_persistent_console_error_queues_one_sanitized_owner_notice(self):
        import time
        write_private(self.state.root/'credentials.json',dict(app_id='cli_fixture',app_secret='example-fixture-only'))
        observer,path,spec=self.observe()
        self.event(path,'task_started',turn_id='manual-fault')
        observer.poll(self.session)
        with patch.object(observer,'poll',side_effect=Invalid('private backend detail')):
            observer.poll_all()
            self.state.db.execute('UPDATE observation_faults SET first_at=?',(time.time()-31,))
            observer.poll_all();observer.poll_all()
        notices=self.state.db.execute('SELECT text FROM assistant_progress').fetchall()
        self.assertEqual(len(notices),1)
        self.assertIn('not interrupted',notices[0]['text']);self.assertNotIn('private backend detail',notices[0]['text'])
        with patch.object(observer,'poll',return_value={}):observer.poll_all()
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM observation_faults').fetchone()[0],0)


class ReactionRecoveryTests(unittest.TestCase):
    setUp = live_fixtures.LiveTests.setUp
    event = live_fixtures.LiveTests.event

    def test_resolved_cancel_keeps_reaction_until_console_completion(self):
        session = self.relay.open_session('project')
        self.relay.receive(self.event('Task', session['chat_id'], 'om_console'))
        item = self.state.claim('agent'); self.relay.begin_typing(item)
        self.state.db.execute('INSERT INTO console_views VALUES(?,?,0,?)',
            (session['id'], str(self.state.root/'unused'), json.dumps({'active': 'console-turn', 'event_seq': item['seq']})))
        self.state.transition(item['seq'], 'cancelled')
        self.relay.cleanup_typing(); self.assertEqual(self.api.removals, [])
        self.state.db.execute("UPDATE console_views SET snapshot='{}'")
        self.relay.cleanup_typing(); self.assertEqual(self.api.removals, self.api.reactions)

    def test_pending_console_marker_also_protects_reaction(self):
        session=self.relay.open_session('project')
        self.relay.receive(self.event('Task',session['chat_id'],'om_pending_console'))
        item=self.state.claim('agent');self.relay.begin_typing(item)
        self.state.db.execute('INSERT INTO console_views VALUES(?,?,0,?)',
            (session['id'],str(self.state.root/'unused'),json.dumps({'pending':{'seq':item['seq']}})))
        self.state.transition(item['seq'],'complete')
        self.relay.cleanup_typing();self.assertEqual(self.api.removals,[])

    def test_doctor_reports_observer_failure_even_when_receiver_connected(self):
        from intern_personal.live_state import write_private
        import time
        write_private(self.state.root/'health.json',dict(receiver_connected=True,updated_at=time.time(),console_error_type='Invalid'))
        report=operations.diagnose(self.state);checks={c['id']:c for c in report['checks']}
        self.assertTrue(checks['receiver']['passed'])
        self.assertTrue(checks['console_observation']['blocking']);self.assertFalse(report['ok'])


if __name__ == '__main__': unittest.main()
