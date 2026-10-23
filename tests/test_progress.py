"""Progress eligibility, transport isolation and restart boundaries."""
import json
import unittest
from unittest.mock import patch

from intern_personal import progress
from intern_personal.codex_bridge import CodexHost
from intern_personal.feishu_live import Relay
from intern_personal.live_state import LiveState
from intern_personal.terminal_bridge import TerminalBridge
from test_live import FakeAPI,FakeHost
import test_live as live_fixture
import test_terminal as terminal_fixture


class ProgressTests(unittest.TestCase):
    event=live_fixture.LiveTests.event

    def setUp(self):
        live_fixture.LiveTests.setUp(self)
        self.console=patch('builtins.print').start()
        self.addCleanup(patch.stopall)

    def running(self):
        session=self.relay.open_session('project')
        self.relay.receive(self.event('Original words.',session['chat_id'],'om_progress'))
        item=self.state.claim('agent');self.state.ledger.ensure_turn(item)
        self.notices=[]
        self.api.progress=lambda chat,text,key:self.notices.append((chat,text,key)) or 'om_receipt'
        return session,item

    def add(self,session,item,text='I found the issue.',identity='paragraph',turn='turn'):
        progress.enqueue(self.state,session,turn,identity,text,source='managed',event_seq=item['seq'])

    def test_completed_commentary_is_forwarded_before_final_without_rewriting_input(self):
        session,item=self.running();host=CodexHost(self.state,tracker=self.tracker);requests=[]
        def message(kind,text='hidden',phase=None,identity='part',**extra):
            return {'method':'item/completed','params':{'threadId':session['thread_id'],'turnId':'turn',
                'item':dict(type=kind,text=text,id=identity,phase=phase),**extra}}
        events=iter([
            {'method':'item/agentMessage/delta','params':{'delta':'unclassified delta'}},
            {'method':'item/reasoning/textDelta','params':{'delta':'private reasoning'}},
            message('reasoning'),message('commandExecution'),message('mcpToolCall'),message('agentMessage',phase='analysis'),
            message('agentMessage','wrong thread',phase='commentary',threadId='another-thread'),
            message('agentMessage','wrong turn',phase='commentary',turnId='another-turn'),
            message('agentMessage','I found the issue.',phase='commentary'),
            message('agentMessage','I found the issue.',phase='commentary'),
            message('agentMessage','Done.',phase='final_answer',identity='final'),
            {'method':'turn/completed','params':{'threadId':session['thread_id'],'turn':{'id':'turn','status':'completed'}}}])
        test=self
        class Wire:
            def request(self,method,params):
                requests.append((method,params));return {'turn':{'id':'turn'}} if method=='turn/start' else {}
            def notification(self,timeout):
                progress.deliver(test.relay)
                return next(events)
            def close(self):pass
        with patch.object(host,'ensure_started'),patch.object(host,'ensure_terminal'),patch.object(host,'connect',return_value=Wire()):
            self.assertEqual(host.run(session,'Original words.'),'Done.')
        self.assertEqual([n[1] for n in self.notices],['I found the issue.'])
        self.console.assert_called_once_with('[Intern Agent: project] I found the issue.',flush=True)
        self.assertEqual(requests[-1][1]['input'],[{'type':'text','text':'Original words.'}])
        self.assertFalse(progress.deliver(self.relay))

    def test_dedup_restart_claims_and_ambiguous_send_never_replay(self):
        s,item=self.running();self.add(s,item);self.add(s,item)
        self.assertTrue(progress.deliver(self.relay));self.assertFalse(progress.deliver(self.relay))
        row=self.state.db.execute('SELECT state,text,receipt FROM assistant_progress').fetchone()
        self.assertEqual(tuple(row),('sent','','om_receipt'))
        reopened=LiveState(self.temp.name);self.addCleanup(reopened.close)
        relay=Relay(reopened,self.api,self.host,self.tracker)
        self.assertFalse(progress.deliver(relay))
        self.state.db.execute('UPDATE assistant_progress SET sent_at=0')
        self.add(s,item,identity='second')
        with patch.object(self.api,'progress',side_effect=RuntimeError('ambiguous')):
            self.assertTrue(progress.deliver(self.relay))
        self.assertFalse(progress.deliver(self.relay));self.assertEqual(len(self.notices),1)
        self.assertEqual(self.state.db.execute("SELECT state FROM assistant_progress WHERE state='claimed'").fetchone()[0],'claimed')

    def test_off_discards_queued_prose_and_milestones_before_reenable(self):
        s,item=self.running();self.add(s,item);progress.mark(self.relay,item,'running')
        progress.set_mode(self.state,s['id'],False);progress.set_mode(self.state,s['id'],True)
        self.assertFalse(progress.deliver(self.relay));self.assertEqual(self.notices,[])
        progress.set_mode(self.state,s['id'],False);self.add(s,item,identity='new')
        self.assertEqual(self.state.db.execute('SELECT COUNT(*) FROM assistant_progress').fetchone()[0],1)

    def test_start_acknowledgements_are_suppressed_including_old_pending_notices(self):
        s,item=self.running();progress.mark(self.relay,item,'running')
        self.assertEqual(self.state.db.execute('SELECT COUNT(*) FROM progress_notices').fetchone()[0],0)
        self.state.db.execute("INSERT INTO progress_notices VALUES(?,'running','pending',?)",(item['seq'],progress.time.time()))
        self.add(s,item,'I found the cause.')
        self.assertTrue(progress.deliver(self.relay));self.assertEqual([n[1] for n in self.notices],['I found the cause.'])
        self.assertTrue(progress.deliver(self.relay));self.assertEqual(len(self.notices),1)
        progress.mark(self.relay,item,'uploading');self.assertTrue(progress.deliver(self.relay))
        self.assertEqual(self.notices[-1][1],progress.LABELS['uploading'])

    def test_completion_stop_revocation_and_stale_updates_are_discarded(self):
        for reason in ['complete','stop','revoked','stale','paused','finish']:
            with self.subTest(reason=reason):
                s,item=self.running();self.add(s,item)
                if reason=='complete':self.state.transition(item['seq'],'complete')
                elif reason=='stop':self.state.ledger.stop(s)
                elif reason=='revoked':self.state.db.execute("UPDATE assistant_progress SET sender='ou_stranger'")
                elif reason=='stale':self.state.db.execute('UPDATE assistant_progress SET created_at=0')
                elif reason=='paused':self.state.set_session(s['id'],state='paused')
                elif reason=='finish':progress.finish(self.state,s,'turn')
                progress.deliver(self.relay);self.assertEqual(self.notices,[])
                self.state.transition(item['seq'],'complete');self.state.set_session(s['id'],state='ready')
                self.state.db.execute('DELETE FROM assistant_progress')
                self.state.db.execute('DELETE FROM events WHERE seq=?',(item['seq'],))

    def test_text_filter_redacts_credentials_omits_code_and_bounds_unicode(self):
        text='I checked it.\n```python\nprint("raw tool output")\n```\nThe next check is ready. example-local-fixture-only cli_test'
        result=progress.prose(self.state,text)
        self.assertIn('I checked it.',result);self.assertIn('The next check is ready.',result)
        self.assertNotIn('print(',result);self.assertNotIn('example-local-fixture-only',result);self.assertNotIn('cli_test',result)
        self.assertEqual(progress.prose(self.state,'```sh\nsecret'), '')
        self.assertEqual(progress.prose(self.state,'{"command":"raw"}'), '')
        result=progress.prose(self.state,'检查。'*4000)
        self.assertLessEqual(len(result.encode()),progress.MAX_TEXT_BYTES);self.assertTrue(result.endswith('…'))

    def test_backlog_and_rate_limit_bound_updates_without_blocking_other_sessions(self):
        s,item=self.running()
        for index in range(50):self.add(s,item,identity=str(index))
        self.assertEqual(self.state.db.execute("SELECT COUNT(*) FROM assistant_progress WHERE state='pending'").fetchone()[0],progress.MAX_PENDING)
        progress.deliver(self.relay);self.assertFalse(progress.deliver(self.relay))
        other=self.relay.open_session('other')
        self.relay.receive(self.event('Other.',other['chat_id'],'om_other'))
        event=self.state.claim('agent');self.add(other,event,identity='other')
        self.assertTrue(progress.deliver(self.relay));self.assertEqual(len(self.notices),2)
        self.state.db.execute('UPDATE assistant_progress SET sent_at=0')
        self.assertTrue(progress.deliver(self.relay))

    def test_failed_optional_queue_does_not_abort_codex_turn(self):
        s,item=self.running();host=CodexHost(self.state,tracker=self.tracker)
        events=iter([
            {'method':'item/completed','params':{'threadId':s['thread_id'],'turnId':'turn','item':{'id':'c','type':'agentMessage','phase':'commentary','text':'I checked.'}}},
            {'method':'item/completed','params':{'threadId':s['thread_id'],'turnId':'turn','item':{'id':'f','type':'agentMessage','phase':'final_answer','text':'Done.'}}},
            {'method':'turn/completed','params':{'threadId':s['thread_id'],'turn':{'id':'turn','status':'completed'}}}])
        class Wire:
            def request(self,*a):return {'turn':{'id':'turn'}}
            def notification(self,*a):return next(events)
            def close(self):pass
        with patch.object(host,'ensure_started'),patch.object(host,'ensure_terminal'),patch.object(host,'connect',return_value=Wire()),patch.object(progress,'managed',side_effect=RuntimeError('optional')):
            self.assertEqual(host.run(s,'Original words.'),'Done.')


class NativeProgressTests(unittest.TestCase):
    setUp=terminal_fixture.TerminalTests.setUp
    event=terminal_fixture.TerminalTests.event
    message=terminal_fixture.TerminalTests.message
    attach=terminal_fixture.TerminalTests.attach

    def assistant(self,text,phase='commentary',kind='message',identity=None):
        with self.rollout.open('a') as f:
            f.write(json.dumps({'type':'response_item','payload':{'type':kind,'role':'assistant','phase':phase,'id':identity,'content':[{'type':'output_text','text':text}]}})+'\n')

    def relay(self):
        api=FakeAPI();api.progress=api.send
        return Relay(self.state,api,FakeHost(self.state),self.tracker)

    def test_native_public_prose_skips_history_echo_tools_reasoning_and_final(self):
        self.event('task_started',turn_id='current');self.message('Owner words.')
        self.assistant('Historical commentary.');self.attach();self.bridge.poll(self.session)
        relay=self.relay();self.assertFalse(progress.deliver(relay))
        self.event('agent_message',phase='commentary',message='Duplicated echo.')
        self.assistant('Private reasoning.',phase='analysis',kind='reasoning')
        self.assistant('Function calls.',kind='function_call')
        self.assistant('Unknown phase.',phase=None)
        self.assistant('Final answer.',phase='final_answer')
        self.assistant('I am checking the connection.')
        self.bridge.poll(self.session);self.bridge.poll(self.session)
        self.assertTrue(progress.deliver(relay))
        self.assertEqual([m[1] for m in relay.api.messages],['I am checking the connection.'])
        self.assertEqual(self.state.db.execute('SELECT COUNT(*) FROM events').fetchone()[0],0)
        restarted=TerminalBridge(self.state,self.tracker);restarted.poll(self.session)
        self.assertFalse(progress.deliver(relay))

    def test_native_cursor_and_progress_commit_or_rollback_together(self):
        self.event('task_started',turn_id='current');self.message('Owner words.');self.attach();self.bridge.poll(self.session)
        before=self.bridge.binding(self.session)['cursor'];self.assistant('I found the cause.',identity='comment')
        self.state.db.execute("CREATE TRIGGER fail_cursor BEFORE UPDATE OF cursor ON terminal_attachments BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with self.assertRaises(Exception):self.bridge.poll(self.session)
        self.assertEqual(self.bridge.binding(self.session)['cursor'],before)
        self.assertEqual(self.state.db.execute('SELECT COUNT(*) FROM assistant_progress').fetchone()[0],0)
        self.state.db.execute('DROP TRIGGER fail_cursor');self.bridge.poll(self.session)
        self.assertEqual(self.state.db.execute('SELECT COUNT(*) FROM assistant_progress').fetchone()[0],1)

    def test_native_abort_and_completion_discard_unsent_text_without_replaying(self):
        for kind in ['turn_aborted','task_complete']:
            self.event('task_started',turn_id=kind)
            if not self.bridge.binding(self.session):self.attach()
            self.bridge.poll(self.session);self.assistant('I am working.')
            self.bridge.poll(self.session)
            self.event(kind,turn_id=kind,last_agent_message='Done.')
            self.bridge.poll(self.session)
            self.assertFalse(progress.deliver(self.relay()))
        self.assertEqual(self.state.db.execute('SELECT COUNT(*) FROM events').fetchone()[0],2)


if __name__=='__main__':unittest.main()
