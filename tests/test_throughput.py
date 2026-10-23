import json
from datetime import datetime,timezone
from pathlib import Path
import time
import unittest
from unittest.mock import patch

from intern_personal import throughput as metric,quota
from intern_personal.codex_bridge import CodexHost
from intern_personal.feishu_live import Relay
from intern_personal.live_state import LiveState
from intern_personal.terminal_bridge import TerminalBridge
import test_live as live
import test_terminal as terminal
import test_git_completion as git_fixture


def usage(output,reasoning=0):
    return dict(outputTokens=output,reasoningOutputTokens=reasoning,inputTokens=999999,cachedInputTokens=999999)


class ArithmeticTests(unittest.TestCase):
    def test_multiple_calls_duplicates_and_reasoning_use_only_output_delta(self):
        view=metric.empty(metric.counter(usage(1000,200)));metric.begin(view,'t',100)
        for out,reason in [(1100,240),(1100,240),(1500,400)]:metric.usage(view,usage(out,reason))
        result=metric.finish(view,'t','completed',at=200,duration_ms=10000)
        self.assertEqual((result['output_tokens'],result['reasoning_tokens']),(500,200))
        self.assertEqual(metric.render(result),'Speed (turn avg): 50.0 output tok/s (500 tokens / 10.00 s; includes tools).')

    def test_counter_reset_is_unknown_even_when_end_event_exceeds_start(self):
        view=metric.empty(metric.counter(usage(100,40)));metric.begin(view,'t',100)
        for count in [150,10,200]:metric.usage(view,usage(count))
        self.assertEqual(metric.render(metric.finish(view,'t','completed',at=110)),metric.UNAVAILABLE)

    def test_missing_baseline_updates_or_time_are_not_zero_or_estimated_tokens(self):
        for prior,update,duration in [(None,usage(100),1000),(metric.counter(usage(0)),None,1000),
                                      (metric.counter(usage(0)),usage(100),None)]:
            with self.subTest(prior=prior,update=update,duration=duration):
                view=metric.empty(prior);metric.begin(view,'t')
                if update:metric.usage(view,update)
                self.assertEqual(metric.render(metric.finish(view,'t','completed',duration_ms=duration)),metric.UNAVAILABLE)

    def test_invalid_and_extreme_numbers_are_unavailable_without_division_errors(self):
        for value in [None,True,'100',-1,float('nan'),float('inf'),10**400]:
            self.assertIsNone(metric.counter(usage(value)))
        self.assertIsNone(metric.counter(usage(10,11)))
        for duration in [None,0,-1,True,'1000',float('nan'),float('inf'),1e-300]:
            self.assertEqual(metric.render(dict(status='completed',output_tokens=5,duration_ms=duration)),metric.UNAVAILABLE)

    def test_observed_zero_and_short_turn_have_defined_meaning(self):
        for count in [0,20]:
            data=dict(status='completed',output_tokens=count,duration_ms=1)
            self.assertIn('0.001 s',metric.render(data));self.assertNotEqual(metric.render(data),metric.UNAVAILABLE)

    def test_failed_interrupted_and_foreign_turns_cannot_produce_success_speed(self):
        for status in ['failed','interrupted']:
            view=metric.empty(metric.counter(usage(0)));metric.begin(view,'t',100);metric.usage(view,usage(100))
            self.assertIsNone(metric.finish(view,'foreign','completed',at=101))
            self.assertEqual(metric.render(metric.finish(view,'t',status,at=101)),metric.UNAVAILABLE)

    def test_missing_usage_invalidates_the_next_start_until_a_known_boundary(self):
        view=metric.empty(metric.counter(usage(1000)));metric.begin(view,'missing',100)
        metric.finish(view,'missing','completed',at=110)
        self.assertIsNone(view['previous'])
        metric.begin(view,'next',120);metric.usage(view,usage(2000))
        self.assertEqual(metric.render(metric.finish(view,'next','completed',at=130)),metric.UNAVAILABLE)
        metric.begin(view,'known',140);metric.usage(view,usage(2500))
        self.assertIn('50.0 output tok/s',metric.render(metric.finish(view,'known','completed',at=150)))


class NativeTests(unittest.TestCase):
    setUp=terminal.TerminalTests.setUp
    event=terminal.TerminalTests.event
    message=terminal.TerminalTests.message
    attach=terminal.TerminalTests.attach

    def timed(self,kind,at,**fields):
        payload=dict(type=kind,**fields)
        if kind=='token_count':payload['info']=dict(total_token_usage=dict(output_tokens=fields['output'],reasoning_output_tokens=fields.get('reasoning',0)))
        with self.rollout.open('a') as stream:
            stream.write(json.dumps(dict(type='event_msg',timestamp=datetime.fromtimestamp(at,timezone.utc).isoformat(),payload=payload))+'\n')

    def test_native_cursor_bootstrap_and_restart_preserve_exact_turn_metrics_before_quota(self):
        self.timed('token_count',99,output=1000,reasoning=200)
        self.timed('task_started',100,turn_id='one');self.message('Original owner message');self.attach()
        self.timed('token_count',102,output=1100,reasoning=220);self.bridge.poll(self.session)
        self.timed('token_count',104,output=1500,reasoning=400)
        self.timed('task_complete',105,turn_id='one',last_agent_message='Saved answer',duration_ms=5000)
        TerminalBridge(self.state,self.tracker).poll(self.session)
        item=dict(self.state.db.execute('SELECT * FROM events').fetchone());text=quota.reply(self.state,item)
        self.assertIn('100.0 output tok/s (500 tokens / 5.00 s',text)
        self.assertLess(text.index('Speed (turn avg)'),text.index('Quota:'))
        self.assertEqual((item['text'],item['result']),('Original owner message','Saved answer'))
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM turn_throughput').fetchone()[0],1)

    def test_native_multiple_turns_same_poll_do_not_share_counts_or_durations(self):
        self.timed('token_count',99,output=1000);self.timed('task_started',100,turn_id='one');self.message('First');self.attach()
        self.timed('token_count',101,output=1100);self.timed('task_complete',102,turn_id='one',last_agent_message='One')
        self.timed('task_started',200,turn_id='two');self.message('Second')
        self.timed('token_count',201,output=1500);self.timed('task_complete',204,turn_id='two',last_agent_message='Two')
        self.bridge.poll(self.session)
        items=[dict(r) for r in self.state.db.execute('SELECT * FROM events ORDER BY seq')]
        self.assertIn('50.0 output tok/s (100 tokens / 2.00 s',quota.reply(self.state,items[0]))
        self.assertIn('100.0 output tok/s (400 tokens / 4.00 s',quota.reply(self.state,items[1]))

    def test_bootstrap_is_bounded_and_never_invents_a_missing_old_start(self):
        self.timed('token_count',99,output=1000);self.timed('task_started',100,turn_id='one');self.message('Owner');self.attach()
        self.bridge.poll(self.session)
        self.state.db.execute('DELETE FROM throughput_views')
        with patch.object(metric,'BOOTSTRAP_BYTES',5):
            self.timed('token_count',105,output=1100);self.timed('task_complete',106,turn_id='one',last_agent_message='Done',duration_ms=6000)
            self.bridge.poll(self.session)
        item=dict(self.state.db.execute('SELECT * FROM events').fetchone())
        self.assertIn(metric.UNAVAILABLE,quota.reply(self.state,item))

    def test_missing_native_usage_is_explicitly_unavailable(self):
        self.event('task_started',turn_id='one');self.message('Owner');self.attach()
        self.event('task_complete',turn_id='one',last_agent_message='Done',duration_ms=5000);self.bridge.poll(self.session)
        item=dict(self.state.db.execute('SELECT * FROM events').fetchone());self.assertIn(metric.UNAVAILABLE,quota.reply(self.state,item))


class ManagedTests(unittest.TestCase):
    setUp=live.LiveTests.setUp
    event=live.LiveTests.event

    def execute(self,session,with_baseline=True,fail_send=False):
        self.relay.receive(self.event('Owner unchanged',session['chat_id'],'om_metric'))
        if with_baseline:metric.cache(self.state,session,metric.counter(usage(1000,200)),100)
        notifications=iter([
            dict(method='thread/tokenUsage/updated',params=dict(threadId='foreign',turnId='turn',tokenUsage=dict(total=usage(99999)))),
            dict(method='thread/tokenUsage/updated',params=dict(threadId=session['thread_id'],turnId='old',tokenUsage=dict(total=usage(88888)))),
            dict(method='thread/tokenUsage/updated',params=dict(threadId=session['thread_id'],turnId='turn',tokenUsage=dict(total=usage(1100,240)))),
            dict(method='thread/tokenUsage/updated',params=dict(threadId=session['thread_id'],turnId='turn',tokenUsage=dict(total=usage(1500,400)))),
            dict(method='item/completed',params=dict(threadId=session['thread_id'],turnId='turn',item=dict(type='agentMessage',phase='final_answer',text='Result'))),
            dict(method='turn/completed',params=dict(threadId=session['thread_id'],turn=dict(id='turn',status='completed',durationMs=10000)))])
        class Wire:
            def request(inner,method,params,timeout=60):
                if method=='turn/start':
                    self.assertEqual(params['input'],[dict(type='text',text='Owner unchanged')]);return dict(turn=dict(id='turn'))
                return dict(rateLimits=dict(primary=dict(usedPercent=20,windowDurationMins=300))) if method=='account/rateLimits/read' else {}
            def notification(inner,timeout):return next(notifications)
            def close(inner):pass
        host=CodexHost(self.state,tracker=self.tracker);self.relay.host=host;self.api.fail=fail_send
        with patch.object(host,'ensure_started'),patch.object(host,'ensure_terminal'),patch.object(host,'connect',return_value=Wire()):
            return self.relay.process_once('agent')

    def test_real_managed_loop_matches_exact_turn_and_excludes_git_delay(self):
        s=self.relay.open_session('project');self.assertEqual(self.execute(s),'complete')
        body=self.api.messages[-1][1];self.assertIn('50.0 output tok/s (500 tokens / 10.00 s',body)
        self.assertLess(body.index('Speed (turn avg)'),body.index('Quota (account)'))
        item=dict(self.state.db.execute("SELECT * FROM events WHERE message_id='om_metric'").fetchone())
        with patch('time.time',return_value=time.time()+36000):self.assertEqual(quota.reply(self.state,item),body)

    def test_no_previous_counter_is_unknown_not_the_entire_session_usage(self):
        s=self.relay.open_session('project');self.assertEqual(self.execute(s,with_baseline=False),'complete')
        self.assertIn(metric.UNAVAILABLE,self.api.messages[-1][1]);self.assertNotIn('1500 tokens',self.api.messages[-1][1])

    def test_send_reconciliation_and_restart_keep_exact_footer_payload(self):
        s=self.relay.open_session('project');self.assertEqual(self.execute(s,fail_send=True),'uncertain_delivery')
        row=dict(self.state.db.execute('SELECT * FROM reply_outbox').fetchone());seq=row['seq'];payload=row['payload']
        root=self.state.root;self.state.close();self.state=LiveState(root);self.addCleanup(self.state.close)
        metric.cache(self.state,s,metric.counter(usage(9999)),time.time())
        self.state.db.execute("UPDATE turn_throughput SET data=?",(json.dumps(dict(turn='turn',status='completed',output_tokens=200,duration_ms=1000)),))
        self.api.fail=False;relay=Relay(self.state,self.api,live.FakeHost(self.state),live.FakeTracker(self.state))
        self.state.resolve(seq,'deliver');self.assertEqual(relay.process_once('agent'),'complete')
        self.assertEqual(self.api.messages[-1][1],payload);self.assertFalse(relay.host.calls)

    def test_thread_scope_and_late_cache_do_not_borrow_other_sessions(self):
        s=self.relay.open_session('project');metric.cache(self.state,s,metric.counter(usage(100)),100)
        metric.cache(self.state,s,metric.counter(usage(999)),99)
        self.assertEqual(metric.cached(self.state,s)['output'],100)
        self.assertIsNone(metric.cached(self.state,dict(s,thread_id='another-thread')))
        self.assertIsNone(metric.cached(self.state,self.relay.open_session('another')))

    def test_valid_transcript_can_refine_rpc_before_freezing_but_never_change_sent_bytes(self):
        s=self.relay.open_session('project');item=dict(seq=99,session=s['id'],result='Result',role='agent')
        valid=dict(turn='turn',status='completed',output_tokens=500,duration_ms=10000)
        metric.save(self.state,s,99,valid,'rpc');metric.save(self.state,s,99,dict(valid,duration_ms=5000),'transcript')
        original=quota.reply(self.state,item);self.assertIn('100.0 output tok/s',original)
        metric.save(self.state,s,99,dict(valid,duration_ms=1000),'transcript');self.assertEqual(quota.reply(self.state,item),original)

    def test_failed_or_disconnected_rpc_cannot_lend_stale_usage_to_a_future_turn(self):
        from intern_personal.codex_bridge import TurnFailed
        for failed in [True,False]:
            s=self.relay.open_session('failed' if failed else 'disconnected')
            metric.cache(self.state,s,metric.counter(usage(1000)),100)
            host=CodexHost(self.state,tracker=self.tracker)
            class Wire:
                def request(inner,method,params,timeout=60):return dict(turn=dict(id='t'))
                def notification(inner,timeout):
                    if failed:return dict(method='turn/completed',params=dict(turn=dict(id='t',status='failed')))
                    raise ConnectionError('Fixture disconnected')
            with self.assertRaises(TurnFailed if failed else ConnectionError):host._turn(Wire(),s,s['thread_id'],'Owner')
            self.assertIsNone(metric.cached(self.state,s))


class GitCompletionTests(unittest.TestCase):
    setUp=git_fixture.RealPublicationTests.setUp
    raw=git_fixture.RealPublicationTests.raw
    bot=git_fixture.RealPublicationTests.bot
    relay=git_fixture.RealPublicationTests.relay
    event=git_fixture.RealPublicationTests.event

    def test_fresh_bot_real_git_failure_keeps_original_speed_and_never_reexecutes_codex(self):
        s=self.bot();relay=self.relay();calls=[]
        metric.cache(self.state,s,metric.counter(usage(0)),100)
        notifications=iter([
            dict(method='thread/tokenUsage/updated',params=dict(threadId=s['thread_id'],turnId='new_turn',tokenUsage=dict(total=usage(500,200)))),
            dict(method='item/completed',params=dict(threadId=s['thread_id'],turnId='new_turn',item=dict(type='agentMessage',text='Completed work'))),
            dict(method='turn/completed',params=dict(threadId=s['thread_id'],turn=dict(id='new_turn',status='completed',durationMs=10000)))])
        class Wire:
            def request(inner,method,params,timeout=60):return dict(turn=dict(id='new_turn')) if method=='turn/start' else {}
            def notification(inner,timeout):return next(notifications)
        host=CodexHost(self.state,tracker=self.tracker)
        def run(session,text,**kwargs):
            calls.append(text);self.tracker.fail_push=True
            return host._turn(Wire(),session,session['thread_id'],text)
        relay.host.run=run;self.state.ingest(self.event(s,'Perform work'))
        self.assertEqual(relay.process_once('agent'),'sync_pending');self.assertFalse(relay.api.messages)
        self.tracker.fail_push=False;self.state.db.execute('UPDATE sync_retries SET retry_at=0');relay.retry_sync()
        with patch('time.time',return_value=time.time()+36000):self.assertEqual(relay.process_once('agent'),'complete')
        self.assertEqual(calls,['Perform work']);self.assertIn('50.0 output tok/s (500 tokens / 10.00 s',relay.api.messages[-1][1])
        branch=self.tracker.intern(s)['code_branch'];self.assertIn('.intern_workspace',self.raw(self.remote,'ls-tree','--name-only',branch))
        self.assertEqual(self.raw(self.remote,'rev-parse','main'),self.original)


if __name__=='__main__':unittest.main()
