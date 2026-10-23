"""Best-effort milestones and public assistant commentary, never reasoning/tools."""
import hashlib
import json
import re
import time

MAX_TEXT_BYTES = 4000
MAX_PENDING = 32
MIN_INTERVAL = 2.0

LABELS={'uploading':'Codex finished. Saving code and validated records to GitHub; this turn is still finalizing.',
        'sync_pending':'Git upload is delayed. The final result is saved; this turn is not complete. Use /status for the blocker. Codex will not be rerun.',
        'observation_failed':'The relay could not read this Codex console. The session was not interrupted. Use /status or /doctor; no request will be rerun automatically.',
        'uncertain_execution':'Execution needs review. Use /status; the message will not be rerun automatically.'}


def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS progress_stage_history(seq INTEGER NOT NULL,stage TEXT NOT NULL,PRIMARY KEY(seq,stage))')
    db.executescript('''CREATE TABLE IF NOT EXISTS progress_settings(session TEXT PRIMARY KEY,enabled INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS progress_notices(seq INTEGER PRIMARY KEY,stage TEXT NOT NULL,state TEXT NOT NULL,created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS assistant_progress(
            key TEXT PRIMARY KEY,session TEXT NOT NULL,turn_id TEXT NOT NULL,source TEXT NOT NULL,
            event_seq INTEGER,app_id TEXT NOT NULL,chat_id TEXT NOT NULL,sender TEXT NOT NULL,
            text TEXT NOT NULL,state TEXT NOT NULL,created_at REAL NOT NULL,sent_at REAL,receipt TEXT);
        CREATE INDEX IF NOT EXISTS assistant_progress_pending ON assistant_progress(state,created_at);
        CREATE INDEX IF NOT EXISTS assistant_progress_session ON assistant_progress(session,turn_id);
        CREATE TABLE IF NOT EXISTS progress_panels(key TEXT PRIMARY KEY,session TEXT NOT NULL,event_seq INTEGER,
            turn_id TEXT,receipt TEXT,text TEXT NOT NULL,state TEXT NOT NULL,updated_at REAL NOT NULL,error_type TEXT);''')
    if 'presentation' not in {r[1] for r in db.execute('PRAGMA table_info(progress_settings)')}:
        db.execute("ALTER TABLE progress_settings ADD COLUMN presentation TEXT NOT NULL DEFAULT 'editable'")
    from .progress_policy import schema as policy_schema
    policy_schema(db)


def enabled(state, session):
    row=state.db.execute('SELECT enabled FROM progress_settings WHERE session=?',(session,)).fetchone()
    return not row or bool(row[0])


def set_mode(state, session, on, presentation='editable'):
    if presentation not in {'editable','messages'}:raise ValueError('Invalid progress presentation')
    state.db.execute('INSERT OR REPLACE INTO progress_settings(session,enabled,presentation) VALUES(?,?,?)',(session,int(on),presentation))
    if not on:
        state.db.execute("UPDATE assistant_progress SET state='expired',text='' WHERE session=? AND state='pending'",(session,))
        state.db.execute("UPDATE progress_notices SET state='claimed' WHERE state='pending' AND seq IN (SELECT seq FROM events WHERE session=?)",(session,))


def prose(state, text):
    """Sanitize explicit commentary; this never accepts a reasoning/tool event."""
    if not isinstance(text,str):return ''
    # A prose update can mention a command inline, but is not a code dump.
    text=re.sub(r'(?ms)^\s*(```|~~~).*?^\s*\1[^\n]*(?:\n|$)', '', text)
    text=re.sub(r'(?m)^\s*(?:```|~~~).*\Z','',text,flags=re.S)
    text=re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]','',text)
    text=re.sub(r'[\x00-\x08\x0b-\x1f\x7f]','',text).strip()
    if not text or text.startswith(('{','[')):return ''
    from .workspace_tracking import WorkspaceTracking
    text=WorkspaceTracking(state).clean(text)
    data=text.encode('utf-8')
    if len(data)>MAX_TEXT_BYTES:text=data[:MAX_TEXT_BYTES-3].decode('utf-8',errors='ignore').rstrip()+'…'
    return text


def enqueue(state, session, turn, item_id, text, *, source, event_seq=None):
    """No network calls. Native callers persist this with their rollout cursor."""
    if (source not in {'native','managed'} or not turn or not item_id
            or session['state']!='ready' or not enabled(state,session['id'])):return
    text=prose(state,text)
    if not text:return
    identity=state.identity()
    event=state.db.execute('SELECT * FROM events WHERE seq=?',(event_seq,)).fetchone() if event_seq is not None else None
    if event_seq is not None and (not event or event['session']!=session['id']):return
    key=hashlib.sha256(json.dumps([session['id'],turn,source,item_id],separators=(',',':')).encode()).hexdigest()
    state.db.execute('''INSERT OR IGNORE INTO assistant_progress
        (key,session,turn_id,source,event_seq,app_id,chat_id,sender,text,state,created_at)
        VALUES(?,?,?,?,?,?,?,?,?,'pending',?)''',
        (key,session['id'],turn,source,event_seq,event['app_id'] if event else identity['app_id'],
         event['chat_id'] if event else session['chat_id'],event['sender'] if event else identity['owner'],text,time.time()))
    # Bound a disconnected sender's backlog. Dedup keys survive discarded text.
    state.db.execute('''UPDATE assistant_progress SET state='expired',text='' WHERE key IN
        (SELECT key FROM assistant_progress WHERE session=? AND turn_id=? AND state='pending'
         ORDER BY created_at DESC,rowid DESC LIMIT -1 OFFSET ?)''',(session['id'],turn,MAX_PENDING))


def finish(state, session, turn):
    state.db.execute("UPDATE assistant_progress SET state='expired',text='' WHERE session=? AND turn_id=? AND state='pending'",(session['id'],turn))


def panel(relay, item, text, *, turn=None):
    """One durable editable message per turn; an ambiguous creation is never replayed."""
    setting=relay.state.db.execute('SELECT presentation FROM progress_settings WHERE session=?',(item['session'],)).fetchone()
    if (setting and setting[0]=='messages') or not hasattr(relay.api,'edit_progress'):
        return relay.api.progress(item['chat_id'],text,'progress:'+str(item.get('key') or item.get('seq'))+':'+str(item.get('stage','prose')))
    seq=item.get('event_seq',item.get('seq'))
    turn=turn or item.get('turn_id')
    db=relay.state.db
    if seq is None and turn:
        native=db.execute('SELECT event_seq FROM terminal_turns WHERE session=? AND turn_id=?',(item['session'],turn)).fetchone()
        if native:seq=native[0]
    if not turn and seq is not None:
        native=db.execute('SELECT turn_id FROM terminal_turns WHERE session=? AND event_seq=?',(item['session'],seq)).fetchone()
        if native:turn=native[0]
    row=db.execute('SELECT * FROM progress_panels WHERE session=? AND event_seq=?',(item['session'],seq)).fetchone() if seq is not None else None
    if not row and turn:row=db.execute('SELECT * FROM progress_panels WHERE session=? AND turn_id=?',(item['session'],turn)).fetchone()
    key=row['key'] if row else hashlib.sha256(json.dumps([item['session'],seq if seq is not None else turn]).encode()).hexdigest()
    if row and row['state'] in {'creating','uncertain','closed'}:return row['receipt']
    combined=(row['text']+'\n\n' if row else '')+text
    while len(combined.encode())>MAX_TEXT_BYTES and '\n\n' in combined:combined=combined.split('\n\n',1)[1]
    combined=prose(relay.state,combined)
    try:
        if row:
            relay.api.edit_progress(row['receipt'],combined)
            receipt=row['receipt']
            db.execute("UPDATE progress_panels SET text=?,event_seq=COALESCE(event_seq,?),state='open',updated_at=?,error_type=NULL WHERE key=?",(combined,seq,time.time(),key))
        else:
            db.execute("INSERT INTO progress_panels VALUES(?,?,?,?,NULL,?,'creating',?,NULL)",(key,item['session'],seq,turn,combined,time.time()))
            receipt=relay.api.progress(item['chat_id'],combined,'progress-panel:'+key)
            if not isinstance(receipt,str) or not receipt or ',' in receipt:raise ValueError('Invalid panel receipt')
            db.execute("UPDATE progress_panels SET receipt=?,state='open' WHERE key=?",(receipt,key))
        return receipt
    except Exception as error:
        db.execute('UPDATE progress_panels SET state=?,error_type=?,updated_at=? WHERE key=?',('edit_failed' if row else 'uncertain',type(error).__name__,time.time(),key))
        return None


def close_delivered(relay):
    row=relay.state.db.execute("""SELECT p.*,e.chat_id,e.sender,e.app_id,e.role FROM progress_panels p JOIN events e ON e.seq=p.event_seq
        WHERE p.state IN ('open','edit_failed') AND e.state='complete' ORDER BY p.updated_at LIMIT 1""").fetchone()
    if not row:return False
    item=dict(row)
    # Final delivery is independent of this optional panel edit.
    relay.state.db.execute("UPDATE progress_panels SET state='closed' WHERE key=?",(row['key'],))
    if enabled(relay.state,row['session']) and relay.state.authorized(item):
        from .turn_uploads import ready
        uploaded=relay.state.db.execute('SELECT historical FROM turn_uploads WHERE seq=?',(row['event_seq'],)).fetchone()
        suffix=('Saved historical records uploaded to GitHub; reply already delivered.' if uploaded and uploaded['historical'] else
                'Turn complete: code and records uploaded to GitHub; reply delivered.') if ready(relay.state,row['event_seq']) else 'Reply delivered.'
        try:relay.api.edit_progress(row['receipt'],prose(relay.state,row['text']+'\n\n'+suffix))
        except Exception as error:
            relay.state.db.execute('UPDATE progress_panels SET error_type=? WHERE key=?',(type(error).__name__,row['key']))
    return True


def managed(state, session, thread, turn, params):
    item=params.get('item',{})
    # Missing phase/identity is not enough evidence that text is public progress.
    if (params.get('threadId')!=thread or params.get('turnId')!=turn
            or item.get('type')!='agentMessage' or item.get('phase')!='commentary'):return
    row=state.db.execute("SELECT seq FROM events WHERE session=? AND role='agent' AND state='running' ORDER BY seq DESC LIMIT 1",(session['id'],)).fetchone()
    if row:enqueue(state,session,turn,item.get('id'),item.get('text'),source='managed',event_seq=row[0])


def native_text(value):
    if not isinstance(value,dict):return ''
    payload=value.get('payload',{})
    if (not isinstance(payload,dict) or value.get('type')!='response_item' or payload.get('type')!='message'
            or payload.get('role')!='assistant' or payload.get('phase')!='commentary'):return ''
    if not isinstance(payload.get('content'),list):return ''
    return '\n'.join(block.get('text','') for block in payload.get('content',[])
                     if isinstance(block,dict) and block.get('type')=='output_text' and isinstance(block.get('text'),str))


def deliver_commentary(relay):
    # One sender owns delivery. Claims are nevertheless conditional and durable:
    # an ambiguous API result or restart must never replay progress or Codex.
    rows=relay.state.db.execute("SELECT * FROM assistant_progress WHERE state='pending' ORDER BY created_at,rowid LIMIT 64").fetchall()
    for row in rows:
        item=dict(row);item['role']='agent';session=relay.state.session(item['session'])
        allowed=bool(session and session['state']=='ready' and enabled(relay.state,item['session'])
                     and session['chat_id']==item['chat_id'] and time.time()-item['created_at']<=120
                     and relay.state.authorized(item))
        if allowed and item['event_seq'] is not None:
            event=relay.state.db.execute('SELECT state FROM events WHERE seq=?',(item['event_seq'],)).fetchone()
            allowed=bool(event and event[0] in {'running','awaiting_terminal'} and not relay.state.ledger.stopped(item['event_seq']))
        if allowed and item['source']=='native':
            binding=relay.state.db.execute('SELECT snapshot FROM terminal_attachments WHERE session=? UNION ALL SELECT snapshot FROM console_views WHERE session=?',(item['session'],item['session'])).fetchone()
            allowed=bool(binding and json.loads(binding[0]).get('active')==item['turn_id'])
        elif allowed and item['source']=='managed':
            allowed=item['event_seq'] is not None
        if not allowed:
            relay.state.db.execute("UPDATE assistant_progress SET state='expired',text='' WHERE key=? AND state='pending'",(item['key'],))
            return True
        last=relay.state.db.execute('SELECT MAX(sent_at) FROM assistant_progress WHERE session=?',(item['session'],)).fetchone()[0]
        if last is not None and time.time()-last<MIN_INTERVAL:continue
        if not hasattr(relay.api,'progress'):return False
        claim=relay.state.db.execute("UPDATE assistant_progress SET state='claimed',sent_at=?,text='' WHERE key=? AND state='pending'",(time.time(),item['key']))
        if not claim.rowcount:continue
        try:print('[Intern Agent: '+session['workspace']+'] '+item['text'],flush=True)
        except Exception:pass
        try:
            receipt=panel(relay,item,item['text'])
            relay.state.db.execute("UPDATE assistant_progress SET state='sent',receipt=? WHERE key=?",(receipt if isinstance(receipt,str) else None,item['key']))
        except Exception:pass
        return True
    return False


def mark(relay,item,stage):
    if item['role']!='agent' or stage not in LABELS or not hasattr(relay.api,'progress'):return
    setting=relay.state.db.execute('SELECT enabled FROM progress_settings WHERE session=?',(item['session'],)).fetchone()
    if setting and not setting[0]:return
    if not relay.state.db.execute('INSERT OR IGNORE INTO progress_stage_history VALUES(?,?)',(item['seq'],stage)).rowcount:return
    relay.state.db.execute("INSERT INTO progress_notices VALUES(?,?,'pending',?) ON CONFLICT(seq) DO UPDATE SET stage=excluded.stage,state='pending',created_at=excluded.created_at WHERE progress_notices.stage!=excluded.stage",(item['seq'],stage,time.time()))


def deliver(relay):
    # Public assistant updates should not wait behind relay housekeeping.
    if deliver_commentary(relay):return True
    row=relay.state.db.execute("SELECT p.stage,p.created_at,e.* FROM progress_notices p JOIN events e ON e.seq=p.seq WHERE p.state='pending' ORDER BY p.created_at LIMIT 1").fetchone()
    if not row:return close_delivered(relay)
    item=dict(row)
    # Claim before send. Failed or ambiguous progress sends are not retried;
    # they never affect execution, final replies or /stop.
    claimed=relay.state.db.execute("UPDATE progress_notices SET state='claimed' WHERE seq=? AND stage=? AND state='pending'",(item['seq'],item['stage']))
    if not claimed.rowcount:return True
    if item['stage'] not in LABELS:return True  # Discard old queued start acknowledgements after upgrade.
    if item['state']=='complete' or time.time()-item['created_at']>120 or not enabled(relay.state,item['session']) or not relay.state.authorized(item):return True
    try:panel(relay,item,LABELS[item['stage']])
    except Exception:pass
    return True


def loop(relay):
    try:
        while not relay.stop.is_set():
            try:
                if not deliver(relay):relay.stop.wait(.5)
            except Exception:relay.stop.wait(1)
    finally:relay.state.close()
