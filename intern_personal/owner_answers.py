"""Forward an owner's public answer while a shared Codex turn keeps running.

An assistant final_answer item is visible output, not proof of turn completion.
The existing notice outbox owns transport; terminal completion still owns the
execution ledger, typing reaction, Git publication and task acceptance.
"""
import hashlib
import json
from pathlib import Path
import time

from .core import Invalid


def schema(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS steering_answer_scans(
        seq INTEGER PRIMARY KEY,cursor INTEGER NOT NULL,inode INTEGER NOT NULL,
        active TEXT NOT NULL,acknowledged INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS steering_answers(
        key TEXT PRIMARY KEY,session TEXT NOT NULL,turn_id TEXT NOT NULL,seq INTEGER NOT NULL,
        result TEXT NOT NULL,notice_id TEXT NOT NULL UNIQUE,end_cursor INTEGER NOT NULL,
        created_at REAL NOT NULL,UNIQUE(session,turn_id,result));
        CREATE TRIGGER IF NOT EXISTS link_owner_answer_completion AFTER INSERT ON terminal_turns
        BEGIN
            INSERT OR IGNORE INTO reply_outbox(seq,payload,state,after_state,receipt,error_type,created_at)
            SELECT NEW.event_seq,n.text,
                CASE n.state WHEN 'delivered' THEN 'delivered' WHEN 'uncertain' THEN 'uncertain' ELSE 'sending' END,
                NULL,n.receipt,CASE WHEN n.state='uncertain' THEN 'OwnerAnswerDeliveryUncertain' END,a.created_at
            FROM steering_answers a JOIN recovery_notices n ON n.id=a.notice_id
                JOIN events e ON e.seq=NEW.event_seq
            WHERE a.session=NEW.session AND a.turn_id=NEW.turn_id
                AND a.result=trim(replace(e.result,char(13)||char(10),char(10)),char(9)||char(10)||char(13)||' ')
                AND e.receipt IS NULL ORDER BY a.end_cursor DESC LIMIT 1;
            UPDATE events SET receipt=(SELECT receipt FROM reply_outbox WHERE seq=NEW.event_seq AND state='delivered')
                WHERE seq=NEW.event_seq AND receipt IS NULL;
        END;
        CREATE TRIGGER IF NOT EXISTS link_owner_answer_delivery AFTER UPDATE OF state,receipt ON recovery_notices
        WHEN NEW.state IN ('delivered','uncertain')
        BEGIN
            UPDATE reply_outbox SET state=NEW.state,receipt=NEW.receipt,
                error_type=CASE WHEN NEW.state='uncertain' THEN 'OwnerAnswerDeliveryUncertain' END
            WHERE seq IN (SELECT t.event_seq FROM steering_answers a
                JOIN terminal_turns t ON t.session=a.session AND t.turn_id=a.turn_id
                JOIN events e ON e.seq=t.event_seq
                WHERE a.notice_id=NEW.id
                    AND a.result=trim(replace(e.result,char(13)||char(10),char(10)),char(9)||char(10)||char(13)||' '))
                AND payload=NEW.text AND state IN ('sending','uncertain');
            UPDATE events SET receipt=NEW.receipt
            WHERE NEW.state='delivered' AND receipt IS NULL AND seq IN (
                SELECT o.seq FROM reply_outbox o JOIN terminal_turns t ON t.event_seq=o.seq
                JOIN steering_answers a ON a.session=t.session AND a.turn_id=t.turn_id
                WHERE a.notice_id=NEW.id
                    AND a.result=trim(replace(events.result,char(13)||char(10),char(10)),char(9)||char(10)||char(13)||' ')
                    AND o.payload=NEW.text AND o.state='delivered');
        END;''')


def normalized(text):
    return hashlib.sha256(text.replace('\r\n','\n').strip().encode()).hexdigest()


def collect(worker, row):
    """Read only post-dispatch public output; never start or resume Codex."""
    state=worker.state
    if row['cursor'] is None or row['inode'] is None:return
    if state.db.execute('SELECT 1 FROM terminal_turns WHERE session=? AND turn_id=?',
                        (row['session'],row['turn_id'])).fetchone():return
    event=dict(state.db.execute('SELECT * FROM events WHERE seq=?',(row['seq'],)).fetchone())
    session=state.session(row['session'])
    if (not session or session['state']!='ready' or session['thread_id']!=row['thread_id']
            or event['state']=='cancelled' or not state.authorized(event)):return
    bridge=worker.terminal if row['transport']=='native' else worker.host.observer
    config=bridge.config(session);path=Path(config['rollout']);info=path.stat()
    scan=state.db.execute('SELECT * FROM steering_answer_scans WHERE seq=?',(row['seq'],)).fetchone()
    cursor=scan['cursor'] if scan else row['cursor']
    if info.st_ino!=row['inode'] or (scan and scan['inode']!=info.st_ino) or info.st_size<cursor:
        raise Invalid('Owner answer transcript replaced or truncated')
    active=scan['active'] if scan else row['turn_id']
    acknowledged=bool(scan and scan['acknowledged'])
    expected=normalized(event['text']);answers=[]
    from .terminal_bridge import MAX_LINE,native_context_message
    from .workspace_tracking import WorkspaceTracking
    with path.open('rb') as stream:
        stream.seek(cursor)
        for _ in range(2000):
            line=stream.readline(MAX_LINE+1)
            if not line:break
            if len(line)>MAX_LINE:raise Invalid('Owner answer transcript record too large')
            if not line.endswith(b'\n'):break
            value=json.loads(line);payload=value.get('payload',{})
            kind=payload.get('type');end=stream.tell()
            if value.get('type')=='event_msg':
                if kind=='task_started':active=payload.get('turn_id') or ''
                elif kind in {'task_complete','turn_aborted'} and payload.get('turn_id')==row['turn_id']:
                    cursor=end;break
            elif value.get('type')=='response_item' and kind=='message' and active==row['turn_id']:
                if payload.get('role')=='user' and not native_context_message(payload):
                    text='\n'.join(x.get('text','') for x in payload.get('content',[]) if x.get('type')=='input_text')
                    if normalized(text)==expected:acknowledged=True
                elif acknowledged and payload.get('role')=='assistant' and payload.get('phase')=='final_answer':
                    text='\n'.join(x.get('text','') for x in payload.get('content',[]) if x.get('type')=='output_text')
                    if text.strip() and len(text.encode())<=1048576:
                        text=WorkspaceTracking(state).clean(text).replace('\r\n','\n').strip()
                        if text:answers.append((end,text))
            cursor=end
    state.db.execute('BEGIN IMMEDIATE')
    try:
        # A completed turn uses its ordinary result outbox. This check and the
        # notice insertion share the observer's transaction boundary.
        ended=state.db.execute('SELECT 1 FROM terminal_turns WHERE session=? AND turn_id=?',
                               (row['session'],row['turn_id'])).fetchone()
        if not ended:
            from .auto_recovery import notice
            from .quota import snapshot,format_line
            footer=format_line(snapshot(state,session))
            for end,text in answers:
                key=hashlib.sha256(json.dumps([row['session'],row['turn_id'],text],separators=(',',':')).encode()).hexdigest()
                notice_id='owner-answer-'+key+':answer'
                changed=state.db.execute('INSERT OR IGNORE INTO steering_answers VALUES(?,?,?,?,?,?,?,?)',
                    (key,row['session'],row['turn_id'],row['seq'],text,notice_id,end,time.time())).rowcount
                if changed:
                    notice(state,dict(id='owner-answer-'+key,session=row['session']),
                           text.rstrip()+'\n\n'+footer if footer else text,'answer')
        state.db.execute('INSERT OR REPLACE INTO steering_answer_scans VALUES(?,?,?,?,?)',
                         (row['seq'],cursor,info.st_ino,active,int(acknowledged)))
        state.db.execute('COMMIT')
    except BaseException:
        state.db.execute('ROLLBACK');raise
