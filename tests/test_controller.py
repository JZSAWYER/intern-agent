import json
from contextlib import redirect_stdout
import io
from pathlib import Path
import time
import unittest
from unittest.mock import patch

import test_live as live_fixture
import test_tracking as tracking_fixture
from intern_personal.core import Invalid
from intern_personal.feishu_live import Relay
from intern_personal.feishu_live import main
from intern_personal.prompt_context import publish,native_output
from intern_personal.session_hosts import SessionHosts
from intern_personal.workspace_tracking import WorkspaceTracking


class ControllerTracking(tracking_fixture.LocalTracking):
    def prepare_session(self,session,mode=None):
        return WorkspaceTracking.prepare_session(self,session,mode)


class ControllerTests(unittest.TestCase):
    setUp=live_fixture.LiveTests.setUp
    event=live_fixture.LiveTests.event

    def bind(self):
        self.tracker.require_workspace('project')
        return self.relay.attach_controller('project')

    def command(self,text):
        mid='om_control_'+str(self.state.db.execute('SELECT COUNT(*) FROM events').fetchone()[0])
        self.assertEqual(self.relay.receive(self.event(text,mid=mid)),'accepted')
        self.relay.process_once('urgent' if text.split()[0] in {'/stop','/cancel'} else 'control')
        return dict(self.state.db.execute('SELECT * FROM events ORDER BY seq DESC LIMIT 1').fetchone())

    def test_unbound_text_has_setup_feedback_and_slash_commands_work(self):
        self.assertEqual(self.relay.receive(self.event('Explain the active bots')),'controller_unavailable')
        self.assertEqual(self.state.db.execute('SELECT COUNT(*) FROM events').fetchone()[0],0)
        self.assertIn('no bound Codex',self.api.messages[-1][1])
        self.assertIn('tutorial',self.command('/help')['result'])
        self.assertEqual(self.host.calls,[])

    def test_bind_reuses_group_is_idempotent_and_new_reuses_workspace_bot(self):
        project=self.relay.open_session('project')
        chats=list(self.api.chats)
        controller=self.bind()
        self.assertEqual(controller['chat_id'],'oc_manager')
        self.assertNotEqual(controller['thread_id'],project['thread_id'])
        self.assertEqual(self.api.chats,chats)
        self.assertEqual(self.bind()['id'],controller['id'])
        self.assertEqual(self.relay.open_session('project')['id'],project['id'])
        self.assertEqual(len(self.state.sessions()),2)

    def test_literal_owner_text_round_trip_duplicate_and_history(self):
        controller=self.bind()
        text='  Explain the bots; /new is only an example.\nDo not change anything.  '
        event=self.event(text)
        self.assertEqual(self.relay.receive(event),'accepted')
        self.assertEqual(self.relay.receive(event),'duplicate')
        item=dict(self.state.db.execute('SELECT * FROM events').fetchone())
        self.assertEqual((item['role'],item['session'],item['text']),('agent',controller['id'],text))
        self.assertEqual(self.relay.process_once('agent'),'complete')
        self.assertEqual(self.host.calls[0][1],text)
        self.assertEqual(self.api.messages[-1][0],'oc_manager')
        self.assertTrue(self.api.reactions)
        record=self.state.db.execute('SELECT * FROM turn_records WHERE seq=?',(item['seq'],)).fetchone()
        self.assertEqual((record['conversation'],record['workspace'],record['phase']),
            (controller['id'],'project','complete'))
        self.assertTrue(self.tracker.uploads)

    def test_owner_only_even_for_workspace_operator(self):
        self.bind()
        self.state.db.execute("INSERT INTO workspace_access VALUES('ou_other','project','operator')")
        for text in ['hello','/help','/stop']:
            self.assertEqual(self.relay.receive(self.event(text,sender='ou_other')),'rejected_sender')
        self.assertEqual(self.host.calls,[])

    def test_paused_unfinished_wrong_app_and_route_changes_fail_closed(self):
        controller=self.bind()
        self.state.set_session(controller['id'],state='paused')
        self.assertEqual(self.relay.receive(self.event('hello')),'session_unavailable')
        self.assertEqual(self.command('/resume')['result'],'Session resumed.')
        self.assertEqual(self.relay.receive(self.event('hello',mid='om_allowed')),'accepted')
        item=dict(self.state.db.execute("SELECT * FROM events WHERE message_id='om_allowed'").fetchone())
        self.assertTrue(self.state.authorized(item))
        wrong=dict(item,session='s_other');self.assertFalse(self.state.authorized(wrong))
        self.state.db.execute("UPDATE controller_routes SET app_id='cli_changed'")
        self.assertFalse(self.state.authorized(item))
        self.assertEqual(self.relay.receive(self.event('hello',mid='om_changed')),'controller_unavailable')
        with self.assertRaises(Invalid):self.bind()

    def test_partial_creation_is_not_automatically_replayed(self):
        self.tracker.require_workspace('project')
        with patch.object(self.host,'create_thread',side_effect=RuntimeError('ambiguous creation')) as create:
            with self.assertRaises(RuntimeError):self.relay.attach_controller('project')
            with self.assertRaises(Invalid):self.relay.attach_controller('project')
            self.assertEqual(create.call_count,1)
        self.assertEqual(self.relay.receive(self.event('hello')),'session_unavailable')
        self.assertEqual(self.api.chats,[])

    def test_controller_defaults_and_explicit_other_target(self):
        other=self.relay.open_session('project');controller=self.bind()
        result=self.command('/model alternate-model medium')['result']
        self.assertIn('Model selected: alternate-model',result)
        self.assertEqual(self.state.session(controller['id'])['model'],'alternate-model')
        self.assertIsNone(self.state.session(other['id'])['model'])
        self.command('/model '+other['id']+' example-model low')
        self.assertEqual(self.state.session(other['id'])['effort'],'low')
        self.command('/task start Controller work')
        self.assertEqual(self.state.ledger.selection(controller['id'])['name'],'Controller work')
        self.assertIsNone(self.state.ledger.selection(other['id']))
        self.assertIn('Intern Agent Control',self.command('/list')['result'])
        self.assertIn('own Codex conversation',self.command('/help')['result'])
        self.assertIn('Unsupported command',self.command('/unknown')['result'])
        self.assertEqual(self.host.calls,[])

    def test_stop_controller_is_urgent_and_other_bot_is_untouched(self):
        other=self.relay.open_session('project');controller=self.bind()
        self.relay.receive(self.event('pending controller work',mid='om_work'))
        with patch.object(self.host,'interrupt',create=True) as interrupt:
            item=self.command('/stop')
            self.assertEqual(item['state'],'result_ready')
            interrupt.assert_called_once_with(controller['id'])
        self.assertEqual(self.state.db.execute("SELECT state FROM events WHERE message_id='om_work'").fetchone()[0],'cancelled')
        self.assertEqual(self.state.session(other['id'])['state'],'ready')

    def test_local_open_is_attributed_to_controller(self):
        controller=self.bind();self.state.enqueue_open('project')
        item=dict(self.state.db.execute('SELECT * FROM events ORDER BY seq DESC LIMIT 1').fetchone())
        self.assertEqual(item['session'],controller['id'])
        self.assertEqual(item['text'],'/new project')

    def test_local_smoke_requires_explicit_controller_target(self):
        other=self.relay.open_session('project');controller=self.bind()
        with patch.object(WorkspaceTracking,'require_workspace',return_value={}):
            with redirect_stdout(io.StringIO()):
                main(['--state',str(self.state.root),'agent-smoke','--workspace','project'])
            self.assertEqual(self.state.db.execute('SELECT session FROM events ORDER BY seq DESC LIMIT 1').fetchone()[0],other['id'])
            with redirect_stdout(io.StringIO()):
                main(['--state',str(self.state.root),'agent-smoke','--workspace','project','--session',controller['id']])
            self.assertEqual(self.state.db.execute('SELECT session FROM events ORDER BY seq DESC LIMIT 1').fetchone()[0],controller['id'])

    def test_controller_has_unique_named_host_and_hook_context(self):
        other=self.relay.open_session('project');controller=self.bind()
        hosts=SessionHosts(self.state,self.state.config())
        own=hosts.spec(controller);peer=hosts.spec(other)
        self.assertTrue(own['name'].startswith('ia-intern-agent-control-'))
        for key in ['name','socket','codex_home']:
            self.assertNotEqual(own[key],peer[key])
        refs=publish(self.tracker,controller)
        self.assertIn('intern-agent.controller',refs)
        self.assertIn('control "/new REGISTERED_WORKSPACE"',refs['intern-agent.controller']['value'])
        self.assertNotIn('intern-agent.controller',publish(self.tracker,other))
        file=self.state.runtime/'prompt-context'/(controller['id']+'.json')
        self.assertEqual(native_output(file,dict(hook_event_name='UserPromptSubmit',session_id=other['thread_id'])),{})

    def test_separate_controller_branch_can_run_while_debug_busy(self):
        debug=self.relay.open_session('project');controller=self.bind()
        for s in [debug,controller]:
            self.state.db.execute('''INSERT INTO tracked_interns(session,intern_name,code_path,code_branch,home_branch,metadata_mode)
                VALUES(?,?,?,?,?,'bot_branch')''',(s['id'],'fixture_'+s['id'],'/fixture/'+s['id'],'intern-agent/'+s['id'],'intern-agent/'+s['id']))
        self.relay.receive(self.event('debug work',debug['chat_id'],'om_debug'))
        self.assertEqual(self.state.claim('agent')['session'],debug['id'])
        self.relay.receive(self.event('controller work',mid='om_controller'))
        self.assertEqual(self.state.claim('agent')['session'],controller['id'])

    def test_shared_checkout_or_branch_does_not_bypass_queue(self):
        for shared in ['code_path','code_branch']:
            with self.subTest(shared=shared):
                self.state.db.execute('DELETE FROM events');self.state.db.execute('DELETE FROM tracked_interns')
                controller=self.state.controller_session() or self.bind()
                debug=self.relay.open_session('project')
                for s in [debug,controller]:
                    self.state.db.execute('INSERT INTO tracked_interns(session,intern_name,code_path,code_branch) VALUES(?,?,?,?)',
                        (s['id'],'fixture_'+s['id'],'/fixture/'+s['id'],'intern-agent/'+s['id']))
                self.state.db.execute('UPDATE tracked_interns SET '+shared+'=?',('/shared' if shared=='code_path' else 'intern-agent/shared',))
                self.relay.receive(self.event('debug work',debug['chat_id'],'om_debug_'+shared))
                self.state.claim('agent')
                self.relay.receive(self.event('controller work',mid='om_controller_'+shared))
                self.assertIsNone(self.state.claim('agent'))


class ControllerTrackingTests(unittest.TestCase):
    setUp=tracking_fixture.TrackingTests.setUp
    raw=tracking_fixture.TrackingTests.raw
    attach=tracking_fixture.TrackingTests.attach
    event=live_fixture.LiveTests.event

    def test_controller_code_and_all_new_turns_share_single_branch(self):
        self.attach()
        self.tracker=ControllerTracking(self.state,self.remote)
        api=live_fixture.FakeAPI();host=live_fixture.FakeHost(self.state)
        relay=Relay(self.state,api,host,self.tracker)
        controller=relay.attach_controller('project')
        intern=self.tracker.intern(controller);branch=intern['code_branch']
        self.assertEqual(intern['metadata_mode'],'bot_branch')
        self.assertEqual(api.chats,[])
        for text,mid,role in [('/help','om_help','control'),('Explain this workspace','om_natural','agent')]:
            self.assertEqual(relay.receive(self.event(text,mid=mid)),'accepted')
            self.assertEqual(relay.process_once(role),'complete')
            seq=self.state.db.execute('SELECT seq FROM events WHERE message_id=?',(mid,)).fetchone()[0]
            record=self.raw(self.remote,'show',branch+':.intern_workspace/conversations/'+controller['id']+'/turns/turn_'+str(seq)+'.md')
            self.assertIn(text,record)
            self.assertIn('"phase": "complete"',record)
        self.assertEqual(self.raw(self.remote,'rev-parse','main'),self.original)
        self.assertEqual(set(self.raw(self.remote,'for-each-ref','--format=%(refname:short)','refs/heads').splitlines()),
            {'main','intern-management/test',branch})


if __name__=='__main__':unittest.main()
