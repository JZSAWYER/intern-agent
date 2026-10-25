import hashlib
import json
from pathlib import Path
import time
import unittest
from unittest.mock import patch

from intern_personal import draft_baselines as drafts,peer_mail
from intern_personal.core import Invalid
from intern_personal.git_diagnostics import failure
from intern_personal.workspace_tracking import TrackingError
import test_git_completion as fixture


class DraftTests(unittest.TestCase):
    setUp=fixture.RealPublicationTests.setUp
    raw=fixture.RealPublicationTests.raw
    bot=fixture.RealPublicationTests.bot
    relay=fixture.RealPublicationTests.relay
    event=fixture.RealPublicationTests.event

    def request(self,s,text='Edit the existing draft'):
        self.state.ingest(self.event(s,text));item=self.state.claim('agent')
        self.tracker.begin_turn(s,item);return item

    def test_changed_bot_draft_is_uploaded_and_before_image_stays_private(self):
        s=self.bot();directory=Path(self.tracker.code_directory(s));file=directory/'draft.py'
        before='value = 1\n';file.write_text(before);(directory/'untouched.txt').write_text('Keep this owner draft\n')
        relay=self.relay();self.tracker.pushes.clear()
        def run(session,text,**kwargs):file.write_text('value = 2\n');return 'Updated and checked'
        relay.host.run=run;self.state.ingest(self.event(s,'Update the draft'))
        self.assertEqual(relay.process_once('agent'),'complete')
        seq=self.state.db.execute("SELECT seq FROM events WHERE role='agent'").fetchone()[0]
        self.assertEqual(self.raw(self.remote,'show',self.tracker.intern(s)['code_branch']+':draft.py'),'value = 2')
        self.assertNotIn('untouched.txt',self.raw(self.remote,'ls-tree','--name-only',self.tracker.intern(s)['code_branch']))
        row=self.state.db.execute('SELECT * FROM draft_baselines WHERE seq=?',(seq,)).fetchone();manifest=json.loads(Path(row['manifest']).read_text())
        self.assertEqual(drafts.get(self.state,manifest['files']['draft.py']['sha256']),before.encode())
        self.assertFalse(Path(row['manifest']).stat().st_mode&0o077);self.assertEqual(len(self.tracker.pushes),1)

    def test_corrupted_before_image_cannot_waive_the_overlap_review(self):
        s=self.bot();file=Path(self.tracker.code_directory(s))/'draft.txt';file.write_text('Before\n');item=self.request(s)
        row=self.state.db.execute('SELECT manifest FROM draft_baselines WHERE seq=?',(item['seq'],)).fetchone()
        digest=json.loads(Path(row[0]).read_text())['files']['draft.txt']['sha256']
        (drafts.root(self.state)/'blobs'/digest).write_text('Corrupted\n');file.write_text('After\n')
        with self.assertRaisesRegex(Invalid,'integrity'):self.tracker.publish_code(s,item,push=False)

    def test_uncaptured_files_keep_an_explained_local_review(self):
        s=self.bot();file=Path(self.tracker.code_directory(s))/'draft.txt';file.write_text('Before\n')
        with patch.object(drafts,'MAX_FILES',0):item=self.request(s)
        file.write_text('After\n')
        with self.assertRaisesRegex(Invalid,'local review, not a network'):self.tracker.publish_code(s,item,push=False)

    def ignored_journal(self,changed):
        s=self.bot();directory=Path(self.tracker.code_directory(s));journal=directory/'runs'/'trace[1].jsonl';journal.parent.mkdir()
        journal.write_bytes(b'old journal\n'*300);draft=directory/'draft.py';draft.write_text('value = 1\n')
        self.state.ingest(self.event(s,'Publish a bounded summary',mid='om_journal_'+str(int(changed))));item=self.state.claim('agent')
        with patch.object(drafts,'MAX_FILE',1024):self.tracker.begin_turn(s,item)
        original=self.state.db.execute('SELECT baseline FROM turn_records WHERE seq=?',(item['seq'],)).fetchone()[0]
        manifest=json.loads(Path(self.state.db.execute('SELECT manifest FROM draft_baselines WHERE seq=?',(item['seq'],)).fetchone()[0]).read_text())
        self.assertNotIn('runs/trace[1].jsonl',manifest['files'])
        if changed:journal.write_bytes(b'new journal\n'*400)
        private_bytes=journal.read_bytes();(directory/'.gitignore').write_text('runs/*.jsonl\n');draft.write_text('value = 2\n')
        (directory/'summary.json').write_text('{"result":"verified"}\n')
        self.tracker.publish_code(s,item,push=False)
        self.assertEqual(journal.read_bytes(),private_bytes)
        self.assertEqual(self.raw(directory,'show','HEAD:draft.py'),'value = 2')
        self.assertEqual(self.raw(directory,'show','HEAD:summary.json'),'{"result":"verified"}')
        self.assertNotIn('runs/trace[1].jsonl',self.raw(directory,'ls-tree','-r','--name-only','HEAD'))
        self.assertEqual(self.state.db.execute('SELECT baseline FROM turn_records WHERE seq=?',(item['seq'],)).fetchone()[0],original)

    def test_newly_ignored_unchanged_journal_does_not_require_a_before_image(self):self.ignored_journal(False)

    def test_newly_ignored_growing_journal_does_not_require_a_before_image(self):self.ignored_journal(True)

    def test_ignore_rule_cannot_hide_a_tracked_draft_review(self):
        s=self.bot();directory=Path(self.tracker.code_directory(s));file=directory/'README.md';file.write_text('Before\n')
        with patch.object(drafts,'MAX_FILES',0):item=self.request(s)
        file.write_text('After\n');(directory/'.gitignore').write_text('README.md\n')
        with self.assertRaisesRegex(Invalid,'local review, not a network'):self.tracker.publish_code(s,item,push=False)

    def test_ignore_rule_cannot_hide_a_deleted_untracked_draft(self):
        s=self.bot();directory=Path(self.tracker.code_directory(s));file=directory/'owner.txt';file.write_text('Before\n')
        with patch.object(drafts,'MAX_FILES',0):item=self.request(s)
        file.unlink();(directory/'.gitignore').write_text('owner.txt\n')
        with self.assertRaisesRegex(Invalid,'local review, not a network'):self.tracker.publish_code(s,item,push=False)

    def test_ignore_rule_cannot_hide_a_linked_draft_replacement(self):
        s=self.bot();directory=Path(self.tracker.code_directory(s));file=directory/'owner.txt';file.write_text('Before\n')
        with patch.object(drafts,'MAX_FILES',0):item=self.request(s)
        target=self.root/'outside.txt';target.write_text('Outside\n');file.unlink();file.symlink_to(target)
        (directory/'.gitignore').write_text('owner.txt\n')
        with self.assertRaisesRegex(Invalid,'local review, not a network'):self.tracker.publish_code(s,item,push=False)

    def test_staged_owner_work_is_rejected_before_codex_and_preserved(self):
        s=self.bot();directory=Path(self.tracker.code_directory(s));file=directory/'draft.txt';file.write_text('Staged by owner\n');self.raw(directory,'add','draft.txt')
        self.state.ingest(self.event(s,'Work after my staged change'));item=self.state.claim('agent')
        with self.assertRaisesRegex(Invalid,'before Codex starts'):self.tracker.begin_turn(s,item)
        self.assertEqual(file.read_text(),'Staged by owner\n');self.assertIn('draft.txt',self.raw(directory,'diff','--cached','--name-only'))

    def test_old_before_image_can_be_recovered_by_exact_hash_without_rewriting_baseline(self):
        s=self.bot();directory=Path(self.tracker.code_directory(s));file=directory/'draft.txt';file.write_text('Exact old draft\n');item=self.request(s)
        old=self.state.db.execute('SELECT baseline FROM turn_records WHERE seq=?',(item['seq'],)).fetchone()[0]
        self.state.db.execute('DELETE FROM draft_baselines WHERE seq=?',(item['seq'],));file.write_text('New draft\n')
        backup=self.root/'backup.txt';backup.write_text('A guessed old draft\n')
        with self.assertRaisesRegex(Invalid,'original starting hash'):drafts.recover(self.tracker,s,item,{'draft.txt':backup})
        backup.write_text('Exact old draft\n');drafts.recover(self.tracker,s,item,{'draft.txt':backup})
        self.tracker.publish_code(s,item,push=False)
        self.assertEqual(self.state.db.execute('SELECT baseline FROM turn_records WHERE seq=?',(item['seq'],)).fetchone()[0],old)

    def test_shared_checkout_does_not_receive_automatic_draft_ownership(self):
        s=self.bot();other=self.bot();directory=Path(self.tracker.code_directory(s))
        self.state.db.execute('UPDATE tracked_interns SET code_path=? WHERE session=?',(str(directory),other['id']))
        file=directory/'draft.txt';file.write_text('Before\n');item=self.request(s);file.write_text('After\n')
        self.assertFalse(self.state.db.execute('SELECT 1 FROM draft_baselines WHERE seq=?',(item['seq'],)).fetchone())
        with self.assertRaises(Invalid):self.tracker.publish_code(s,item,push=False)

    def test_exact_current_review_preserves_original_baseline_and_limits_upload_paths(self):
        s=self.bot();directory=Path(self.tracker.code_directory(s));file=directory/'draft.txt';file.write_text('Before\n');item=self.request(s)
        original=self.state.db.execute('SELECT baseline FROM turn_records WHERE seq=?',(item['seq'],)).fetchone()[0]
        self.state.db.execute('DELETE FROM draft_baselines WHERE seq=?',(item['seq'],));file.write_text('Reviewed current draft\n')
        (directory/'unrelated-new-output.txt').write_text('Leave research output alone\n')
        self.state.transition(item['seq'],'record_blocked',result='Saved original answer');self.state.ledger.update(item,'record_blocked',activity='completed')
        event=self.event(s,'/status');event['event']['message']['message_id']='om_new_owner_review';self.state.ingest(event);owner=self.state.claim('control')
        drafts.approve_current(self.tracker,item,owner,['draft.txt'],'Owner requested repair; the current draft was inspected and privately snapshotted.')
        self.tracker.publish_code(s,item,push=False)
        self.assertEqual(self.state.db.execute('SELECT baseline FROM turn_records WHERE seq=?',(item['seq'],)).fetchone()[0],original)
        self.assertNotIn('unrelated-new-output.txt',self.raw(directory,'ls-tree','--name-only','HEAD'))
        row=self.state.db.execute('SELECT manifest FROM draft_reviews WHERE seq=?',(item['seq'],)).fetchone()
        data=json.loads(Path(row[0]).read_text());self.assertEqual(data['original_record']['phase'],'record_blocked')
        file.write_text('Changed after review\n')
        with self.assertRaisesRegex(Invalid,'changed again'):self.tracker.publish_code(s,item,push=False)

    def test_read_only_peer_turn_does_not_publish_or_block_on_background_output(self):
        a=self.bot();b=self.bot();self.state.db.execute('UPDATE peer_policy SET enabled=1')
        directory=Path(self.tracker.code_directory(b));(directory/'README.md').write_text('Prior working draft\n')
        message=peer_mail.send(self.state,self.tracker,a,b,'What result is saved?',source_seq=None)
        relay=self.relay();peer_mail.poll(relay)
        def run(session,text,**kwargs):
            (directory/'README.md').write_text('Background job updated the draft\n')
            (directory/'background-output.txt').write_text('A background artifact\n')
            return 'Existing results inspected'
        relay.host.run=run;self.assertEqual(relay.process_once('agent'),'complete')
        branch=self.tracker.intern(b)['code_branch']
        self.assertEqual(self.raw(self.remote,'show',branch+':README.md'),'Original project')
        self.assertNotIn('background-output.txt',self.raw(self.remote,'ls-tree','--name-only',branch))

    def test_owner_requested_note_records_real_observation_and_valid_git_history_once(self):
        from intern_personal.auto_recovery import observe
        from intern_personal.records import parse
        a=self.bot();b=self.bot();self.state.db.execute('UPDATE peer_policy SET enabled=1')
        note=peer_mail.send(self.state,self.tracker,a,b,'The saved result was delivered; preserve the research.',kind='note')
        relay=self.relay();self.state.ingest(self.event(a,'/mail process '+note['id'],mid='om_process_note'))
        self.assertEqual(relay.process_once('control'),'complete');peer_mail.poll(relay)
        saved=peer_mail.owned(self.state,b,note['id']);seq=saved['event_seq'];calls=[]
        directory=Path(self.tracker.code_directory(b));(directory/'background.txt').write_text('Unrelated research output\n')
        def run(session,text,**kwargs):
            calls.append(text);observe(self.state,session,'turn_note','inProgress',seq=seq)
            observe(self.state,session,'turn_note','completed',seq=seq)
            return 'Acknowledged the delivery; no new research started.'
        relay.host.run=run;self.assertEqual(relay.process_once('agent'),'complete')
        peer_mail.poll(relay);self.tracker.retry_pending()
        row=peer_mail.owned(self.state,b,note['id'])
        self.assertEqual((row['state'],row['reply_state'],row['reply_seq']),('delivered','none',None))
        self.assertIsNotNone(row['received_at']);self.assertIsNotNone(row['read_at'])
        branch=self.tracker.intern(b)['code_branch']
        document=self.raw(self.remote,'show',branch+':.intern_workspace/peer/'+note['id']+'.md')
        value=parse(document)['peer'];peer_mail.validate_record(value)
        self.assertIn('note_handled',[r['stage'] for r in value['receipts']])
        self.assertEqual(value['reply'],row['reply'])
        self.assertNotIn('background.txt',self.raw(self.remote,'ls-tree','--name-only',branch))
        self.assertFalse(self.state.db.execute('SELECT 1 FROM bot_tracking_pending').fetchone())
        self.assertEqual(len(calls),1);peer_mail.request_note(self.state,dict(self.state.db.execute("SELECT * FROM events WHERE message_id='om_process_note'").fetchone()),note['id'])
        peer_mail.poll(relay);self.assertEqual(peer_mail.owned(self.state,b,note['id'])['event_seq'],seq)


class GitDiagnosisTests(unittest.TestCase):
    def test_network_auth_ref_and_local_validation_are_distinguished_without_echoing_secrets(self):
        rows=[('Connection reset by peer',TrackingError,'network'),('Permission denied (publickey)',Invalid,'authentication'),
              ('non-fast-forward',Invalid,'changed branch'),('Intern Agent Git guard blocked operation: Invalid',Invalid,'validation'),
              ('fatal: index.lock exists',TrackingError,'local repository lock')]
        for stderr,kind,expected in rows:
            with self.subTest(stderr=stderr):
                error=failure('push',1,stderr+' https://example.invalid/?token=fixture-private-value')
                self.assertIsInstance(error,kind);self.assertIn(expected,str(error));self.assertNotIn('fixture-private-value',str(error))


if __name__=='__main__':unittest.main()
