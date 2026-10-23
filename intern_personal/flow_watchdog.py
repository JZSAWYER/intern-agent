"""Durable diagnostics for unanswered messages, independent of Git and progress."""
import hashlib
import time


def alert(relay,item,cause,text,*,debug=False):
    db=relay.state.db
    db.execute('SAVEPOINT flow_alert')
    try:_alert(relay,item,cause,text,debug=debug)
    except BaseException:
        db.execute('ROLLBACK TO flow_alert');db.execute('RELEASE flow_alert');raise
    else:db.execute('RELEASE flow_alert')


def _alert(relay,item,cause,text,*,debug=False):
    state=relay.state
    if not item.get('session') or not state.authorized(item):return
    identity='flow_'+hashlib.sha256((str(item['seq'])+':'+cause).encode()).hexdigest()[:16]
    changed=state.db.execute("INSERT OR IGNORE INTO flow_incidents VALUES(?,?,?,'open',?)",(identity,item['seq'],cause,time.time())).rowcount
    if not changed:return
    from .auto_recovery import notice,settings
    notice(state,dict(id=identity,session=item['session']),'Turn '+str(item['seq'])+': '+text,'diagnostic')
    if debug:
        target=settings(state)['incident_session']
        prompt=('Relay flow incident '+identity+' in workspace '+item['workspace']+' ('+cause+'). '
            'Inspect the saved event and reply outbox, Codex observation and Git preflight; repair the actual blocker. '
            'Do not replay an executed prompt, erase a review, change credentials or reset research jobs. '
            'If Codex never received this event, restore its original queued request through the normal guarded dispatch. '
            'Reply with the diagnosis and repair result. No PR without owner instruction.')
        state.db.execute("INSERT OR IGNORE INTO bot_mailbox VALUES(?,?,?,?,'incident',?,'pending',NULL,?)",
            ('incident:'+identity,identity,item['session'],target,prompt,time.time()))
        controller=state.controller_session()
        if controller and controller['id']!=item['session']:
            notice(state,dict(id=identity,session=controller['id']),'Workspace '+item['workspace']+', turn '+str(item['seq'])+': '+text,'control-diagnostic')


def preflight(relay,item,error):
    from .core import Invalid
    text=('This message has not reached Codex because its Git/workspace preflight failed. '
        'The original request is saved; Recovery Sentinel will investigate. Use /status. No work was replayed.')
    alert(relay,item,'preflight_review' if isinstance(error,Invalid) else 'preflight_transport',text,debug=True)


def scan(relay,now=None):
    now=time.time() if now is None else now;state=relay.state
    rows=state.db.execute('''SELECT e.*,r.received_at,r.activity FROM events e JOIN turn_records r ON r.seq=e.seq
        WHERE e.role='agent' AND e.receipt IS NULL AND e.state IN
        ('queued','running','awaiting_terminal','preparing_reply','result_ready','sync_pending','record_blocked','uncertain_execution','uncertain_delivery')
        ORDER BY e.seq LIMIT 100''').fetchall()
    for row in rows:
        item=dict(row);age=now-(row['received_at'] or now)
        if age<60:continue
        session=state.session(row['session'])
        if not session or not state.authorized(item):continue
        box=state.db.execute('SELECT state FROM reply_outbox WHERE seq=?',(row['seq'],)).fetchone()
        if row['state']=='uncertain_delivery' or box and box['state']=='uncertain':
            alert(relay,item,'delivery_unconfirmed','Feishu delivery could not be confirmed. The full saved answer is retained; it will not be blindly resent. Recovery Sentinel has a delivery incident. Use /status.',debug=True)
        elif row['state']=='uncertain_execution':
            alert(relay,item,'execution_unconfirmed','Codex execution could not be confirmed. The request is preserved, and Recovery Sentinel will inspect the existing session before any continuation.',debug=True)
        elif row['state']=='record_blocked' and not row['result']:
            if not state.db.execute("SELECT 1 FROM flow_incidents WHERE seq=? AND cause LIKE 'preflight_%'",(row['seq'],)).fetchone():
                preflight(relay,item,ValueError())
        elif row['state']=='queued':
            active=state.ledger.busy(row['session'])
            if session['state']=='paused':
                alert(relay,item,'paused','Your message is saved, but this conversation is paused. Use /resume when ready; do not resend it.')
            elif active:
                alert(relay,item,'waiting_turn','Your message is queued behind unfinished work. It has not reached Codex yet. Use /status or /stop; do not resend it.')
            elif age>=180:
                state.db.execute('INSERT OR IGNORE INTO flow_observation VALUES(?,?)',(row['seq'],now))
                first=state.db.execute('SELECT first_at FROM flow_observation WHERE seq=?',(row['seq'],)).fetchone()[0]
                if now-first>=15:
                    alert(relay,item,'dispatch_wait','Your message is saved but has not reached Codex. Recovery Sentinel has a queue incident to investigate; do not resend it.',debug=True)
        elif row['result'] and row['activity'] in {'completed','failed','stopped'}:
            outcome=state.db.execute('SELECT event_seq,observed_at FROM codex_outcomes WHERE session=?',(row['session'],)).fetchone()
            state.db.execute('INSERT OR IGNORE INTO flow_observation VALUES(?,?)',(row['seq'],now))
            ended=outcome['observed_at'] if outcome and outcome['event_seq']==row['seq'] else state.db.execute('SELECT first_at FROM flow_observation WHERE seq=?',(row['seq'],)).fetchone()[0]
            if now-ended>=180:
                alert(relay,item,'answer_wait','Codex ended and its answer is saved, but final delivery is still pending. The independent reply sender and Recovery Sentinel will check delivery; no prompt will be replayed.',debug=True)
        elif row['activity'] in {'running','awaiting_approval','stop_requested'}:
            # Silence is not a fault. Forward real public commentary, actual
            # approval requests and ended failures through their own paths;
            # elapsed time alone never generates repeated Feishu messages.
            state.db.execute('DELETE FROM flow_observation WHERE seq=?',(row['seq'],))
    # A diagnostic can become obsolete while the operator is busy. Drop only incidents
    # not yet dispatched; preserve diagnosis turns already accepted by Codex.
    for incident in state.db.execute("SELECT f.*,e.state AS event_state,e.receipt FROM flow_incidents f JOIN events e ON e.seq=f.seq WHERE f.state='open'").fetchall():
        from .incident_lifecycle import obsolete
        recovered=obsolete(state,dict(incident))
        if recovered:
            state.db.execute("UPDATE flow_incidents SET state='resolved' WHERE id=?",(incident['id'],))
            state.db.execute("UPDATE bot_mailbox SET state='resolved' WHERE episode=? AND state='pending'",(incident['id'],))
