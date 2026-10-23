import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from intern_personal.core import Invalid,github_url
from intern_personal.live_state import LiveState,write_private
from intern_personal import github_auth,host_profile,portable_settings,repository_recovery
from intern_personal.memory import context,remember
from intern_personal.recovery import restore
from intern_personal.speed import save as fast_save,saved as fast_saved
from intern_personal.feishu_live import Relay
from test_live import FakeAPI,FakeHost
from test_tracking import LocalTracking
import test_tracking as tracking_fixture
import test_recovery_memory as history_fixture


class Transport(LocalTracking):
    """All Git operations are real; GitHub endpoints map only to test bare repos."""
    def __init__(self,state,remotes):
        self.remotes=remotes;super().__init__(state,next(iter(remotes.values())))

    def prepare_session(self,session,mode=None):
        return super().prepare_session(session,mode or 'bot_branch')

    def git(self,directory,*args,network=False):
        if args[0]=='clone':
            args=list(args);url=github_url(args[-2]);args[-2]=str(next(v for k,v in self.remotes.items() if k.casefold()==url.casefold()))
            result=super().git(directory,*args)
            super().git(args[-1],'remote','set-url','origin',url)
            return result
        if args[0] in {'push','fetch','ls-remote'}:
            url=github_url(super().git(directory,'config','--get','remote.origin.url'))
            old=self.remote;self.remote=self.remotes[url]
            try:return super().git(directory,*args,network=network)
            finally:self.remote=old
        return super().git(directory,*args,network=network)


class PortableRecoveryTests(unittest.TestCase):
    raw=tracking_fixture.TrackingTests.raw
    delivered=history_fixture.WorkspaceUpgradeTests.delivered

    def setUp(self):
        tracking_fixture.TrackingTests.setUp(self)
        self.url='https://github.com/example-owner/example-project.git'
        self.raw(self.remote,'symbolic-ref','HEAD','refs/heads/main')
        self.remotes={self.url:self.remote};self.tracker=Transport(self.state,self.remotes)
        self.tracker.attach('project',self.source,self.url,mode='bot_branch')

    def bot(self):
        session,_=self.state.reserve_session('project',new=True)
        self.tracker.prepare_session(session)
        self.state.set_session(session['id'],state='ready')
        return self.state.session(session['id'])

    def fresh(self):
        state=LiveState(self.root/'new-machine');self.addCleanup(state.close)
        write_private(state.root/'live.json',dict(schema='intern-agent.live.v1',github_owner='example-owner',model='example-model',
            sandbox='workspace-write',codex_command=['/usr/bin/false'],codex_auth_home=str(self.root/'new-auth'),launcher_home=str(self.root)))
        return state,Transport(state,self.remotes)

    def test_cold_clone_restores_tasks_preferences_history_and_reconnects_exact_bot(self):
        from intern_personal.task_branches import command
        first=self.bot();second=self.bot()
        self.state.set_model(first['id'],'alternate-model','medium');fast_save(self.state,first,'priority')
        self.state.db.execute("INSERT INTO progress_settings VALUES(?,0,'messages')",(first['id'],))
        self.state.db.execute("INSERT INTO trigger_settings VALUES(?,'mentions')",(first['id'],))
        command(self.tracker,first,'start','Continue the saved investigation')
        remember(self.tracker,first,'task','The verified sample must be preserved')
        saved_turn=self.delivered(first,text='Exact original question',result='Verified original answer')
        branch=self.tracker.intern(first)['code_branch'];task=self.state.ledger.selection(first['id'])
        config=dict(schema='intern-agent.live.v1',github_owner='example-owner',model='example-model',sandbox='workspace-write',
            codex_command=['/usr/bin/false'],codex_auth_home=str(self.root/'new-auth'),launcher_home=str(self.root));self.state.close()
        # Only the bare remote survives. Neither the original ledger, checkout,
        # credential file nor worktree registry is available to recovery.
        shutil.rmtree(self.state.root);shutil.rmtree(self.source)
        state=LiveState(self.root/'new-machine');self.addCleanup(state.close)
        write_private(state.root/'live.json',config);tracker=Transport(state,self.remotes)
        plan=repository_recovery.preview(tracker,'example-project')
        self.assertEqual(state.sessions(),[]);self.assertEqual(plan['conversations'],2)
        result=repository_recovery.apply(tracker,plan['plan_id'])
        self.assertTrue(result['applied']);self.assertFalse(result['routing_restored'])
        recovered=state.session(first['id']);self.assertEqual((recovered['model'],recovered['effort']),('alternate-model','medium'))
        self.assertEqual(fast_saved(state,recovered),'priority')
        self.assertEqual(state.ledger.selection(first['id'])['id'],task['id'])
        self.assertIn('verified sample',context(tracker,recovered))
        self.assertFalse(state.identity()['owner']);self.assertIsNone(recovered['chat_id']);self.assertIsNone(recovered['thread_id'])
        self.assertEqual(state.db.execute('SELECT state FROM events').fetchone()[0],'archived')
        self.assertIsNone(state.claim('agent'));self.assertIsNone(state.claim('control'))
        write_private(state.root/'credentials.json',dict(app_id='cli_fresh',app_secret='fresh-fixture-secret-only'))
        state.credentials();code=state.pairing_code().read_text().strip();self.assertTrue(state.pair('cli_fresh','ou_fresh','oc_fresh',code))
        relay=Relay(state,FakeAPI(),FakeHost(state),tracker)
        opened=relay.open_session('project',session_id=first['id'])
        self.assertEqual(opened['id'],first['id']);self.assertEqual(tracker.intern(opened)['code_branch'],branch)
        self.assertEqual(state.session(second['id'])['state'],'restored')
        self.assertEqual(relay.host.calls,[])
        self.assertEqual(state.db.execute('SELECT result FROM events WHERE seq=?',(saved_turn['seq'],)).fetchone()[0],'Verified original answer')
        reopened=relay.open_session('project',session_id=first['id']);self.assertEqual(reopened['thread_id'],opened['thread_id'])
        self.assertTrue(repository_recovery.apply(tracker,plan['plan_id'])['already_applied'])
        self.assertEqual(self.raw(self.remote,'rev-parse','main'),self.original)

    def test_repository_names_and_urls_have_the_same_credential_free_destination(self):
        state,_=self.fresh()
        for value in ['example-project','example-project.git','example-owner/example-project','example-owner/example-project.git',self.url,'git@github.com:example-owner/example-project.git']:
            self.assertEqual(repository_recovery.repository(state,value),self.url)
        for value in ['../other','https://elsewhere/project','https://user:private@github.com/a/b.git','/tmp/local']:
            with self.assertRaises(Invalid):repository_recovery.repository(state,value)
        self.assertEqual(repository_recovery.transport(state,self.url),'git@github.com:example-owner/example-project.git')
        write_private(github_auth.path(state),dict(schema='intern-agent.github-auth.v1',pat='example-pat-fixture-only'))
        self.assertEqual(repository_recovery.transport(state,self.url),self.url)

    def test_existing_host_project_is_reused_without_a_clone(self):
        first=self.bot()
        with patch.object(self.tracker,'git',wraps=self.tracker.git) as git:
            result=repository_recovery.preview(self.tracker,'EXAMPLE-PROJECT')
        self.assertEqual(result['state'],'already_registered')
        self.assertFalse(any(call.args[1]=='clone' for call in git.call_args_list))
        self.assertEqual(len(self.state.sessions()),1)

    def test_repository_case_does_not_create_a_foreign_archive_identity(self):
        first=self.bot();self.delivered(first)
        state,tracker=self.fresh();plan=repository_recovery.preview(tracker,'EXAMPLE-PROJECT')
        self.assertEqual(plan['repo_url'],self.url)
        self.assertTrue(repository_recovery.apply(tracker,plan['plan_id'])['applied'])

    def pair_fresh(self,state):
        write_private(state.root/'credentials.json',dict(app_id='cli_replacement',app_secret='example-replacement-app-secret'))
        state.credentials();code=state.pairing_code().read_text().strip()
        self.assertTrue(state.pair('cli_replacement','ou_replacement','oc_replacement',code))

    def test_controller_manual_debug_and_recovery_operator_roles_survive_cold_recovery(self):
        old=Relay(self.state,FakeAPI(),FakeHost(self.state),self.tracker)
        controller=old.attach_controller('project');debug=self.bot();operator=self.bot()
        self.state.db.execute("INSERT INTO restored_roles(session,role) VALUES(?,'native')",(debug['id'],))
        config=self.state.config();config['auto_recovery']=dict(enabled=True,incident_session=operator['id'])
        write_private(self.state.root/'live.json',config)
        self.state.db.execute('INSERT INTO recovery_session_settings VALUES(?,0)',(operator['id'],))
        self.delivered(controller)
        state,tracker=self.fresh();plan=repository_recovery.preview(tracker,self.url)
        repository_recovery.apply(tracker,plan['plan_id']);self.pair_fresh(state)
        relay=Relay(state,FakeAPI(),FakeHost(state),tracker)
        reply=relay.control(dict(seq=901,text='/reconnect '+controller['id'],sender='ou_replacement',chat_id='oc_replacement',session=None))
        self.assertIn('reconnected',reply);recovered=state.controller_session()
        self.assertEqual(recovered['id'],controller['id']);self.assertEqual(recovered['chat_id'],'oc_replacement')
        self.assertEqual(relay.api.chats,[])
        from intern_personal.peer_mail import manual
        self.assertTrue(manual(state,state.session(debug['id'])))
        relay.open_session('project',session_id=operator['id'])
        from intern_personal.auto_recovery import settings,enabled
        self.assertEqual(settings(state)['incident_session'],operator['id'])
        self.assertFalse(enabled(state,operator['id']))
        self.assertTrue(enabled(state,controller['id']))

    def test_paused_and_archived_bots_are_not_silently_activated_by_recovery(self):
        paused=self.bot();archived=self.bot()
        self.state.set_session(paused['id'],state='paused');self.state.set_session(archived['id'],state='archived')
        self.tracker.record(self.state.session(paused['id']),phase='Paused')
        state,tracker=self.fresh();plan=repository_recovery.preview(tracker,self.url)
        repository_recovery.apply(tracker,plan['plan_id'])
        self.assertEqual(state.session(archived['id'])['state'],'archived')
        self.pair_fresh(state);relay=Relay(state,FakeAPI(),FakeHost(state),tracker)
        restored=relay.open_session('project',session_id=paused['id'])
        self.assertEqual(restored['state'],'paused');self.assertEqual(relay.host.calls,[])
        self.assertEqual(state.session(archived['id'])['state'],'archived')
        self.assertEqual(state.reserve_session('project')[0]['id'],paused['id'])
        self.assertEqual(relay.open_session('project',session_id=archived['id'])['state'],'ready')
        self.assertEqual(relay.host.calls,[])

    def test_research_definition_is_visible_but_requires_explicit_new_host_schedule(self):
        from intern_personal import research
        first=self.bot();self.state.set_session(first['id'],chat_id='oc_research',thread_id='fixture-research')
        first=self.state.session(first['id'])
        watch=research.start(self.state,first,'10h','Inspect existing experiment evidence',checks=4,evidence=['research/status.json'])
        self.tracker.record(first,phase='Ready')
        state,tracker=self.fresh();plan=repository_recovery.preview(tracker,self.url)
        repository_recovery.apply(tracker,plan['plan_id'])
        self.assertEqual(state.db.execute('SELECT count(*) FROM research_watches').fetchone()[0],0)
        archived=research.listing(state,state.session(first['id']))
        self.assertEqual(archived[0]['state'],'restored_paused')
        self.pair_fresh(state);relay=Relay(state,FakeAPI(),FakeHost(state),tracker)
        recovered=relay.open_session('project',session_id=first['id'])
        restored=research.execute(state,recovered,['restore',watch['id'],'10h'])
        self.assertNotEqual(restored['id'],watch['id'])
        self.assertEqual(research.execute(state,recovered,['restore',watch['id'],'10h'])['id'],restored['id'])
        self.assertEqual(state.db.execute('SELECT count(*) FROM research_watches').fetchone()[0],1)
        self.assertEqual(relay.host.calls,[])

    def test_remote_progress_after_preview_is_not_silently_discarded(self):
        first=self.bot();self.delivered(first)
        state,tracker=self.fresh();plan=repository_recovery.preview(tracker,self.url)
        self.delivered(first,text='A newer question',result='A newer answer')
        with self.assertRaisesRegex(Invalid,'progress changed'):repository_recovery.apply(tracker,plan['plan_id'])
        self.assertEqual(state.sessions(),[]);self.assertEqual(state.db.execute('SELECT count(*) FROM events').fetchone()[0],0)

    def test_conflicting_history_is_preserved_instead_of_renumbered(self):
        first=self.bot();self.delivered(first)
        state,tracker=self.fresh();plan=repository_recovery.preview(tracker,self.url)
        state.db.execute("INSERT INTO events(seq,app_id,message_id,chat_id,sender,role,workspace,text,state) VALUES(1,'archive','prior','','','agent','different','Existing history','archived')")
        before=tuple(state.db.execute('SELECT * FROM events').fetchone())
        with self.assertRaisesRegex(Invalid,'identities conflict'):repository_recovery.apply(tracker,plan['plan_id'])
        self.assertEqual(tuple(state.db.execute('SELECT * FROM events').fetchone()),before)
        self.assertIsNone(state.workspace('project'))

    def test_multiple_repositories_from_one_installation_restore_into_shared_state(self):
        first=self.bot();self.delivered(first)
        source=self.root/'second-source';source.mkdir()
        remote=self.root/'second.git';url='https://github.com/example-owner/second-project.git'
        self.raw(source,'init','-q','-b','main');(source/'README.md').write_text('Second project\n')
        self.raw(source,'add','README.md');self.raw(source,'commit','-qm','Initial second project')
        self.raw(self.root,'init','--bare','-q',str(remote));self.raw(remote,'symbolic-ref','HEAD','refs/heads/main')
        self.raw(source,'remote','add','origin',url);self.raw(source,'push','-q',str(remote),'main');self.raw(source,'update-ref','refs/remotes/origin/main','HEAD')
        self.remotes[url]=remote;self.tracker.attach('second',source,url,mode='bot_branch')
        second,_=self.state.reserve_session('second');self.tracker.prepare_session(second)
        self.state.set_session(second['id'],state='ready');self.delivered(second,text='Second project request')
        state,tracker=self.fresh()
        for destination in [self.url,url]:
            plan=repository_recovery.preview(tracker,destination);repository_recovery.apply(tracker,plan['plan_id'])
        self.assertEqual({s['workspace'] for s in state.sessions()},{'project','second'})
        self.assertEqual(state.db.execute('SELECT count(*) FROM events').fetchone()[0],2)
        self.assertEqual(state.db.execute('SELECT count(*) FROM restore_receipts').fetchone()[0],2)
        self.assertEqual(state.db.execute("SELECT count(*) FROM events WHERE state!='archived'").fetchone()[0],0)

    def test_private_account_and_machine_fields_cannot_enter_portable_preferences(self):
        first=self.bot();data=portable_settings.snapshot(self.state,'project')
        portable_settings.validate(data,{first['id']})
        data['conversations'][0]['app_secret']='fixture-must-not-be-exported'
        with self.assertRaises(Invalid):portable_settings.validate(data,{first['id']})
        data=portable_settings.snapshot(self.state,'project');data['conversations'][0]['conversation']='foreign-conversation'
        with self.assertRaises(Invalid):portable_settings.validate(data,{first['id']})

    def test_unpublished_session_is_not_exported_as_a_recoverable_bot(self):
        first=self.bot();unpublished,_=self.state.reserve_session('project',new=True)
        self.tracker.record(first,phase='Ready')
        archive=json.loads((Path(self.tracker.intern(first)['code_path'])/'.intern_workspace/recovery.json').read_text())
        self.assertNotIn(unpublished['id'],[r['conversation'] for r in archive['preferences']['conversations']])

    def test_reconnecting_older_bot_branch_preserves_unused_sibling_history(self):
        older=self.bot();sibling=self.bot();self.tracker.record(sibling,phase='Ready')
        state,tracker=self.fresh();plan=repository_recovery.preview(tracker,self.url)
        repository_recovery.apply(tracker,plan['plan_id']);self.pair_fresh(state)
        relay=Relay(state,FakeAPI(),FakeHost(state),tracker)
        restored=relay.open_session('project',session_id=older['id'])
        branch=Path(tracker.intern(restored)['code_path'])
        data=json.loads((branch/'.intern_workspace/recovery.json').read_text())
        self.assertIn(sibling['id'],[c['id'] for c in data['conversations']])
        self.assertTrue((branch/'.intern_workspace/conversations'/sibling['id']/'preferences.md').is_file())
        self.assertEqual(state.session(sibling['id'])['state'],'restored')

    def test_settings_only_checkpoint_has_a_new_revision_but_unchanged_projection_does_not(self):
        first=self.bot();second=self.bot();self.tracker.record(second,phase='Ready')
        before=self.state.db.execute('SELECT max(revision) FROM record_events').fetchone()[0]
        self.state.set_model(first['id'],'alternate-model','medium');self.tracker.record(first,phase='Ready')
        current=self.state.db.execute('SELECT max(revision) FROM record_events').fetchone()[0]
        self.assertGreater(current,before)
        state,tracker=self.fresh()
        chosen=repository_recovery.preview(tracker,self.url)
        self.assertEqual(chosen['branch'],self.tracker.intern(first)['code_branch'])
        self.tracker.record(first,phase='Ready')
        self.assertEqual(self.state.db.execute('SELECT max(revision) FROM record_events').fetchone()[0],current)

    def test_changed_preference_requires_a_matching_markdown_checkpoint(self):
        from intern_personal.recovery import validate
        first=self.bot();root=Path(self.tracker.intern(first)['code_path'])
        data=json.loads((root/'.intern_workspace/recovery.json').read_text())
        documents=[(str(p.relative_to(root)),p.read_text()) for p in (root/'.intern_workspace').rglob('*.md')]
        validate(data,documents)
        data['preferences']['conversations'][0]['lifecycle']='paused'
        with self.assertRaisesRegex(Invalid,'Markdown checkpoint'):validate(data,documents)


class CredentialAndProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.state=LiveState(self.root/'state');self.addCleanup(self.state.close)
        self.value='example-memory-only-fixture'

    def test_pat_verification_is_private_and_helper_serves_only_github_https(self):
        with patch('intern_personal.github_api.GitHubAPI') as api:
            api.return_value.viewer.return_value={'login':'example-owner'}
            result=github_auth.configure(self.state,self.value)
        self.assertNotIn(self.value,repr(result));self.assertEqual(github_auth.path(self.state).stat().st_mode&0o777,0o600)
        self.assertNotIn(self.value,repr(github_auth.git_options(self.state)))
        for query in ['protocol=https\nhost=elsewhere.example\n\n','protocol=http\nhost=github.com\n\n']:
            output=io.StringIO();github_auth.helper([str(self.state.root),'get'],io.StringIO(query),output)
            self.assertEqual(output.getvalue(),'')
        output=io.StringIO();github_auth.helper([str(self.state.root),'get'],io.StringIO('protocol=https\nhost=github.com\n\n'),output)
        self.assertIn('password='+self.value,output.getvalue())
        github_auth.path(self.state).chmod(0o644)
        with self.assertRaises(Invalid):github_auth.token(self.state)

    def test_failed_pat_validation_leaves_existing_authentication_unchanged(self):
        before=dict(schema='intern-agent.github-auth.v1',pat=self.value);write_private(github_auth.path(self.state),before)
        with patch('intern_personal.github_api.GitHubAPI') as api:
            api.return_value.viewer.side_effect=Invalid('Authentication unavailable')
            with self.assertRaises(Invalid):github_auth.configure(self.state,'replacement-fixture-only')
        self.assertEqual(json.loads(github_auth.path(self.state).read_text()),before)

    def test_real_git_credential_protocol_uses_private_stdin_not_token_arguments(self):
        write_private(github_auth.path(self.state),dict(schema='intern-agent.github-auth.v1',pat=self.value))
        from intern_personal.core import git_environment
        args=['/usr/bin/git',*github_auth.git_options(self.state),'credential','fill']
        env=git_environment();env['PYTHONDONTWRITEBYTECODE']='1'
        result=subprocess.run(args,input='protocol=https\nhost=github.com\n\n',env=env,capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0);self.assertIn('password='+self.value,result.stdout)
        self.assertNotIn(self.value,repr(args));self.assertNotIn(self.value,result.stderr)

    def test_host_profile_reconnects_another_editor_without_copying_state_or_tokens(self):
        config=dict(schema='intern-agent.live.v1',github_owner='example-owner',model='example-model',sandbox='workspace-write',codex_command=['/usr/bin/false'])
        write_private(self.state.root/'live.json',config)
        with patch.dict(os.environ,{'XDG_STATE_HOME':str(self.root/'host-state')}):
            self.assertFalse(host_profile.discover()['found'])
            host_profile.register(self.state);profile=host_profile.discover()
            self.assertEqual(profile['state_directory'],str(self.state.root))
            self.assertEqual(host_profile.location().stat().st_mode&0o777,0o600)
            self.assertNotIn('pat',profile);self.assertNotIn('credentials',profile)
            other=LiveState(self.root/'other');self.addCleanup(other.close);write_private(other.root/'live.json',config)
            with self.assertRaisesRegex(Invalid,'Another host relay'):host_profile.register(other)

    def test_first_host_bootstrap_detects_account_and_preserves_separate_logins(self):
        from intern_personal.onboarding import bootstrap
        with patch('intern_personal.github_api.GitHubAPI') as api:
            api.return_value.viewer.return_value={'login':'example-owner'}
            result=bootstrap(self.state,self.value,executable='/usr/bin/false')
        self.assertTrue(result['initialized']);self.assertNotIn(self.value,repr(result))
        self.assertEqual(self.state.config()['github_owner'],'example-owner')
        self.assertEqual(self.state.config()['reasoning_effort'],'xhigh')
        self.assertFalse((self.state.root/'credentials.json').exists())
        with self.assertRaisesRegex(Invalid,'configured'):bootstrap(self.state,self.value,executable='/usr/bin/false')

    def test_bundled_skills_do_not_replace_modified_user_instructions(self):
        from intern_personal.skill_bundle import install
        source=self.root/'release-skills';skill=source/'recover-intern-agent/SKILL.md';skill.parent.mkdir(parents=True)
        skill.write_text('# Public recovery instructions\n')
        destination=self.root/'codex-skills';result=install(destination,source)
        self.assertEqual(result['installed'],['recover-intern-agent'])
        self.assertEqual(install(destination,source),result)
        installed=destination/'recover-intern-agent/SKILL.md';installed.write_text('# Owner instructions\n')
        with self.assertRaisesRegex(Invalid,'differs'):install(destination,source)
        self.assertEqual(installed.read_text(),'# Owner instructions\n')


if __name__=='__main__':unittest.main()
