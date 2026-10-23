"""Retire obsolete, unsubmitted diagnostics without discarding their reviews."""
import json
import time


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS incident_resolutions(
        mailbox TEXT PRIMARY KEY,seq INTEGER NOT NULL,reason TEXT NOT NULL,
        original_event TEXT NOT NULL,original_turn TEXT,proof TEXT NOT NULL,at REAL NOT NULL)''')
    db.executescript('''CREATE TABLE IF NOT EXISTS incident_operator_policy(singleton INTEGER PRIMARY KEY,session TEXT);
        CREATE TRIGGER IF NOT EXISTS reject_obsolete_incident_claim
        BEFORE UPDATE OF state ON bot_mailbox
        WHEN OLD.kind='incident' AND OLD.state='pending' AND NEW.state='queued'
            AND NEW.recipient=(SELECT session FROM incident_operator_policy WHERE singleton=1)
            AND EXISTS(SELECT 1 FROM flow_incidents f JOIN events root ON root.seq=f.seq
                WHERE f.id=NEW.episode AND (
                    (root.receipt IS NOT NULL AND f.cause IN
                        ('dispatch_wait','answer_wait','delivery_unconfirmed','preflight_review','preflight_transport'))
                    OR (f.cause='dispatch_wait' AND (
                        root.state IN ('cancelled','cancelled_policy_changed','archived')
                        OR EXISTS(SELECT 1 FROM terminal_turns t WHERE t.event_seq=root.seq)
                        OR EXISTS(SELECT 1 FROM codex_outcomes o WHERE o.event_seq=root.seq)
                        OR EXISTS(SELECT 1 FROM steered_inputs s WHERE s.seq=root.seq AND s.state IN ('accepted','completed'))))))
        BEGIN SELECT RAISE(IGNORE); END;''')
    db.executescript('''CREATE VIEW IF NOT EXISTS unsubmitted_preflight_events AS
        SELECT e.seq FROM events e LEFT JOIN dispatch_attempts d ON d.seq=e.seq
        WHERE e.result IS NULL AND e.receipt IS NULL
            AND ((e.state='queued' AND (d.seq IS NULL OR d.stage='preflight'))
                OR (e.state IN ('running','record_blocked') AND d.stage='preflight'))
            AND NOT EXISTS(SELECT 1 FROM terminal_turns t WHERE t.event_seq=e.seq)
            AND NOT EXISTS(SELECT 1 FROM codex_outcomes o WHERE o.event_seq=e.seq)
            AND NOT EXISTS(SELECT 1 FROM event_failures f WHERE f.seq=e.seq)
            AND NOT EXISTS(SELECT 1 FROM reply_outbox o WHERE o.seq=e.seq)
            AND NOT EXISTS(SELECT 1 FROM steered_inputs s WHERE s.seq=e.seq)
            AND NOT EXISTS(SELECT 1 FROM console_views c WHERE c.session=e.session AND
                (json_extract(c.snapshot,'$.pending.seq')=e.seq OR
                    (json_extract(c.snapshot,'$.active') IS NOT NULL AND json_extract(c.snapshot,'$.event_seq')=e.seq)))
            AND NOT EXISTS(SELECT 1 FROM terminal_attachments n WHERE n.session=e.session AND
                (json_extract(n.snapshot,'$.pending.seq')=e.seq OR
                    (json_extract(n.snapshot,'$.active') IS NOT NULL AND json_extract(n.snapshot,'$.event_seq')=e.seq)));
        CREATE TRIGGER IF NOT EXISTS retain_unsubmitted_queue_incident
        BEFORE UPDATE OF state ON flow_incidents
        WHEN OLD.state='open' AND NEW.state='resolved' AND OLD.cause='dispatch_wait'
            AND EXISTS(SELECT 1 FROM unsubmitted_preflight_events p WHERE p.seq=OLD.seq)
            AND EXISTS(SELECT 1 FROM bot_mailbox b WHERE b.episode=OLD.id AND b.kind='incident'
                AND b.recipient=(SELECT session FROM incident_operator_policy WHERE singleton=1))
        BEGIN SELECT RAISE(IGNORE); END;
        CREATE TRIGGER IF NOT EXISTS retain_unsubmitted_incident_mail
        BEFORE UPDATE OF state ON bot_mailbox
        WHEN OLD.kind='incident' AND OLD.state='pending' AND NEW.state='resolved'
            AND NEW.recipient=(SELECT session FROM incident_operator_policy WHERE singleton=1)
            AND EXISTS(SELECT 1 FROM flow_incidents f JOIN unsubmitted_preflight_events p ON p.seq=f.seq
                WHERE f.id=NEW.episode AND f.cause='dispatch_wait')
        BEGIN SELECT RAISE(IGNORE); END;''')


def configure(state,target):
    row=state.db.execute('SELECT session FROM incident_operator_policy WHERE singleton=1').fetchone()
    if not row or row[0]!=target:
        state.db.execute('INSERT INTO incident_operator_policy VALUES(1,?) ON CONFLICT(singleton) DO UPDATE SET session=excluded.session',(target,))


def obsolete(state,flow):
    root=state.db.execute('SELECT * FROM events WHERE seq=?',(flow['seq'],)).fetchone()
    if not root:return False
    if flow['cause']=='dispatch_wait':
        if root['receipt'] or root['state'] in {'cancelled','cancelled_policy_changed','archived'}:return True
        # A claim and its Git/database preflight are not a Codex acceptance.
        return bool(state.db.execute('SELECT 1 FROM terminal_turns WHERE event_seq=?',(root['seq'],)).fetchone()
            or state.db.execute('SELECT 1 FROM codex_outcomes WHERE event_seq=?',(root['seq'],)).fetchone()
            or state.db.execute("SELECT 1 FROM steered_inputs WHERE seq=? AND state IN ('accepted','completed')",(root['seq'],)).fetchone())
    return bool(root['receipt'] and flow['cause'] in {
        'answer_wait','delivery_unconfirmed','preflight_review','preflight_transport'})


def unsubmitted(state,event,*,include_claimed_preflight=False):
    """Only a never-claimed queue entry or a durable preflight witness is safe."""
    states={'queued','record_blocked'}|({'running'} if include_claimed_preflight else set())
    if event['state'] not in states or event['result'] or event['receipt']:return False
    proof=state.db.execute('SELECT stage FROM dispatch_attempts WHERE seq=?',(event['seq'],)).fetchone()
    if proof and proof['stage']!='preflight':return False
    if not proof and event['state']!='queued':return False  # Legacy review needs a manual transcript audit.
    for table,column in [('terminal_turns','event_seq'),('codex_outcomes','event_seq'),
                         ('event_failures','seq'),('reply_outbox','seq'),('steered_inputs','seq')]:
        if state.db.execute('SELECT 1 FROM '+table+' WHERE '+column+'=?',(event['seq'],)).fetchone():return False
    for table in ['terminal_attachments','console_views']:
        row=state.db.execute('SELECT snapshot FROM '+table+' WHERE session=?',(event['session'],)).fetchone()
        saved=json.loads(row['snapshot']) if row else {}
        if (saved.get('pending') or {}).get('seq')==event['seq'] or saved.get('active') and saved.get('event_seq')==event['seq']:return False
    return True


def _retire(state,mail,event,proof):
    """Caller holds the dispatch write lock and has proved no transport entry."""
    turn=state.db.execute('SELECT * FROM turn_records WHERE seq=?',(event['seq'],)).fetchone()
    reason='Source incident resolved before this diagnostic reached Codex; obsolete diagnostic cancelled without execution.'
    state.db.execute('INSERT INTO incident_resolutions VALUES(?,?,?,?,?,?,?)',
        (mail['id'],event['seq'],reason,json.dumps(dict(event)),json.dumps(dict(turn)) if turn else None,json.dumps(proof),time.time()))
    state.db.execute("UPDATE events SET state='cancelled' WHERE seq=?",(event['seq'],))
    state.db.execute("UPDATE bot_mailbox SET state='resolved' WHERE id=?",(mail['id'],))
    # Keep the failed phase, sync flag, code_start/baseline and original evidence.
    # Append the resolution instead of rewriting the historical review as success.
    if turn:
        evidence=json.loads(turn['evidence']);evidence.append(reason)
        revision=state.ledger.revision(turn['workspace'],'turn',str(event['seq']),'evidence',
            conversation=turn['conversation'],task=turn['task_id'],turn='turn_'+str(event['seq']))
        state.db.execute('UPDATE turn_records SET evidence=?,revision=? WHERE seq=?',
            (json.dumps(evidence),revision,event['seq']))
    if state.db.execute('SELECT 1 FROM tracked_interns WHERE session=? AND published=1',(event['session'],)).fetchone():
        state.db.execute('INSERT OR IGNORE INTO bot_tracking_pending VALUES(?,NULL,0)',(event['session'],))


def reconcile(state):
    """Old receivers can use this through the independent singleton helper."""
    from .auto_recovery import settings,notice
    target=settings(state)['incident_session']
    configure(state,target)
    if not target:return
    candidates=state.db.execute('''SELECT b.* FROM bot_mailbox b JOIN flow_incidents f ON f.id=b.episode
        JOIN events root ON root.seq=f.seq WHERE b.kind='incident' AND b.recipient=?
        AND (b.state IN ('pending','queued') OR (b.state='resolved' AND b.event_seq IS NULL
            AND f.cause='dispatch_wait' AND root.state IN ('queued','running','record_blocked') AND root.receipt IS NULL))
        ORDER BY b.created_at''',(target,)).fetchall()
    if not candidates:return
    state.db.execute('BEGIN IMMEDIATE')
    try:
        for saved in candidates:
            mail=state.db.execute('SELECT * FROM bot_mailbox WHERE id=?',(saved['id'],)).fetchone()
            if mail['state'] not in {'pending','queued','resolved'}:continue
            flow=state.db.execute('SELECT * FROM flow_incidents WHERE id=?',(mail['episode'],)).fetchone()
            root=state.db.execute('SELECT * FROM events WHERE seq=?',(flow['seq'],)).fetchone()
            session=state.session(target)
            if not root or not state.authorized(dict(root)) or not session or session['state']!='ready':continue
            if mail['state']=='resolved':
                # An older receiver prematurely closed a queue incident at
                # claim(), then its preflight failed. Restore that same mail,
                # only when the request is still provably unsubmitted.
                if flow['cause']!='dispatch_wait' or mail['event_seq'] is not None or not unsubmitted(state,root,include_claimed_preflight=True):continue
                state.db.execute("UPDATE flow_incidents SET state='open' WHERE id=?",(flow['id'],))
                state.db.execute("UPDATE bot_mailbox SET state='pending' WHERE id=?",(mail['id'],))
                mail=state.db.execute('SELECT * FROM bot_mailbox WHERE id=?',(mail['id'],)).fetchone()
            if obsolete(state,flow):
                state.db.execute("UPDATE flow_incidents SET state='resolved' WHERE id=? AND state='open'",(flow['id'],))
                if mail['state']=='pending' and mail['event_seq'] is None:
                    state.db.execute("UPDATE bot_mailbox SET state='resolved' WHERE id=?",(mail['id'],));continue
                event=state.db.execute('SELECT * FROM events WHERE seq=?',(mail['event_seq'],)).fetchone()
                if (event and event['session']==target and event['role']=='agent'
                        and event['message_id']=='bot-'+mail['id'] and event['text']==mail['text']
                        and state.authorized(dict(event)) and unsubmitted(state,event)):
                    _retire(state,mail,event,dict(kind='unsubmitted',source_incident=flow['id']))
            else:
                blocker=state.db.execute('''SELECT e.seq FROM events e WHERE e.session=?
                    AND e.state IN ('record_blocked','uncertain_execution','uncertain_delivery')
                    AND e.receipt IS NULL ORDER BY e.seq LIMIT 1''',(target,)).fetchone()
                if blocker:
                    text=('Recovery Sentinel received this incident, but its own unresolved turn '+str(blocker['seq'])+
                        ' blocks dispatch. The incident is saved and has not reached its Codex. Owner review is required; Debug Bot will not be interrupted.')
                    notice(state,dict(id=flow['id'],session=root['session']),text,'operator-blocked-'+str(blocker['seq']))
                    notice(state,dict(id='operator-blocked-'+target+'-'+str(blocker['seq']),session=target),text,'health')
        state.db.execute('COMMIT')
    except BaseException:state.db.execute('ROLLBACK');raise
