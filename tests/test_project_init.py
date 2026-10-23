import json
import io
from contextlib import redirect_stdout
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock,patch

from intern_personal.core import Invalid,git_environment
from intern_personal.feishu_live import Relay,main
from intern_personal.live_state import LiveState,write_private
from intern_personal.project_init import ProjectInit,jobs
from intern_personal.workspace_tracking import WorkspaceTracking,TrackingError
from intern_personal.git_hooks import verified_path
from intern_personal.github_api import GitHubError
from test_live import FakeAPI,FakeHost
import test_live as live_fixture


class InitTracking(WorkspaceTracking):
    def __init__(self,state,remote):
        super().__init__(state);self.remote=remote;self.calls=[];self.fail_after_main=False

    def git(self,directory,*args,network=False):
        self.calls.append(tuple(args));actual=list(args)
        if network and actual[0] in {'push','fetch','ls-remote'}:
            actual=[str(self.remote) if x=='origin' else x for x in actual]
        env=git_environment();env['GIT_ALLOW_PROTOCOL']='file'
        result=subprocess.run(['/usr/bin/git','-c','core.hooksPath=/dev/null','-c','user.name=Fixture',
            '-c','user.email=fixture@example.invalid','-C',str(directory),*actual],env=env,capture_output=True,text=True,timeout=15)
        if result.returncode:raise TrackingError('Fixture Git '+args[0]+' failed')
        if self.fail_after_main and args==('push','origin','HEAD:refs/heads/main'):
            self.fail_after_main=False;raise TrackingError('Fixture connection lost after initial push')
        return result.stdout.strip()


class FakeGitHub:
    def __init__(self):
        self.repo=None;self.creates=[];self.fail_after_create=False;self.closed=False
    def viewer(self):return {'login':'example-owner','type':'User'}
    def repository(self,owner,name):return self.repo
    def create(self,owner,name,marker):
        self.creates.append((owner,name,marker))
        self.repo={'id':123,'name':name,'owner':{'login':owner},'private':True,'description':marker}
        if self.fail_after_create:
            self.fail_after_create=False;raise TimeoutError('credential-sentinel-must-not-leak')
        return self.repo
    def close(self):self.closed=True


class ProjectInitTests(unittest.TestCase):
    event=live_fixture.LiveTests.event
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='ia-init-test-');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.state=LiveState(self.root/'private');self.addCleanup(self.state.close)
        write_private(self.state.root/'credentials.json',{'app_id':'cli_test','app_secret':'fixture-private-sentinel-123'})
        write_private(self.state.root/'live.json',{'schema':'intern-agent.live.v1','github_owner':'example-owner',
            'model':'example-model','sandbox':'workspace-write','codex_command':['/bin/false'],'codex_auth_home':str(self.root/'auth'),'launcher_home':str(self.root)})
        self.state.credentials();self.state.db.execute("UPDATE identity SET owner='ou_owner',management_chat='oc_manager'")
        self.remote=self.root/'remote.git'
        subprocess.run(['/usr/bin/git','init','--bare','-q',str(self.remote)],check=True)
        self.tracker=InitTracking(self.state,self.remote);self.api=FakeAPI();self.host=FakeHost(self.state)
        self.host.create_thread=Mock(wraps=self.host.create_thread)
        self.relay=Relay(self.state,self.api,self.host,self.tracker);self.github=FakeGitHub()
        self.manager=ProjectInit(self.relay,self.github)

    def plan(self):return self.manager.preview('my-project')['plan_id']

    def ref(self,name):return self.tracker.git(self.remote,'rev-parse','refs/heads/'+name)

    def command(self,text,mid):
        self.assertEqual(self.relay.receive(self.event(text,mid=mid)),'accepted')
        with patch('intern_personal.project_init.GitHubAPI',return_value=self.github):
            self.relay.process_once('control')
        return dict(self.state.db.execute('SELECT * FROM events ORDER BY seq DESC LIMIT 1').fetchone())

    def test_preview_has_no_repository_files_push_or_bot_side_effects(self):
        value=self.manager.preview('my-project')
        self.assertEqual(value['visibility'],'private');self.assertFalse(value['pull_request'])
        self.assertEqual(value['phase'],'preview');self.assertEqual(self.github.creates,[])
        self.assertFalse(Path(value['source_path']).exists());self.assertEqual(self.api.chats,[])
        self.assertEqual(self.state.sessions(),[]);self.assertEqual(self.state.db.execute('SELECT COUNT(*) FROM workspaces').fetchone()[0],0)

    def test_apply_empty_base_one_bot_branch_private_chat_and_verified_hooks(self):
        value=self.manager.apply(self.plan());self.assertEqual(value['phase'],'complete')
        session=self.state.session(value['session']);intern=self.tracker.intern(session)
        self.assertEqual(intern['metadata_mode'],'bot_branch')
        self.assertEqual(self.tracker.git(self.remote,'ls-tree','-r','--name-only','main'),'')
        self.assertEqual(self.tracker.git(self.remote,'rev-list','--count','main'),'1')
        branches=self.tracker.git(self.remote,'for-each-ref','--format=%(refname:short)','refs/heads').splitlines()
        self.assertEqual(set(branches),{'main',intern['code_branch']})
        self.assertIn('intern-agent/my-project/',intern['code_branch'])
        self.assertEqual(len(self.api.chats),1);self.assertEqual(self.api.chats[0][1],'ou_owner')
        self.host.create_thread.assert_called_once();self.assertTrue(value['chat_url'])
        binding=self.state.db.execute('SELECT config_path FROM git_hook_bindings WHERE directory=?',(intern['code_path'],)).fetchone()
        verified_path(binding[0],intern['code_path'])
        self.assertIn('Records: same bot branch',self.tracker.describe(session))

    def test_maximum_valid_workspace_name_passes_real_record_validation(self):
        name='p'*100;plan=self.manager.preview(name)
        value=self.manager.apply(plan['plan_id']);self.assertEqual(value['phase'],'complete')
        session=self.state.session(value['session']);intern=self.tracker.intern(session)
        self.assertEqual(session['workspace'],name);self.assertIn('/'+name+'/',intern['code_branch'])
        self.assertLessEqual(len(intern['intern_name']),101)

    def test_repeat_apply_reuses_everything_and_initial_main_never_moves(self):
        plan=self.plan();value=self.manager.apply(plan);before=self.ref('main');calls=len(self.tracker.calls)
        again=self.manager.apply(plan)
        self.assertEqual(again['session'],value['session']);self.assertEqual(self.ref('main'),before)
        self.assertEqual(len(self.github.creates),1);self.assertEqual(len(self.api.chats),1)
        self.host.create_thread.assert_called_once()
        self.assertFalse(any(c[0] in {'push','commit'} for c in self.tracker.calls[calls:]))

    def test_existing_repository_and_workspace_are_never_adopted(self):
        self.github.repo={'id':999}
        with self.assertRaises(Invalid):self.plan()
        self.assertEqual(self.github.creates,[])
        self.github.repo=None
        directory=self.root/'existing';directory.mkdir();self.state.register_workspace('my-project',directory)
        with self.assertRaises(Invalid):self.plan()
        self.assertEqual(self.github.creates,[]);self.assertEqual(list(directory.iterdir()),[])

    def test_remote_or_local_appearance_after_preview_cancels_creation(self):
        plan=self.plan();self.github.repo={'id':999}
        value=self.manager.apply(plan);self.assertEqual(value['phase'],'preview');self.assertEqual(value['error_type'],'Invalid')
        self.assertEqual(self.github.creates,[])
        self.github.repo=None;root=Path(value['source_path']).parent;root.mkdir(parents=True,mode=0o700)
        (root/'user-file').write_text('preserve')
        value=self.manager.apply(plan);self.assertEqual(self.github.creates,[])
        self.assertEqual((root/'user-file').read_text(),'preserve')

    def test_ambiguous_create_adopts_only_exact_operation_without_second_post(self):
        plan=self.plan();self.github.fail_after_create=True
        value=self.manager.apply(plan)
        self.assertEqual(value['phase'],'creating_repository');self.assertEqual(value['error_type'],'TimeoutError')
        self.assertNotIn('credential-sentinel',json.dumps(value))
        self.assertFalse(Path(value['source_path']).exists())
        value=self.manager.apply(plan)
        self.assertEqual(value['phase'],'complete');self.assertEqual(len(self.github.creates),1)

    def test_unconfirmed_creation_is_not_repeated(self):
        plan=self.plan();self.github.fail_after_create=True;self.manager.apply(plan);self.github.repo=None
        value=self.manager.apply(plan)
        self.assertEqual(value['phase'],'creating_repository');self.assertEqual(len(self.github.creates),1)
        self.assertIn('no second creation request',value['error_message'])

    def test_wrong_privacy_marker_or_repository_identity_blocks_resume(self):
        plan=self.plan();self.github.fail_after_create=True;self.manager.apply(plan)
        for key,value in [('private',False),('description','someone else')]:
            original=self.github.repo[key];self.github.repo[key]=value
            result=self.manager.apply(plan);self.assertEqual(result['error_type'],'Invalid')
            self.assertFalse(Path(result['source_path']).exists());self.github.repo[key]=original
        self.assertEqual(len(self.github.creates),1)

    def test_push_disconnect_recovers_exact_commit_without_repush(self):
        plan=self.plan();self.tracker.fail_after_main=True
        failed=self.manager.apply(plan);self.assertEqual(failed['phase'],'initial_commit_ready')
        before=self.ref('main');self.assertEqual(self.manager.apply(plan)['phase'],'complete')
        self.assertEqual(self.ref('main'),before)
        self.assertEqual(self.tracker.calls.count(('push','origin','HEAD:refs/heads/main')),1)

    def test_foreign_remote_main_is_preserved(self):
        plan=self.plan();source=self.root/'foreign';source.mkdir()
        self.tracker.git(source,'init','-b','main');self.tracker.git(source,'commit','--allow-empty','-m','Foreign baseline')
        self.tracker.git(source,'push',str(self.remote),'HEAD:refs/heads/main');before=self.ref('main')
        value=self.manager.apply(plan);self.assertEqual(value['error_type'],'Invalid')
        self.assertEqual(self.ref('main'),before);self.assertEqual(self.api.chats,[])

    def test_incomplete_bot_creation_never_launches_a_duplicate(self):
        plan=self.plan();original=self.api.create_chat
        def ambiguous(*args):original(*args);raise TimeoutError('ambiguous chat')
        self.api.create_chat=ambiguous
        first=self.manager.apply(plan);self.assertEqual(first['phase'],'creating_conversation')
        self.api.create_chat=original
        again=self.manager.apply(plan);self.assertEqual(again['error_type'],'Invalid')
        self.assertEqual(len(self.api.chats),1);self.assertEqual(len(self.github.creates),1)
        self.host.create_thread.assert_not_called();self.assertEqual(len(self.state.sessions()),1)

    def test_post_creation_record_retry_reuses_chat_thread_and_bot_branch(self):
        plan=self.plan()
        with patch.object(self.tracker,'record',side_effect=TrackingError('fixture upload failure')):
            failed=self.manager.apply(plan)
        self.assertIsNotNone(failed['session']);before=self.ref('main')
        self.assertEqual(self.manager.apply(plan)['phase'],'complete')
        self.assertEqual(self.ref('main'),before);self.assertEqual(len(self.api.chats),1)
        self.host.create_thread.assert_called_once()

    def test_owner_or_target_change_and_links_fail_before_creation(self):
        plan=self.plan();config=self.state.config();config['github_owner']='different-owner';write_private(self.state.root/'live.json',config)
        with self.assertRaises(Invalid):self.manager.apply(plan)
        self.assertEqual(self.github.creates,[])

    def test_invalid_names_and_repository_options_never_reach_api(self):
        for workspace,repo in [('../escape',None),('my-project','$(execute)'),('my-project','repo.git'),('status',None)]:
            with self.assertRaises(Invalid):self.manager.preview(workspace,repo)
        self.assertEqual(self.github.creates,[])
        parent=self.state.runtime/'projects';parent.symlink_to(self.root)
        with self.assertRaises(Invalid):self.plan()
        self.assertEqual(self.github.creates,[])

    def test_first_init_records_preview_and_apply_original_commands_in_markdown(self):
        preview=self.command('/init my-project','om_preview')
        self.assertEqual(preview['state'],'complete');self.assertIsNone(preview['session'])
        plan=jobs(self.state)[0]['plan_id']
        applied=self.command('/init apply '+plan,'om_apply')
        self.assertEqual(applied['state'],'complete');self.assertEqual(jobs(self.state)[0]['phase'],'complete')
        session=self.state.session(jobs(self.state)[0]['session']);branch=self.tracker.intern(session)['code_branch']
        for seq,text in [(preview['seq'],'/init my-project'),(applied['seq'],'/init apply '+plan)]:
            body=self.tracker.git(self.remote,'show',branch+':.intern_workspace/conversations/'+session['id']+'/turns/turn_'+str(seq)+'.md')
            self.assertIn(text,body);self.assertIn('"phase": "complete"',body)

    def test_all_previews_keep_original_receipts_before_first_project_exists(self):
        first=self.command('/INIT my-project','om_preview_one');second=self.command('/init my-project','om_preview_two')
        receipts={r['seq']:dict(r) for r in self.state.db.execute('SELECT * FROM initialization_receipts')}
        plan=jobs(self.state)[0]['plan_id'];self.command('/init APPLY '+plan,'om_apply')
        session=self.state.session(jobs(self.state)[0]['session']);branch=self.tracker.intern(session)['code_branch']
        for item in [first,second]:
            turn=self.state.db.execute('SELECT * FROM turn_records WHERE seq=?',(item['seq'],)).fetchone()
            self.assertEqual(turn['received_at'],receipts[item['seq']]['received_at'])
            self.assertEqual(turn['completed_at'],receipts[item['seq']]['completed_at'])
            self.assertEqual(turn['source'],'feishu')
            body=self.tracker.git(self.remote,'show',branch+':.intern_workspace/conversations/'+session['id']+'/turns/turn_'+str(item['seq'])+'.md')
            self.assertIn(item['text'],body)

    def test_existing_controller_history_is_not_moved_to_new_project(self):
        directory=self.root/'controller';directory.mkdir();self.state.register_workspace('controller-workspace',directory)
        controller,_=self.state.reserve_controller('controller-workspace')
        self.state.set_session(controller['id'],state='ready',thread_id='fixture-controller-thread')
        self.relay.receive(self.event('/init apply '+self.plan(),mid='om_bound_apply'))
        item=dict(self.state.db.execute('SELECT * FROM events ORDER BY seq DESC LIMIT 1').fetchone())
        value=self.manager.apply(jobs(self.state)[0]['plan_id'],item=item)
        self.assertEqual(value['phase'],'complete');self.assertEqual(item['session'],controller['id'])
        turn=self.state.db.execute('SELECT * FROM turn_records WHERE seq=?',(item['seq'],)).fetchone()
        self.assertEqual(turn['workspace'],'controller-workspace');self.assertEqual(turn['conversation'],controller['id'])

    def test_local_apply_only_queues_reviewed_plan_and_captures_receipt(self):
        plan=self.plan()
        with redirect_stdout(io.StringIO()) as output:main(['--state',str(self.state.root),'project-init','apply',plan])
        self.assertTrue(json.loads(output.getvalue())['queued']);self.assertEqual(self.github.creates,[])
        event=self.state.db.execute('SELECT * FROM events ORDER BY seq DESC LIMIT 1').fetchone()
        self.assertEqual(event['text'],'/init apply '+plan)
        receipt=self.state.db.execute('SELECT * FROM initialization_receipts WHERE seq=?',(event['seq'],)).fetchone()
        self.assertEqual(receipt['source'],'local');self.assertIsNotNone(receipt['received_at'])

    def test_definite_post_rejection_can_retry_after_permissions_fixed(self):
        plan=self.plan()
        with patch.object(self.github,'create',side_effect=GitHubError('request',403)):
            failed=self.manager.apply(plan)
        self.assertEqual(failed['phase'],'preview');self.assertEqual(failed['error_type'],'GitHubError')
        self.assertEqual(self.manager.apply(plan)['phase'],'complete');self.assertEqual(len(self.github.creates),1)

    def test_read_only_api_failure_is_known_without_ambiguous_execution(self):
        with patch.object(self.github,'viewer',side_effect=GitHubError('request',401)):
            with self.assertRaises(Invalid):self.plan()
        self.assertEqual(jobs(self.state),[]);self.assertEqual(self.github.creates,[])

    def test_init_stays_owner_management_only(self):
        self.assertEqual(self.relay.receive(self.event('/init project',sender='ou_other')),'rejected_sender')
        with self.assertRaises(Invalid):self.relay.control({'text':'/init project','chat_id':'oc_elsewhere','sender':'ou_owner','session':None})
        self.assertEqual(self.github.creates,[])


if __name__=='__main__':unittest.main()
