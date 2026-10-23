"""A proven pre-start busy refusal is waiting input, never a completed answer."""
import json
import os
from pathlib import Path
import time

from .core import Invalid

LEGACY_BUSY='This conversation is already running in its Codex terminal; finish or /stop it first'


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS busy_input_deferrals(
        seq INTEGER PRIMARY KEY,result TEXT,receipt TEXT,outbox TEXT,baseline TEXT,
        at REAL NOT NULL,reason TEXT NOT NULL)''')


def defer(state,item,turn=None,*,legacy=False):
    """Called only after the adapter proves that turn/start was not attempted."""
    session=state.session(item['session'])
    state.db.execute('BEGIN IMMEDIATE')
    try:
        event=dict(state.db.execute('SELECT * FROM events WHERE seq=?',(item['seq'],)).fetchone())
        record=state.db.execute('SELECT * FROM turn_records WHERE seq=?',(item['seq'],)).fetchone()
        attempt=state.db.execute('SELECT * FROM dispatch_attempts WHERE seq=?',(item['seq'],)).fetchone()
        if (not attempt or attempt['stage']!='dispatching' or not record or record['phase']=='complete'
                or state.db.execute('SELECT 1 FROM terminal_turns WHERE event_seq=?',(item['seq'],)).fetchone()
                or state.db.execute('SELECT 1 FROM codex_outcomes WHERE event_seq=?',(item['seq'],)).fetchone()):
            raise Invalid('Busy-input deferral lacks a proven unstarted request')
        if any('task_complete' in x or 'turn/completed' in x for x in json.loads(record['evidence'])):
            raise Invalid('Observed execution cannot be repeated as a busy deferral')
        if legacy and (event['result']!=LEGACY_BUSY or event['state'] not in {'result_ready','sync_pending'}):
            raise Invalid('Legacy busy refusal changed before reconciliation')
        if state.ledger.stopped(item['seq']) or not state.authorized(event):
            raise Invalid('Stopped or unauthorized input cannot be resumed')
        target=state.db.execute('SELECT * FROM steering_targets WHERE session=?',(session['id'],)).fetchone()
        owner=state.identity()
        steer=bool(target and target['turn_id'] and (turn is None or target['turn_id']==turn)
            and target['thread_id']==session['thread_id'] and event['sender']==owner['owner']
            and event['message_id'].startswith(('om_','queued:'))
            and not state.db.execute('SELECT 1 FROM attachments WHERE seq=?',(item['seq'],)).fetchone()
            and not state.db.execute('SELECT 1 FROM peer_event_scope WHERE seq=?',(item['seq'],)).fetchone())
        outbox=state.db.execute('SELECT * FROM reply_outbox WHERE seq=?',(item['seq'],)).fetchone()
        state.db.execute('INSERT OR IGNORE INTO busy_input_deferrals VALUES(?,?,?,?,?,?,?)',
            (item['seq'],event['result'],event['receipt'],json.dumps(dict(outbox)) if outbox else None,
             record['baseline'],time.time(),'Explicit adapter refusal before turn/start; no Codex input accepted'))
        # Archive the false legacy completion/transport before reopening the
        # unchanged input. An old delivery receipt must not swallow its real reply.
        state.db.execute('DELETE FROM reply_outbox WHERE seq=?',(item['seq'],))
        state.db.execute('DELETE FROM reply_quota WHERE seq=?',(item['seq'],))
        phase='awaiting_terminal' if steer else 'queued'
        state.db.execute('UPDATE events SET state=?,result=NULL,receipt=NULL WHERE seq=?',(phase,item['seq']))
        state.db.execute('UPDATE dispatch_attempts SET stage=?,retry_at=?,updated_at=? WHERE seq=?',
            ('busy_before_start' if steer else 'preflight',0 if steer else time.time()+5,time.time(),item['seq']))
        if steer:
            prior=state.db.execute('SELECT state FROM steered_inputs WHERE seq=?',(item['seq'],)).fetchone()
            if prior and prior['state']!='fallback':raise Invalid('Steering already owns this input')
            state.db.execute('''INSERT INTO steered_inputs(seq,session,thread_id,turn_id,parent_seq,transport,state,created_at,updated_at)
                VALUES(?,?,?,?,?,?,'pending',?,?) ON CONFLICT(seq) DO UPDATE SET
                thread_id=excluded.thread_id,turn_id=excluded.turn_id,parent_seq=excluded.parent_seq,
                transport=excluded.transport,state='pending',cursor=NULL,inode=NULL,accepted_at=NULL,
                updated_at=excluded.updated_at,error_type=NULL WHERE steered_inputs.state='fallback' ''',
                (item['seq'],session['id'],session['thread_id'],target['turn_id'],target['parent_seq'],target['transport'],time.time(),time.time()))
        state.db.execute('COMMIT')
    except BaseException:
        state.db.execute('ROLLBACK');raise
    values=dict(activity='waiting',sync='pending',completed_at=None)
    if not steer:values.update(baseline=None,code_start=None,code_end=None)
    state.ledger.update(event,'awaiting_terminal' if steer else 'waiting',**values)
    state.ledger.evidence(item['seq'],'Pre-start busy refusal archived: original input was not accepted by Codex; restored unchanged for guarded delivery, not replayed.')
    return phase


def recover(worker):
    """Repair the old adapter only after its sole conversation worker moved on."""
    state=worker.state
    rows=state.db.execute('''SELECT e.* FROM events e JOIN dispatch_attempts d ON d.seq=e.seq
        JOIN turn_records r ON r.seq=e.seq
        WHERE e.role='agent' AND e.state IN ('result_ready','sync_pending') AND e.result=?
            AND substr(e.message_id,1,3)='om_' AND d.stage='dispatching' AND r.phase!='complete'
            AND NOT EXISTS(SELECT 1 FROM busy_input_deferrals b WHERE b.seq=e.seq)
            AND NOT EXISTS(SELECT 1 FROM terminal_turns t WHERE t.event_seq=e.seq)
            AND NOT EXISTS(SELECT 1 FROM codex_outcomes o WHERE o.event_seq=e.seq)
            AND ((r.revision>0 AND r.projected_revision>=r.revision) OR EXISTS(
                SELECT 1 FROM events newer JOIN dispatch_attempts n ON n.seq=newer.seq
                WHERE newer.session=e.session AND newer.role='agent' AND newer.seq>e.seq AND n.updated_at>d.updated_at))
        ORDER BY e.seq LIMIT 8''',(LEGACY_BUSY,)).fetchall()
    from .terminal_bridge import MAX_LINE,native_context_message
    from .owner_answers import normalized
    for row in rows:
        event=dict(row);session=state.session(event['session']);view=worker.host.observer
        # Refuse even a historical identical match rather than risk repeating
        # executed work. This rare legacy path never infers safety from idle/age.
        seen=False
        try:
            config=view.config(session);info=Path(config['rollout']).stat()
            if info.st_ino!=config['rollout_inode'] or info.st_uid!=os.getuid():continue
            with open(config['rollout'],'rb') as stream:
                while True:
                    line=stream.readline(MAX_LINE+1)
                    if not line:break
                    if len(line)>MAX_LINE or not line.endswith(b'\n'):seen=True;break
                    value=json.loads(line);p=value.get('payload',{})
                    if (value.get('type')=='response_item' and p.get('type')=='message' and p.get('role')=='user'
                            and not native_context_message(p)):
                        text='\n'.join(x.get('text','') for x in p.get('content',[]) if x.get('type')=='input_text')
                        if normalized(text)==normalized(event['text']):seen=True;break
            if not seen:defer(state,event,legacy=True)
        except (Invalid,OSError,ValueError,KeyError):
            continue  # Unverifiable input cannot block other bots' delivery.
