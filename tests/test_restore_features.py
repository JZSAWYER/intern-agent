import copy
import hashlib
import json
from pathlib import Path
import time
import unittest
from unittest.mock import patch

from intern_personal.core import Invalid
from intern_personal.live_state import LiveState
from intern_personal import feature_state,progress,recovery,repairs,skills,triggers
from intern_personal.task_branches import command as task_command
import test_features as features_fixture
import test_progress as progress_fixture


class RestoredFeaturesTests(unittest.TestCase):
    setUp=features_fixture.FeaturesTests.setUp
    raw=features_fixture.FeaturesTests.raw
    attach=features_fixture.FeaturesTests.attach
    conversation=features_fixture.FeaturesTests.conversation
    relay=features_fixture.FeaturesTests.relay
    event=features_fixture.FeaturesTests.event
    team=features_fixture.FeaturesTests.team

    def test_mentions_require_this_app_bot_and_controls_remain_available(self):
        s=self.conversation();relay=self.relay();relay.api.bot_identity=lambda:'ou_this_app_bot'
        triggers.command(relay,s,'mentions')
        self.assertEqual(self.state.ingest(self.event(s,'Untargeted text')),'ignored_unmentioned')
        p=self.event(s,'@_user_1 Hello.\n  Keep this spacing.');p['event']['message']['mentions']=[dict(key='@_user_1',id=dict(open_id='ou_other_bot'))]
        self.assertEqual(self.state.ingest(p),'ignored_unmentioned')
        p['event']['message']['mentions'][0]['id']['open_id']='ou_this_app_bot'
        self.assertEqual(self.state.ingest(p),'accepted')
        self.assertEqual(self.state.db.execute("SELECT text FROM events WHERE role='agent'").fetchone()[0],'Hello.\n  Keep this spacing.')
        self.assertEqual(self.state.ingest(self.event(s,'/stop',mid='om_stop')),'accepted')
        self.assertEqual(self.state.db.execute("SELECT role FROM events WHERE message_id='om_stop'").fetchone()[0],'urgent')
        self.assertEqual(triggers.normalize(self.state,p['event']['message'],'@_user_9 Someone else'),'@_user_9 Someone else')
        self.state.db.execute('DELETE FROM bot_identity')
        self.assertFalse(triggers.accepts(self.state,s,p['event']['message']))
        relay.api.bot_identity=lambda:'wrong'
        with self.assertRaises(Invalid):triggers.command(relay,s,'mentions')

    def test_trigger_and_skill_changes_remain_owner_only(self):
        s=self.conversation();r=self.relay()
        for text in ['/trigger mentions','/skills enable pinned']:
            with self.assertRaises(Invalid):r.control(dict(text=text,chat_id=s['chat_id'],session=s['id'],sender='ou_someone'))

    def test_task_brief_report_and_catalog_recovery_match_markdown(self):
        s=self.conversation();task_command(self.tracker,s,'start','Verify restoration')
        task_command(self.tracker,s,'brief','scope Parser and compatibility behavior only')
        task_command(self.tracker,s,'brief','verification Run regression suite and inspect errors')
        task_command(self.tracker,s,'report','python -m unittest | Passed | Offline fixture; live UI not certified')
        feature_state.save(self.tracker,s,'skill_catalog','catalog',dict(entries={},enabled={s['id']:[]}))
        path=Path(self.tracker.require_workspace('project')['metadata_path'])
        data=json.loads((path/'.intern_workspace/recovery.json').read_text())
        docs=[(str(p.relative_to(path)),p.read_text()) for p in (path/'.intern_workspace').rglob('*.md')]
        recovery.validate(data,docs)
        wrong=copy.deepcopy(data);wrong['feature_state'][0]['data']={}
        with self.assertRaises(Invalid):recovery.validate(wrong,docs)
        source=self.root/'restore-source';self.raw(self.root,'clone','-q',str(self.remote),str(source))
        self.raw(source,'remote','set-url','origin','https://github.com/example-owner/example-project.git')
        fresh=LiveState(self.root/'fresh');self.addCleanup(fresh.close)
        from test_tracking import LocalTracking
        tracker=LocalTracking(fresh,self.remote)
        result=recovery.restore(tracker,source,'https://github.com/example-owner/example-project','intern-management/test',apply=True)
        self.assertTrue(result['applied']);self.assertEqual(len(feature_state.snapshot(fresh,'project')),2)
        self.assertFalse(fresh.identity()['owner']);self.assertFalse(fresh.db.execute("SELECT 1 FROM events WHERE state='queued'").fetchone())

    def test_new_team_assignment_requires_acceptance_and_verification_plan(self):
        commands,team,lead,worker=self.team()
        task_command(self.tracker,worker,'start','Worker task');task=self.state.ledger.selection(worker['id'])
        task_command(self.tracker,worker,'pause')
        with self.assertRaises(Invalid):commands.command(['assign',team['id'],worker['id'],task['id']])
        task_command(self.tracker,worker,'use',task['id']);task_command(self.tracker,worker,'criteria','Tests pass')
        task_command(self.tracker,worker,'brief','verification Run regressions');task_command(self.tracker,worker,'pause')
        commands.command(['assign',team['id'],worker['id'],task['id']])
        self.assertEqual(self.state.ledger.selection(worker['id'])['id'],task['id'])
        with self.assertRaises(Invalid):commands.command(['accept',team['id'],task['id'],'Done'])

    def pinned(self):
        s=self.conversation();files={'SKILL.md':b'---\nname: review\ndescription: Review parser boundaries\n---\nRead tests before changes.\n','references/guide.md':b'Check explicit owner instructions.\n'}
        def git(_,*args):
            if args[0]=='rev-parse':return b'a'*40+b'\n'
            if args[0]=='ls-tree':return b''.join(b'100644 blob '+str(i).encode()+b' '+str(len(v)).encode()+b'\t'+k.encode()+b'\0' for i,(k,v) in enumerate(files.items()))
            if args[0]=='cat-file':return list(files.values())[int(args[2])]
            return b''
        with patch.object(skills,'git',side_effect=git):skills.command(self.tracker,s,['add','review-v1','https://github.com/example-owner/example-skills','a'*40,'skills/review'])
        return s,skills.catalog(self.state,'project')['entries']['review-v1']

    def test_skill_requires_github_exact_pin_enablement_and_verified_cache(self):
        s,e=self.pinned()
        self.assertEqual(skills.context(self.state,s),'')
        skills.command(self.tracker,s,['enable','review-v1'])
        self.assertIn('Read tests before changes.',skills.context(self.state,s))
        other=self.conversation();self.assertEqual(skills.context(self.state,other),'')
        (skills.root(self.state)/e['digest']/'SKILL.md').write_text('Changed skill')
        self.assertIn('unavailable',skills.context(self.state,s))
        with self.assertRaises(Invalid):skills.command(self.tracker,s,['enable','review-v1'])
        for repo,commit,path in [('https://codeup.example/repo','a'*40,'skill'),('https://github.com/example-owner/skills','main','skill'),('https://github.com/example-owner/skills','a'*40,'../skill')]:
            with self.assertRaises(Invalid):skills.fetch(self.state,repo,commit,path)
        with self.assertRaises(Invalid):skills.command(self.tracker,s,['add','review-v1','https://github.com/example-owner/example-skills','b'*40,'skills/review'])

    def test_skill_resources_reject_links_private_content_and_oversize(self):
        s=self.conversation()
        with patch.object(skills,'git',side_effect=[b'',b'',b'a'*40+b'\n',b'120000 blob 1 4\tSKILL.md\0']):
            with self.assertRaises(Invalid):skills.fetch(self.state,'https://github.com/example-owner/skills','a'*40,'skill')
        fake_header=' '.join(['-----BEGIN','RSA','PRIVATE','KEY-----']).encode()
        for files in [{'SKILL.md':b'x'*65537},{'SKILL.md':fake_header}, {'../SKILL.md':b'x'}]:
            with self.assertRaises(Invalid):skills.inspect_files(files)
        self.assertFalse(list(skills.root(self.state).glob('.fetch-*')))

    def test_reviewed_permission_repair_staleness_and_secret_free_rollback(self):
        file=self.state.root/'credentials.json';file.chmod(0o640)
        plan=repairs.preview(self.state)
        encoded=json.dumps(plan)
        self.assertNotIn('private-test-sentinel',encoded);self.assertEqual(len(plan['actions']),1)
        file.chmod(0o644)
        with self.assertRaises(Invalid):repairs.apply(self.state,plan['plan_id'])
        file.chmod(0o640);result=repairs.apply(self.state,plan['plan_id'])
        self.assertEqual(file.stat().st_mode&0o777,0o600)
        receipt=json.dumps(json.loads((repairs.parent(self.state)/(plan['plan_id']+'.receipt.json')).read_text()))
        self.assertNotIn('private-test-sentinel',receipt)
        repairs.rollback(self.state,result['rollback_id']);self.assertEqual(file.stat().st_mode&0o777,0o640)

    def test_reviewed_guard_repair_verifies_snapshot_and_refuses_changed_rollback(self):
        self.conversation()
        binding=self.state.db.execute('SELECT * FROM git_hook_bindings ORDER BY directory LIMIT 1').fetchone()
        hook=Path(binding['config_path']).parent/'pre-commit';hook.write_text('#!/bin/sh\nexit 1\n')
        plan=repairs.preview(self.state);self.assertEqual(len(plan['actions']),1)
        repairs.apply(self.state,plan['plan_id'])
        from intern_personal.git_hooks import verified_path
        verified_path(binding['config_path'],binding['directory'])
        after=hook.read_text();hook.write_text(after+'# Later owner edit\n')
        with self.assertRaises(Invalid):repairs.rollback(self.state,plan['plan_id'])
        hook.write_text(after);repairs.rollback(self.state,plan['plan_id'])
        self.assertEqual(hook.read_text(),'#!/bin/sh\nexit 1\n')

    def test_feature_records_reject_changed_pins_and_deleted_verification(self):
        from intern_personal.records import render,validate_evolution
        workspace='project';brief=dict(fields={},reports=[dict(command='tests',result='pass',limits='fixture',conversation='session',at=1)])
        def doc(kind,data,revision=1):
            feature=dict(workspace=workspace,kind=kind,key='catalog' if kind=='skill_catalog' else 'task',data=data,revision=revision)
            return render('Feature',dict(kind='feature',workspace=workspace,revision=revision,feature=feature),[])
        with self.assertRaises(Invalid):validate_evolution(doc('task_brief',brief),doc('task_brief',dict(fields={},reports=[]),2))
        s,e=self.pinned();catalog=dict(entries={'review-v1':e},enabled={})
        changed=copy.deepcopy(catalog);changed['entries']['review-v1']['commit']='b'*40
        with self.assertRaises(Invalid):validate_evolution(doc('skill_catalog',catalog),doc('skill_catalog',changed,2))


class EditableProgressTests(unittest.TestCase):
    setUp=progress_fixture.ProgressTests.setUp
    event=progress_fixture.ProgressTests.event
    running=progress_fixture.ProgressTests.running
    add=progress_fixture.ProgressTests.add

    def question_card(self,s,turn='turn',source='rpc',identity=1):
        from intern_personal import user_questions
        row=user_questions.register(self.state,s,dict(threadId=s['thread_id'],turnId=turn,isBlocking=False,
            questions=[dict(id='level',header='Level',question='Which level?',options=[dict(label='Internship',description='Student role')])]),identity,source=source)
        self.state.db.execute("UPDATE user_questions SET state='pending',receipt='om_card',delivery_state='delivered' WHERE code=?",(row['code'],))
        return dict(self.state.db.execute('SELECT * FROM user_questions WHERE code=?',(row['code'],)).fetchone())

    def test_progress_moves_below_card_and_submitted_answer_without_replaying_either(self):
        from intern_personal import user_questions
        s,item=self.running();edits=[]
        self.api.edit_progress=lambda receipt,text:edits.append((receipt,text))
        self.api.progress=lambda chat,text,key:self.notices.append((chat,text,key)) or 'om_panel_'+str(len(self.notices))
        value=dict(item,event_seq=item['seq'],turn_id='turn')
        first=progress.panel(self.relay,value,'Before the question.')
        question=self.question_card(s)
        second=progress.panel(self.relay,value,'Continuing while the card is visible.')
        self.assertNotEqual(first,second)
        user_questions.answer(self.state,s,question['code'],'Internship')
        self.state.db.execute("UPDATE user_questions SET state='submitted' WHERE code=?",(question['code'],))
        self.api.update_question=lambda *args:None
        user_questions.close_card(self.relay,question)
        third=progress.panel(self.relay,value,'Using your submitted answer.')
        self.assertNotIn(third,(first,second));self.assertEqual(len(self.notices),3)
        self.assertEqual(self.notices[-1][1],'Using your submitted answer.')
        reopened=LiveState(self.temp.name);self.addCleanup(reopened.close)
        from intern_personal.feishu_live import Relay
        resumed=Relay(reopened,self.api,self.host,self.tracker)
        self.assertEqual(progress.panel(resumed,value,'New verified progress.'),third)
        self.assertEqual(len(self.notices),3);self.assertEqual(edits[-1][0],third)
        self.assertEqual(json.loads(self.state.db.execute('SELECT answers FROM user_questions WHERE code=?',(question['code'],)).fetchone()[0]),{'level':{'answers':['Internship']}})
        self.assertEqual(self.host.calls,[])
        self.state.transition(item['seq'],'complete')
        for _ in range(3):self.assertTrue(progress.close_delivered(self.relay))
        self.assertFalse(progress.close_delivered(self.relay))
        self.assertEqual(self.state.db.execute("SELECT count(*) FROM progress_panels WHERE state='closed'").fetchone()[0],3)

    def test_unrelated_old_thread_old_turn_and_demo_cards_do_not_reanchor_progress(self):
        s,item=self.running();self.api.edit_progress=lambda *args:None
        value=dict(item,event_seq=item['seq'],turn_id='turn')
        original=progress.panel(self.relay,value,'Original progress.')
        self.question_card(s,turn='older-turn',identity=1)
        old=self.question_card(s,identity=2)
        self.state.db.execute("UPDATE user_questions SET thread='old-fixture-thread' WHERE code=?",(old['code'],))
        self.question_card(s,source='demo',identity=3)
        other=self.relay.open_session('project',new=True);self.question_card(other,identity=4)
        self.assertEqual(progress.panel(self.relay,value,'Still the same progress message.'),original)
        self.assertEqual(len(self.notices),1)

    def test_native_unbound_progress_reanchors_by_exact_turn(self):
        s,item=self.running();self.api.edit_progress=lambda *args:None
        self.api.progress=lambda chat,text,key:self.notices.append((chat,text,key)) or 'om_native_'+str(len(self.notices))
        value=dict(session=s['id'],chat_id=s['chat_id'],turn_id='turn')
        first=progress.panel(self.relay,value,'Native progress before card.')
        self.question_card(s)
        self.assertNotEqual(progress.panel(self.relay,value,'Native progress after card.'),first)
        self.assertEqual(len(self.notices),2)

    def test_card_does_not_retry_an_ambiguous_progress_creation(self):
        s,item=self.running();self.api.edit_progress=lambda *args:None
        value=dict(item,event_seq=item['seq'],turn_id='turn')
        with patch.object(self.api,'progress',side_effect=TimeoutError):self.assertIsNone(progress.panel(self.relay,value,'Unknown delivery.'))
        self.question_card(s)
        self.assertIsNone(progress.panel(self.relay,value,'Later commentary.'))
        self.assertEqual(self.notices,[])
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM progress_panels').fetchone()[0],1)

    def test_one_panel_per_turn_updates_and_closes_only_after_final_delivery(self):
        s,item=self.running();edits=[];self.api.edit_progress=lambda receipt,text:edits.append((receipt,text))
        self.add(s,item,'First public update.');progress.deliver(self.relay)
        self.state.db.execute('UPDATE assistant_progress SET sent_at=0')
        self.add(s,item,'Second public update.',identity='second');progress.deliver(self.relay)
        progress.mark(self.relay,item,'uploading');progress.deliver(self.relay)
        self.assertEqual(len(self.notices),1);self.assertEqual(len(edits),2)
        self.assertIn('First public update.',edits[-1][1]);self.assertIn('Second public update.',edits[-1][1])
        progress.finish(self.state,s,'turn');self.assertFalse(progress.deliver(self.relay))
        self.state.transition(item['seq'],'complete');self.assertTrue(progress.deliver(self.relay))
        self.assertIn('Reply delivered.',edits[-1][1]);self.assertFalse(progress.deliver(self.relay))

    def test_ambiguous_creation_and_failed_edit_do_not_duplicate_or_block_final(self):
        s,item=self.running();self.api.edit_progress=lambda *args:None
        self.add(s,item)
        with patch.object(self.api,'progress',side_effect=TimeoutError):progress.deliver(self.relay)
        progress.mark(self.relay,item,'uploading');progress.deliver(self.relay)
        self.assertEqual(self.notices,[])
        self.assertEqual(self.state.db.execute('SELECT state FROM progress_panels').fetchone()[0],'uncertain')
        self.state.db.execute('DELETE FROM progress_panels');self.state.db.execute('UPDATE assistant_progress SET sent_at=0')
        self.add(s,item,identity='later');progress.deliver(self.relay)
        self.api.edit_progress=lambda *args:(_ for _ in ()).throw(RuntimeError())
        self.state.db.execute('UPDATE assistant_progress SET sent_at=0');self.add(s,item,identity='edit');progress.deliver(self.relay)
        self.assertEqual(len(self.notices),1)
        self.state.transition(item['seq'],'complete');progress.deliver(self.relay)
        self.assertEqual(self.state.db.execute('SELECT state FROM progress_panels').fetchone()[0],'closed')


if __name__=='__main__':unittest.main()
