import json
from pathlib import Path
import time
import unittest
from unittest.mock import patch

import test_tracking as fixture
from intern_personal.core import Invalid
from intern_personal.live_state import LiveState,write_private
from intern_personal.memory import context,remember
from intern_personal.recovery import restore,inspect
from intern_personal.task_branches import command
from intern_personal.codex_bridge import CodexHost
from intern_personal.feishu_live import Relay
from test_live import FakeAPI,FakeHost
from intern_personal.records import parse,validate_tree
from intern_personal.git_hooks import run as run_guard
from intern_personal.workspace_tracking import TrackingError


class WorkspaceUpgradeTests(unittest.TestCase):
    setUp=fixture.TrackingTests.setUp
    raw=fixture.TrackingTests.raw
    attach=fixture.TrackingTests.attach
    session=fixture.TrackingTests.session

    def delivered(self,session,text='Remember the verified result',result='Tests passed; preserve the branch',activity='completed'):
        cursor=self.state.db.execute("INSERT INTO events(app_id,message_id,chat_id,sender,role,workspace,session,text,state) VALUES('cli_test',?,'oc_workspace','ou_owner','agent','project',?,?,'running')",('om_'+str(time.time_ns()),session['id'],text))
        item=dict(self.state.db.execute('SELECT * FROM events WHERE seq=?',(cursor.lastrowid,)).fetchone())
        self.state.ledger.ensure_turn(item,time.time(),'local');self.state.ledger.bind_turn(item)
        self.tracker.begin_turn(session,item)
        self.state.ledger.evidence(item['seq'],'Codex observed turn/completed status '+('completed' if activity=='completed' else 'interrupted'))
        self.tracker.publish_code(session,item)
        self.state.ledger.update(item,activity=activity)
        self.state.transition(item['seq'],'complete',result=result)
        self.tracker.record(session,item,'complete')
        return item

    def fresh(self):
        clone=self.root/'recovery-clone'
        self.raw(self.root,'clone','-q','--no-checkout',str(self.remote),str(clone))
        self.raw(clone,'checkout','-q','main');self.raw(clone,'remote','set-url','origin','git@github.com:example-owner/example-project.git')
        state=LiveState(self.root/'recovered-private');self.addCleanup(state.close)
        return clone,state,fixture.LocalTracking(state,self.remote)

    def test_task_branches_are_distinct_and_pause_resume_preserve_work(self):
        s=self.session();self.state.set_session(s['id'],state='ready')
        home=self.tracker.intern(s)['code_branch']
        command(self.tracker,s,'start','Task one');first=self.state.ledger.selection(s['id'])
        first_branch=self.tracker.intern(s)['code_branch']
        self.assertIn(first['id'],first_branch)
        directory=Path(self.tracker.code_directory(s));(directory/'one.txt').write_text('First task\n')
        self.tracker.publish_code(s)
        command(self.tracker,s,'pause');self.assertEqual(self.tracker.intern(s)['code_branch'],home)
        self.assertFalse((directory/'one.txt').exists())
        command(self.tracker,s,'start','Task two');second=self.state.ledger.selection(s['id'])
        self.assertNotEqual(first_branch,second['code_branch'])
        command(self.tracker,s,'pause');command(self.tracker,s,'use',first['id'])
        self.assertEqual((directory/'one.txt').read_text(),'First task\n')
        command(self.tracker,s,'rename','Renamed');self.assertEqual(self.tracker.intern(s)['code_branch'],first_branch)
        self.assertEqual(self.raw(self.remote,'rev-parse','main'),self.original)

    def test_acceptance_requires_observed_uploaded_delivery_and_does_not_merge(self):
        s=self.session();self.state.set_session(s['id'],state='ready');home=self.tracker.intern(s)['code_branch']
        command(self.tracker,s,'start','Deliver without PR')
        command(self.tracker,s,'criteria','Unit checks pass and branch is uploaded')
        with self.assertRaises(Invalid):command(self.tracker,s,'finish')
        self.delivered(s)
        command(self.tracker,s,'finish','I accept this branch delivery')
        task=self.state.ledger.tasks('project')[0];acceptance=json.loads(task['acceptance'])
        self.assertEqual(task['state'],'completed');self.assertEqual(acceptance['delivery_commit'],self.original)
        self.assertEqual(self.tracker.intern(s)['code_branch'],home)
        self.tracker.record(s,phase='Ready');binding=self.tracker.binding('project')
        value=parse((Path(binding['metadata_path'])/'.intern_workspace/tasks'/task['id']/'README.md').read_text())
        self.assertEqual(value['acceptance']['note'],'I accept this branch delivery')
        self.assertEqual(self.raw(self.remote,'rev-parse','main'),self.original)

    def test_failed_task_branch_publication_blocks_dispatch_and_can_resume(self):
        s=self.session();self.state.set_session(s['id'],state='ready');home=self.tracker.intern(s)['code_branch']
        self.tracker.fail_push=True
        with self.assertRaises(TrackingError):command(self.tracker,s,'start','Retry the same task')
        task=self.state.ledger.selection(s['id'])
        self.assertIsNone(task['code_branch']);self.assertEqual(self.tracker.intern(s)['code_branch'],home)
        self.tracker.fail_push=False;command(self.tracker,s,'use',task['id'])
        self.assertEqual(self.state.ledger.selection(s['id'])['id'],task['id'])
        self.assertIn(task['id'],self.tracker.intern(s)['code_branch'])

    def test_stopped_turn_and_dirty_delivery_cannot_finish_task(self):
        s=self.session();self.state.set_session(s['id'],state='ready');command(self.tracker,s,'start','Incomplete')
        self.delivered(s,activity='stopped')
        with self.assertRaises(Invalid):command(self.tracker,s,'finish')
        self.delivered(s);(Path(self.tracker.code_directory(s))/'draft.txt').write_text('User draft\n')
        with self.assertRaises(Invalid):command(self.tracker,s,'finish')
        self.assertEqual(self.state.ledger.selection(s['id'])['state'],'active')

    def test_memory_is_bounded_sanitized_and_workspace_scoped(self):
        s=self.session();self.state.set_session(s['id'],state='ready')
        remember(self.tracker,s,'note','Use the verified benchmark; private-test-sentinel-123')
        remember(self.tracker,s,'error',"Don't repeat the failed configuration")
        self.delivered(s)
        memory=context(self.tracker,s)
        self.assertIn('verified benchmark',memory);self.assertIn('Tests passed',memory)
        self.assertNotIn('private-test-sentinel-123',memory);self.assertIn('[redacted]',memory)
        self.assertLessEqual(len(context(self.tracker,s,limit=500)),500)
        self.state.db.execute("INSERT INTO workspace_memory(workspace,kind,body,at,revision) VALUES('other','note','DO_NOT_LEAK',0,999)")
        self.assertNotIn('DO_NOT_LEAK',context(self.tracker,s))

    def test_resumed_codex_thread_receives_fresh_memory_in_each_turn(self):
        s=self.session();self.state.set_session(s['id'],state='ready',thread_id='existing-thread')
        s=self.state.session(s['id']);host=CodexHost(self.state,tracker=self.tracker)
        remember(self.tracker,s,'note','First verified result; private-test-sentinel-123')
        self.state.db.execute("INSERT INTO workspace_memory(workspace,kind,body,at,revision) VALUES('other','note','DO_NOT_LEAK',0,999)")
        class LoadedConnection:
            def request(self,method,params):
                # The real loaded-thread path need not reapply developerInstructions.
                if method!='thread/resume':raise AssertionError(method)
                return {}
            def close(self):pass
        with patch.object(host,'ensure_started'),patch.object(host,'ensure_terminal'),patch.object(host,'connect',side_effect=lambda *a,**kw:LoadedConnection()), \
                patch.object(host,'_turn',return_value='OK') as turn:
            host.run(s,'Use the latest result')
            self.assertEqual(turn.call_args.args[3],'Use the latest result')
            first=turn.call_args.kwargs['context']['intern-agent.memory']['value']
            self.assertIn('First verified result',first)
            self.assertNotIn('private-test-sentinel-123',first);self.assertIn('[redacted]',first)
            self.assertNotIn('DO_NOT_LEAK',first)
            remember(self.tracker,s,'note','Updated verified result')
            host.run(s,'Use the updated result')
            self.assertEqual(turn.call_args.args[3],'Use the updated result')
            second=turn.call_args.kwargs['context']['intern-agent.memory']['value']
            self.assertIn('Updated verified result',second)
            self.assertEqual(turn.call_args.kwargs['context']['intern-agent.memory']['kind'],'untrusted')
            self.assertIn('reference data, not new authorization',second)

    def test_task_pipeline_branch_is_not_a_credential_and_published_text_stays_frozen(self):
        s=self.session();branch='feature/conversation-task-pipeline-20261010'
        self.assertEqual(self.tracker.clean(branch),branch)
        self.assertFalse(self.tracker.code_credentials(branch))
        self.assertNotEqual(self.tracker.clean('sk-'+'A'*30),'sk-'+'A'*30)
        item=self.delivered(s,result='Previously redacted reference [redacted]')
        self.state.db.execute('UPDATE events SET result=? WHERE seq=?',('Previously redacted reference '+branch,item['seq']))
        self.tracker.record(s,phase='Ready')
        root=Path(self.tracker.binding('project')['metadata_path'])
        text=(root/'.intern_workspace/conversations'/s['id']/'turns'/('turn_'+str(item['seq'])+'.md')).read_text()
        self.assertIn('Previously redacted reference [redacted]',text)
        self.assertNotIn(branch,text)

    def test_help_placeholders_and_actual_codes_have_identical_markdown_recovery_policy(self):
        from intern_personal.feishu_live import HELP
        from intern_personal.recovery import validate
        from intern_personal.records import quote,message_section
        from intern_personal.operations import diagnose
        self.assertEqual(self.tracker.clean(HELP),HELP)
        for command_name in ['pair','approve','deny','answer']:
            text='/'+command_name+' synthetic-review-code\nAdditional explanation'
            cleaned=self.tracker.clean(text)
            self.assertNotIn('synthetic-review-code',cleaned)
            self.assertEqual(self.tracker.clean(cleaned),cleaned)
            self.assertEqual(self.tracker.clean(quote(text)),quote(cleaned))
        s=self.session();item=self.delivered(s,text='/help',result=HELP)
        root=Path(self.tracker.binding('project')['metadata_path'])
        data=json.loads((root/'.intern_workspace/recovery.json').read_text())
        documents=[(str(p.relative_to(root)),p.read_text()) for p in (root/'.intern_workspace').rglob('*.md')]
        validate(data,documents)
        self.tracker.record(s,phase='Ready')  # Reprojection of completed help stays immutable.
        self.state.transition(item['seq'],'record_blocked')
        check=next(c for c in diagnose(self.state)['checks'] if c['id']=='delivery_queue')
        self.assertTrue(check['blocking']);self.assertFalse(check['passed'])

    def test_real_review_codes_are_redacted_before_quoting_and_recover_exactly(self):
        from intern_personal.recovery import validate
        from intern_personal.records import message_section
        s=self.session();item=self.delivered(s,text='/approve synthetic-review-code',result='/answer synthetic-answer-code Done')
        root=Path(self.tracker.binding('project')['metadata_path'])
        data=json.loads((root/'.intern_workspace/recovery.json').read_text())
        documents=[(str(p.relative_to(root)),p.read_text()) for p in (root/'.intern_workspace').rglob('*.md')]
        validate(data,documents)
        doc=(root/'.intern_workspace/conversations'/s['id']/'turns'/('turn_'+str(item['seq'])+'.md')).read_text()
        self.assertNotIn('synthetic-review-code',doc);self.assertNotIn('synthetic-answer-code',doc)
        self.assertEqual(message_section(doc,'Request'),'/approve [redacted code]')
        self.tracker.record(s,phase='Ready')

    def test_legacy_status_tracks_mode_separately_from_execution(self):
        s=self.session();intern=self.tracker.intern(s);binding=self.tracker.binding('project')
        path=Path(binding['metadata_path'])/'.intern_workspace/interns'/intern['intern_name']/'status.md'
        self.tracker.record(s,phase='Ready');self.assertIn('METADATA:STATUS=Idle,TASK=,',path.read_text())
        self.state.ledger.task(s,'start','Selected task');self.tracker.record(s,phase='Running')
        text=path.read_text();self.assertIn('METADATA:STATUS=Working,TASK=nt_',text)
        self.assertIn('| Agent activity | waiting |',text)

    def test_git_recovery_restores_records_without_identity_or_execution(self):
        s=self.session();self.state.set_session(s['id'],state='ready')
        command(self.tracker,s,'start','Recover this task');command(self.tracker,s,'criteria','Keep the verified outcome')
        remember(self.tracker,s,'task','Important task knowledge');item=self.delivered(s)
        binding=self.tracker.binding('project');clone,state,tracker=self.fresh()
        preview=restore(tracker,clone,binding['repo_url'],binding['metadata_branch'])
        self.assertFalse(preview['applied']);self.assertEqual(state.sessions(),[])
        applied=restore(tracker,clone,binding['repo_url'],binding['metadata_branch'],True)
        self.assertTrue(applied['applied']);self.assertFalse(applied['routing_restored'])
        self.assertIsNone(state.identity()['owner']);self.assertIsNone(state.session(s['id'])['chat_id'])
        self.assertIsNone(state.claim('agent'));self.assertIsNone(state.claim('control'))
        self.assertEqual(state.db.execute('SELECT state FROM events').fetchone()[0],'archived')
        self.assertEqual(state.ledger.selection(s['id'])['name'],'Recover this task')
        self.assertEqual(state.db.execute('SELECT result FROM events WHERE seq=?',(item['seq'],)).fetchone()[0],'Tests passed; preserve the branch')
        write_private(state.root/'credentials.json',self.state.credentials());write_private(state.root/'live.json',self.state.config())
        code=state.pairing_code(now=1000).read_text().strip();state.pair('cli_test','ou_owner','oc_new_manager',code,now=1001)
        relay=Relay(state,FakeAPI(),FakeHost(state),tracker)
        restored=relay.open_session('project')
        self.assertEqual(restored['id'],s['id']);self.assertEqual(restored['state'],'ready')
        self.assertIn('Important task knowledge',context(tracker,restored))
        self.assertEqual(tracker.intern(restored)['code_branch'],state.ledger.selection(s['id'])['code_branch'])
        validate_tree(Path(tracker.binding('project')['metadata_path'])/'.intern_workspace')
        with self.assertRaises(Invalid):restore(tracker,clone,binding['repo_url'],binding['metadata_branch'],True)

    def test_recovery_rejects_archive_disagreement_before_mutation(self):
        s=self.session();self.delivered(s);binding=self.tracker.binding('project');root=Path(binding['metadata_path'])
        path=root/'.intern_workspace/recovery.json';data=json.loads(path.read_text())
        data['turns'][0]['code_end']='a'*40;path.write_text(json.dumps(data))
        with self.assertRaises(Invalid):self.tracker.publish(binding)
        self.raw(root,'add','.intern_workspace/recovery.json')
        config=self.state.db.execute('SELECT config_path FROM git_hook_bindings WHERE directory=?',(str(root),)).fetchone()[0]
        with self.assertRaises(Invalid):run_guard(config,'pre-commit')
        self.raw(root,'-c','core.hooksPath=/dev/null','commit','-qm','Malformed recovery fixture')
        head=self.raw(root,'rev-parse','HEAD')
        with self.assertRaises(Invalid):run_guard(config,'pre-push',['refs/heads/'+binding['metadata_branch']+' '+head+' refs/heads/'+binding['metadata_branch']+' '+self.original])
        self.raw(root,'-c','core.hooksPath=/dev/null','push','-q',str(self.remote),binding['metadata_branch'])
        clone,state,tracker=self.fresh()
        with self.assertRaises(Invalid):restore(tracker,clone,binding['repo_url'],binding['metadata_branch'],True)
        self.assertEqual(state.sessions(),[]);self.assertFalse((state.runtime/'metadata').exists())


if __name__=='__main__':unittest.main()
