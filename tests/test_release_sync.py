from pathlib import Path
from types import SimpleNamespace
import unittest

from intern_personal import release_sync
from intern_personal.core import Invalid
from intern_personal.live_state import LiveState
import test_tracking as fixture


class ReleaseSyncTests(unittest.TestCase):
    setUp=fixture.TrackingTests.setUp
    raw=fixture.TrackingTests.raw
    attach=fixture.TrackingTests.attach
    session=fixture.TrackingTests.session

    def prepare(self):
        self.attach();source,_=self.state.reserve_session('project',new=True)
        self.tracker.prepare_session(source,mode='bot_branch');operator,_=self.state.reserve_session('project',new=True)
        self.tracker.prepare_session(operator,mode='bot_branch')
        for s in [source,operator]:self.state.set_session(s['id'],state='ready',chat_id='oc_'+s['id'],thread_id='fixture_'+s['id'])
        release_sync.configure(self.state,source['id'],operator['id'],'https://github.com/example-owner/intern-agent.git')
        return self.state.session(source['id']),self.state.session(operator['id']),SimpleNamespace(state=self.state,tracker=self.tracker)

    def test_release_job_is_owner_authorized_durable_and_deduplicated(self):
        source,operator,relay=self.prepare();release_sync.poll(relay);release_sync.poll(relay)
        rows=self.state.db.execute('SELECT * FROM events').fetchall();self.assertEqual(len(rows),1)
        event=rows[0]
        self.assertEqual((event['role'],event['session'],event['sender'],event['state']),('agent',operator['id'],'ou_owner','queued'))
        self.assertIn('separate installable public product',event['text'])
        self.assertIn('No PR, merge',event['text']);self.assertIn('Do not send an automatic task',event['text'])
        self.assertTrue(self.state.authorized(dict(event)))
        reopened=LiveState(self.state.root)
        try:
            release_sync.poll(SimpleNamespace(state=reopened,tracker=fixture.LocalTracking(reopened,self.remote)))
            self.assertEqual(reopened.db.execute('SELECT count(*) FROM release_jobs').fetchone()[0],1)
        finally:reopened.close()

    def test_records_only_update_does_not_generate_another_release(self):
        source,operator,relay=self.prepare();release_sync.poll(relay)
        directory=Path(self.tracker.code_directory(source));records=directory/'.intern_workspace';records.mkdir(exist_ok=True)
        (records/'release-test.txt').write_text('Private record only\n')
        self.raw(directory,'add','.intern_workspace/release-test.txt');self.raw(directory,'commit','-qm','History only')
        self.raw(directory,'push','-q',str(self.remote),self.tracker.intern(source)['code_branch'])
        release_sync.poll(relay);self.assertEqual(self.state.db.execute('SELECT count(*) FROM release_jobs').fetchone()[0],1)
        (directory/'README.md').write_text('Maintained feature update\n');self.tracker.publish_code(source)
        release_sync.poll(relay);self.assertEqual(self.state.db.execute('SELECT count(*) FROM release_jobs').fetchone()[0],2)

    def test_unpublished_commit_cannot_queue_a_public_release(self):
        source,operator,relay=self.prepare();directory=Path(self.tracker.code_directory(source))
        (directory/'README.md').write_text('Not uploaded\n');self.tracker.publish_code(source,push=False)
        release_sync.poll(relay);self.assertEqual(self.state.db.execute('SELECT count(*) FROM release_jobs').fetchone()[0],0)

    def test_dependency_license_workflow_and_skill_resources_change_release_content(self):
        source,operator,relay=self.prepare();directory=Path(self.tracker.code_directory(source))
        prior=release_sync.fingerprint(self.tracker,directory,self.raw(directory,'rev-parse','HEAD'))
        files={'requirements/public.lock':'example-library==1.0\n','LICENSE':'MIT license fixture\n',
               'release-product.json':'{\"version\": \"0.1.0\"}\n',
               'docs/dependency-profile.json':'{\"fixture\": true}\n',
               '.github/workflows/release.yml':'name: release fixture\n',
               'skills/recover-intern-agent/references/installation.md':'Recovery reference fixture\n'}
        for name,body in files.items():
            with self.subTest(name=name):
                path=directory/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(body)
                self.raw(directory,'add',name);self.raw(directory,'commit','-qm','Update public release input')
                current=release_sync.fingerprint(self.tracker,directory,self.raw(directory,'rev-parse','HEAD'))
                self.assertNotEqual(current,prior);prior=current

    def test_busy_release_operator_queues_work_without_interruption(self):
        source,operator,relay=self.prepare()
        cursor=self.state.db.execute("INSERT INTO events(app_id,message_id,chat_id,sender,role,workspace,session,text,state) VALUES('cli_test','existing','oc_busy','ou_owner','agent','project',?,'Existing work','running')",(operator['id'],))
        release_sync.poll(relay)
        self.assertEqual(self.state.db.execute('SELECT state FROM events WHERE seq=?',(cursor.lastrowid,)).fetchone()[0],'running')
        self.assertIsNone(self.state.claim('agent'))
        self.state.transition(cursor.lastrowid,'complete',result='Existing result')
        self.assertEqual(self.state.claim('agent')['session'],operator['id'])

    def test_no_automatic_publication_policy_is_inferred_for_other_bots(self):
        source,operator,relay=self.prepare()
        (self.state.root/'release-policy.json').unlink();release_sync.poll(relay)
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM events').fetchone()[0],0)
        with self.assertRaises(Invalid):release_sync.configure(self.state,source['id'],source['id'],'https://github.com/example-owner/intern-agent.git')
        with self.assertRaisesRegex(Invalid,'separate public'):release_sync.configure(self.state,source['id'],operator['id'],'https://github.com/example-owner/example-project.git')

    def test_disabling_policy_cancels_unexecuted_handoff_without_replaying_it(self):
        source,operator,relay=self.prepare();release_sync.poll(relay)
        item=dict(self.state.db.execute('SELECT * FROM events').fetchone())
        release_sync.guard(relay,item)
        from intern_personal.live_state import write_private
        settings=release_sync.policy(self.state);settings['enabled']=False
        write_private(self.state.root/'release-policy.json',settings)
        from intern_personal.auto_recovery import RecoverySkipped
        with self.assertRaises(RecoverySkipped):release_sync.guard(relay,item)
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM events').fetchone()[0],1)

    def test_manual_debug_cannot_be_selected_as_automatic_release_operator(self):
        source,operator,relay=self.prepare()
        self.state.db.execute("INSERT INTO restored_roles(session,role) VALUES(?,'native')",(operator['id'],))
        with self.assertRaisesRegex(Invalid,'manual Debug'):release_sync.configure(self.state,source['id'],operator['id'],'https://github.com/example-owner/intern-agent.git')


if __name__=='__main__':unittest.main()
