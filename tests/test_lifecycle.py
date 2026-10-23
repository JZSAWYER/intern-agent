import json
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch,Mock

import test_live as live_fixture
import test_tracking as tracking_fixture
import test_terminal as terminal_fixture
from intern_personal.core import Invalid
from intern_personal.records import parse,validate_tree,validate_evolution,validate_document,render
from intern_personal.codex_bridge import CodexHost
from intern_personal.live_state import write_private


class LifecycleTests(unittest.TestCase):
    setUp=live_fixture.LiveTests.setUp
    event=live_fixture.LiveTests.event
    command=live_fixture.LiveTests.command

    def test_named_task_covers_multiple_turns_and_idle_is_recorded(self):
        session=self.relay.open_session('project')
        self.command('/task start "Fix relay ordering"',session)
        selected=self.state.ledger.selection(session['id'])
        for n in range(3):
            self.relay.receive(self.event('Turn '+str(n),session['chat_id'],'om_turn_'+str(n)))
            self.assertEqual(self.relay.process_once('agent'),'complete')
        rows=self.state.db.execute("SELECT task_id FROM turn_records r JOIN events e ON e.seq=r.seq WHERE e.role='agent'").fetchall()
        self.assertEqual({r[0] for r in rows},{selected['id']})
        self.command('/task rename Better name',session)
        self.assertEqual(self.state.ledger.selection(session['id'])['id'],selected['id'])
        self.command('/chat',session)
        self.assertIsNone(self.state.ledger.selection(session['id']))
        self.assertEqual(self.state.ledger.tasks('project')[0]['state'],'paused')
        self.relay.receive(self.event('Idle conversation',session['chat_id'],'om_idle'))
        self.relay.process_once('agent')
        last=self.state.db.execute('SELECT * FROM turn_records ORDER BY seq DESC LIMIT 1').fetchone()
        self.assertIsNone(last['task_id'])
        self.command('/task use '+selected['id'],session)
        self.command('/task finish',session)
        self.assertEqual(self.state.ledger.tasks('project')[0]['state'],'completed')

    def test_task_selection_respects_command_arrival_order(self):
        session=self.relay.open_session('project')
        self.relay.receive(self.event('/task start User name',session['chat_id'],'om_start'))
        self.relay.receive(self.event('Do the work',session['chat_id'],'om_work'))
        self.assertEqual(self.relay.process_once('agent'),'idle')
        self.relay.process_once('control');self.relay.process_once('agent')
        row=self.state.db.execute("SELECT r.task_id FROM turn_records r JOIN events e ON e.seq=r.seq WHERE e.role='agent'").fetchone()
        self.assertEqual(row[0],self.state.ledger.selection(session['id'])['id'])

    def test_stop_bypasses_busy_control_and_git_and_cancels_queue(self):
        session=self.relay.open_session('project')
        self.relay.receive(self.event('/help',session['chat_id'],'om_busy_control'))
        self.state.claim('control')
        for n in range(2):self.relay.receive(self.event('Work',session['chat_id'],'om_work_'+str(n)))
        self.relay.receive(self.event('/stop',session['chat_id'],'om_stop'))
        with patch.object(self.host,'interrupt',create=True) as interrupt,patch.object(self.tracker,'record',side_effect=AssertionError('Git must not run before stop')):
            start=time.monotonic();self.assertEqual(self.relay.process_once('urgent'),'result_ready')
            self.assertLess(time.monotonic()-start,0.5)
            interrupt.assert_called_once_with(session['id'])
        self.assertEqual(self.host.calls,[])
        self.assertEqual(self.state.db.execute("SELECT COUNT(*) FROM events WHERE role='agent' AND state='cancelled'").fetchone()[0],2)
        self.assertEqual(self.state.session(session['id'])['state'],'ready')

    def test_urgent_is_owner_scoped_and_deduplicated(self):
        session=self.relay.open_session('project')
        self.assertEqual(self.relay.receive(self.event('/stop',session['chat_id'],'om_stop','ou_other')),'rejected_sender')
        event=self.event('/stop',session['chat_id'],'om_stop')
        self.assertEqual(self.relay.receive(event),'accepted')
        self.assertEqual(self.relay.receive(event),'duplicate')
        with patch.object(self.host,'interrupt',create=True) as interrupt:
            self.relay.process_once('urgent');self.assertEqual(self.relay.process_once('urgent'),'idle')
            self.assertEqual(interrupt.call_count,1)

    def test_slow_acknowledgement_cannot_delay_the_next_interrupt(self):
        session=self.relay.open_session('project')
        with patch.object(self.host,'interrupt',create=True) as interrupt,patch.object(self.api,'send',side_effect=AssertionError('Urgent effects must not send')):
            for n in range(2):
                self.relay.receive(self.event('/stop',session['chat_id'],'om_stop_'+str(n)))
                self.assertEqual(self.relay.process_once('urgent'),'result_ready')
            self.assertEqual(interrupt.call_count,2)

    def test_existing_connection_interrupt_does_not_open_or_read_another_socket(self):
        host=CodexHost(self.state,tracker=self.tracker);connection=Mock()
        host.active['session']=('thread','turn');host.active_connections['session']=connection
        with patch.object(host,'connect',side_effect=AssertionError('No handshake on stop')):
            host.interrupt('session')
        message=connection.send.call_args.args[0]
        self.assertEqual(message['method'],'turn/interrupt');self.assertIsInstance(message['id'],str)
        connection.request.assert_not_called()

    def test_recovery_observes_known_native_turn_without_requeue(self):
        session=self.relay.open_session('project')
        self.relay.receive(self.event('Work',session['chat_id'],'om_work'))
        item=self.state.claim('agent')
        snapshot=json.dumps({'active':'known-turn','event_seq':item['seq']})
        self.state.db.execute('INSERT INTO terminal_attachments(session,config_path,snapshot) VALUES(?,?,?)',(session['id'],'unused',snapshot))
        self.state.recover()
        self.assertEqual(self.state.db.execute('SELECT state FROM events').fetchone()[0],'awaiting_terminal')
        self.assertIsNone(self.state.claim('agent'))


class PipelineGitTests(unittest.TestCase):
    setUp=tracking_fixture.TrackingTests.setUp
    raw=tracking_fixture.TrackingTests.raw
    attach=tracking_fixture.TrackingTests.attach
    session=tracking_fixture.TrackingTests.session

    def add_turn(self,session,text='request',state='queued'):
        cursor=self.state.db.execute("INSERT INTO events(app_id,message_id,chat_id,sender,role,workspace,session,text,state) VALUES('cli_test',?,'oc_workspace','ou_owner','agent','project',?,?,?)",('om_'+str(time.time_ns()),session['id'],text,state))
        item=dict(self.state.db.execute('SELECT * FROM events WHERE seq=?',(cursor.lastrowid,)).fetchone())
        self.state.ledger.ensure_turn(item,time.time(),'local');return item

    def test_projection_aggregate_and_old_retry_cannot_overwrite_newer_work(self):
        session=self.session();self.state.set_session(session['id'],state='ready')
        old=self.add_turn(session,state='complete');old['result']='older result';self.state.transition(old['seq'],'complete',result=old['result'])
        self.tracker.record(session,old,'complete')
        self.state.ledger.task(session,'start','User task')
        new=self.add_turn(session,state='running');self.state.ledger.bind_turn(new)
        self.tracker.record(session,new,'Running')
        self.tracker.record(session,old,'complete')
        root=Path(self.tracker.binding('project')['metadata_path'])
        meta=parse((root/'.intern_workspace/conversations'/session['id']/'status.md').read_text())
        self.assertEqual((meta['mode'],meta['activity']),('working','running'))
        aggregate=parse((root/'.intern_workspace/status.md').read_text())
        self.assertEqual(aggregate['working_conversations'],1)
        validate_tree(root/'.intern_workspace')

    def test_preexisting_dirty_file_is_preserved_and_agent_file_uploaded(self):
        session=self.session();directory=Path(self.tracker.code_directory(session))
        draft=directory/'draft.md';draft.write_text('User draft\n')
        item=self.add_turn(session);self.tracker.begin_turn(session,item)
        (directory/'agent.txt').write_text('Agent work\n')
        self.tracker.publish_code(session,item)
        self.assertEqual(draft.read_text(),'User draft\n')
        self.assertEqual(self.raw(self.remote,'show',self.tracker.intern(session)['code_branch']+':agent.txt'),'Agent work')
        self.assertIn('draft.md',self.raw(directory,'ls-files','--others','--exclude-standard'))
        draft.write_text('Changed user draft\n')
        with self.assertRaises(Invalid):self.tracker.publish_code(session,item)

    def test_reprojection_preserves_completed_trailing_newlines_and_spaces(self):
        session=self.session();root=Path(self.tracker.binding('project')['metadata_path'])
        preserved={}
        for ending in ['\n','\n\n','  ','  \n\n']:
            item=self.add_turn(session,text='Original request'+ending,state='complete')
            self.state.transition(item['seq'],'complete',result='Visible final answer'+ending)
            self.tracker.record(session,item,'complete')
            path=root/'.intern_workspace/conversations'/session['id']/'turns'/('turn_'+str(item['seq'])+'.md')
            preserved[path]=path.read_bytes()
        self.state.ledger.task(session,'start','Continue the existing draft')
        waiting=self.add_turn(session);self.state.ledger.bind_turn(waiting)
        self.tracker.record(session,waiting,'Running')
        self.tracker.record(session,waiting,'Running')
        for path,content in preserved.items():self.assertEqual(path.read_bytes(),content)
        validate_tree(root/'.intern_workspace')

    def test_history_is_append_only_and_cross_references_are_checked(self):
        session=self.session();item=self.add_turn(session,state='complete')
        self.state.transition(item['seq'],'complete',result='answer');self.tracker.record(session,item,'complete')
        root=Path(self.tracker.binding('project')['metadata_path'])/'.intern_workspace'
        history=root/'conversations'/session['id']/'history_log.md';before=history.read_text()
        value=parse(before);value['entries']=[]
        after=render('Conversation history',value,[('Events','none')])
        with self.assertRaises(Invalid):validate_evolution(before,after)
        turn=root/'conversations'/session['id']/'turns'/('turn_'+str(item['seq'])+'.md')
        text=turn.read_text();value=parse(text);value['task_id']='nt_missing'
        turn.write_text(render('Turn',value,[('Request','> request'),('Response','> answer')]))
        with self.assertRaises(Invalid):validate_tree(root)
        value['activity']='garbage'
        with self.assertRaises(Invalid):validate_document('turn.md',render('Turn',value,[('Request','> request'),('Response','> answer')]))

    def test_controller_history_uses_only_explicit_private_archive(self):
        session=self.session();config=self.state.config();config['records_workspace']='project';write_private(self.state.root/'live.json',config)
        cursor=self.state.db.execute("INSERT INTO events(app_id,message_id,chat_id,sender,role,workspace,text,state,result) VALUES('cli_test','om_control','oc_manager','ou_owner','control','__control__','/help','complete','tutorial')")
        item=dict(self.state.db.execute('SELECT * FROM events WHERE seq=?',(cursor.lastrowid,)).fetchone())
        self.state.ledger.ensure_turn(item,time.time(),'local');self.assertTrue(self.tracker.queue_record(None,item,'complete'))
        root=Path(self.tracker.binding('project')['metadata_path'])
        self.assertTrue((root/'.intern_workspace/conversations/controller/turns'/('turn_'+str(item['seq'])+'.md')).exists())

    def test_two_conversations_keep_independent_modes(self):
        first=self.session();second,_=self.state.reserve_session('project',new=True);self.tracker.prepare_session(second)
        self.state.ledger.task(first,'start','Still working')
        item=self.add_turn(second,state='complete');self.state.transition(item['seq'],'complete',result='Idle reply')
        self.tracker.record(second,item,'complete')
        root=Path(self.tracker.binding('project')['metadata_path'])/'.intern_workspace'
        self.assertEqual(parse((root/'status.md').read_text())['mode'],'working')
        self.assertEqual(parse((root/'conversations'/first['id']/'status.md').read_text())['mode'],'working')
        self.assertEqual(parse((root/'conversations'/second['id']/'status.md').read_text())['mode'],'idle')


class NativeStopTests(unittest.TestCase):
    setUp=terminal_fixture.TerminalTests.setUp
    attach=terminal_fixture.TerminalTests.attach
    event=terminal_fixture.TerminalTests.event

    def test_interrupt_bypasses_dispatch_lock_and_transcript_scan(self):
        self.event('task_started',turn_id='active');self.attach();self.bridge.poll(self.session)
        held=threading.Event();release=threading.Event()
        def occupy():
            with self.bridge.lock:held.set();release.wait(2)
        worker=threading.Thread(target=occupy);worker.start();held.wait(1)
        try:
            with patch.object(self.bridge,'poll',side_effect=AssertionError('Stop must not poll')),patch.object(self.bridge,'config',return_value={'pane':'%1'}),patch('intern_personal.terminal_bridge.verify_pane',return_value=True),patch('intern_personal.terminal_bridge.tmux') as transport:
                start=time.monotonic();self.bridge.interrupt(self.session)
                self.assertLess(time.monotonic()-start,0.3)
                self.assertEqual(transport.call_args.args[-1],'Escape')
        finally:release.set();worker.join(2)

    def test_stop_arriving_during_git_check_prevents_later_enter(self):
        self.event('task_started',turn_id='prior');self.attach()
        self.event('task_complete',turn_id='prior',last_agent_message='prior answer');self.bridge.poll(self.session)
        self.state.db.execute("INSERT INTO events(seq,app_id,message_id,chat_id,sender,role,workspace,session,text,state) VALUES(42,'cli_test','om_pending','oc_debug','ou_owner','agent','debug-bot',?,'pending work','running')",(self.session['id'],))
        def check(session):
            self.state.ledger.stop(session)
            return self.state.workspace(session['workspace'])['cwd']
        with patch.object(self.tracker,'code_directory',side_effect=check),patch('intern_personal.terminal_bridge.tmux') as transport:
            self.assertEqual(self.bridge.run(self.session,'pending work',42),'Cancelled before execution.')
            transport.assert_not_called()
