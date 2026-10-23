"""Real local Git proves one published branch per new bot and legacy coexistence."""
import json
from pathlib import Path
import time
import unittest

import test_tracking as fixture
from intern_personal.core import Invalid
from intern_personal.workspace_tracking import WorkspaceTracking
from intern_personal.feishu_live import Relay
from intern_personal.live_state import LiveState,write_private
from intern_personal import recovery
from intern_personal.task_branches import command
from intern_personal.git_hooks import run as guard_run
from intern_personal.live_state import private_json
from test_live import FakeAPI,FakeHost


class SingleTracking(fixture.LocalTracking):
    def prepare_session(self,session,mode=None):return WorkspaceTracking.prepare_session(self,session,mode)


class SingleBranchTests(unittest.TestCase):
    raw=fixture.TrackingTests.raw

    def setUp(self):
        fixture.TrackingTests.setUp(self)
        self.tracker=SingleTracking(self.state,self.remote)
        self.api=FakeAPI()

    def attach(self):
        return self.tracker.attach('project',self.source,'https://github.com/example-owner/example-project')

    def session(self):
        if not self.tracker.binding('project'):self.attach()
        return Relay(self.state,self.api,FakeHost(self.state),self.tracker).open_session('project',new=True)

    def deliver(self,s,text='Request',result='Verified result'):
        seq=self.state.db.execute('''INSERT INTO events(app_id,message_id,chat_id,sender,role,workspace,session,text,state)
            VALUES('cli_test',? ,?,'ou_owner','agent','project',?,?,'running')''',('om_'+str(time.time_ns()),s['chat_id'],s['id'],text)).lastrowid
        item=dict(self.state.db.execute('SELECT * FROM events WHERE seq=?',(seq,)).fetchone())
        self.state.ledger.ensure_turn(item,time.time(),'local');self.state.ledger.bind_turn(item)
        self.tracker.begin_turn(s,item)
        self.tracker.record(s,item,'Running')
        self.state.ledger.evidence(seq,'Codex observed turn/completed status completed')
        (Path(self.tracker.code_directory(s))/'result.txt').write_text('Tested code\n')
        self.tracker.publish_code(s,item,push=False)
        self.state.transition(seq,'result_ready',result=result);item['result']=result
        self.state.ledger.update(item,activity='completed')
        self.tracker.record(s,item,'Reply ready')
        self.state.transition(seq,'complete',result=result);self.tracker.record(s,item,'complete')
        return item

    def branches(self):return set(self.raw(self.remote,'for-each-ref','--format=%(refname:short)','refs/heads').splitlines())

    def test_new_attach_creates_no_branch_and_each_bot_publishes_exactly_one(self):
        self.attach();self.assertEqual(self.branches(),{'main'});self.assertEqual(self.tracker.pushes,[])
        a=self.session();b=self.session();aa=self.tracker.intern(a);bb=self.tracker.intern(b)
        self.assertEqual(self.branches(),{'main',aa['code_branch'],bb['code_branch']})
        for s,intern in [(a,aa),(b,bb)]:
            self.assertEqual(intern['metadata_mode'],'bot_branch');self.assertEqual(intern['home_branch'],intern['code_branch'])
            self.assertIn('Original project',self.raw(self.remote,'show',intern['code_branch']+':README.md'))
            self.assertIn('same as current branch',self.raw(self.remote,'show',intern['code_branch']+':.intern_workspace/interns/'+intern['intern_name']+'/status.md'))
        self.assertTrue(all(len([arg for arg in p if ':refs/heads/' in arg])==1 for p in self.tracker.pushes))
        self.assertEqual(self.raw(self.remote,'rev-parse','main'),self.original)

    def test_code_and_formatted_history_upload_together_and_task_never_switches_branch(self):
        s=self.session();branch=self.tracker.intern(s)['code_branch'];command(self.tracker,s,'start','Task one')
        task=self.state.ledger.selection(s['id']);self.assertEqual(task['code_branch'],branch)
        item=self.deliver(s)
        self.assertIn('Tested code',self.raw(self.remote,'show',branch+':result.txt'))
        record=self.raw(self.remote,'show',branch+':.intern_workspace/conversations/'+s['id']+'/turns/turn_'+str(item['seq'])+'.md')
        self.assertIn('Verified result',record);self.assertIn('intern-agent.record.v2',record)
        command(self.tracker,s,'finish','Accepted')
        command(self.tracker,s,'start','Task two');second=self.state.ledger.selection(s['id'])
        command(self.tracker,s,'pause');command(self.tracker,s,'use',second['id'])
        self.assertEqual(self.tracker.intern(s)['code_branch'],branch)
        self.assertEqual(self.branches(),{'main',branch})
        self.assertTrue((Path(self.tracker.code_directory(s))/'result.txt').exists())

    def test_new_bot_in_legacy_workspace_does_not_publish_legacy_metadata(self):
        legacy=fixture.LocalTracking(self.state,self.remote)
        legacy.attach('project',self.source,'https://github.com/example-owner/example-project','intern-management/test',mode='metadata_branch')
        a,_=self.state.reserve_session('project');legacy.prepare_session(a)
        before=self.raw(self.remote,'rev-parse','intern-management/test');a_branch=legacy.intern(a)['code_branch']
        s=self.session();self.deliver(s)
        self.assertEqual(self.raw(self.remote,'rev-parse','intern-management/test'),before)
        self.assertEqual(self.raw(self.remote,'rev-parse',a_branch),self.original)
        self.assertEqual(legacy.intern(a)['metadata_mode'],'metadata_branch')
        self.assertTrue(all(len([arg for arg in p if ':refs/heads/' in arg])==1 for p in self.tracker.pushes))

    def test_guard_validates_code_and_records_and_rejects_default_branch_or_deleted_history(self):
        s=self.session();self.deliver(s);intern=self.tracker.intern(s);directory=Path(intern['code_path'])
        cfg=self.state.db.execute('SELECT config_path FROM git_hook_bindings WHERE directory=?',(str(directory),)).fetchone()[0]
        self.assertTrue(private_json(cfg)['combined_records']);guard_run(cfg,'pre-commit')
        head=self.raw(directory,'rev-parse','HEAD');branch='refs/heads/'+intern['code_branch']
        guard_run(cfg,'pre-push',[branch+' '+head+' '+branch+' '+'0'*40])
        with self.assertRaises(Invalid):guard_run(cfg,'pre-push',[branch+' '+head+' refs/heads/main '+'0'*40])
        turn=next((directory/'.intern_workspace/conversations').rglob('turn_*.md'))
        turn.write_text(turn.read_text().replace('Verified result','Forged result'))
        archive=directory/'.intern_workspace/recovery.json';data=json.loads(archive.read_text());data['turns'][0]['response']='Forged result';archive.write_text(json.dumps(data))
        self.raw(directory,'add','.intern_workspace')
        with self.assertRaises(Invalid):guard_run(cfg,'pre-commit')
        self.raw(directory,'reset','--hard','HEAD');self.raw(directory,'rm',str(turn.relative_to(directory)))
        with self.assertRaises(Invalid):guard_run(cfg,'pre-commit')

    def test_upload_retry_preserves_saved_result_and_other_bots_branch(self):
        a=self.session();b=self.session();b_branch=self.tracker.intern(b)['code_branch'];before=self.raw(self.remote,'rev-parse',b_branch)
        self.tracker.fail_push=True
        self.assertFalse(self.tracker.queue_record(a))
        self.assertEqual(self.state.db.execute('SELECT COUNT(*) FROM bot_tracking_pending').fetchone()[0],1)
        self.tracker.fail_push=False;self.state.db.execute('UPDATE bot_tracking_pending SET retry_at=0');self.tracker.retry_pending()
        self.assertEqual(self.state.db.execute('SELECT COUNT(*) FROM bot_tracking_pending').fetchone()[0],0)
        self.assertEqual(self.raw(self.remote,'rev-parse',b_branch),before)
        self.assertEqual(self.state.db.execute('SELECT COUNT(*) FROM events').fetchone()[0],0)

    def test_late_code_changes_require_another_verified_task_turn(self):
        s=self.session();command(self.tracker,s,'start','Verify changes');self.deliver(s)
        (Path(self.tracker.code_directory(s))/'result.txt').write_text('Changed after verified delivery\n')
        self.tracker.publish_code(s)
        with self.assertRaises(Invalid):command(self.tracker,s,'finish')

    def test_preexisting_owner_draft_survives_code_and_history_publication(self):
        s=self.session();directory=Path(self.tracker.code_directory(s));draft=directory/'owner-draft.txt'
        draft.write_text('Preserve this owner draft\n');self.deliver(s)
        self.assertEqual(draft.read_text(),'Preserve this owner draft\n')
        branch=self.tracker.intern(s)['code_branch']
        self.assertNotIn('owner-draft.txt',self.raw(self.remote,'ls-tree','--name-only',branch))

    def test_team_assignment_uses_workers_existing_branch_without_creating_task_branch(self):
        from intern_personal.teams import TeamCommands,snapshot
        lead=self.session();worker=self.session();relay=Relay(self.state,self.api,FakeHost(self.state),self.tracker)
        commands=TeamCommands(relay);commands.command(['create','Test team','project',lead['id'],'1'])
        team=snapshot(self.state)['teams'][0];commands.command(['add',team['id'],worker['id']])
        command(self.tracker,lead,'start','Unworked task')
        command(self.tracker,lead,'criteria','Worker delivers the tested change on its own branch')
        command(self.tracker,lead,'brief','verification Run the relevant tests and inspect the delivered branch')
        task=self.state.ledger.selection(lead['id']);command(self.tracker,lead,'pause')
        commands.command(['assign',team['id'],worker['id'],task['id']])
        self.assertEqual(self.state.ledger.selection(worker['id'])['code_branch'],self.tracker.intern(worker)['code_branch'])
        self.assertEqual(self.branches(),{'main',self.tracker.intern(lead)['code_branch'],self.tracker.intern(worker)['code_branch']})

    def test_worked_task_cannot_move_to_another_bot_or_steal_its_selection(self):
        lead=self.session();worker=self.session();command(self.tracker,lead,'start','Existing work')
        task=self.state.ledger.selection(lead['id']);self.deliver(lead);command(self.tracker,lead,'pause')
        with self.assertRaises(Invalid):command(self.tracker,worker,'use',task['id'])
        self.assertIsNone(self.state.ledger.selection(worker['id']))
        self.assertEqual(self.state.db.execute('SELECT session FROM named_tasks WHERE id=?',(task['id'],)).fetchone()[0],lead['id'])

    def test_single_branch_archive_restores_without_extra_branch_or_prompt_replay(self):
        s=self.session();self.deliver(s);branch=self.tracker.intern(s)['code_branch']
        clone=self.root/'clone';self.raw(self.root,'clone','-q','--branch','main',str(self.remote),str(clone))
        self.raw(clone,'remote','set-url','origin','git@github.com:example-owner/example-project.git')
        state=LiveState(self.root/'fresh');self.addCleanup(state.close);tracker=SingleTracking(state,self.remote)
        preview=recovery.restore(tracker,clone,'https://github.com/example-owner/example-project',branch)
        self.assertFalse(preview['applied']);self.assertEqual(state.sessions(),[])
        recovery.restore(tracker,clone,'https://github.com/example-owner/example-project',branch,True)
        self.assertEqual(tracker.binding('project')['metadata_mode'],'bot_branch')
        write_private(state.root/'credentials.json',{'app_id':'cli_test','app_secret':'private-test-sentinel-123'})
        write_private(state.root/'live.json',self.state.config());state.credentials()
        restored=state.session(s['id']);tracker.prepare_session(restored)
        self.assertEqual(tracker.intern(restored)['code_branch'],branch)
        self.assertEqual(self.branches(),{'main',branch})
        self.assertEqual(state.db.execute("SELECT COUNT(*) FROM events WHERE state<>'archived'").fetchone()[0],0)

    def test_mixed_archive_recovery_preserves_legacy_branch_and_new_bot_layout(self):
        legacy=fixture.LocalTracking(self.state,self.remote)
        legacy.attach('project',self.source,'https://github.com/example-owner/example-project','intern-management/test',mode='metadata_branch')
        old,_=self.state.reserve_session('project');legacy.prepare_session(old)
        s=self.session();self.deliver(s);branch=self.tracker.intern(s)['code_branch']
        previous=self.raw(self.remote,'rev-parse','intern-management/test')
        clone=self.root/'clone';self.raw(self.root,'clone','-q','--branch','main',str(self.remote),str(clone))
        self.raw(clone,'remote','set-url','origin','git@github.com:example-owner/example-project.git')
        state=LiveState(self.root/'fresh');self.addCleanup(state.close);tracker=SingleTracking(state,self.remote)
        recovery.restore(tracker,clone,'https://github.com/example-owner/example-project',branch,True)
        self.assertEqual(tracker.binding('project')['metadata_branch'],'intern-management/test')
        write_private(state.root/'credentials.json',{'app_id':'cli_test','app_secret':'private-test-sentinel-123'})
        write_private(state.root/'live.json',self.state.config());state.credentials()
        tracker.prepare_session(state.session(s['id']))
        self.assertEqual(tracker.intern(s)['metadata_mode'],'bot_branch')
        self.assertEqual(self.raw(self.remote,'rev-parse','intern-management/test'),previous)

    def test_controller_records_use_explicit_bot_branch_and_memory_reports_same_branch(self):
        from intern_personal.memory import context
        from intern_personal.prompt_context import publish
        s=self.session();branch=self.tracker.intern(s)['code_branch']
        config=self.state.config();config.update(records_workspace='project',records_bot_session=s['id'])
        write_private(self.state.root/'live.json',config)
        seq=self.state.db.execute("INSERT INTO events(app_id,message_id,chat_id,sender,role,workspace,text,state,result) VALUES('cli_test','om_ctrl','oc_manager','ou_owner','control','__control__','/help','complete','Help')").lastrowid
        item=dict(self.state.db.execute('SELECT * FROM events WHERE seq=?',(seq,)).fetchone())
        self.state.ledger.ensure_turn(item,time.time(),'local')
        self.assertTrue(self.tracker.queue_record(None,item,'complete'))
        self.assertIn('Help',self.raw(self.remote,'show',branch+':.intern_workspace/conversations/controller/turns/turn_'+str(seq)+'.md'))
        refs=publish(self.tracker,s)
        self.assertIn('same permanent branch',refs['intern-agent.workspace']['value'])
        self.assertIn('Named tasks do not create branches',context(self.tracker,s))
        self.assertEqual(self.branches(),{'main',branch})


if __name__=='__main__':unittest.main()
