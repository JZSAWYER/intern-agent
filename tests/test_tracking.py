import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from intern_personal.core import Invalid,git_environment
from intern_personal.live_state import LiveState,write_private
from intern_personal.feishu_live import Relay
from intern_personal.workspace_tracking import WorkspaceTracking,TrackingError
from test_live import FakeAPI,FakeHost


class LocalTracking(WorkspaceTracking):
    """Real Git/worktrees/commits, with only a local bare repository as transport."""
    def __init__(self,state,remote):
        super().__init__(state)
        self.remote,self.pushes,self.fail_push=remote,[],False
    def prepare_session(self,session,mode=None):
        # This fixture covers compatibility with the former two-branch layout.
        return super().prepare_session(session,mode or 'metadata_branch')
    def git(self,directory,*args,network=False):
        args=list(args)
        if args[0] in {'push','ls-remote','fetch'}:
            self.assert_local_transport(args)
            args[args.index('origin')]=str(self.remote)
            if args[0]=='push':
                self.pushes.append(args)
                if self.fail_push:raise TrackingError('fixture push failure')
        env=git_environment();env['GIT_ALLOW_PROTOCOL']='file'
        p=subprocess.run(['git','-c','core.hooksPath=/dev/null','-c','user.name=Fixture','-c','user.email=fixture@example.invalid','-C',str(directory),*args],
                         env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=10)
        if p.returncode:raise TrackingError('fixture Git '+args[0]+' failed')
        return p.stdout.strip()
    def assert_local_transport(self,args):
        if 'origin' not in args or '--force' in args or any(arg.endswith(':refs/heads/main') for arg in args):
            raise AssertionError('unsafe branch push')


class TrackingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='tracking-test-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.source,self.remote=self.root/'source',self.root/'remote.git'
        self.source.mkdir()
        self.raw(self.source,'init','-q','-b','main')
        (self.source/'README.md').write_text('Original project\n')
        self.raw(self.source,'add','README.md');self.raw(self.source,'commit','-qm','Original')
        self.raw(self.root,'init','--bare','-q',str(self.remote))
        self.raw(self.source,'push','-q',str(self.remote),'main')
        self.raw(self.source,'remote','add','origin','git@github.com:example-owner/example-project.git')
        self.raw(self.source,'update-ref','refs/remotes/origin/main','HEAD')
        self.original=self.raw(self.remote,'rev-parse','main')
        self.state=LiveState(self.root/'private');self.addCleanup(self.state.close)
        write_private(self.state.root/'credentials.json',{'app_id':'cli_test','app_secret':'private-test-sentinel-123'})
        write_private(self.state.root/'live.json',{'schema':'intern-agent.live.v1','github_owner':'example-owner','model':'example-model',
            'sandbox':'workspace-write','codex_command':['/usr/bin/false'],'codex_auth_home':str(self.root/'auth'),'launcher_home':str(self.root)})
        self.state.credentials()
        code=self.state.pairing_code(now=1000).read_text().strip()
        self.state.pair('cli_test','ou_owner','oc_manager',code,now=1001)
        self.tracker=LocalTracking(self.state,self.remote)
    def raw(self,path,*args):
        p=subprocess.run(['git','-c','user.name=Fixture','-c','user.email=fixture@example.invalid','-C',str(path),*args],capture_output=True,text=True,check=True)
        return p.stdout.strip()
    def attach(self):
        return self.tracker.attach('project',self.source,'https://github.com/example-owner/example-project','intern-management/test',mode='metadata_branch')
    def session(self):
        self.attach()
        session,_=self.state.reserve_session('project')
        self.tracker.prepare_session(session)
        return session

    def test_metadata_and_intern_branches_are_published_without_main_changes(self):
        session=self.session();intern=self.tracker.intern(session)
        self.assertEqual(self.raw(self.remote,'rev-parse','main'),self.original)
        self.assertEqual(self.raw(self.source,'symbolic-ref','--short','HEAD'),'main')
        self.assertEqual(self.raw(self.remote,'rev-parse',intern['code_branch']),self.original)
        status=self.raw(self.remote,'show','intern-management/test:.intern_workspace/interns/'+intern['intern_name']+'/status.md')
        self.assertIn('<!-- METADATA:STATUS=Idle,TASK=,ROLE=independent,TEAM_ID= -->',status)
        self.assertIn('| Current branch | '+intern['code_branch']+' |',status)
        self.assertTrue(self.tracker.intern(session)['published'])
        self.assertEqual(self.tracker.code_directory(session),intern['code_path'])
        self.assertTrue(all('--force' not in command for command in self.tracker.pushes))

    def test_unknown_workspace_fails_before_directory_chat_or_thread_creation(self):
        api=FakeAPI();host=FakeHost(self.state);relay=Relay(self.state,api,host,self.tracker)
        with self.assertRaises(Invalid):relay.open_session('unknown')
        self.assertEqual(api.chats,[]);self.assertEqual(self.state.sessions(),[])
        self.assertFalse((self.state.root/'workspaces/unknown').exists())
        self.state.register_workspace('project',self.source)
        with self.assertRaises(Invalid):relay.open_session('project')
        self.assertEqual(api.chats,[])

    def test_default_or_existing_management_branch_is_not_overwritten(self):
        with self.assertRaises(Invalid):self.tracker.attach('project',self.source,'https://github.com/example-owner/example-project','main',mode='metadata_branch')
        self.raw(self.source,'branch','other')
        self.raw(self.source,'push','-q',str(self.remote),'other')
        with self.assertRaises(Invalid):self.tracker.attach('project',self.source,'https://github.com/example-owner/example-project','other',mode='metadata_branch')
        self.assertEqual(self.raw(self.remote,'rev-parse','main'),self.original)

    def test_metadata_history_is_idempotent_and_credentials_are_redacted(self):
        session=self.session()
        item={'seq':7,'text':'Question private-test-sentinel-123','result':'Answer '+('ghp_'+'x'*24)}
        self.tracker.record(session,item,'complete');self.tracker.record(session,item,'complete')
        history=self.raw(self.remote,'show','intern-management/test:.intern_workspace/tasks/task_7/history_log.md')
        request=self.raw(self.remote,'show','intern-management/test:.intern_workspace/tasks/task_7/README.md')
        self.assertEqual(history.count('<!-- event:7:complete -->'),1)
        self.assertNotIn('private-test-sentinel-123',request)
        self.assertNotIn('ghp_'+'x'*24,history)
        self.assertIn('[redacted]',history)
        self.assertEqual(self.raw(self.remote,'rev-parse','main'),self.original)

    def test_wrong_branch_or_non_github_origin_prevents_dispatch(self):
        session=self.session();intern=self.tracker.intern(session)
        self.raw(intern['code_path'],'checkout','--detach')
        with self.assertRaises(Invalid):self.tracker.code_directory(session)
        self.raw(self.source,'remote','set-url','origin','https://codeup.aliyun.com/example/project')
        with self.assertRaises(Invalid):self.tracker.require_workspace('project')

    def test_code_edits_are_committed_and_uploaded_only_to_intern_branch(self):
        session=self.session();intern=self.tracker.intern(session)
        (Path(intern['code_path'])/'tracking-test.txt').write_text('Tracked test bytes\n')
        self.tracker.publish_code(session)
        self.assertEqual(self.raw(self.remote,'show',intern['code_branch']+':tracking-test.txt'),'Tracked test bytes')
        self.assertEqual(self.raw(self.remote,'rev-parse','main'),self.original)

    def test_private_files_and_credential_history_cannot_be_pushed(self):
        session=self.session();intern=self.tracker.intern(session);directory=Path(intern['code_path'])
        (directory/'credentials.json').write_text('{}')
        with self.assertRaises(Invalid):self.tracker.publish_code(session)
        (directory/'credentials.json').unlink()
        (directory/'example.txt').write_text('sk-'+'A'*30)
        self.raw(directory,'add','example.txt');self.raw(directory,'-c','core.hooksPath=/dev/null','commit','-qm','secret fixture')
        self.raw(directory,'rm','-q','example.txt');self.raw(directory,'commit','-qm','remove fixture')
        with self.assertRaises(Invalid):self.tracker.publish_code(session)
        self.assertEqual(self.raw(self.remote,'rev-parse',intern['code_branch']),self.original)

    def test_record_upload_retry_does_not_repeat_agent_work(self):
        session=self.session();self.state.set_session(session['id'],state='ready',chat_id='oc_workspace',thread_id='fixture-thread')
        self.state.db.execute("INSERT INTO events(app_id,message_id,chat_id,sender,role,workspace,session,text,state,result) VALUES('cli_test','om_task','oc_workspace','ou_owner','agent','project',?,'fixture task','complete','fixture result')",(session['id'],))
        item=dict(self.state.db.execute('SELECT * FROM events').fetchone())
        self.tracker.fail_push=True
        self.assertFalse(self.tracker.queue_record(self.state.session(session['id']),item,'complete'))
        self.tracker.fail_push=False
        self.state.db.execute('UPDATE tracking_pending SET retry_at=0')
        self.tracker.retry_pending()
        self.assertEqual(self.state.db.execute('SELECT COUNT(*) FROM tracking_pending').fetchone()[0],0)
        self.assertEqual(self.state.db.execute('SELECT state FROM events').fetchone()[0],'complete')
        self.assertIn('fixture result',self.raw(self.remote,'show','intern-management/test:.intern_workspace/conversations/'+session['id']+'/turns/turn_1.md'))

    def test_unpublished_intern_cannot_start_if_initial_push_fails(self):
        self.attach();session,_=self.state.reserve_session('project')
        self.tracker.fail_push=True
        with self.assertRaises(TrackingError):self.tracker.prepare_session(session)
        with self.assertRaises(Invalid):self.tracker.code_directory(session)
        self.tracker.fail_push=False
        self.tracker.prepare_session(session)
        self.assertTrue(self.tracker.intern(session)['published'])

    def test_management_history_cannot_smuggle_unrelated_or_private_commits(self):
        binding=self.attach();directory=Path(binding['metadata_path'])
        (directory/'outside.txt').write_text('Outside metadata\n')
        self.raw(directory,'add','outside.txt');self.raw(directory,'-c','core.hooksPath=/dev/null','commit','-qm','outside fixture')
        self.raw(directory,'rm','-q','outside.txt');self.raw(directory,'commit','-qm','remove fixture')
        with self.assertRaises(Invalid):self.tracker.publish(binding)
        self.assertEqual(self.raw(self.remote,'rev-parse','main'),self.original)

    def test_upload_failure_preserves_result_and_deliver_never_reruns_agent(self):
        self.attach();api=FakeAPI();host=FakeHost(self.state);relay=Relay(self.state,api,host,self.tracker)
        session=relay.open_session('project')
        # The worktree and initial records must be published before chat creation.
        self.assertTrue(self.tracker.intern(api.chats[0][0])['published'])
        self.state.db.execute("INSERT INTO events(app_id,message_id,chat_id,sender,role,workspace,session,text,state) VALUES('cli_test','om_task',?,'ou_owner','agent','project',?,'fixture task','queued')",(session['chat_id'],session['id']))
        with patch.object(self.tracker,'publish_code',side_effect=TrackingError('fixture upload failure')):
            self.assertEqual(relay.process_once('agent'),'sync_pending')
        saved=dict(self.state.db.execute('SELECT * FROM events').fetchone())
        self.assertEqual(saved['result'],'Result: fixture task');self.assertEqual(len(host.calls),1)
        self.state.db.execute('UPDATE sync_retries SET retry_at=0')
        relay.retry_sync()
        self.assertEqual(relay.process_once('agent'),'complete')
        self.assertEqual(self.state.db.execute('SELECT state FROM events').fetchone()[0],'complete')
        self.assertEqual(len(host.calls),1)
        from intern_personal.quota import UNAVAILABLE
        self.assertEqual(api.messages[-1][1],'Result: fixture task\n\n'+UNAVAILABLE)
        self.assertEqual(self.state.db.execute('SELECT result FROM events').fetchone()[0],'Result: fixture task')

    def test_turn_preflight_waits_for_inflight_history_publication(self):
        self.attach();session,_=self.state.reserve_session('project')
        self.tracker.prepare_session(session,mode='bot_branch')
        self.state.set_session(session['id'],state='ready',chat_id='oc_fixture',thread_id='fixture')
        session=self.state.session(session['id']);directory=self.tracker.code_directory(session)
        self.state.db.execute("INSERT INTO events(app_id,message_id,chat_id,sender,role,workspace,session,text,state) VALUES('cli_test','om_race','oc_fixture','ou_owner','agent','project',?,'Original request','queued')",(session['id'],))
        item=dict(self.state.db.execute("SELECT * FROM events WHERE message_id='om_race'").fetchone())
        self.state.ledger.ensure_turn(item)
        committed=threading.Event();release=threading.Event();sampling=threading.Event();entered=threading.Event();finished=threading.Event();errors=[]
        original_git=self.tracker.git
        def observed_git(path,*args,**kwargs):
            if args[0]=='ls-remote' and threading.current_thread().name=='preflight':sampling.set()
            return original_git(path,*args,**kwargs)
        def publisher():
            try:
                with self.tracker.code_lock(session):
                    self.tracker.git(directory,'commit','--allow-empty','-m','Fixture record publication')
                    committed.set()
                    if not release.wait(5):raise RuntimeError('Publication test did not release')
                    branch=self.tracker.intern(session)['code_branch']
                    self.tracker.git(directory,'push','origin','refs/heads/'+branch+':refs/heads/'+branch,network=True)
            except Exception as e:errors.append(e)
            finally:self.state.close()
        def preflight():
            try:entered.set();self.tracker.begin_turn(session,item)
            except Exception as e:errors.append(e)
            finally:finished.set();self.state.close()
        with patch.object(self.tracker,'git',side_effect=observed_git):
            writer=threading.Thread(target=publisher,name='publisher');reader=threading.Thread(target=preflight,name='preflight')
            writer.start()
            try:
                self.assertTrue(committed.wait(5));reader.start();self.assertTrue(entered.wait(5))
                self.assertFalse(sampling.wait(.2));self.assertFalse(finished.is_set())
            finally:
                release.set();writer.join(5)
                if reader.ident is not None:reader.join(5)
        self.assertFalse(errors);self.assertTrue(finished.is_set());self.assertTrue(sampling.is_set())
        record=dict(self.state.db.execute('SELECT * FROM turn_records WHERE seq=?',(item['seq'],)).fetchone())
        self.assertEqual(json.loads(record['baseline'])['head'],self.raw(self.remote,'rev-parse',self.tracker.intern(session)['code_branch']))
        self.assertEqual(self.state.db.execute('SELECT text FROM events WHERE seq=?',(item['seq'],)).fetchone()[0],'Original request')

    def test_turn_preflight_still_blocks_genuinely_unpublished_code(self):
        session=self.session();directory=self.tracker.code_directory(session)
        (Path(directory)/'unpublished.txt').write_text('Saved code must remain under review\n')
        self.tracker.git(directory,'add','unpublished.txt');self.tracker.git(directory,'commit','-m','Unpublished fixture work')
        item=dict(seq=701,session=session['id'],workspace='project',role='agent',text='Original request')
        with self.assertRaisesRegex(Invalid,'Publish or isolate'):
            self.tracker.begin_turn(session,item)
        record=dict(self.state.db.execute('SELECT * FROM turn_records WHERE seq=701').fetchone())
        self.assertFalse(record['baseline']);self.assertEqual((Path(directory)/'unpublished.txt').read_text(),'Saved code must remain under review\n')
        self.assertEqual(self.raw(self.remote,'rev-parse','main'),self.original)

    def test_sparse_project_coverage_is_preserved_in_both_new_worktrees(self):
        (self.source/'large-artifact.bin').write_bytes(b'fixture artifact')
        self.raw(self.source,'add','large-artifact.bin');self.raw(self.source,'commit','-qm','fixture artifact')
        self.raw(self.source,'push','-q',str(self.remote),'main')
        self.raw(self.source,'update-ref','refs/remotes/origin/main','HEAD')
        self.raw(self.source,'sparse-checkout','init','--no-cone')
        self.raw(self.source,'sparse-checkout','set','--no-cone','/README.md')
        session=self.session();intern=self.tracker.intern(session);binding=self.tracker.binding('project')
        for directory in [binding['metadata_path'],intern['code_path']]:
            self.assertTrue((Path(directory)/'README.md').exists())
            self.assertFalse((Path(directory)/'large-artifact.bin').exists())
            self.assertEqual(self.raw(directory,'status','--porcelain'),'')
            self.assertIn('large-artifact.bin',self.raw(directory,'ls-tree','--name-only','HEAD'))
        new_file=Path(intern['code_path'])/'new-source.py'
        new_file.write_text('answer = 42\n')
        self.tracker.publish_code(session)
        self.assertEqual(self.raw(self.remote,'show',intern['code_branch']+':new-source.py'),'answer = 42')
        self.assertEqual(self.raw(self.remote,'show',intern['code_branch']+':large-artifact.bin'),'fixture artifact')


if __name__=='__main__':unittest.main()
