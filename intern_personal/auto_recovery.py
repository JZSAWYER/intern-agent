"""Bounded same-thread recovery and a durable, owner-authorized bot mailbox."""
import json
import time
import uuid

from .core import Invalid

CONTINUE=('Continue the unfinished task from this conversation after the transient connection or capacity failure. '
          'Inspect saved files and existing background jobs first. Do not repeat completed actions or launch duplicate jobs.')
OPEN={'waiting','queued','running'}


class RecoverySkipped(Invalid):
    """The recovery guard proved that a queued continuation is obsolete."""


def schema(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS codex_outcomes(
        session TEXT PRIMARY KEY,thread_id TEXT NOT NULL,turn_id TEXT NOT NULL,status TEXT NOT NULL,
        category TEXT,event_seq INTEGER,observed_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS recovery_episodes(
        id TEXT PRIMARY KEY,session TEXT NOT NULL,thread_id TEXT NOT NULL,root_turn TEXT NOT NULL,
        root_seq INTEGER NOT NULL,task_id TEXT,branch TEXT,state TEXT NOT NULL,attempts INTEGER NOT NULL,
        due_at REAL NOT NULL,last_turn TEXT NOT NULL,category TEXT NOT NULL,created_at REAL NOT NULL,
        updated_at REAL NOT NULL,UNIQUE(session,thread_id,root_turn));
        CREATE TABLE IF NOT EXISTS recovery_runs(
        seq INTEGER PRIMARY KEY,episode TEXT NOT NULL,attempt INTEGER NOT NULL,expected_turn TEXT NOT NULL,
        UNIQUE(episode,attempt));
        CREATE TABLE IF NOT EXISTS bot_mailbox(
        id TEXT PRIMARY KEY,episode TEXT NOT NULL,sender TEXT NOT NULL,recipient TEXT,kind TEXT NOT NULL,
        text TEXT NOT NULL,state TEXT NOT NULL,event_seq INTEGER,created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS recovery_notices(
        id TEXT PRIMARY KEY,session TEXT NOT NULL,text TEXT NOT NULL,state TEXT NOT NULL,
        receipt TEXT,created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS recovery_session_settings(session TEXT PRIMARY KEY,enabled INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS recovery_rearms(episode TEXT PRIMARY KEY,ceiling INTEGER NOT NULL);''')
    # Early local drafts used a per-episode uniqueness constraint. An explicit
    # repair can rearm another bounded cycle, which needs its own mailbox entry.
    definition=db.execute("SELECT sql FROM sqlite_master WHERE name='bot_mailbox'").fetchone()[0]
    if 'UNIQUE(episode,kind)' in definition:
        db.executescript('''BEGIN IMMEDIATE;
            ALTER TABLE bot_mailbox RENAME TO bot_mailbox_draft;
            CREATE TABLE bot_mailbox(id TEXT PRIMARY KEY,episode TEXT NOT NULL,sender TEXT NOT NULL,
                recipient TEXT,kind TEXT NOT NULL,text TEXT NOT NULL,state TEXT NOT NULL,
                event_seq INTEGER,created_at REAL NOT NULL);
            INSERT INTO bot_mailbox SELECT * FROM bot_mailbox_draft;
            DROP TABLE bot_mailbox_draft;COMMIT;''')
    # Quiet healthy work stays quiet, including while an older receiver finishes
    # an active Codex turn. The database guard applies immediately without a
    # receiver restart and keeps actual incident/recovery notices unchanged.
    db.executescript('''CREATE TRIGGER IF NOT EXISTS suppress_flow_heartbeats
        BEFORE INSERT ON recovery_notices WHEN substr(NEW.id,1,10)='heartbeat_'
        BEGIN SELECT RAISE(IGNORE); END;
        UPDATE recovery_notices SET state='suppressed'
        WHERE substr(id,1,10)='heartbeat_' AND state='pending';''')


def validate(config):
    value=config.get('auto_recovery',{})
    if not isinstance(value,dict):raise Invalid('Invalid automatic recovery configuration')
    enabled=value.get('enabled',False);maximum=value.get('max_attempts',3)
    delays=value.get('backoff_seconds',[30,120,300])
    if (type(enabled) is not bool or type(maximum) is not int or not 1<=maximum<=5
            or not isinstance(delays,list) or len(delays)<maximum
            or any(type(x) is not int or not 1<=x<=3600 for x in delays)):
        raise Invalid('Recovery needs a boolean enabled flag, 1-5 attempts and bounded backoff seconds')
    target=value.get('incident_session',value.get('debug_session'))
    if target is not None and (not isinstance(target,str) or not target.startswith('s_')):raise Invalid('Choose a registered recovery operator')
    return dict(enabled=enabled,max_attempts=maximum,backoff_seconds=delays,incident_session=target)


def settings(state):return validate(state.config())


def enabled(state,session):
    row=state.db.execute('SELECT enabled FROM recovery_session_settings WHERE session=?',(session,)).fetchone()
    return bool(row[0]) if row else settings(state)['enabled']


def classify(error):
    """Consume only an authoritative Codex error; never retain its raw text."""
    if isinstance(error,str):info=None;message=error
    elif isinstance(error,dict):info=error.get('codexErrorInfo');message=error.get('message','')
    else:return None
    kind=next(iter(info),'') if isinstance(info,dict) else info if isinstance(info,str) else ''
    canonical=kind.replace('_','').lower()
    terminal={'usagelimitexceeded','sessionbudgetexceeded','contextwindowexceeded','unauthorized',
        'badrequest','sandboxerror','cyberpolicy','misalignmentpolicyviolation','toomanydenials'}
    if canonical in terminal:return None
    detail=info.get(kind,{}) if isinstance(info,dict) else {}
    code=detail.get('httpStatusCode') if isinstance(detail,dict) else None
    if code in {401,403,400,404,422}:return None
    if code==429 or canonical in {'serveroverloaded','ratelimitexceeded','flexunavailable'}:return 'capacity'
    if code in {500,502,503,504} or canonical=='internalservererror':return 'service'
    if canonical in {'responsestreamconnectionfailed','responsestreamdisconnected','responsetoomanyfailedattempts','httpconnectionfailed'}:return 'network'
    if canonical not in {'','other'}:return None
    text=message.lower() if isinstance(message,str) else ''
    if any(p in text for p in ('unauthorized','authentication failed','usage limit','quota exceeded','billing','context window exceeded')):return None
    if any(p in text for p in ('overloaded','over capacity','temporarily unavailable','capacity exceeded','rate limit')):return 'capacity'
    if any(p in text for p in ('stream disconnected','network','connection reset','connection closed',
            'connection error','connection failed','timed out','timeout','error decoding response body')):return 'network'
    return None


def observe(state,session,turn,status,error=None,seq=None,at=None):
    if not session or not session.get('thread_id') or not turn or status not in {'inProgress','completed','failed','interrupted'}:return
    state.db.execute('''INSERT INTO codex_outcomes VALUES(?,?,?,?,?,?,?) ON CONFLICT(session) DO UPDATE SET
        thread_id=excluded.thread_id,turn_id=excluded.turn_id,status=excluded.status,category=excluded.category,
        event_seq=CASE WHEN excluded.event_seq IS NOT NULL THEN excluded.event_seq
            WHEN excluded.thread_id=codex_outcomes.thread_id AND excluded.turn_id=codex_outcomes.turn_id
            THEN codex_outcomes.event_seq ELSE NULL END,observed_at=excluded.observed_at
        WHERE codex_outcomes.thread_id!=excluded.thread_id OR excluded.observed_at>=codex_outcomes.observed_at''',
        (session['id'],session['thread_id'],turn,status,classify(error) if status=='failed' else None,seq,time.time() if at is None else at))
    if status=='failed' and seq and classify(error) and enabled(state,session['id']):
        current=state.db.execute('SELECT * FROM codex_outcomes WHERE session=?',(session['id'],)).fetchone()
        if current and current['turn_id']==turn:ensure_episode(state,session,dict(current))


def ensure_episode(state,s,outcome):
    if state.db.execute("SELECT 1 FROM recovery_episodes WHERE session=? AND thread_id=? AND (state IN ('waiting','queued','running') OR root_turn=? OR last_turn=?)",
            (s['id'],s['thread_id'],outcome['turn_id'],outcome['turn_id'])).fetchone():return
    root=state.db.execute('SELECT * FROM events WHERE seq=?',(outcome['event_seq'],)).fetchone()
    if not root or not state.authorized(dict(root)) or s['state']!='ready':return
    policy=settings(state);intern=state.db.execute('SELECT code_branch FROM tracked_interns WHERE session=?',(s['id'],)).fetchone()
    task=state.ledger.selection(s['id']);now=time.time();identity='recovery_'+uuid.uuid4().hex[:12]
    state.db.execute("INSERT OR IGNORE INTO recovery_episodes VALUES(?,?,?,?,?,?,?,'waiting',0,?,?,?,?,?)",
        (identity,s['id'],s['thread_id'],outcome['turn_id'],outcome['event_seq'],task['id'] if task else None,
         intern['code_branch'] if intern else None,now+policy['backoff_seconds'][0],outcome['turn_id'],outcome['category'],now,now))
    ep=dict(state.db.execute('SELECT * FROM recovery_episodes WHERE session=? AND thread_id=? AND root_turn=?',(s['id'],s['thread_id'],outcome['turn_id'])).fetchone())
    notice(state,ep,'Transient '+ep['category']+' failure. Same-conversation recovery will try in '+str(policy['backoff_seconds'][0])+' seconds (up to '+str(policy['max_attempts'])+' attempts). /stop cancels it.','scheduled')


def busy(relay,session):
    if session['id'] in relay.host.active or relay.state.ledger.busy(session['id']):return True
    for bridge in (relay.terminal,getattr(relay.host,'observer',None)):
        if bridge and bridge.binding(session):
            snapshot=bridge.poll(session)
            if snapshot.get('active') or snapshot.get('pending'):return True
    return False


def notice(state,episode,text,key,at=None):
    state.db.execute("INSERT OR IGNORE INTO recovery_notices VALUES(?,?,?,'pending',NULL,?)",
        (episode['id']+':'+key,episode['session'],text,time.time() if at is None else at))


def cancel(state,session,reason='cancelled'):
    rows=state.db.execute("SELECT id FROM recovery_episodes WHERE session=? AND state IN ('waiting','queued','running')",(session,)).fetchall()
    for row in rows:
        state.db.execute('UPDATE recovery_episodes SET state=?,updated_at=? WHERE id=?',(reason,time.time(),row['id']))
        state.db.execute("UPDATE events SET state='cancelled',result='Automatic continuation cancelled before execution.' WHERE state='queued' AND seq IN (SELECT seq FROM recovery_runs WHERE episode=?)",(row['id'],))
    return len(rows)


def protected_typing(state,seq):
    return bool(state.db.execute("SELECT 1 FROM recovery_episodes WHERE root_seq=? AND state IN ('waiting','queued','running')",(seq,)).fetchone())


def guard(relay,item):
    row=relay.state.db.execute('''SELECT r.*,e.* FROM recovery_runs r JOIN recovery_episodes e ON e.id=r.episode WHERE r.seq=?''',(item['seq'],)).fetchone()
    mailbox=relay.state.db.execute("SELECT * FROM bot_mailbox WHERE event_seq=? AND kind='incident'",(item['seq'],)).fetchone()
    if not row and not mailbox:return
    s=relay.state.session(item['session'])
    if row:
        if not enabled(relay.state,s['id']) or row['state'] not in OPEN or s['thread_id']!=row['thread_id']:
            cancel(relay.state,s['id'],'superseded')
            raise RecoverySkipped('Automatic continuation was cancelled or its conversation changed')
        task=relay.state.ledger.selection(s['id'])
        intern=relay.tracker.intern(s) if hasattr(relay.tracker,'intern') else None
        if (task['id'] if task else None)!=row['task_id'] or (intern and intern['code_branch']!=row['branch']):
            cancel(relay.state,s['id'],'superseded')
            raise RecoverySkipped('Task or code branch changed; automatic continuation cancelled')
        if relay.terminal.binding(s):
            relay.terminal.poll(s)
            latest=relay.state.db.execute('SELECT * FROM codex_outcomes WHERE session=?',(s['id'],)).fetchone()
        else:latest=relay.host.turn_state(s)
        if not latest or latest['turn_id']!=row['expected_turn'] or latest['status']!='failed':
            cancel(relay.state,s['id'],'superseded')
            raise RecoverySkipped('The failed turn is no longer current; continuation cancelled')
        relay.state.db.execute("UPDATE recovery_episodes SET state='running',updated_at=? WHERE id=?",(time.time(),row['episode']))
    elif mailbox['recipient']!=s['id'] or settings(relay.state)['incident_session']!=s['id']:
        relay.state.db.execute("UPDATE bot_mailbox SET state='owner_review' WHERE id=?",(mailbox['id'],))
        raise RecoverySkipped('Incident recipient changed')


class Recovery:
    def __init__(self,relay):self.relay=relay;self.state=relay.state;self.last_error=None;self.bootstrapped=set()

    def bootstrap(self,s):
        if s['id'] in self.bootstrapped:return
        if self.relay.terminal.binding(s):return  # Native observation never attaches another backend.
        if self.state.db.execute('SELECT 1 FROM codex_outcomes WHERE session=?',(s['id'],)).fetchone():
            self.bootstrapped.add(s['id']);return
        latest=self.relay.host.turn_state(s)
        self.bootstrapped.add(s['id'])
        if not latest or latest['status']!='failed':return
        old=self.state.db.execute('SELECT event_seq FROM terminal_turns WHERE session=? AND turn_id=?',(s['id'],latest['turn_id'])).fetchone()
        if not old:return
        observe(self.state,s,latest['turn_id'],'failed',latest.get('error'),old['event_seq'])

    def scan(self):
        self.last_error=None
        policy=settings(self.state)
        for s in self.state.sessions():
            if s['state']!='ready' or not s.get('thread_id') or not enabled(self.state,s['id']):
                cancel(self.state,s['id']);continue
            try:
                stale=self.state.db.execute("SELECT 1 FROM recovery_episodes WHERE session=? AND thread_id!=? AND state IN ('waiting','queued','running')",(s['id'],s['thread_id'])).fetchone()
                if stale:cancel(self.state,s['id'],'superseded')
                self.bootstrap(s)
                outcome=self.state.db.execute('SELECT * FROM codex_outcomes WHERE session=?',(s['id'],)).fetchone()
                if not outcome or outcome['thread_id']!=s['thread_id']:continue
                episode=self.state.db.execute("SELECT * FROM recovery_episodes WHERE session=? AND thread_id=? AND state IN ('waiting','queued','running') ORDER BY created_at DESC LIMIT 1",(s['id'],s['thread_id'])).fetchone()
                if episode:
                    root=self.state.db.execute('SELECT * FROM events WHERE seq=?',(episode['root_seq'],)).fetchone()
                    if root and self.state.authorized(dict(root)):self.relay.begin_typing(dict(root))
                    self.advance(s,dict(episode),dict(outcome),policy)
                elif outcome['status']=='failed' and outcome['category'] and outcome['event_seq']:
                    seen=self.state.db.execute('SELECT 1 FROM recovery_episodes WHERE session=? AND thread_id=? AND (root_turn=? OR last_turn=?)',(s['id'],s['thread_id'],outcome['turn_id'],outcome['turn_id'])).fetchone()
                    if not seen:self.begin(s,dict(outcome),policy)
            except Exception as error:self.last_error=type(error).__name__
        from .flow_watchdog import scan as flow_scan
        flow_scan(self.relay)
        self.mailbox(policy)
        self.notifications()

    def begin(self,s,outcome,policy):
        ensure_episode(self.state,s,outcome)

    def advance(self,s,ep,outcome,policy):
        if outcome['turn_id']!=ep['last_turn']:
            run=self.state.db.execute('SELECT e.* FROM recovery_runs r JOIN events e ON e.seq=r.seq WHERE r.episode=? ORDER BY r.attempt DESC LIMIT 1',(ep['id'],)).fetchone()
            if not run or outcome['event_seq']!=run['seq']:
                cancel(self.state,s['id'],'superseded');return
            if outcome['status']=='completed':
                self.state.db.execute("UPDATE recovery_episodes SET state='resolved',last_turn=?,updated_at=? WHERE id=?",(outcome['turn_id'],time.time(),ep['id']))
                notice(self.state,ep,'Same-conversation recovery completed. Saved work and background jobs were preserved.','resolved');return
            if outcome['status']=='interrupted':cancel(self.state,s['id']);return
            if outcome['status']=='failed':
                ep.update(last_turn=outcome['turn_id'],category=outcome['category'] or 'non_transient')
                self.state.db.execute('UPDATE recovery_episodes SET last_turn=?,category=?,updated_at=? WHERE id=?',(ep['last_turn'],ep['category'],time.time(),ep['id']))
                rearm=self.state.db.execute('SELECT ceiling FROM recovery_rearms WHERE episode=?',(ep['id'],)).fetchone()
                ceiling=rearm['ceiling'] if rearm else policy['max_attempts']
                if not outcome['category'] or ep['attempts']>=ceiling:
                    self.escalate(s,ep);return
                delay=policy['backoff_seconds'][ep['attempts']%policy['max_attempts']]
                due=time.time()+delay
                self.state.db.execute("UPDATE recovery_episodes SET state='waiting',due_at=? WHERE id=?",(due,ep['id']))
                notice(self.state,ep,'Recovery attempt '+str(ep['attempts'])+' failed; next attempt waits '+str(delay)+' seconds.','retry-'+str(ep['attempts']))
                return
        if ep['state']=='queued' or ep['state']=='running':
            run=self.state.db.execute('SELECT e.* FROM recovery_runs r JOIN events e ON e.seq=r.seq WHERE r.episode=? ORDER BY r.attempt DESC LIMIT 1',(ep['id'],)).fetchone()
            if run and run['state'] in {'record_blocked','sync_pending','uncertain_execution','uncertain_delivery','cancelled_policy_changed'}:
                if outcome['status']!='inProgress':self.escalate(s,ep,'tracking_or_transport_blocker')
            return
        if time.time()<ep['due_at'] or busy(self.relay,s):return
        if self.state.db.execute("SELECT 1 FROM events WHERE session=? AND role='agent' AND state='queued'",(s['id'],)).fetchone():
            cancel(self.state,s['id'],'superseded');return
        root=self.state.db.execute('SELECT * FROM events WHERE seq=?',(ep['root_seq'],)).fetchone()
        if not root or not self.state.authorized(dict(root)):cancel(self.state,s['id']);return
        if root['state'] in {'uncertain_execution','uncertain_delivery'} or not root['receipt']:return
        self.queue_run(s,ep)

    def queue_run(self,s,ep):
        self.state.db.execute('BEGIN IMMEDIATE')
        try:
            current=self.state.db.execute('SELECT * FROM recovery_episodes WHERE id=?',(ep['id'],)).fetchone()
            if current['state']!='waiting':self.state.db.execute('COMMIT');return
            rearm=self.state.db.execute('SELECT ceiling FROM recovery_rearms WHERE episode=?',(ep['id'],)).fetchone()
            if current['attempts']>=(rearm['ceiling'] if rearm else settings(self.state)['max_attempts']):
                self.state.db.execute('COMMIT');self.escalate(s,dict(current));return
            attempt=current['attempts']+1
            seq=self.event(s,CONTINUE,'auto-resume:'+ep['id']+':'+str(attempt))
            self.state.db.execute('INSERT INTO recovery_runs VALUES(?,?,?,?)',(seq,ep['id'],attempt,current['last_turn']))
            self.state.db.execute("UPDATE recovery_episodes SET state='queued',attempts=?,updated_at=? WHERE id=?",(attempt,time.time(),ep['id']))
            self.state.db.execute('COMMIT')
        except BaseException:self.state.db.execute('ROLLBACK');raise
        self.state.ledger.evidence(seq,'Owner-authorized recovery '+ep['id']+', attempt '+str(attempt)+', follows recorded turn '+str(ep['root_seq'])+'; original request and failed turn remain unchanged.')

    def event(self,s,text,key):
        owner=self.state.identity()
        self.state.db.execute("INSERT OR IGNORE INTO events(app_id,message_id,chat_id,sender,role,workspace,session,text,state) VALUES(?,?,?,?,'agent',?,?,?,'queued')",
            (owner['app_id'],key,s['chat_id'],owner['owner'],s['workspace'],s['id'],text))
        item=dict(self.state.db.execute('SELECT * FROM events WHERE app_id=? AND message_id=?',(owner['app_id'],key)).fetchone())
        self.state.ledger.ensure_turn(item,time.time(),'local')
        return item['seq']

    def escalate(self,s,ep,reason=None):
        self.state.db.execute('SAVEPOINT recovery_escalation')
        try:self._escalate(s,ep,reason)
        except BaseException:
            self.state.db.execute('ROLLBACK TO recovery_escalation');self.state.db.execute('RELEASE recovery_escalation');raise
        else:self.state.db.execute('RELEASE recovery_escalation')

    def _escalate(self,s,ep,reason=None):
        self.state.db.execute("UPDATE recovery_episodes SET state='escalated',updated_at=? WHERE id=?",(time.time(),ep['id']))
        target=settings(self.state)['incident_session']
        text=('Relay incident '+ep['id']+' from workspace '+s['workspace']+': '+str(ep['attempts'])+
              ' bounded continuation attempts ended without recovery ('+(reason or ep['category'])+'). '
              'Investigate connectivity and relay state for this existing conversation. Preserve its thread, workspace, background jobs and credentials; '
              'do not repeat research actions or reset running sessions. Report your diagnosis and any repair through this incident reply. '
              'After fixing a transient cause, use the relay recovery retry control with this incident ID to rearm one bounded recovery cycle. No PR without owner instruction.')
        self.state.db.execute("INSERT OR IGNORE INTO bot_mailbox VALUES(?,?,?,?,'incident',?,'pending',NULL,?)",
            ('incident:'+ep['id']+':'+str(ep['attempts']),ep['id'],s['id'],target,text,time.time()))
        destination='Recovery Sentinel has a durable incident to investigate.' if target and target!=s['id'] else 'Owner review is needed; the recovery operator cannot escalate to itself.'
        notice(self.state,ep,'Recovery stopped after '+str(ep['attempts'])+' attempts. '+destination+' No session was replaced.','escalated-'+str(ep['attempts']))

    def mailbox(self,policy):
        from .incident_lifecycle import reconcile
        reconcile(self.state)
        for message in self.state.db.execute("SELECT * FROM bot_mailbox WHERE state='pending' AND kind='incident'").fetchall():
            root=self.state.db.execute('SELECT e.* FROM events e WHERE e.seq IN (SELECT root_seq FROM recovery_episodes WHERE id=? UNION ALL SELECT seq FROM flow_incidents WHERE id=?)',(message['episode'],message['episode'])).fetchone()
            if not root or not self.state.authorized(dict(root)):
                self.state.db.execute("UPDATE bot_mailbox SET state='owner_review' WHERE id=?",(message['id'],));continue
            target=self.state.session(message['recipient'])
            if not target or target['id']!=policy['incident_session']:
                self.state.db.execute("UPDATE bot_mailbox SET state='owner_review' WHERE id=?",(message['id'],));continue
            if target['state']!='ready':continue
            if target['id']==message['sender']:
                self.state.db.execute("UPDATE bot_mailbox SET state='owner_review' WHERE id=?",(message['id'],));continue
            if busy(self.relay,target):continue
            if self.state.db.execute("SELECT 1 FROM events WHERE session=? AND role='agent' AND state='queued'",(target['id'],)).fetchone():continue
            self.state.db.execute('BEGIN IMMEDIATE')
            try:
                claimed=self.state.db.execute("UPDATE bot_mailbox SET state='queued' WHERE id=? AND state='pending'",(message['id'],))
                if claimed.rowcount:
                    seq=self.event(target,message['text'],'bot-'+message['id'])
                    self.state.db.execute('UPDATE bot_mailbox SET event_seq=? WHERE id=?',(seq,message['id']))
                self.state.db.execute('COMMIT')
            except BaseException:self.state.db.execute('ROLLBACK');raise
        for message in self.state.db.execute("SELECT * FROM bot_mailbox WHERE kind='incident' AND state='queued'").fetchall():
            # An operator turn can itself need recovery. Follow only its proven,
            # resolved continuation chain, never an unrelated newer turn.
            seq=message['event_seq'];seen=set()
            while seq not in seen:
                seen.add(seq)
                result=self.state.db.execute('SELECT e.result,e.receipt,r.activity FROM events e JOIN turn_records r ON r.seq=e.seq WHERE e.seq=?',(seq,)).fetchone()
                if result and result['activity']=='completed' and result['result'] and result['receipt']:break
                recovered=self.state.db.execute("SELECT id FROM recovery_episodes WHERE root_seq=? AND state='resolved'",(seq,)).fetchone()
                run=self.state.db.execute('SELECT seq FROM recovery_runs WHERE episode=? ORDER BY attempt DESC LIMIT 1',(recovered['id'],)).fetchone() if recovered else None
                if not run:result=None;break
                seq=run['seq']
            else:result=None
            if not result:continue
            text='Recovery Sentinel update for '+message['episode']+':\n'+self.relay.tracker.clean(result['result'])[:16000]
            self.state.db.execute("INSERT OR IGNORE INTO bot_mailbox VALUES(?,?,?,?,'reply',?,'pending',NULL,?)",
                ('reply:'+message['id'],message['episode'],message['recipient'],message['sender'],text,time.time()))
            original=self.state.db.execute('SELECT * FROM recovery_episodes WHERE id=?',(message['episode'],)).fetchone()
            ep=dict(original) if original else dict(id=message['episode'],session=message['sender'])
            notice(self.state,ep,text,'reply:'+message['id'])
            self.state.db.execute("UPDATE bot_mailbox SET state='handled' WHERE id=?",(message['id'],))
            self.state.db.execute("UPDATE flow_incidents SET state='handled' WHERE id=?",(message['episode'],))

    def notifications(self):
        for row in self.state.db.execute("SELECT * FROM recovery_notices WHERE state='pending' ORDER BY created_at LIMIT 5").fetchall():
            if row['id'].startswith('heartbeat_'):
                self.state.db.execute("UPDATE recovery_notices SET state='suppressed' WHERE id=? AND state='pending'",(row['id'],))
                continue
            session=self.state.session(row['session'])
            if not session:continue
            owner=self.state.identity()
            item=dict(session=session['id'],sender=owner['owner'],app_id=owner['app_id'],chat_id=session['chat_id'],role='agent')
            if not self.state.authorized(item):continue
            if not self.state.db.execute("UPDATE recovery_notices SET state='sending' WHERE id=? AND state='pending'",(row['id'],)).rowcount:continue
            try:
                receipt=self.relay.api.send(session['chat_id'],row['text'],'recovery-notice:'+row['id'])
                if not isinstance(receipt,str) or not receipt:raise Invalid('Recovery notice has no delivery receipt')
                self.state.db.execute("UPDATE recovery_notices SET state='delivered',receipt=? WHERE id=?",(receipt,row['id']))
                if ':reply:' in row['id']:
                    self.state.db.execute("UPDATE bot_mailbox SET state='delivered' WHERE id=? AND kind='reply'",(row['id'].split(':',1)[1],))
            except Exception:self.state.db.execute("UPDATE recovery_notices SET state='uncertain' WHERE id=?",(row['id'],))

    def loop(self):
        try:
            while not self.relay.stop.is_set():
                try:self.scan()
                except Exception as error:self.last_error=type(error).__name__
                self.relay.stop.wait(5)
        finally:self.state.close()


def status(state,session=None):
    query='SELECT id,session,state,attempts,category,due_at FROM recovery_episodes'
    rows=state.db.execute(query+(' WHERE session=?' if session else '')+' ORDER BY created_at DESC LIMIT 20',(session,) if session else ()).fetchall()
    messages=state.db.execute('SELECT id,episode,sender,recipient,kind,state,event_seq FROM bot_mailbox'+
        (' WHERE sender=? OR recipient=?' if session else '')+' ORDER BY created_at DESC LIMIT 20',(session,session) if session else ()).fetchall()
    return {'enabled':enabled(state,session) if session else settings(state)['enabled'],'episodes':[dict(r) for r in rows],
        'inbox':[dict(r) for r in messages],
        'notices':dict(state.db.execute('SELECT state,count(*) FROM recovery_notices GROUP BY state').fetchall()),
        'mailbox':dict(state.db.execute('SELECT state,count(*) FROM bot_mailbox GROUP BY state').fetchall())}


def command(relay,item,tokens):
    state=relay.state;target=item.get('session');tokens=list(tokens)
    if tokens and tokens[0].startswith('s_'):
        target=tokens.pop(0)
        if item['chat_id']!=state.identity()['management_chat']:raise Invalid('Use Control to manage another bot')
    action=tokens.pop(0) if tokens else 'status'
    if action in {'status','inbox'}:
        if tokens:raise Invalid('Use /recovery status or /recovery inbox')
        return json.dumps(status(state,target),ensure_ascii=False)
    if item['sender']!=state.identity()['owner']:raise Invalid('Only the owner can change recovery controls')
    session=state.session(target)
    if not session:raise Invalid('Choose a registered conversation')
    if action in {'on','off'} and not tokens:
        state.db.execute('INSERT OR REPLACE INTO recovery_session_settings VALUES(?,?)',(target,int(action=='on')))
        if action=='off':cancel(state,target)
        return 'Automatic recovery '+('enabled.' if action=='on' else 'disabled; pending continuations cancelled.')
    if action=='retry' and len(tokens)==1:
        episode=state.db.execute('SELECT * FROM recovery_episodes WHERE id=?',(tokens[0],)).fetchone()
        if not episode or episode['state']!='escalated':raise Invalid('Choose an escalated recovery incident')
        if target not in {episode['session'],settings(state)['incident_session']} and item['chat_id']!=state.identity()['management_chat']:
            raise Invalid('This incident belongs to another bot')
        source=state.session(episode['session'])
        if not source or source['state']!='ready' or busy(relay,source):raise Invalid('Wait for the source conversation to be ready and idle')
        if not enabled(state,source['id']):raise Invalid('Enable recovery for the source conversation first')
        if relay.terminal.binding(source):
            relay.terminal.poll(source)
            latest=state.db.execute('SELECT * FROM codex_outcomes WHERE session=?',(source['id'],)).fetchone()
        else:latest=relay.host.turn_state(source)
        if (source['thread_id']!=episode['thread_id'] or not latest or latest['status']!='failed'
                or latest['turn_id']!=episode['last_turn']):
            raise Invalid('The incident no longer describes the current failed turn')
        # Keep attempt numbers monotonic; each deliberate repair permits three
        # more attempts through a separate persisted ceiling, not an auto loop.
        maximum=settings(state)['max_attempts']
        state.db.execute('INSERT OR REPLACE INTO recovery_rearms VALUES(?,?)',(episode['id'],episode['attempts']+maximum))
        state.db.execute("UPDATE recovery_episodes SET state='waiting',due_at=?,updated_at=? WHERE id=?",(time.time()+settings(state)['backoff_seconds'][0],time.time(),episode['id']))
        return 'One bounded recovery cycle rearmed; the same conversation is preserved.'
    raise Invalid('Use /recovery status|on|off|inbox or /recovery retry INCIDENT')
