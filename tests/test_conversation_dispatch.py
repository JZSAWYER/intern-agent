"""Independent terminals never share input workers or unrelated Git locks."""
import json
import threading
import unittest

import test_live as live_fixture
import test_single_branch as git_fixture
from intern_personal.core import Invalid


class ConversationDispatchTests(unittest.TestCase):
    setUp=live_fixture.LiveTests.setUp
    event=live_fixture.LiveTests.event

    def bots(self,count=2):
        result=[]
        for _ in range(count):
            s=self.relay.open_session('project',new=True)
            self.state.db.execute('''INSERT INTO tracked_interns(session,intern_name,code_path,code_branch,home_branch,published,metadata_mode)
                VALUES(?,?,?,?,?,1,'bot_branch')''',(s['id'],'fixture_'+s['id'],str(self.state.root/'checkouts'/s['id']),
                'intern-agent/'+s['id'],'intern-agent/'+s['id']))
            result.append(s)
        return result

    def queue(self,s,text,mid):
        self.assertEqual(self.relay.receive(self.event(text,s['chat_id'],mid)),'accepted')

    def test_ordinary_bots_in_same_workspace_are_independent(self):
        a,b=self.bots()
        self.queue(a,'Long owner task','om_long');self.queue(b,'Original owner text','om_target')
        self.assertEqual(self.state.claim('agent',a['id'])['session'],a['id'])
        target=self.state.claim('agent',b['id'])
        self.assertEqual(target['session'],b['id']);self.assertEqual(target['text'],'Original owner text')

    def test_another_bots_waiting_or_failed_delivery_never_blocks_input(self):
        a,b=self.bots();self.queue(a,'Earlier work','om_earlier');first=self.state.claim('agent',a['id'])
        self.queue(b,'Target owner message','om_target')
        for phase in ['running','awaiting_terminal','preparing_reply','sending','uncertain_execution','uncertain_delivery','record_blocked','sync_pending','queued','result_ready']:
            with self.subTest(phase=phase):
                self.state.transition(first['seq'],phase)
                self.state.db.execute("UPDATE events SET state='queued' WHERE message_id='om_target'")
                self.assertEqual(self.state.claim('agent',b['id'])['message_id'],'om_target')

    def test_same_terminal_keeps_fifo_and_does_not_replay_uncertainty(self):
        a=self.bots(1)[0];self.queue(a,'First','om_one');self.queue(a,'Second','om_two')
        first=self.state.claim('agent',a['id'])
        self.assertIsNone(self.state.claim('agent',a['id']))
        self.state.transition(first['seq'],'uncertain_execution')
        self.assertIsNone(self.state.claim('agent',a['id']))
        self.state.transition(first['seq'],'complete')
        self.assertEqual(self.state.claim('agent',a['id'])['message_id'],'om_two')

    def test_shared_checkout_or_branch_preserves_exclusive_execution(self):
        a,b=self.bots();self.queue(a,'First','om_one');self.queue(b,'Second','om_two')
        self.state.claim('agent',a['id'])
        original=self.state.db.execute('SELECT * FROM tracked_interns WHERE session=?',(b['id'],)).fetchone()
        for field in ['code_path','code_branch']:
            with self.subTest(shared=field):
                value=self.state.db.execute('SELECT '+field+' FROM tracked_interns WHERE session=?',(a['id'],)).fetchone()[0]
                self.state.db.execute('UPDATE tracked_interns SET '+field+'=? WHERE session=?',(value,b['id']))
                self.assertIsNone(self.state.claim('agent',b['id']))
                self.state.db.execute('UPDATE tracked_interns SET '+field+'=? WHERE session=?',(original[field],b['id']))

    def test_targeted_worker_cannot_claim_another_conversation_or_control(self):
        a,b=self.bots();self.queue(a,'First','om_one');self.queue(b,'Second','om_two')
        self.assertEqual(self.state.claim('agent',b['id'])['message_id'],'om_two')
        self.assertEqual(self.state.db.execute("SELECT state FROM events WHERE message_id='om_one'").fetchone()[0],'queued')
        with self.assertRaises(Invalid):self.state.claim('control',b['id'])

    def workers(self):
        dispatcher=threading.Thread(target=self.relay.agent_dispatch_loop,daemon=True)
        dispatcher.start()
        def cleanup():
            self.relay.stop.set();dispatcher.join(3)
            for worker in list(self.relay.agent_workers.values()):worker.join(5)
        self.addCleanup(cleanup)

    def test_four_busy_bots_cannot_starve_an_idle_debug_terminal(self):
        bots=self.bots(5);busy=bots[:4];debug=bots[-1]
        started={s['id']:threading.Event() for s in bots};release=threading.Event();timeouts=[]
        self.addCleanup(release.set)
        original=self.host.run
        def run(s,text,**kwargs):
            started[s['id']].set()
            if not release.wait(8):timeouts.append(s['id'])
            return original(s,text,**kwargs)
        self.host.run=run
        binding=self.relay.terminal.binding
        self.relay.terminal.binding=lambda s:dict(snapshot=json.dumps({})) if s and s['id']==debug['id'] else binding(s)
        def terminal(s,text,seq,**kwargs):started[s['id']].set();return 'DIRECT_DEBUG_OK'
        self.relay.terminal.run=terminal;self.relay.terminal.wait_idle=lambda s:None
        for i,s in enumerate(busy):self.queue(s,'Busy '+str(i),'om_busy_'+str(i))
        self.workers()
        try:
            for s in busy:self.assertTrue(started[s['id']].wait(5))
            self.queue(debug,'Original debug text','om_debug')
            self.assertTrue(started[debug['id']].wait(2),'Idle Debug was starved by unrelated busy workers')
            self.assertFalse(release.is_set());self.assertFalse(timeouts)
        finally:release.set()

    def test_one_worker_per_conversation_preserves_original_prompts(self):
        a,b=self.bots();first=threading.Event();second=threading.Event();other=threading.Event();release=threading.Event();seen=[]
        self.addCleanup(release.set)
        original=self.host.run
        text='  Original owner text\nkeep these spaces  '
        def run(s,prompt,**kwargs):
            seen.append((s['id'],prompt))
            if s['id']==a['id'] and prompt==text:
                first.set()
                if not release.wait(8):raise RuntimeError('Fixture release missing')
            elif s['id']==a['id']:second.set()
            else:other.set()
            return original(s,prompt,**kwargs)
        self.host.run=run;self.queue(a,text,'om_first');self.workers()
        try:
            self.assertTrue(first.wait(5));worker=self.relay.agent_workers[a['id']]
            self.queue(a,'Second prompt','om_second');self.queue(b,'Independent prompt','om_other')
            self.assertTrue(other.wait(2));self.relay.ensure_agent_workers()
            self.assertIs(worker,self.relay.agent_workers[a['id']]);self.assertFalse(second.is_set())
            release.set();self.assertTrue(second.wait(5))
            self.assertEqual([p for sid,p in seen if sid==a['id']],[text,'Second prompt'])
        finally:release.set()


class ConversationGitLockTests(unittest.TestCase):
    setUp=git_fixture.SingleBranchTests.setUp
    raw=git_fixture.SingleBranchTests.raw
    attach=git_fixture.SingleBranchTests.attach
    session=git_fixture.SingleBranchTests.session

    def test_other_bots_inflight_git_upload_does_not_block_preflight(self):
        a=self.session();b=self.session();held=threading.Event();release=threading.Event();finished=threading.Event();errors=[]
        item=dict(seq=900,session=b['id'],workspace='project',role='agent',text='Unchanged target request')
        def writer():
            try:
                with self.tracker.code_lock(a):
                    held.set()
                    if not release.wait(8):raise RuntimeError('Fixture release missing')
            except Exception as e:errors.append(type(e).__name__)
            finally:self.state.close()
        def preflight():
            try:self.tracker.begin_turn(b,item)
            except Exception as e:errors.append(type(e).__name__)
            finally:finished.set();self.state.close()
        writer_thread=threading.Thread(target=writer);reader_thread=threading.Thread(target=preflight)
        writer_thread.start()
        try:
            self.assertTrue(held.wait(3));reader_thread.start()
            self.assertTrue(finished.wait(3),'Another bot Git upload blocked this checkout')
            self.assertFalse(release.is_set());self.assertFalse(errors)
        finally:
            release.set();writer_thread.join(5)
            if reader_thread.ident is not None:reader_thread.join(5)
        record=self.state.db.execute('SELECT baseline FROM turn_records WHERE seq=900').fetchone()
        self.assertTrue(record['baseline']);self.assertEqual(self.raw(self.remote,'rev-parse','main'),self.original)
