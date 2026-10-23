"""Real Git publication completes a turn; retained history is not re-executed."""
import hashlib
import json
from pathlib import Path
import time
import unittest
from unittest.mock import patch

from intern_personal import archive_parts,delivery,git_hooks,turn_uploads
from intern_personal.core import Invalid
from intern_personal.feishu_live import Relay
from intern_personal.live_state import private_json
from intern_personal.records import parse
from intern_personal.workspace_tracking import TrackingError
import test_features as feature_fixture
import test_live as live_fixture
import test_tracking as tracking_fixture


class PublicationGateTests(unittest.TestCase):
    setUp=live_fixture.LiveTests.setUp
    event=live_fixture.LiveTests.event

    def test_courier_cannot_release_a_result_or_remove_typing_before_git(self):
        s=self.relay.open_session('project');self.api.messages.clear();self.relay.receive(self.event('Do the task',s['chat_id'],'om_task'))
        with patch.object(self.tracker,'publish_code',side_effect=TrackingError('Git connection unavailable')):
            self.assertEqual(self.relay.process_once('agent'),'sync_pending')
        seq=self.state.db.execute("SELECT seq FROM events WHERE role='agent'").fetchone()[0]
        delivery.pump(self.relay);self.relay.cleanup_typing()
        self.assertFalse(self.api.messages);self.assertFalse(self.api.removals)
        self.assertIn('Git connection unavailable',self.state.turn_status(s['id']))
        self.state.db.execute('UPDATE sync_retries SET retry_at=0');self.relay.retry_sync()
        self.assertEqual(self.relay.process_once('agent'),'complete');self.relay.cleanup_typing()
        self.assertEqual(len(self.host.calls),1);self.assertEqual(len(self.api.messages),1)
        self.assertEqual(self.api.removals,self.api.reactions);self.assertTrue(turn_uploads.ready(self.state,seq))

    def test_transient_git_failures_back_off_and_explain_the_exact_saved_blocker(self):
        s=self.relay.open_session('project');self.relay.receive(self.event('Task',s['chat_id']))
        item=self.state.claim('agent')
        self.state.transition(item['seq'],'preparing_reply',result='Saved result')
        self.state.ledger.update(item,'result_ready',activity='completed')
        delays=[]
        for _ in range(5):
            before=time.time();self.relay.defer_sync(item,TrackingError('Git push failed (exit 1)'))
            delays.append(round(self.state.db.execute('SELECT retry_at FROM sync_retries WHERE seq=?',(item['seq'],)).fetchone()[0]-before))
        self.assertEqual(delays,[30,60,120,240,300]);self.assertFalse(self.host.calls)
        status=self.state.turn_status(s['id']);self.assertIn('attempt 5',status);self.assertIn('not run again',status)


class RealPublicationTests(unittest.TestCase):
    setUp=tracking_fixture.TrackingTests.setUp
    raw=tracking_fixture.TrackingTests.raw
    attach=tracking_fixture.TrackingTests.attach
    conversation=feature_fixture.FeaturesTests.conversation
    relay=feature_fixture.FeaturesTests.relay
    event=feature_fixture.FeaturesTests.event

    def bot(self):
        if not self.state.workspace('project'):
            self.tracker.attach('project',self.source,'https://github.com/example-owner/example-project',mode='bot_branch')
        s,_=self.state.reserve_session('project',new=True);self.tracker.prepare_session(s,mode='bot_branch')
        self.state.set_session(s['id'],state='ready',chat_id='oc_'+s['id'],thread_id='fixture_'+s['id'])
        return self.state.session(s['id'])

    def test_no_pre_turn_push_and_one_final_push_contains_a_complete_record(self):
        s=self.bot();relay=self.relay();self.tracker.pushes.clear();code=Path(self.tracker.code_directory(s))
        def run(session,text,**kwargs):
            self.assertFalse(self.tracker.pushes)
            (code/'change.py').write_text('value = 1\n');return 'Saved answer'
        relay.host.run=run;self.state.ingest(self.event(s,'Make the change'))
        self.assertEqual(relay.process_once('agent'),'complete')
        seq=self.state.db.execute("SELECT max(seq) FROM events WHERE role='agent'").fetchone()[0]
        branch=self.tracker.intern(s)['code_branch']
        document=self.raw(self.remote,'show',branch+':.intern_workspace/conversations/'+s['id']+'/turns/turn_'+str(seq)+'.md')
        self.assertEqual((parse(document)['phase'],parse(document)['sync']),('complete','synced'))
        self.assertTrue(turn_uploads.ready(self.state,seq));self.assertEqual(len(self.tracker.pushes),1)
        self.assertFalse(self.state.db.execute('SELECT 1 FROM tracking_pending').fetchone())
        self.assertEqual(self.raw(self.remote,'rev-parse','main'),self.original)

    def test_failed_final_push_retries_the_prepared_record_without_rerunning_codex(self):
        s=self.bot();relay=self.relay();code=Path(self.tracker.code_directory(s));calls=[]
        def run(session,text,**kwargs):
            calls.append(text);(code/'work.txt').write_text('done\n');self.tracker.fail_push=True;return 'Completed work'
        relay.host.run=run;self.state.ingest(self.event(s,'Implement'))
        self.assertEqual(relay.process_once('agent'),'sync_pending');self.assertFalse(relay.api.messages)
        row=self.state.db.execute('SELECT * FROM turn_uploads').fetchone();old_head=row['code_head']
        self.assertEqual(row['state'],'prepared');self.tracker.fail_push=False
        self.state.db.execute('UPDATE sync_retries SET retry_at=0');relay.retry_sync()
        self.assertEqual(relay.process_once('agent'),'complete');self.assertEqual(calls,['Implement'])
        row=self.state.db.execute('SELECT * FROM turn_uploads').fetchone()
        self.assertEqual(row['code_head'],old_head);self.assertEqual(row['state'],'uploaded')
        self.assertEqual(len(relay.api.messages),1)

    def test_completed_terminal_observations_are_uploaded_in_one_batch_without_staging_research(self):
        s=self.bot();relay=self.relay();old=[]
        for i in range(5):
            cursor=self.state.db.execute("""INSERT INTO events(app_id,message_id,chat_id,sender,role,workspace,session,text,state,result,receipt)
                VALUES('cli_test',?,?,'ou_owner','agent','project',?,'Historical task','result_ready',?,'om_prior_reply')""",
                ('local_terminal_'+str(i),s['chat_id'],s['id'],'Saved observation '+str(i)))
            item=dict(self.state.db.execute('SELECT * FROM events WHERE seq=?',(cursor.lastrowid,)).fetchone());old.append(item['seq'])
            self.state.ledger.ensure_turn(item,time.time(),'terminal');self.state.ledger.update(item,'result_ready',activity='completed')
            self.state.db.execute('INSERT INTO terminal_turns VALUES(?,?,?,?,?)',(s['id'],'turn_'+str(i),item['seq'],item['result'],'task_complete'))
        blocked=dict(self.state.db.execute('SELECT * FROM events WHERE seq=?',(old[-1],)).fetchone())
        self.state.transition(blocked['seq'],'record_blocked');self.state.ledger.update(blocked,'record_blocked',sync='blocked')
        review=dict(self.state.db.execute('SELECT * FROM turn_records WHERE seq=?',(blocked['seq'],)).fetchone());old.pop()
        code=Path(self.tracker.code_directory(s));(code/'unrelated-research.txt').write_text('Do not upload this file\n')
        self.tracker.pushes.clear();self.assertEqual(relay.process_once('agent'),'complete')
        self.assertFalse(relay.host.calls);self.assertEqual(len(self.tracker.pushes),1)
        for seq in old:
            row=self.state.db.execute('SELECT phase,code_start,baseline,code_end FROM turn_records WHERE seq=?',(seq,)).fetchone()
            self.assertEqual(tuple(row),('complete',None,None,None));self.assertTrue(turn_uploads.ready(self.state,seq))
        branch=self.tracker.intern(s)['code_branch']
        self.assertNotIn('unrelated-research.txt',self.raw(self.remote,'ls-tree','--name-only',branch))
        after=dict(self.state.db.execute('SELECT * FROM turn_records WHERE seq=?',(blocked['seq'],)).fetchone())
        for key in ['phase','sync','code_start','baseline','code_end','evidence']:self.assertEqual(after[key],review[key])

    def test_legacy_parent_queue_targets_actual_bot_branches(self):
        s=self.bot();self.state.db.execute("INSERT INTO tracking_pending VALUES('project','Invalid',0)")
        with patch('intern_personal.bot_records.queue',return_value=True) as queue:self.tracker.retry_pending()
        self.assertFalse(self.state.db.execute('SELECT 1 FROM tracking_pending').fetchone())
        self.assertEqual(queue.call_args[0][1]['id'],s['id'])
        self.assertFalse((self.source/'.intern_workspace').exists())

    def test_validated_graph_cache_reuses_proofs_but_catches_added_then_deleted_private_blobs(self):
        s=self.bot();path=Path(self.tracker.code_directory(s));config=self.state.db.execute('SELECT config_path FROM git_hook_bindings WHERE directory=?',(str(path.resolve()),)).fetchone()[0]
        (path/'value.py').write_text('value = 3\n');self.raw(path,'add','value.py');self.raw(path,'commit','-qm','legitimate work')
        def check():
            head=self.raw(path,'rev-parse','HEAD');branch=self.tracker.intern(s)['code_branch']
            git_hooks.run(config,'pre-push',['refs/heads/'+branch+' '+head+' refs/heads/'+branch+' '+'0'*40])
        with patch.object(git_hooks,'validate',wraps=git_hooks.validate) as validate:
            check();self.assertGreater(validate.call_count,0);validate.reset_mock();check();self.assertEqual(validate.call_count,0)
        (path/'bad.py').write_text("api_key = 'not-a-fixture-value'\n")
        self.raw(path,'-c','core.hooksPath=/dev/null','add','bad.py');self.raw(path,'-c','core.hooksPath=/dev/null','commit','-qm','uninspected addition')
        self.raw(path,'-c','core.hooksPath=/dev/null','rm','bad.py');self.raw(path,'-c','core.hooksPath=/dev/null','commit','-qm','removed from tip')
        with self.assertRaises(Invalid):check()

    def test_validation_cache_is_invalidated_by_policy_or_base_changes(self):
        from intern_personal.git_objects import Proofs
        s=self.bot();path=Path(self.tracker.code_directory(s))
        config=self.state.db.execute('SELECT config_path FROM git_hook_bindings WHERE directory=?',(str(path.resolve()),)).fetchone()[0]
        value=private_json(config);base=self.raw(path,'rev-parse','refs/remotes/origin/main')
        self.assertTrue(Proofs(config,value,base).tips)
        changed=dict(value,hashes=dict(value['hashes'],module='0'*64))
        self.assertFalse(Proofs(config,changed,base).tips)
        self.assertFalse(Proofs(config,value,'0'*40).tips)

    def test_inherited_skill_link_is_opaque_but_new_links_are_rejected(self):
        link=self.source/'.intern_workspace/.skill_sources/intern_agent_skills'
        link.parent.mkdir(parents=True);link.symlink_to('/unavailable/shared/skills')
        self.raw(self.source,'add','.intern_workspace')
        self.raw(self.source,'update-index','--add','--cacheinfo','160000',self.original,'.intern_workspace/.skill_sources/provider-submodule')
        self.raw(self.source,'commit','-qm','Existing project skill providers')
        self.raw(self.source,'push',str(self.remote),'main')
        self.raw(self.source,'update-ref','refs/remotes/origin/main','HEAD')
        path=self.source;branch='intern-agent/link-test'
        self.raw(path,'checkout','-qb',branch)
        config=git_hooks.install(self.state,path,[branch],combined_records=True)
        git_hooks.run(config,'pre-commit')
        bad=path/'.intern_workspace/new-link.txt';bad.symlink_to('/must/not/read')
        self.raw(path,'add','--sparse',str(bad))
        with self.assertRaises(Invalid):git_hooks.run(config,'pre-commit')

    def test_pruned_recovery_parts_still_restore_from_old_git_commits(self):
        binding=dict(metadata_path=str(self.source));old=dict(text='a'*400000)
        archive_parts.write(self.tracker,binding,old)
        self.raw(self.source,'-c','core.hooksPath=/dev/null','add','.intern_workspace');self.raw(self.source,'-c','core.hooksPath=/dev/null','commit','-qm','old snapshot')
        old_head=self.raw(self.source,'rev-parse','HEAD');manifest=json.loads((self.source/'.intern_workspace/recovery.json').read_text())
        obsolete=manifest['parts'][0]['path'];new=dict(text='b'*400000);archive_parts.write(self.tracker,binding,new)
        self.assertFalse((self.source/obsolete).exists())
        restored=archive_parts.expand(manifest,lambda name:self.raw(self.source,'show',old_head+':'+name))
        self.assertEqual(restored,old)
        current=json.loads((self.source/'.intern_workspace/recovery.json').read_text())
        self.assertEqual(archive_parts.expand(current,lambda name:(self.source/name).read_text()),new)

    def test_inherited_enterprise_records_do_not_block_new_history_but_edits_need_review(self):
        old=self.source/'.intern_workspace/old-team/team.json';old.parent.mkdir(parents=True)
        old.write_text(json.dumps(dict(member='ou_fixture_legacy_identity'))+'\n')
        (old.parent/'experiment.py').write_text('value = 1\n')
        (old.parent/'results.jsonl').write_text('{"score": 1}\n')
        self.raw(self.source,'add','.intern_workspace');self.raw(self.source,'commit','-qm','Existing enterprise records')
        self.raw(self.source,'push',str(self.remote),'main')
        self.raw(self.source,'update-ref','refs/remotes/origin/main','HEAD')
        s=self.bot();path=Path(self.tracker.code_directory(s))/'.intern_workspace/old-team/team.json'
        self.assertEqual(path.read_bytes(),old.read_bytes())
        path.write_text(json.dumps(dict(member='ou_fixture_changed_identity'))+'\n')
        with self.assertRaisesRegex(Invalid,'Credential material'):self.tracker.record(s,phase='Ready')

    def test_pruning_preserves_a_modified_historical_chunk_for_review(self):
        binding=dict(metadata_path=str(self.source));archive_parts.write(self.tracker,binding,dict(text='a'*400000))
        self.raw(self.source,'-c','core.hooksPath=/dev/null','add','.intern_workspace');self.raw(self.source,'-c','core.hooksPath=/dev/null','commit','-qm','old snapshot')
        manifest=json.loads((self.source/'.intern_workspace/recovery.json').read_text());path=self.source/manifest['parts'][0]['path']
        data=path.read_text()+' ';path.write_text(data)
        with self.assertRaises(Invalid):archive_parts.write(self.tracker,binding,dict(text='b'*400000))
        self.assertEqual(path.read_text(),data)


if __name__=='__main__':unittest.main()
