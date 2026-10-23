import json
from pathlib import Path
import tempfile
import tomllib
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_live as fixture
from intern_personal.core import Invalid
from intern_personal.live_state import private_json,write_private
from intern_personal.codex_bridge import CodexHost
from intern_personal.execution_policy import settings,thread_fields,write_config,yolo
from intern_personal.session_hosts import SessionHosts


YOLO={'sandbox':'danger-full-access','approval_policy':'never','approvals_reviewer':'user'}


class ConfigPolicyTests(unittest.TestCase):
    def test_explicit_yolo_and_default_policy(self):
        self.assertEqual(settings({})['approval_policy'],'on-request')
        self.assertFalse(yolo({}))
        self.assertTrue(yolo(YOLO))
        self.assertEqual(thread_fields(YOLO),{'sandbox':'danger-full-access','approvalPolicy':'never','approvalsReviewer':'user'})
        for bad in [{'approval_policy':{}},{'sandbox':'unknown'},{'approval_policy':True},{'approvals_reviewer':'guess'}]:
            with self.assertRaises(Invalid):settings(bad)

    def test_private_toml_update_preserves_models_projects_and_other_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'config.toml'
            path.write_text('model = "example-model"\napprovals_reviewer = "auto_review"\nservice_tier = "priority"\n[projects."/data/project"]\ntrust_level = "trusted"\n')
            path.chmod(0o600)
            self.assertTrue(write_config(path,YOLO,project='/data/project'))
            data=tomllib.loads(path.read_text())
            self.assertEqual(data['model'],'example-model');self.assertEqual(data['service_tier'],'priority')
            self.assertEqual(data['projects'],{'/data/project':{'trust_level':'trusted'}})
            self.assertEqual(data['approval_policy'],'never');self.assertEqual(data['sandbox_mode'],'danger-full-access')
            self.assertEqual(path.stat().st_mode&0o777,0o600)
            self.assertFalse(write_config(path,YOLO,project='/data/project'))

    def test_linked_public_or_invalid_toml_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'config.toml';target=Path(directory)/'target'
            target.write_text('model = "example-model"\n');path.symlink_to(target)
            with self.assertRaises(Invalid):write_config(path,YOLO)
            self.assertEqual(target.read_text(),'model = "example-model"\n')
            path.unlink();path.write_text('not valid = TOML');path.chmod(0o600)
            with self.assertRaises(Invalid):write_config(path,YOLO)
            self.assertEqual(path.read_text(),'not valid = TOML')
            path.chmod(0o644)
            with self.assertRaises(Invalid):write_config(path,YOLO)


class BridgePolicyTests(unittest.TestCase):
    setUp=fixture.LiveTests.setUp

    def enable(self):
        config=self.state.config();config.update(YOLO);write_private(self.state.root/'live.json',config)

    def test_existing_host_reads_new_policy_without_reverting_it_on_resume(self):
        session=self.relay.open_session('project');host=CodexHost(self.state,tracker=self.tracker)
        self.assertEqual(host.thread_params(session)['approvalPolicy'],'on-request')
        self.enable()
        params=host.thread_params(session)
        self.assertEqual(params['approvalPolicy'],'never');self.assertEqual(params['sandbox'],'danger-full-access')
        self.assertIn('Never create a pull request unless',params['developerInstructions'])
        self.assertNotIn('Ask for approval for publication',params['developerInstructions'])

    def test_yolo_turn_preserves_owner_prompt_and_model(self):
        session=self.relay.open_session('project');self.enable();host=CodexHost(self.state,tracker=self.tracker);requests=[]
        events=iter([{'method':'item/completed','params':{'threadId':session['thread_id'],'turnId':'fixture_turn','item':{'type':'agentMessage','phase':'final_answer','text':'Verified'}}},
                     {'method':'turn/completed','params':{'threadId':session['thread_id'],'turn':{'id':'fixture_turn','status':'completed'}}}])
        class Wire:
            def request(self,method,params):requests.append((method,params));return {'turn':{'id':'fixture_turn'}}
            def notification(self,timeout):return next(events)
        self.assertEqual(host._turn(Wire(),session,session['thread_id'],'Exactly the owner message'),'Verified')
        params=requests[0][1]
        self.assertEqual(params['approvalPolicy'],'never');self.assertEqual(params['sandboxPolicy'],{'type':'dangerFullAccess'})
        self.assertEqual(params['input'],[{'type':'text','text':'Exactly the owner message'}])
        self.assertEqual(params['model'],'example-model')

    def test_legacy_command_and_file_requests_auto_accept_only_explicit_yolo(self):
        session=self.relay.open_session('project');self.enable();host=CodexHost(self.state,tracker=self.tracker);sent=[];wire=SimpleNamespace(send=sent.append)
        with patch.object(host,'notify') as notify:
            for method in ['item/commandExecution/requestApproval','item/fileChange/requestApproval']:
                host._approval(session,wire,{'id':1,'method':method,'params':{'threadId':session['thread_id']}})
            notify.assert_not_called()
        self.assertEqual(sent,[{'id':1,'result':{'decision':'accept'}}]*2)
        self.assertEqual(host.approvals,{})

    def test_yolo_does_not_answer_questions_or_approve_foreign_threads(self):
        session=self.relay.open_session('project');self.enable();host=CodexHost(self.state,tracker=self.tracker);sent=[];wire=SimpleNamespace(send=sent.append)
        host._approval(session,wire,{'id':1,'method':'item/commandExecution/requestApproval','params':{'threadId':'another-thread'}})
        self.assertIn('error',sent[-1])
        with patch.object(host,'notify') as notify:
            host._approval(session,wire,{'id':2,'method':'item/tool/requestUserInput','params':{'questions':[{'id':'q','question':'Which dataset?'}]}})
            notify.assert_called_once()
        self.assertEqual(len(host.approvals),1)

    def test_stop_intent_prevents_automatic_approval(self):
        session=self.relay.open_session('project');self.enable();host=CodexHost(self.state,tracker=self.tracker);sent=[];wire=SimpleNamespace(send=sent.append)
        with patch.object(self.state.ledger,'session_stopped',return_value=True):
            host._approval(session,wire,{'id':1,'method':'item/commandExecution/requestApproval','params':{'threadId':session['thread_id']}})
        self.assertEqual(sent[-1]['result']['decision'],'decline')

    def test_future_isolated_host_configs_and_launches_use_requested_policy(self):
        self.enable();session=self.relay.open_session('project');config=self.state.config()
        auth=Path(config['codex_auth_home']);auth.mkdir(mode=0o700);(auth/'auth.json').write_text('{}')
        hosts=SessionHosts(self.state,config)
        # Test a not-yet-started host: no transcript needs adoption.
        session=dict(session,thread_id=None)
        spec=hosts.prepare(session)
        data=tomllib.loads((spec['codex_home']/'config.toml').read_text())
        self.assertEqual(data['approval_policy'],'never');self.assertEqual(data['sandbox_mode'],'danger-full-access')
        calls=[]
        def tmux(s,*args,**kwargs):
            calls.append(args);return SimpleNamespace(returncode=1 if args[0]=='has-session' else 0)
        with patch.object(hosts,'tmux',side_effect=tmux):hosts.start_backend(spec,session,cwd=self.state.workspace('project')['cwd'])
        launch=next(args[-1] for args in calls if args[0]=='new-session')
        self.assertIn('approval_policy=',launch);self.assertIn('never',launch);self.assertIn('danger-full-access',launch)
