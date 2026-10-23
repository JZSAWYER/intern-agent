"""File uploads wait for the same sender's next ordinary message, durably."""
import json
import time


def schema(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS incoming_file_batches(
        source_seq INTEGER PRIMARY KEY,app_id TEXT NOT NULL,session TEXT NOT NULL,
        thread_id TEXT NOT NULL,chat_id TEXT NOT NULL,sender TEXT NOT NULL,
        received_at REAL NOT NULL,state TEXT NOT NULL,bound_seq INTEGER);
        CREATE TABLE IF NOT EXISTS attachment_origins(
        seq INTEGER NOT NULL,ordinal INTEGER NOT NULL,source_seq INTEGER NOT NULL,
        source_ordinal INTEGER NOT NULL,message_id TEXT NOT NULL,PRIMARY KEY(seq,ordinal));
        CREATE INDEX IF NOT EXISTS incoming_file_batches_pending
            ON incoming_file_batches(session,sender,state,received_at);''')


def file_only(message):
    kind=message.get('message_type')
    if kind in {'file','image'}:return True
    if kind!='post':return False
    content=json.loads(message['content'])
    if 'content' not in content:
        content=next((v for v in content.values() if isinstance(v,dict) and 'content' in v),{})
    if str(content.get('title','')).strip():return False
    return not any(str(part.get('text','')).strip() or part.get('tag')=='a' and part.get('href')
        for row in content.get('content',[]) for part in row if part.get('tag') in {'text','a'})


def note(state,item,text,phase=None,**values):
    """Append within intake's write transaction; never alter owner input."""
    row=state.db.execute('SELECT * FROM turn_records WHERE seq=?',(item['seq'],)).fetchone()
    if not row:return
    evidence=json.loads(row['evidence'])
    if text not in evidence:evidence.append(text)
    revision=state.ledger.revision(row['workspace'],'turn',str(item['seq']),phase or 'evidence',
        conversation=row['conversation'],task=row['task_id'],turn='turn_'+str(item['seq']))
    values.update(evidence=json.dumps(evidence),revision=revision)
    if phase:values['phase']=phase
    state.db.execute('UPDATE turn_records SET '+','.join(key+'=?' for key in values)+' WHERE seq=?',
        (*values.values(),item['seq']))


def stage(state,item,at):
    session=state.session(item['session'])
    state.db.execute("INSERT INTO incoming_file_batches VALUES(?,?,?,?,?,?,?,'waiting',NULL)",
        (item['seq'],item['app_id'],item['session'],session['thread_id'],item['chat_id'],item['sender'],at))
    state.db.execute("UPDATE events SET state='awaiting_message' WHERE seq=?",(item['seq'],))
    # This reservation was created in the same intake transaction and has never
    # been sent. A file upload must not steer or start a Codex turn by itself.
    state.db.execute("DELETE FROM steered_inputs WHERE seq=? AND state='pending' AND cursor IS NULL AND accepted_at IS NULL",(item['seq'],))
    note(state,item,'File upload held for this sender\'s next text message; no Codex turn started.','waiting',activity='waiting')
    from .auto_recovery import notice
    count=len(pending(state,session,item['sender']))
    notice(state,dict(id='files-waiting-'+str(item['seq']),session=item['session']),
        str(count)+' file'+('s' if count!=1 else '')+' waiting. Send your message to use them together. /files pending lists them; /files clear discards them.','received')


def bind(state,item,at):
    session=state.session(item['session'])
    batches=state.db.execute('''SELECT b.*,e.message_id FROM incoming_file_batches b
        JOIN events e ON e.seq=b.source_seq WHERE b.state='waiting' AND e.state='awaiting_message'
        AND b.app_id=? AND b.session=? AND b.thread_id=? AND b.chat_id=? AND b.sender=?
        AND b.received_at<=? ORDER BY b.received_at,b.source_seq''',
        (item['app_id'],item['session'],session['thread_id'],item['chat_id'],item['sender'],at)).fetchall()
    if not batches:return
    own=state.db.execute('SELECT * FROM attachments WHERE seq=? ORDER BY ordinal',(item['seq'],)).fetchall()
    # A captioned post may contain files too. Earlier uploads precede that post's
    # resources; ordinal prefixes keep repeated filenames distinct on disk.
    state.db.execute('DELETE FROM attachments WHERE seq=?',(item['seq'],))
    ordinal=0
    for batch in batches:
        rows=state.db.execute('SELECT * FROM attachments WHERE seq=? ORDER BY ordinal',(batch['source_seq'],)).fetchall()
        for row in rows:
            state.db.execute('INSERT INTO attachments VALUES(?,?,?,?,?,NULL)',
                (item['seq'],ordinal,row['resource_key'],row['name'],row['type']))
            state.db.execute('INSERT INTO attachment_origins VALUES(?,?,?,?,?)',
                (item['seq'],ordinal,batch['source_seq'],row['ordinal'],batch['message_id']))
            ordinal+=1
        state.db.execute("UPDATE incoming_file_batches SET state='bound',bound_seq=? WHERE source_seq=?",(item['seq'],batch['source_seq']))
        state.db.execute("UPDATE events SET state='attachment_bound' WHERE seq=?",(batch['source_seq'],))
        state.db.execute("UPDATE recovery_notices SET state='cancelled' WHERE id=? AND state='pending'",('files-waiting-'+str(batch['source_seq'])+':received',))
        source=dict(state.db.execute('SELECT * FROM events WHERE seq=?',(batch['source_seq'],)).fetchone())
        note(state,source,'Upload attached to text turn '+str(item['seq'])+'; no separate Codex request.',
             'complete',activity='completed',completed_at=time.time())
    for row in own:
        state.db.execute('INSERT INTO attachments VALUES(?,?,?,?,?,?)',
            (item['seq'],ordinal,row['resource_key'],row['name'],row['type'],row['sha256']))
        ordinal+=1
    note(state,item,str(ordinal)+' files attached in upload order to this unchanged message.')


def pending(state,session,sender=None):
    if not session:return []
    query='''SELECT a.name,b.source_seq,b.thread_id,b.received_at FROM incoming_file_batches b
        JOIN attachments a ON a.seq=b.source_seq WHERE b.session=? AND b.state='waiting' '''
    args=[session['id']]
    if sender:query+=' AND b.sender=?';args.append(sender)
    return [dict(row) for row in state.db.execute(query+' ORDER BY b.received_at,b.source_seq,a.ordinal',args)]


def describe(state,session,sender=None):
    rows=pending(state,session,sender)
    if not rows:return 'No files waiting for your next message.'
    return ('Files waiting for your next message:\n'+'\n'.join(str(i+1)+'. '+r['name']+
        (' (from a previous thread; use /files clear)' if r['thread_id']!=session['thread_id'] else '') for i,r in enumerate(rows))+
        '\nSend an ordinary message to submit this conversation\'s files together. Commands do not consume them.')


def clear(state,session,sender):
    if not session:return 'Choose a workspace conversation.'
    state.db.execute('BEGIN IMMEDIATE')
    try:
        rows=state.db.execute("SELECT source_seq FROM incoming_file_batches WHERE session=? AND sender=? AND state='waiting'",(session['id'],sender)).fetchall()
        for row in rows:
            state.db.execute("UPDATE incoming_file_batches SET state='discarded' WHERE source_seq=?",(row['source_seq'],))
            state.db.execute("UPDATE events SET state='cancelled' WHERE seq=? AND state='awaiting_message'",(row['source_seq'],))
            state.db.execute("UPDATE recovery_notices SET state='cancelled' WHERE id=? AND state='pending'",('files-waiting-'+str(row['source_seq'])+':received',))
            item=dict(state.db.execute('SELECT * FROM events WHERE seq=?',(row['source_seq'],)).fetchone())
            note(state,item,'Waiting upload discarded by its sender before any Codex request.','cancelled',activity='stopped',completed_at=time.time())
        state.db.execute('COMMIT')
    except BaseException:state.db.execute('ROLLBACK');raise
    return 'Cleared waiting uploads. Your active Codex turn continues.'
