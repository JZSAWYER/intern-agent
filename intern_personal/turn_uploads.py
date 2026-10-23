"""A prepared final Markdown snapshot becomes complete only after Git ack."""
import json
from pathlib import Path
import time

from .core import Invalid


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS turn_uploads(seq INTEGER PRIMARY KEY,session TEXT NOT NULL,
        scope TEXT NOT NULL,state TEXT NOT NULL,snapshot TEXT NOT NULL,code_head TEXT,records_head TEXT,
        created_at REAL NOT NULL,verified_at REAL,historical INTEGER NOT NULL DEFAULT 0)''')
    db.execute('''CREATE TABLE IF NOT EXISTS git_sync_errors(seq INTEGER PRIMARY KEY,error_type TEXT NOT NULL,
        reason TEXT NOT NULL,attempts INTEGER NOT NULL,first_at REAL NOT NULL,updated_at REAL NOT NULL,resolved_at REAL)''')


def failure(state,item,error):
    from .workspace_tracking import WorkspaceTracking
    reason=WorkspaceTracking(state).clean(str(error)).replace('\n',' ')[:280]
    previous=state.db.execute('SELECT * FROM git_sync_errors WHERE seq=?',(item['seq'],)).fetchone()
    count=previous['attempts']+1 if previous else 1
    state.db.execute('INSERT OR REPLACE INTO git_sync_errors VALUES(?,?,?,?,?,?,NULL)',
        (item['seq'],type(error).__name__,reason,count,previous['first_at'] if previous else time.time(),time.time()))
    return min(300,30*2**min(count-1,4))


def scope(binding):return str(Path(binding['metadata_path']).resolve())+'|'+binding['metadata_branch']


def ready(state,seq):
    return bool(state.db.execute("SELECT 1 FROM turn_uploads WHERE seq=? AND state='uploaded'",(seq,)).fetchone())


def observation(state,item):
    row=state.db.execute('SELECT * FROM turn_records WHERE seq=?',(item['seq'],)).fetchone()
    return bool(row and row['source']=='terminal' and not row['baseline'] and item['message_id'].startswith('local_terminal_')
        and item['result'] and row['activity'] in {'completed','failed','stopped'}
        and not state.db.execute('SELECT 1 FROM dispatch_attempts WHERE seq=?',(item['seq'],)).fetchone()
        and state.db.execute("SELECT 1 FROM terminal_turns WHERE event_seq=? AND status IN ('task_complete','turn_aborted') AND result=?",(item['seq'],item['result'])).fetchone())


def prepare(tracker,session,item,binding):
    state=tracker.state
    event=dict(state.db.execute('SELECT * FROM events WHERE seq=?',(item['seq'],)).fetchone())
    record=state.ledger.ensure_turn(event)
    if not event['result'] or not record or record['activity'] not in {'completed','failed','stopped'}:
        raise Invalid('Codex completion is not confirmed; Git finalization must wait')
    historic=observation(state,event);items=[(event,historic)]
    for row in state.db.execute("""SELECT e.* FROM events e JOIN turn_records r ON r.seq=e.seq
        WHERE e.session=? AND e.role='agent' AND e.result IS NOT NULL AND e.receipt IS NOT NULL
        AND e.state IN ('result_ready','reply_ready','sync_pending') AND r.source='terminal'
        AND r.baseline IS NULL AND e.seq!=? ORDER BY e.seq""",(session['id'],item['seq'])).fetchall():
        candidate=dict(row)
        if observation(state,candidate):items.append((candidate,True))
    parents={entry['seq'] for entry,_ in items}
    for row in state.db.execute("""SELECT e.*,s.parent_seq FROM events e JOIN steered_inputs s ON s.seq=e.seq
        JOIN turn_records r ON r.seq=e.seq WHERE e.session=? AND e.role='agent'
        AND e.result IS NOT NULL AND e.receipt IS NOT NULL AND e.state IN ('result_ready','reply_ready','sync_pending')
        AND s.state='completed' AND s.accepted_at IS NOT NULL AND r.baseline IS NULL""",(session['id'],)).fetchall():
        if row['parent_seq'] in parents:items.append((dict(row),True))
    for entry,is_history in items:
        if is_history:state.ledger.evidence(entry['seq'],
            'Historical completed observation: upload its saved records only. Preserve the missing original code baseline; do not stage current research files or replay Codex.')
    state.db.execute('BEGIN IMMEDIATE')
    try:
        for entry,is_history in items:
            if state.db.execute('SELECT 1 FROM turn_uploads WHERE seq=?',(entry['seq'],)).fetchone():continue
            saved=dict(state.db.execute('SELECT * FROM turn_records WHERE seq=?',(entry['seq'],)).fetchone())
            if saved['phase']=='record_blocked':continue
            saved.update(text=entry['text'],result=entry['result'],role=entry['role'],state='complete',phase='complete',sync='synced',
                         completed_at=saved['completed_at'] or time.time())
            state.db.execute("INSERT INTO turn_uploads VALUES(?,?,?,'prepared',?,?,NULL,?,NULL,?)",
                (entry['seq'],session['id'],scope(binding),json.dumps(saved),saved['code_end'],time.time(),int(is_history)))
        state.db.execute('COMMIT')
    except BaseException:state.db.execute('ROLLBACK');raise


def overlay(state,binding,turns):
    result={turn['seq']:turn for turn in turns}
    for row in state.db.execute('''SELECT * FROM turn_uploads WHERE scope=?
        AND (state='uploaded' OR session=?)''',(scope(binding),binding.get('publication_session',''))):
        if row['seq'] not in result:continue
        snapshot=json.loads(row['snapshot']);current=result[row['seq']]
        if snapshot['text']!=current['text'] or snapshot['result']!=current['result']:
            raise Invalid('Prepared Git record no longer matches the original request/result')
        result[row['seq']]=snapshot
    return [result[turn['seq']] for turn in turns]


def uploaded(tracker,session,item,binding):
    state=tracker.state
    pending=state.db.execute("SELECT * FROM turn_uploads WHERE scope=? AND session=? AND state='prepared'",(scope(binding),session['id'])).fetchall()
    if not pending:return
    root=binding['metadata_path'];branch=binding['metadata_branch'];head=tracker.git(root,'rev-parse','HEAD')
    code_branch=tracker.intern(session)['code_branch'];refs={'refs/heads/'+branch,'refs/heads/'+code_branch}
    remote=tracker.git(root,'ls-remote','--heads','origin',*sorted(refs),network=True)
    known={line.split()[1]:line.split()[0] for line in remote.splitlines() if len(line.split())==2}
    if known.get('refs/heads/'+branch)!=head:raise Invalid('GitHub did not confirm the exact prepared records commit')
    code_tip=known.get('refs/heads/'+code_branch)
    for row in pending:
        if row['code_head']:
            if not code_tip:raise Invalid('GitHub did not confirm the code branch')
            tracker.git(root,'merge-base','--is-ancestor',row['code_head'],code_tip)
    completed=[]
    state.db.execute('BEGIN IMMEDIATE')
    try:
        for row in pending:
            state.db.execute("UPDATE turn_uploads SET state='uploaded',records_head=?,verified_at=? WHERE seq=?",(head,time.time(),row['seq']))
            state.db.execute('UPDATE git_sync_errors SET resolved_at=? WHERE seq=?',(time.time(),row['seq']))
            if row['historical']:
                changed=state.db.execute("UPDATE events SET state='complete' WHERE seq=? AND receipt IS NOT NULL AND state IN ('result_ready','reply_ready','sync_pending','preparing_reply')",(row['seq'],)).rowcount
                if changed:
                    state.db.execute('DELETE FROM sync_retries WHERE seq=?',(row['seq'],));completed.append(row['seq'])
        state.db.execute('COMMIT')
    except BaseException:state.db.execute('ROLLBACK');raise
    for seq in completed:
        state.ledger.observe(dict(state.db.execute('SELECT * FROM events WHERE seq=?',(seq,)).fetchone()),'complete')


def committed(state,binding,head):
    if binding.get('publication_session'):
        state.db.execute("UPDATE turn_uploads SET records_head=? WHERE scope=? AND session=? AND state='prepared'",
            (head,scope(binding),binding['publication_session']))


def retry_commit(tracker,session,item,binding):
    """Push the exact already validated commit; do not rerender or restage."""
    row=tracker.state.db.execute("SELECT * FROM turn_uploads WHERE seq=? AND state='prepared'",(item['seq'],)).fetchone()
    if not row or not row['records_head']:return False
    root=binding['metadata_path'];branch=binding['metadata_branch']
    tracker.verify_origin(binding);tracker.verify_branch(root,branch)
    if tracker.git(root,'rev-parse','HEAD')!=row['records_head']:raise Invalid('The prepared Git branch advanced; review before retrying the saved upload')
    refs=['refs/heads/'+branch+':refs/heads/'+branch]
    if binding.get('metadata_mode')!='bot_branch' and not row['historical']:
        code=tracker.intern(session)['code_branch']
        if tracker.git(tracker.code_directory(session),'rev-parse','HEAD')!=row['code_head']:raise Invalid('The prepared code branch advanced; review before retrying')
        from .git_hooks import install
        install(tracker.state,root,[branch,code],management=True,base_branch=binding['base_branch'])
        refs.append('refs/heads/'+code+':refs/heads/'+code)
    tracker.audit_history(root,binding,management=binding.get('metadata_mode')!='bot_branch')
    tracker.git(root,'push','--atomic','origin',*refs,network=True)
    uploaded(tracker,session,item,binding)
    tracker.state.db.execute('DELETE FROM bot_tracking_pending WHERE session=?',(session['id'],))
    return True


def enqueue_status(state,session):
    if not session:return
    row=state.db.execute('SELECT metadata_mode FROM tracked_interns WHERE session=?',(session['id'],)).fetchone()
    if row and row['metadata_mode']=='bot_branch':
        state.db.execute('INSERT OR REPLACE INTO bot_tracking_pending VALUES(?,NULL,0)',(session['id'],))
    else:state.db.execute('INSERT OR REPLACE INTO tracking_pending VALUES(?,NULL,0)',(session['workspace'],))


def pending(state,session):
    return bool(state.db.execute("SELECT 1 FROM turn_uploads WHERE session=? AND state='prepared'",(session['id'],)).fetchone())
