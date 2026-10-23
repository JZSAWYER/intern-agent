import json
import os
from pathlib import Path
import tempfile
import time
import unittest
import importlib.util
from types import SimpleNamespace
from unittest.mock import patch

from intern_personal.core import Invalid
from intern_personal.live_state import LiveState, private_json, write_private
from intern_personal.feishu_live import Relay, FeishuAPI, FeishuError, text_chunks, chat_link
from intern_personal.codex_bridge import Connection, CodexHost, BridgeError, TurnTimedOut


class FakeAPI:
    def __init__(self):
        self.chats, self.messages = [], []
        self.fail = False
        self.reactions, self.removals = [], []
        self.fail_reaction = False
        self.fail_removal = False

    def create_chat(self, session, owner):
        self.chats.append((session, owner))
        return "oc_created_" + str(len(self.chats))

    def send(self, chat, text, key):
        if self.fail:
            raise FeishuError("fixture_send", 999)
        self.messages.append((chat, text, key))
        return "om_reply_" + str(len(self.messages))

    def add_typing(self, message):
        if self.fail_reaction:
            raise FeishuError("add_typing", 99991672)
        reaction = "reaction_" + str(len(self.reactions)+1)
        self.reactions.append((message, reaction))
        return reaction

    def remove_typing(self, message, reaction):
        if self.fail_removal:
            raise FeishuError("remove_typing", 999)
        self.removals.append((message, reaction))


class FakeHost:
    def __init__(self, state):
        self.state, self.calls = state, []
        self.active = {}
        self.fail = False
        self.decisions = []

    def create_thread(self, session):
        return "thread_" + session["id"]

    def model_selection(self,session=None):
        return {"model":(session or {}).get("model") or self.state.config()["model"],
                "effort":(session or {}).get("effort") or self.state.config().get("reasoning_effort","high")}

    def ensure_started(self, session=None):pass

    def models(self, session=None):
        return [{"model":"example-model","supportedReasoningEfforts":[{"reasoningEffort":x} for x in ["low","medium","high"]],"defaultReasoningEffort":"medium"},
                {"model":"alternate-model","supportedReasoningEfforts":[{"reasoningEffort":x} for x in ["low","medium"]],"defaultReasoningEffort":"low"}]

    def set_service_tier(self, session, tier):
        return tier

    def run(self, session, text, inputs=None, resource_context=''):
        self.calls.append((session, text))
        if self.fail:
            raise BridgeError("fixture failure")
        return "Result: " + text

    def decide(self, *args):
        self.decisions.append(args)


class FakeTracker:
    """Explicit offline adapter; production uses real Git-backed workspaces."""
    def __init__(self,state):self.state,self.records,self.uploads=state,[],[]
    def require_workspace(self,name):
        if not self.state.workspace(name):
            directory=self.state.root/'fixture-workspaces'/name
            directory.mkdir(parents=True,exist_ok=True)
            self.state.register_workspace(name,directory)
        return self.state.workspace(name)
    def prepare_session(self,session):return None
    def code_directory(self,session):return self.state.workspace(session['workspace'])['cwd']
    def record(self,session,item=None,phase='Ready'):
        self.records.append((session['id'],item['seq'] if item else None,phase))
        if item:self.state.ledger.observe(item,phase)
    def queue_record(self,session,item=None,phase='Ready'):
        self.record(session,item,phase);return True
    def publish_code(self,session):self.uploads.append(session['id'])
    def retry_pending(self):pass
    def describe(self,session):return 'Workspace: '+session['workspace']+'\nTracking: offline fixture'


class LiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="live-test-")
        self.addCleanup(self.temp.cleanup)
        self.state = LiveState(self.temp.name)
        self.addCleanup(self.state.close)
        write_private(self.state.root / "credentials.json", {
            "app_id": "cli_test", "app_secret": "example-local-fixture-only"})
        write_private(self.state.root / "live.json", {
            "schema": "intern-agent.live.v1", "github_owner": "example-account", "model": "example-model",
            "sandbox": "workspace-write", "codex_command": ["/usr/bin/false"],
            "codex_auth_home": str(self.state.root / "auth"), "launcher_home": str(self.state.root)})
        self.state.credentials()
        self.api = FakeAPI()
        self.host = FakeHost(self.state)
        self.tracker=FakeTracker(self.state)
        self.relay = Relay(self.state, self.api, self.host,self.tracker)
        code = self.state.pairing_code(now=1000).read_text().strip()
        self.assertTrue(self.state.pair("cli_test", "ou_owner", "oc_manager", code, now=1001))

    def event(self, text, chat="oc_manager", mid="om_event", sender="ou_owner", app="cli_test"):
        return {"header": {"app_id": app, "event_type": "im.message.receive_v1"},
                "event": {"sender": {"sender_type": "user", "sender_id": {"open_id": sender}},
                          "message": {"chat_id": chat, "message_id": mid, "chat_type": "group",
                                      "message_type": "text", "create_time": str(int(time.time() * 1000)),
                                      "content": json.dumps({"text": text})}}}

    def test_management_new_and_workspace_roundtrip(self):
        with patch("socket.socket", side_effect=AssertionError("network forbidden")), \
                patch("subprocess.Popen", side_effect=AssertionError("external process forbidden")):
            self.assertEqual(self.relay.receive(self.event("/new project")), "accepted")
            self.assertEqual(self.relay.process_once("control"), "complete")
            session = self.state.sessions()[0]
            self.assertEqual(session["state"], "ready")
            literal = "$(never execute this) ; --option"
            event = self.event(literal, session["chat_id"], "om_task")
            self.assertEqual(self.relay.receive(event), "accepted")
            self.assertEqual(self.relay.receive(event), "duplicate")
            self.assertEqual(self.relay.process_once("agent"), "complete")
        self.assertEqual(self.host.calls[0][1], literal)
        self.assertEqual(self.api.messages[-1][0], session["chat_id"])
        self.assertEqual(len(self.host.calls), 1)

    def test_reopening_reuses_chat_and_new_session_has_distinct_thread(self):
        first = self.relay.open_session("project")
        second = self.relay.open_session("project")
        third = self.relay.open_session("project", new=True)
        self.assertEqual(first["id"], second["id"])
        self.assertNotEqual(first["chat_id"], third["chat_id"])
        self.assertNotEqual(first["thread_id"], third["thread_id"])
        self.assertEqual(len(self.api.chats), 2)

    def test_owner_words_and_whitespace_survive_ingest_dispatch_and_tracking(self):
        session=self.relay.open_session('project')
        text='  Please keep this exact wording.\n\n第二行：`literal` $(literal)  '
        self.assertEqual(self.relay.receive(self.event(text,session['chat_id'],'om_raw')),'accepted')
        self.assertEqual(self.relay.process_once('agent'),'complete')
        self.assertEqual(self.host.calls[-1][1],text)
        row=self.state.db.execute("SELECT text,state FROM events WHERE message_id='om_raw'").fetchone()
        self.assertEqual(row['text'],text);self.assertEqual(row['state'],'complete')
        self.assertEqual(self.state.db.execute('SELECT phase FROM turn_records WHERE seq=(SELECT seq FROM events WHERE message_id=?)',('om_raw',)).fetchone()[0],'complete')
        self.assertEqual(self.relay.receive(self.event('@_user_1 ',session['chat_id'],'om_only_mention')),'rejected_text')

    def test_typing_surrounds_agent_execution_and_final_delivery(self):
        session = self.relay.open_session("project")
        self.relay.receive(self.event("task",session["chat_id"],"om_task"))
        run, send = self.host.run, self.api.send
        def while_running(*args):
            self.assertEqual(self.api.reactions,[("om_task","reaction_1")])
            self.assertEqual(self.api.removals,[])
            self.relay.cleanup_typing()
            self.assertEqual(self.api.removals,[])
            return run(*args)
        def while_sending(*args):
            self.assertEqual(self.api.removals,[])
            return send(*args)
        with patch.object(self.host,"run",side_effect=while_running), patch.object(self.api,"send",side_effect=while_sending):
            self.assertEqual(self.relay.process_once("agent"),"complete")
        self.assertEqual(self.api.removals,self.api.reactions)
        self.assertEqual(self.state.status()["pending_reaction_cleanup"],0)
        self.assertEqual(self.relay.receive(self.event("task",session["chat_id"],"om_task")),"duplicate")
        self.assertEqual(self.relay.process_once("agent"),"idle")
        self.assertEqual(len(self.api.reactions),1)

    def test_typing_for_control_but_not_local_commands_or_rejected_sender(self):
        self.relay.receive(self.event("/help",mid="om_help"))
        self.assertEqual(self.relay.process_once('control'),'complete')
        self.assertEqual(self.api.removals,self.api.reactions)
        self.relay.open_session("project")
        self.state.enqueue_open("project")
        self.assertEqual(self.relay.process_once("control"),"complete")
        self.assertEqual(len(self.api.reactions),1)
        self.relay.receive(self.event("/help",mid="om_bad",sender="ou_other"))
        self.assertEqual(self.relay.process_once("control"),"idle")
        self.assertEqual(len(self.api.reactions),1)

    def test_typing_failure_does_not_block_reply_and_backs_off(self):
        self.api.fail_reaction=True
        for mid in ["om_one","om_two"]:
            self.relay.receive(self.event("/help",mid=mid))
        with patch.object(self.api,"add_typing",wraps=self.api.add_typing) as add:
            self.assertEqual(self.relay.process_once("control"),"complete")
            self.assertEqual(self.relay.process_once("control"),"complete")
        self.assertEqual(add.call_count,1)
        self.assertEqual(len(self.api.messages),2)
        self.assertEqual(self.relay.typing_error,"FeishuError")

    def test_typing_kept_until_execution_or_delivery_failure_is_resolved(self):
        for failing in ["host","delivery"]:
            with self.subTest(failing=failing):
                session=self.relay.open_session(failing)
                self.relay.receive(self.event("task",session["chat_id"],"om_"+failing))
                self.host.fail = failing=="host"
                self.api.fail = failing=="delivery"
                previous = len(self.api.removals)
                self.assertEqual(self.relay.process_once("agent"), "uncertain_execution" if failing=="host" else "uncertain_delivery")
                self.assertEqual(len(self.api.removals),previous)
                self.relay.cleanup_typing()
                self.assertEqual(len(self.api.removals),previous)
                seq=self.state.db.execute('SELECT seq FROM events WHERE message_id=?',('om_'+failing,)).fetchone()[0]
                self.state.resolve(seq,'cancel');self.relay.cleanup_typing()
                self.assertEqual(self.api.removals,self.api.reactions)
                self.host.fail=self.api.fail=False

    def test_failed_removal_retries_without_agent_replay(self):
        session=self.relay.open_session("project")
        self.relay.receive(self.event("task",session["chat_id"],"om_task"))
        self.api.fail_removal=True
        self.assertEqual(self.relay.process_once("agent"),"complete")
        self.assertEqual(self.state.status()["pending_reaction_cleanup"],1)
        self.api.fail_removal=False
        self.relay.cleanup_typing()
        self.assertEqual(self.api.removals,[])
        self.state.db.execute("UPDATE message_reactions SET retry_at=0")
        self.relay.cleanup_typing()
        self.assertEqual(self.api.removals,self.api.reactions)
        self.assertEqual(len(self.host.calls),1)
        self.assertEqual(self.state.status()["pending_reaction_cleanup"],0)

    def test_restart_keeps_uncertain_reaction_until_explicit_resolution_without_rerunning_task(self):
        session=self.relay.open_session("project")
        self.relay.receive(self.event("task",session["chat_id"],"om_task"))
        item=self.state.claim("agent")
        self.relay.begin_typing(item)
        from intern_personal.dispatch import start as begin_dispatch
        begin_dispatch(self.state,item)
        self.state.close()
        reopened=LiveState(self.temp.name)
        self.addCleanup(reopened.close)
        relay=Relay(reopened,self.api,self.host,FakeTracker(reopened))
        reopened.recover()
        relay.cleanup_typing()
        self.assertEqual(self.api.removals,[])
        self.assertEqual(relay.process_once("agent"),"idle")
        self.assertEqual(self.host.calls,[])
        reopened.resolve(item['seq'],'cancel');relay.cleanup_typing()
        self.assertEqual(self.api.removals,self.api.reactions)

    def test_native_handoff_and_restart_keep_one_reaction_until_observed_completion_and_reply(self):
        session=self.relay.open_session('project')
        self.state.db.execute("INSERT INTO terminal_attachments VALUES(?,?,0,'{}')",(session['id'],str(self.state.root/'unused')))
        self.relay.receive(self.event('task',session['chat_id'],'om_native'))
        def waiting(session,prompt,seq,resource_context=''):
            self.state.db.execute('UPDATE terminal_attachments SET snapshot=? WHERE session=?',
                (json.dumps({'active':'native-turn','event_seq':seq}),session['id']))
            raise RuntimeError('Receiver handed off to the existing terminal')
        with patch.object(self.relay.terminal,'run',side_effect=waiting):
            self.assertEqual(self.relay.process_once('agent'),'awaiting_terminal')
        self.relay.cleanup_typing();self.assertEqual(self.api.removals,[])
        item=dict(self.state.db.execute("SELECT * FROM events WHERE message_id='om_native'").fetchone())
        reopened=LiveState(self.temp.name);self.addCleanup(reopened.close)
        relay=Relay(reopened,self.api,self.host,FakeTracker(reopened));reopened.recover()
        relay.begin_typing(item);relay.cleanup_typing()
        self.assertEqual(self.api.reactions,[('om_native','reaction_1')]);self.assertEqual(self.api.removals,[])
        reopened.db.execute("UPDATE terminal_attachments SET snapshot='{}' WHERE session=?",(session['id'],))
        reopened.transition(item['seq'],'result_ready',result='Observed final response')
        send=self.api.send
        def final_reply(*args):
            relay.cleanup_typing();self.assertEqual(self.api.removals,[])
            return send(*args)
        with patch.object(relay.terminal,'wait_idle'),patch.object(self.api,'send',side_effect=final_reply):
            self.assertEqual(relay.process_once('agent'),'complete')
        self.assertEqual(self.api.reactions,[('om_native','reaction_1')]);self.assertEqual(self.api.removals,self.api.reactions)
        self.assertEqual(self.host.calls,[])

    def test_all_unfinished_states_and_known_active_turns_protect_reaction(self):
        session=self.relay.open_session('project')
        self.relay.receive(self.event('task',session['chat_id'],'om_pending'))
        item=self.state.claim('agent');self.relay.begin_typing(item)
        for phase in ['queued','running','awaiting_terminal','result_ready','sending','sync_pending','record_blocked','uncertain_execution','uncertain_delivery']:
            with self.subTest(phase=phase):
                self.state.transition(item['seq'],phase)
                self.relay.end_typing(item);self.relay.cleanup_typing()
                self.assertEqual(self.api.removals,[])
        self.state.db.execute('INSERT INTO terminal_attachments VALUES(?,?,0,?)',
            (session['id'],str(self.state.root/'unused'),json.dumps({'active':'native-turn','event_seq':item['seq']})))
        self.state.transition(item['seq'],'cancelled');self.relay.cleanup_typing()
        self.assertEqual(self.api.removals,[])
        self.state.db.execute('UPDATE terminal_attachments SET snapshot=? WHERE session=?',
            (json.dumps({'pending':{'seq':item['seq']}}),session['id']))
        self.relay.cleanup_typing();self.assertEqual(self.api.removals,[])
        self.state.db.execute("UPDATE terminal_attachments SET snapshot='{}' WHERE session=?",(session['id'],))
        self.host.active[session['id']]=('thread','managed-turn')
        self.relay.cleanup_typing();self.assertEqual(self.api.removals,[])
        self.host.active.clear();self.relay.cleanup_typing()
        self.assertEqual(self.api.removals,self.api.reactions)

    def test_delivery_retry_reuses_original_reaction_without_another_codex_turn(self):
        session=self.relay.open_session('project')
        self.relay.receive(self.event('task',session['chat_id'],'om_delivery_retry'))
        self.api.fail=True
        self.assertEqual(self.relay.process_once('agent'),'uncertain_delivery')
        self.assertEqual(self.api.removals,[])
        seq=self.state.db.execute("SELECT seq FROM events WHERE message_id='om_delivery_retry'").fetchone()[0]
        self.api.fail=False;self.state.resolve(seq,'deliver')
        self.assertEqual(self.relay.process_once('agent'),'complete')
        self.assertEqual(len(self.host.calls),1);self.assertEqual(self.api.reactions,[('om_delivery_retry','reaction_1')])
        self.assertEqual(self.api.removals,self.api.reactions)

    def test_typing_removed_if_cancelled_or_unexpected_worker_error(self):
        session=self.relay.open_session("project")
        for mid in ["om_cancelled","om_error"]:
            self.relay.receive(self.event("task",session["chat_id"],mid))
            def finish(item,role):
                self.state.transition(item["seq"],"cancelled")
                if mid=="om_error":
                    raise RuntimeError("fixture")
                return "cancelled"
            with patch.object(self.relay,"process_item",side_effect=finish):
                if mid=="om_error":
                    with self.assertRaises(RuntimeError):self.relay.process_once("agent")
                else:self.assertEqual(self.relay.process_once("agent"),"cancelled")
            self.assertEqual(self.api.removals,self.api.reactions)

    def test_workspace_links_use_openapi_identifier_for_new_and_list(self):
        from urllib.parse import urlparse,parse_qs
        session = self.relay.open_session("project")
        for command,mid in [("/new project","om_open"),("/list","om_list")]:
            self.relay.receive(self.event(command,mid=mid))
            self.assertEqual(self.relay.process_once("control"),"complete")
            link = self.api.messages[-1][1].split()[-1]
            url = urlparse(link)
            self.assertEqual(url.netloc,"applink.feishu.cn")
            self.assertEqual(url.path,"/client/chat/open")
            self.assertEqual(parse_qs(url.query),{"openChatId":[session["chat_id"]]})
        with self.assertRaises(Invalid):
            chat_link("oc_id&chatId=unexpected")

    def command(self,text,session=None,mid=None):
        self.relay.receive(self.event(text,session["chat_id"] if session else "oc_manager",mid or "om_"+str(len(self.api.messages))))
        role='urgent' if text.split()[0].lower() in {'/stop','/cancel'} else 'control'
        if role=='urgent':
            self.assertEqual(self.relay.process_once(role),'result_ready')
            role='urgent_reply'
        self.assertEqual(self.relay.process_once(role),"complete")
        return self.api.messages[-1][1]

    def test_model_command_discovery_selection_reasoning_and_restart(self):
        a=self.relay.open_session("project")
        b=self.relay.open_session("other")
        text=self.command("/model",a)
        self.assertIn("Current model: example-model",text)
        self.assertIn("alternate-model",text)
        self.assertEqual(self.host.calls,[])
        self.assertIn("Model selected: alternate-model",self.command("/model alternate-model",a))
        chosen=self.state.session(a["id"])
        self.assertEqual(self.host.model_selection(chosen),{"model":"alternate-model","effort":"low"})
        self.assertEqual(self.host.model_selection(self.state.session(b["id"])),{"model":"example-model","effort":"high"})
        self.assertIn("Reasoning effort: medium",self.command("/reasoning medium",a))
        reopened=LiveState(self.temp.name)
        try:self.assertEqual(reopened.session(a["id"])["effort"],"medium")
        finally:reopened.close()
        self.assertIn("example-model",self.command("/model default",a))
        self.assertEqual(self.state.session(a["id"])["effort"],"high")

    def test_model_rejects_invalid_selections_and_keeps_existing_settings(self):
        session=self.relay.open_session("project")
        self.command("/model alternate-model medium",session)
        before=self.state.session(session["id"])
        for command in ["/model missing","/model alternate-model high","/reasoning ultra","/model alternate-model medium extra","/model default high","/models alternate-model"]:
            with self.subTest(command=command):
                self.command(command,session)
                self.assertEqual(self.state.session(session["id"]),before)
        with patch.object(self.host,"models",side_effect=BridgeError("fixture")):
            self.assertIn("No settings changed",self.command("/model",session))
        self.assertEqual(self.state.status()["uncertain"],[])

    def test_management_model_targeting_and_workspace_isolation(self):
        a=self.relay.open_session("one")
        b=self.relay.open_session("two")
        self.assertIn("Configured defaults",self.command("/model"))
        self.assertIn("Select a session",self.command("/model alternate-model"))
        self.command("/model "+b["id"]+" alternate-model medium")
        self.assertEqual(self.state.session(b["id"])["model"],"alternate-model")
        self.assertIsNone(self.state.session(a["id"])["model"])
        self.command("/model "+b["id"]+" example-model",a)
        self.assertEqual(self.state.session(b["id"])["model"],"alternate-model")
        self.command("/reasoning "+b["id"]+" low")
        self.assertEqual(self.state.session(b["id"])["effort"],"low")
        self.assertIn("Unknown or unfinished",self.command("/model s_unknown alternate-model"))

    def test_help_tutorial_and_unknown_commands_never_go_to_agent(self):
        session=self.relay.open_session("project")
        for context in [None,session]:
            text=self.command("/help",context)
            for fragment in ["Getting started","/model MODEL","/approve CODE","/new-session NAME"]:
                self.assertIn(fragment,text)
            self.assertIn("Model tutorial",self.command("/help model",context))
            self.assertIn("Unsupported command",self.command("/permissions",context))
        self.assertEqual(self.host.calls,[])

    def test_stop_alias_uses_workspace_scope(self):
        session=self.relay.open_session("project")
        with patch.object(self.host,"interrupt",create=True) as interrupt:
            self.assertIn("Cancellation requested",self.command("/stop",session))
            interrupt.assert_called_once_with(session["id"])

    def test_bridge_uses_saved_selection_in_resume_and_turn(self):
        session=self.relay.open_session("project")
        self.state.set_model(session["id"],"alternate-model","medium")
        session=self.state.session(session["id"])
        host=CodexHost(self.state,tracker=self.tracker)
        requests=[]
        events=iter([{"method":"item/completed","params":{"threadId":session["thread_id"],"turnId":"fixture_turn","item":{"id":"final","type":"agentMessage","text":"OK"}}},
                     {"method":"turn/completed","params":{"threadId":session["thread_id"],"turn":{"id":"fixture_turn","status":"completed"}}}])
        class Wire:
            def request(self,method,params):
                requests.append((method,params))
                return {"turn":{"id":"fixture_turn"}} if method=="turn/start" else {}
            def notification(self,timeout):return next(events)
            def close(self):pass
        with patch.object(host,"ensure_started"),patch.object(host,"ensure_terminal"),patch.object(host,"connect",return_value=Wire()):
            self.assertEqual(host.run(session,"hello"),"OK")
        resume,turn=requests
        self.assertEqual(resume[1]["model"],"alternate-model")
        self.assertEqual(resume[1]["config"]["model_reasoning_effort"],"medium")
        self.assertEqual(turn[1]["model"],"alternate-model")
        self.assertEqual(turn[1]["effort"],"medium")
        self.assertEqual(turn[1]["input"],[{"type":"text","text":"hello"}])

    def test_long_turn_does_not_hit_an_unrequested_fifteen_minute_cutoff(self):
        session=self.relay.open_session('project')
        host=CodexHost(self.state,tracker=self.tracker)
        clock={'now':0}
        class Wire:
            def request(self,method,params):return {'turn':{'id':'long_turn'}}
            def notification(self,timeout):
                clock['now']+=1000
                if clock['now']==1000:
                    return {'method':'item/completed','params':{'threadId':session['thread_id'],'turnId':'long_turn',
                        'item':{'type':'agentMessage','phase':'final_answer','text':'Long task completed'}}}
                return {'method':'turn/completed','params':{'threadId':session['thread_id'],'turn':{'id':'long_turn','status':'completed'}}}
        with patch('intern_personal.codex_bridge.time.monotonic',side_effect=lambda:clock['now']),patch.object(host,'interrupt') as interrupt:
            self.assertEqual(host._turn(Wire(),session,session['thread_id'],'Owner request unchanged'),'Long task completed')
            interrupt.assert_not_called()

    def test_explicit_execution_limit_is_still_enforced(self):
        session=self.relay.open_session('project')
        host=CodexHost(self.state,tracker=self.tracker)
        host.config['turn_timeout_seconds']=1
        clock={'now':0}
        class Wire:
            def request(self,method,params):return {'turn':{'id':'limited_turn'}}
            def notification(self,timeout):
                clock['now']=2
                raise TimeoutError()
        with patch('intern_personal.codex_bridge.time.monotonic',side_effect=lambda:clock['now']),patch.object(host,'interrupt') as interrupt:
            with self.assertRaises(TurnTimedOut):host._turn(Wire(),session,session['thread_id'],'Owner request unchanged')
            self.assertTrue(interrupt.called)

    def test_workspace_status_explains_only_its_own_blocked_turn(self):
        session=self.relay.open_session('project')
        other=self.relay.open_session('other')
        for s,mid in [(session,'om_blocked'),(other,'om_other_blocked')]:
            self.relay.receive(self.event('Private owner request',s['chat_id'],mid))
            row=self.state.db.execute('SELECT * FROM events WHERE message_id=?',(mid,)).fetchone()
            self.state.transition(row['seq'],'uncertain_delivery',result='The terminal turn was interrupted.')
            self.state.db.execute('INSERT INTO terminal_turns VALUES(?,?,?,?,?)',(s['id'],mid,row['seq'],row['result'] or 'stopped','turn_aborted'))
            self.state.ledger.update(dict(row),activity='stopped')
            self.state.note_failure(row['seq'],TurnTimedOut('do not disclose this exception text'))
        local=self.command('/status',session)
        self.assertIn('configured execution time limit',local)
        self.assertIn('agent stopped',local)
        self.assertIn('not a completed task result',local)
        self.assertIn('/resolve',local)
        self.assertIn('without rerunning Codex',local)
        self.assertNotIn('Private owner request',local)
        self.assertNotIn('exception text',local)
        other_seq=self.state.db.execute("SELECT seq FROM events WHERE message_id='om_other_blocked'").fetchone()[0]
        self.assertNotIn('Turn '+str(other_seq)+':',local)
        self.assertEqual(self.host.calls,[])

    def test_failure_category_survives_restart_without_saving_exception_text(self):
        session=self.relay.open_session('project')
        self.relay.receive(self.event('Owner research request',session['chat_id'],'om_failure_category'))
        self.host.fail=True
        self.assertEqual(self.relay.process_once('agent'),'uncertain_execution')
        seq=self.state.db.execute("SELECT seq FROM events WHERE message_id='om_failure_category'").fetchone()[0]
        reopened=LiveState(self.state.root)
        try:
            row=reopened.db.execute('SELECT * FROM event_failures WHERE seq=?',(seq,)).fetchone()
            self.assertEqual(row['kind'],'execution_unconfirmed')
            self.assertEqual(set(row.keys()),{'seq','kind','at'})
        finally:reopened.close()

    def test_model_catalog_pagination_and_connection_cleanup(self):
        host=CodexHost(self.state,tracker=self.tracker)
        pages=iter([{"data":[{"model":"one"}],"nextCursor":"next"},
                    {"data":[{"model":"two"},{"model":"hidden","hidden":True}],"nextCursor":None}])
        wire=SimpleNamespace(request=lambda *a:next(pages),close=lambda:None)
        with patch.object(host,"ensure_started"),patch.object(host,"ensure_terminal"),patch.object(host,"connect",return_value=wire), \
                patch.object(wire,"request",wraps=wire.request) as request,patch.object(wire,"close") as close:
            self.assertEqual([row["model"] for row in host.models()],["one","two"])
            self.assertEqual(request.call_args_list[-1].args[1]["cursor"],"next")
            close.assert_called_once()
        wire=SimpleNamespace(request=lambda *a:{"data":[],"nextCursor":"repeat"},close=lambda:None)
        with patch.object(host,"ensure_started"),patch.object(host,"ensure_terminal"),patch.object(host,"connect",return_value=wire),patch.object(wire,"close") as close:
            with self.assertRaises(BridgeError):host.models()
            close.assert_called_once()

    def test_owner_chat_application_and_sender_checks(self):
        session = self.relay.open_session("project")
        for kwargs in [{"sender": "ou_other"}, {"app": "cli_other"}, {"chat": "oc_unknown"}]:
            self.assertNotEqual(self.relay.receive(self.event("hello", **kwargs)), "accepted")
        event = self.event("hello", session["chat_id"])
        event["event"]["sender"]["sender_type"] = "app"
        self.assertEqual(self.relay.receive(event), "ignored_sender")
        self.assertEqual(self.state.status()["events"], {})

    def test_pairing_is_explicit_expiring_one_time_and_private(self):
        with tempfile.TemporaryDirectory(prefix="pair-test-") as temp:
            state = LiveState(temp)
            try:
                write_private(state.root / "credentials.json", {
                    "app_id": "cli_test", "app_secret": "example-local-fixture-only"})
                state.credentials()
                path = state.pairing_code(now=1000)
                code = path.read_text().strip()
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertFalse(state.pair("cli_test", "ou_other", "oc_chat", "wrong", now=1001))
                self.assertFalse(state.pair("cli_test", "ou_other", "oc_chat", code, now=1901))
                self.assertFalse(state.pair("cli_other", "ou_owner", "oc_chat", code, now=1001))
                self.assertTrue(state.pair("cli_test", "ou_owner", "oc_chat", code, now=1001))
                self.assertFalse(state.pair("cli_test", "ou_other", "oc_other", code, now=1002))
                self.assertFalse(path.exists())
                self.assertEqual(state.identity()["owner"], "ou_owner")
            finally:
                state.close()

    def test_untracked_shared_workspace_sessions_remain_serialized(self):
        a = self.relay.open_session("project")
        b = self.relay.open_session("project", new=True)
        c = self.relay.open_session("other")
        for session, mid in [(a, "om_a"), (b, "om_b"), (c, "om_c")]:
            self.relay.receive(self.event("task", session["chat_id"], mid))
        first = self.state.claim("agent")
        second = self.state.claim("agent")
        self.assertEqual(first["session"], a["id"])
        self.assertEqual(second["session"], c["id"])
        self.assertIsNone(self.state.claim("agent"))

    def test_restart_uncertainty_blocks_execution_but_management_still_works(self):
        session = self.relay.open_session("project")
        self.relay.receive(self.event("one", session["chat_id"], "om_one"))
        self.relay.receive(self.event("two", session["chat_id"], "om_two"))
        first = self.state.claim("agent")
        from intern_personal.dispatch import start as begin_dispatch
        begin_dispatch(self.state,first)
        self.state.recover()
        self.assertIsNone(self.state.claim("agent"))
        self.relay.receive(self.event("/status", mid="om_status"))
        self.assertEqual(self.relay.process_once("control"), "complete")
        self.assertIn("uncertain_execution", self.api.messages[-1][1])
        self.state.resolve(first["seq"], "cancel")
        self.assertEqual(self.state.claim("agent")["message_id"], "om_two")

    def test_uncertain_control_does_not_disable_reconciliation_commands(self):
        self.relay.receive(self.event("/new project", mid="om_create"))
        self.state.claim("control")
        self.state.recover()
        self.relay.receive(self.event("/status", mid="om_status"))
        self.assertEqual(self.relay.process_once("control"), "complete")

    def test_saved_reply_can_be_delivered_without_rerunning_agent(self):
        session = self.relay.open_session("project")
        self.relay.receive(self.event("task", session["chat_id"], "om_task"))
        self.api.fail = True
        self.assertEqual(self.relay.process_once("agent"), "uncertain_delivery")
        item = self.state.db.execute("SELECT seq FROM events WHERE message_id='om_task'").fetchone()
        self.state.resolve(item["seq"], "deliver")
        self.api.fail = False
        self.assertEqual(self.relay.process_once("agent"), "complete")
        self.assertEqual(len(self.host.calls), 1)

    def test_pause_preserves_queued_task_until_resume(self):
        session = self.relay.open_session("project")
        self.relay.receive(self.event("task", session["chat_id"], "om_task"))
        self.state.set_session(session["id"], state="paused")
        self.assertIsNone(self.state.claim("agent"))
        self.state.set_session(session["id"], state="ready")
        self.assertEqual(self.relay.process_once("agent"), "complete")

    def test_other_session_cannot_issue_approval_for_a_peer(self):
        a = self.relay.open_session("one")
        b = self.relay.open_session("two")
        self.relay.receive(self.event("/pause " + b["id"], a["chat_id"]))
        self.assertEqual(self.relay.process_once("control"), "complete")
        self.assertEqual(self.state.session(b["id"])["state"], "ready")
        self.assertIn("management chat", self.api.messages[-1][1])

    def test_actual_bridge_approvals_are_session_bound_single_use_and_expire(self):
        notices = []
        host = CodexHost(self.state, lambda s,t: notices.append(t),tracker=self.tracker)
        wire = FakeWire()
        connection = Connection("/unused", connector=lambda *a,**k: wire)
        session = self.relay.open_session("project")
        message = {"id": "approval-1", "method": "item/fileChange/requestApproval", "params": {}}
        host._approval(session, connection, message)
        code = next(iter(host.approvals))
        with self.assertRaises(Invalid):
            host.decide(code, "approve", "another-session")
        self.assertEqual(len(host.approvals), 1)
        host.decide(code, "approve", session["id"])
        self.assertEqual(wire.sent[-1], {"id": "approval-1", "result": {"decision": "accept"}})
        with self.assertRaises(Invalid):
            host.decide(code, "approve", session["id"])
        host._approval(session, connection, message)
        code, entry = next(iter(host.approvals.items()))
        host.approvals[code] = (*entry[:3], time.monotonic() - 1)
        with self.assertRaises(Invalid):
            host.decide(code, "approve", session["id"])
        host._approval(session, connection, {"id": "unknown", "method": "unsupported", "params": {}})
        self.assertIn("error", wire.sent[-1])

    def test_new_thread_finishes_initialization_before_disconnect_and_resumes(self):
        # Regression for the real app-server's unpersisted empty-thread behavior.
        session = self.relay.open_session("project")
        self.state.set_session(session["id"], thread_id=None)
        session = self.state.session(session["id"])
        host = CodexHost(self.state,tracker=self.tracker)
        order = []

        class PersistingConnection:
            def request(self, method, params):
                order.append(method)
                if method == "thread/start":
                    self_outer.assertEqual(params["sandbox"], "read-only")
                    return {"thread": {"id": "persistent-thread"}}
                if method == "thread/resume":
                    self_outer.assertIn("initialization", order)
                    return {}
            def close(self):
                order.append("close")

        self_outer = self
        def turn(connection, session, thread, text, **metadata):
            order.append("initialization" if "INTERN_AGENT_READY" in text else "owner-turn")
            return "OK"
        with patch.object(host, "ensure_started"),patch.object(host,"ensure_terminal"), patch.object(host, "connect", side_effect=lambda *a,**kw: PersistingConnection()), \
                patch.object(host, "_turn", side_effect=turn):
            self.assertEqual(host.run(session, "owner prompt"), "OK")
        self.assertLess(order.index("initialization"), order.index("close"))
        self.assertLess(order.index("thread/resume"), order.index("owner-turn"))

    def test_private_files_reject_symlink_and_public_permissions(self):
        path = self.state.root / "credentials.json"
        path.chmod(0o644)
        with self.assertRaises(Invalid):
            self.state.credentials()
        path.chmod(0o600)
        alias = self.state.root / "linked.json"
        alias.symlink_to(path)
        with self.assertRaises(OSError):
            private_json(alias)

    def test_placeholder_and_changed_application_rejected(self):
        path = self.state.root / "credentials.json"
        write_private(path, {"app_id": "<app-id>", "app_secret": "<app-secret>"})
        with self.assertRaises(Invalid):
            self.state.credentials()
        write_private(path, {"app_id": "cli_different", "app_secret": "example-local-fixture-only"})
        with self.assertRaises(Invalid):
            self.state.credentials()

    def test_utf8_splitting_preserves_response_and_bounds(self):
        text = "中🙂abc" * 2000
        chunks = list(text_chunks(text))
        self.assertEqual("".join(chunks), text)
        self.assertTrue(all(len(x.encode()) <= 8000 for x in chunks))

    @unittest.skipUnless(importlib.util.find_spec("lark_oapi"), "Live SDK is optional for offline tests")
    def test_installed_sdk_typing_request_builders_without_network(self):
        api=FeishuAPI({"app_id":"cli_test","app_secret":"example-local-fixture-only"})
        adds, removes=[],[]
        def add(request):
            adds.append(request)
            return SimpleNamespace(success=lambda:True,data=SimpleNamespace(reaction_id="reaction_test"),code=0)
        def remove(request):
            removes.append(request)
            return SimpleNamespace(success=lambda:True,code=0)
        with patch.object(api.client.im.v1.message_reaction,"create",side_effect=add), \
                patch.object(api.client.im.v1.message_reaction,"delete",side_effect=remove), \
                patch("socket.socket",side_effect=AssertionError("network forbidden")):
            reaction=api.add_typing("om_test")
            api.remove_typing("om_test",reaction)
        self.assertEqual(adds[0].message_id,"om_test")
        self.assertEqual(adds[0].request_body.reaction_type.emoji_type,"Typing")
        self.assertEqual(removes[0].message_id,"om_test")
        self.assertEqual(removes[0].reaction_id,"reaction_test")

    @unittest.skipUnless(importlib.util.find_spec("lark_oapi"), "Live SDK is optional for offline tests")
    def test_reaction_sdk_errors_and_already_removed_cleanup(self):
        api=FeishuAPI({"app_id":"cli_test","app_secret":"example-local-fixture-only"})
        denied=SimpleNamespace(success=lambda:False,code=99991672,data=None)
        gone=SimpleNamespace(success=lambda:False,code=231011)
        with patch.object(api.client.im.v1.message_reaction,"create",return_value=denied), \
                patch.object(api.client.im.v1.message_reaction,"delete",return_value=gone):
            with self.assertRaises(FeishuError) as error:api.add_typing("om_test")
            self.assertEqual(error.exception.code,99991672)
            api.remove_typing("om_test","reaction_removed")
        with patch.object(api.client.im.v1.message_reaction,"delete",return_value=denied):
            with self.assertRaises(FeishuError):api.remove_typing("om_test","reaction_test")

    @unittest.skipUnless(importlib.util.find_spec("lark_oapi"), "Live SDK is optional for offline tests")
    def test_installed_sdk_request_builders_and_reply_identity_without_network(self):
        secret = "example-local-fixture-only"
        api = FeishuAPI({"app_id": "cli_test", "app_secret": secret})
        chats, sends = [], []
        def create_chat(request):
            chats.append(request)
            return SimpleNamespace(success=lambda: True, data=SimpleNamespace(chat_id="oc_test"), code=0)
        def send_message(request):
            sends.append(request)
            return SimpleNamespace(success=lambda: True, data=SimpleNamespace(message_id="om_test"), code=0)
        session = self.relay.open_session("project")
        with patch.object(api.client.im.v1.chat, "create", side_effect=create_chat), \
                patch.object(api.client.im.v1.message, "create", side_effect=send_message), \
                patch("socket.socket", side_effect=AssertionError("network forbidden")), patch("time.sleep"):
            self.assertEqual(api.create_chat(session, "ou_owner"), "oc_test")
            self.assertEqual(api.send("oc_test", "Reply " + secret, "stable-task"), "om_test")
            api.send("oc_test", "Reply " + secret, "stable-task")
        self.assertEqual(chats[0].uuid, session["creation_key"])
        self.assertEqual(chats[0].request_body.bot_id_list, ["cli_test"])
        self.assertEqual(sends[0].request_body.uuid, sends[1].request_body.uuid)
        self.assertNotIn(secret, sends[0].request_body.content)
        self.assertIn("[redacted]", sends[0].request_body.content)

    def test_malformed_old_large_messages_do_not_enqueue(self):
        for value in [None, {}, [], {"header": []}]:
            self.assertNotEqual(self.relay.receive(value), "accepted")
        for key,value in [("create_time", "1"), ("message_type", "image"), ("chat_type", "p2p"),
                          ("content", json.dumps({"text": "x" * 33000}))]:
            event = self.event("/new name")
            event["event"]["message"][key] = value
            self.assertNotEqual(self.relay.receive(event), "accepted")
        self.assertEqual(self.state.status()["events"], {})

    def test_workspace_registration_never_changes_git_and_rejects_codeup(self):
        import subprocess
        directory = self.state.root / "repository"
        directory.mkdir()
        subprocess.run(["git", "init", "-q", str(directory)], check=True)
        subprocess.run(["git", "-C", str(directory), "config", "remote.origin.url", "https://codeup.aliyun.com/example/repo.git"], check=True)
        before = (directory / ".git/config").read_bytes()
        with self.assertRaises(Invalid):
            self.state.register_workspace("repository", directory)
        self.assertEqual((directory / ".git/config").read_bytes(), before)


class FakeWire:
    def __init__(self):
        self.incoming, self.sent = [], []

    def send(self, text):
        message = json.loads(text)
        self.sent.append(message)
        if message.get("method") == "initialize":
            self.incoming.append({"id": message["id"], "result": {}})
        elif message.get("method") == "fixture":
            self.incoming.extend([{"method": "item/completed", "params": {"item": {"id": "a"}}},
                                  {"id": "server_request", "method": "item/fileChange/requestApproval", "params": {}},
                                  {"id": message["id"], "result": {"ok": True}}])

    def recv(self, timeout=None):
        if not self.incoming:
            raise TimeoutError
        return json.dumps(self.incoming.pop(0))

    def close(self):
        pass


class ProtocolTests(unittest.TestCase):
    def test_interleaved_server_requests_and_notifications_are_preserved(self):
        wire = FakeWire()
        approvals = []
        connection = Connection("/unused", lambda c,m: approvals.append(m), connector=lambda *a,**k: wire)
        self.assertEqual(connection.request("fixture", {}), {"ok": True})
        self.assertEqual(approvals[0]["id"], "server_request")
        self.assertEqual(connection.notification(1)["method"], "item/completed")
        self.assertEqual(wire.sent[1]["method"], "initialized")


if __name__ == "__main__":
    unittest.main()
