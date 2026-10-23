"""A leased reply outbox independent of Git publication and Codex execution."""
import json
import time

from .core import Invalid


def schema(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS reply_outbox(
        seq INTEGER PRIMARY KEY,payload TEXT NOT NULL,state TEXT NOT NULL,
        after_state TEXT,receipt TEXT,error_type TEXT,created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS flow_incidents(
        id TEXT PRIMARY KEY,seq INTEGER NOT NULL,cause TEXT NOT NULL,state TEXT NOT NULL,
        created_at REAL NOT NULL,UNIQUE(seq,cause));
        CREATE TABLE IF NOT EXISTS flow_observation(seq INTEGER PRIMARY KEY,first_at REAL NOT NULL);''')


def finalize(relay,seq):
    state=relay.state
    row=state.db.execute('SELECT * FROM reply_outbox WHERE seq=?',(seq,)).fetchone()
    if not row or row['state']!='delivered' or not row['after_state']:return
    event=state.db.execute('SELECT * FROM events WHERE seq=?',(seq,)).fetchone()
    if not event or event['state'] in {'cancelled','cancelled_policy_changed'}:return
    from .turn_uploads import ready
    if event['role']=='agent':
        if not ready(state,seq):return
        # Older couriers persisted a pending-Git phase as the send callback.
        # A transport receipt cannot reopen a positively uploaded turn.
        if row['after_state']!='complete':
            state.db.execute("UPDATE reply_outbox SET after_state='complete' WHERE seq=? AND state='delivered'",(seq,))
            row=dict(row,after_state='complete')
    record=state.db.execute('SELECT phase FROM turn_records WHERE seq=?',(seq,)).fetchone()
    if event['state']==row['after_state'] and (row['after_state']!='complete' or not record or record['phase']=='complete'):return
    state.transition(seq,row['after_state'],receipt=row['receipt'])
    if row['after_state']=='complete' and event['role']=='agent':
        state.ledger.observe(dict(event),'complete')
        from .teams import complete
        complete(state,dict(event))
        state.db.execute('DELETE FROM sync_retries WHERE seq=?',(seq,))
        from .turn_uploads import ready,enqueue_status
        if not ready(state,seq):enqueue_status(state,state.session(event['session']))


def send(relay,item,after_state=None):
    state=relay.state
    state.db.execute('BEGIN IMMEDIATE')
    try:
        event=dict(state.db.execute('SELECT * FROM events WHERE seq=?',(item['seq'],)).fetchone())
        if event['role']=='agent' and after_state is None:after_state='complete'
        if event['role']=='agent' and after_state!='complete':
            state.db.execute('COMMIT');return event['state']
        if event['role']=='agent':
            from .turn_uploads import ready
            if not ready(state,event['seq']):state.db.execute('COMMIT');return event['state']
        if event['receipt']:
            if after_state:state.db.execute('UPDATE reply_outbox SET after_state=? WHERE seq=?',(after_state,item['seq']))
            if after_state:state.transition(item['seq'],after_state)
            state.db.execute('COMMIT');return after_state or event['state']
        if not state.authorized(event):state.db.execute('COMMIT');return 'cancelled_policy_changed'
        if not event['result']:state.db.execute('COMMIT');return event['state']
        row=state.db.execute('SELECT * FROM reply_outbox WHERE seq=?',(item['seq'],)).fetchone()
        if not row:
            from .quota import reply
            payload=reply(state,event)
            state.db.execute("INSERT INTO reply_outbox VALUES(?,?,'pending',?,NULL,NULL,?)",(item['seq'],payload,after_state,time.time()))
        elif after_state:
            state.db.execute('UPDATE reply_outbox SET after_state=? WHERE seq=?',(after_state,item['seq']))
        row=state.db.execute('SELECT * FROM reply_outbox WHERE seq=?',(item['seq'],)).fetchone()
        if row['state']=='uncertain':
            if after_state:state.transition(item['seq'],'uncertain_delivery')
            state.db.execute('COMMIT');return 'uncertain_delivery'
        if row['state']=='sending':state.db.execute('COMMIT');return 'preparing_reply'
        claimed=state.db.execute("UPDATE reply_outbox SET state='sending' WHERE seq=? AND state='pending'",(item['seq'],)).rowcount
        if claimed and row['after_state']:state.transition(item['seq'],'sending')
        state.db.execute('COMMIT')
    except BaseException:state.db.execute('ROLLBACK');raise
    if not claimed:
        finalize(relay,item['seq']);return after_state or event['state']
    try:
        receipt=relay.api.send(event['chat_id'],row['payload'],event['app_id']+':'+event['message_id'])
        if not isinstance(receipt,str) or not receipt:raise Invalid('Missing reply receipt')
    except Exception as error:
        relay.last_error=type(error).__name__
        state.db.execute("UPDATE reply_outbox SET state='uncertain',error_type=? WHERE seq=?",(type(error).__name__,item['seq']))
        current=state.db.execute('SELECT after_state FROM reply_outbox WHERE seq=?',(item['seq'],)).fetchone()
        if current['after_state']:state.transition(item['seq'],'uncertain_delivery')
        return 'uncertain_delivery'
    state.db.execute('BEGIN IMMEDIATE')
    try:
        state.db.execute("UPDATE reply_outbox SET state='delivered',receipt=?,error_type=NULL WHERE seq=?",(receipt,item['seq']))
        state.db.execute('UPDATE events SET receipt=? WHERE seq=?',(receipt,item['seq']))
        state.db.execute('COMMIT')
    except BaseException:state.db.execute('ROLLBACK');raise
    finalize(relay,item['seq'])
    return after_state or event['state']


def pump(relay):
    rows=relay.state.db.execute('''SELECT e.* FROM events e JOIN turn_records r ON r.seq=e.seq
        JOIN turn_uploads u ON u.seq=e.seq AND u.state='uploaded'
        LEFT JOIN reply_outbox o ON o.seq=e.seq WHERE e.role='agent' AND e.result IS NOT NULL
        AND e.receipt IS NULL AND e.state IN ('result_ready','preparing_reply','sync_pending','record_blocked')
        AND r.activity IN ('completed','failed','stopped') AND (o.seq IS NULL OR o.state='pending')
        ORDER BY e.seq LIMIT 10''').fetchall()
    for row in rows:
        outcome=relay.state.db.execute('SELECT status,event_seq FROM codex_outcomes WHERE session=?',(row['session'],)).fetchone()
        if outcome and outcome['event_seq']==row['seq'] and outcome['status']=='inProgress':continue
        active=False
        for table in ('terminal_attachments','console_views'):
            binding=relay.state.db.execute('SELECT snapshot FROM '+table+' WHERE session=?',(row['session'],)).fetchone()
            if binding:
                snap=json.loads(binding['snapshot'])
                active=active or bool(snap.get('active') and snap.get('event_seq')==row['seq']) or (snap.get('pending') or {}).get('seq')==row['seq']
        if not active:send(relay,dict(row))
    for row in relay.state.db.execute("SELECT seq FROM reply_outbox WHERE state='delivered' AND after_state IS NOT NULL").fetchall():
        finalize(relay,row['seq'])


def loop(relay):
    try:
        while not relay.stop.is_set():
            try:pump(relay)
            except Exception as error:relay.last_error=type(error).__name__
            relay.stop.wait(1)
    finally:relay.state.close()
