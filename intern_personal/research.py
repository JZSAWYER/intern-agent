"""Persistent research alarms; normal tracked dispatch, never sleeping Codex turns."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import PurePosixPath
import re
import time
import uuid

from .core import Invalid

OPEN = {'waiting', 'queued', 'running'}
STATES = OPEN | {'paused', 'completed', 'cancelled', 'exhausted', 'review_needed', 'superseded'}
MAX_HORIZON = 30 * 86400
HELP = ('Research wake-ups\n\n'
        '/research start 10h "Inspect the existing experiment and continue the authorized plan" '
        '--every 1h --checks 12 --for 48h --key EXPERIMENT\n'
        '/research status [WATCH_ID]\n/research pause WATCH_ID /research resume WATCH_ID\n'
        '/research defer WATCH_ID 2h --note "Existing job needs longer"\n'
        '/research finish WATCH_ID --note "Results checked and saved"\n'
        '/research cancel WATCH_ID\n\n'
        'Add --evidence RELATIVE_PATH for existing job/status/result files (repeatable). '
        'Times use s/m/h/d durations; the first check is never early. '
        'Alarms survive relay restarts and wait for this same conversation to become idle. '
        'Each completed check schedules the next until finished, cancelled, or its check/time budget ends. '
        'No experiment is launched by the timer. /stop cancels this conversation\'s research alarms. '
        'The host and relay must remain running; overdue checks run once after restart, without a catch-up burst.')


class Deferred(Invalid):
    """Not dispatched: yield to active work or a newer owner message."""


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Invalid('Invalid research command. Use /help research for syntax.')


def schema(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS research_watches(
        id TEXT PRIMARY KEY,session TEXT NOT NULL,workspace TEXT NOT NULL,thread_id TEXT NOT NULL,
        app_id TEXT NOT NULL,owner TEXT NOT NULL,chat_id TEXT NOT NULL,task_id TEXT,branch TEXT NOT NULL,
        source_seq INTEGER,client_key TEXT NOT NULL,spec TEXT NOT NULL,goal TEXT NOT NULL,evidence TEXT NOT NULL,
        state TEXT NOT NULL,due_at REAL NOT NULL,expires_at REAL NOT NULL,interval_seconds INTEGER NOT NULL,
        max_checks INTEGER NOT NULL,checks INTEGER NOT NULL DEFAULT 0,next_due_at REAL,last_note TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL,updated_at REAL NOT NULL,errors INTEGER NOT NULL DEFAULT 0,retry_at REAL NOT NULL DEFAULT 0,
        UNIQUE(session,client_key));
        CREATE INDEX IF NOT EXISTS research_due ON research_watches(state,due_at);
        CREATE TABLE IF NOT EXISTS research_runs(
        seq INTEGER PRIMARY KEY,watch TEXT NOT NULL,number INTEGER NOT NULL,not_before REAL NOT NULL DEFAULT 0,
        UNIQUE(watch,number));
        CREATE TABLE IF NOT EXISTS research_dirty(session TEXT PRIMARY KEY);
        CREATE VIEW IF NOT EXISTS research_event_scope AS
        WITH RECURSIVE scope(seq,watch,number) AS (
            SELECT seq,watch,number FROM research_runs
            UNION SELECT r.seq,s.watch,s.number FROM scope s
            JOIN recovery_episodes e ON e.root_seq=s.seq JOIN recovery_runs r ON r.episode=e.id)
        SELECT * FROM scope;
        CREATE VIEW IF NOT EXISTS background_event_scope AS
            SELECT seq FROM peer_event_scope UNION SELECT seq FROM research_event_scope;''')


def duration(value):
    match = re.fullmatch(r'([1-9][0-9]{0,6})(s|m|h|d)', value or '')
    if not match:
        raise Invalid('Use a duration such as 30m, 10h or 2d')
    seconds = int(match[1]) * {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}[match[2]]
    if not 60 <= seconds <= MAX_HORIZON:
        raise Invalid('Research intervals must be 1 minute to 30 days')
    return seconds


def utc(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec='seconds')


def current(state, session):
    row = state.db.execute("SELECT * FROM events WHERE session=? AND role='agent' AND state IN ('running','awaiting_terminal') ORDER BY seq DESC LIMIT 1", (session['id'],)).fetchone()
    return dict(row) if row else None


def actor(state, context):
    from .file_transfer import context_session
    session = context_session(state, context)
    if os.environ.get('CODEX_THREAD_ID') != session['thread_id']:
        raise Invalid('Research tools require this conversation\'s exact Codex thread context')
    return session


def row_for(state, session, ident):
    row = state.db.execute('SELECT * FROM research_watches WHERE id=? AND session=?', (ident, session['id'])).fetchone()
    if not row:
        raise Invalid('Choose a research watch from this conversation')
    return dict(row)


def public(row):
    return dict(id=row['id'], state=row['state'], goal=row['goal'], evidence=json.loads(row['evidence']),
                next_check_utc=utc(row['next_due_at'] or row['due_at']),
                expires_utc=utc(row['expires_at']), interval_seconds=row['interval_seconds'],
                checks=row['checks'], max_checks=row['max_checks'], note=row['last_note'])


def listing(state, session, ident=None):
    if not session:
        raise Invalid('Choose a registered conversation')
    active=[public(dict(r)) for r in state.db.execute('SELECT * FROM research_watches WHERE session=? ORDER BY created_at DESC LIMIT 20',(session['id'],))]
    if ident and state.db.execute('SELECT 1 FROM research_watches WHERE id=? AND session=?',(ident,session['id'])).fetchone():return [public(row_for(state,session,ident))]
    known={row['id'] for row in active}
    for row in state.db.execute("SELECT data FROM feature_documents WHERE workspace=? AND kind='research_watch' ORDER BY revision DESC",(session['workspace'],)):
        data=json.loads(row['data'])
        if data['conversation']!=session['id'] or data['watch'] in known:continue
        if state.db.execute('SELECT 1 FROM research_watches WHERE id=?',(data['watch'],)).fetchone():continue
        restorable=data['state'] in OPEN|{'paused','review_needed'} and data['checks']<data['max_checks']
        active.append(dict(id=data['watch'],state='restored_paused' if restorable else 'archived',goal=data['goal'],evidence=data['evidence'],
            next_check_utc=None,expires_utc=utc(data['expires_at']),
            interval_seconds=data['interval_seconds'],checks=data['checks'],max_checks=data['max_checks'],
            archived_state=data['state'],next_action='Use /research restore WATCH AFTER to explicitly schedule checks on this new host; experiments are not restarted.' if restorable else 'Recorded terminal watch history; no new checks are scheduled.'))
    if ident:
        found=[row for row in active if row['id']==ident]
        if not found:raise Invalid('Choose a research watch from this conversation')
        return found
    return active[:40]


def validate_text(state, text, maximum=3000):
    if not isinstance(text, str) or not text.strip() or len(text) > maximum or any(ord(c) < 32 and c not in '\n\t' for c in text):
        raise Invalid('Use bounded, nonempty research instructions or evidence')
    from .workspace_tracking import WorkspaceTracking
    if WorkspaceTracking(state).clean(text) != text:
        raise Invalid('Research instructions cannot contain credentials or private account identifiers')
    return text


def selection(state, session):
    task = state.ledger.selection(session['id'])
    intern = state.db.execute('SELECT * FROM tracked_interns WHERE session=?', (session['id'],)).fetchone()
    if not intern or not intern['published']:
        raise Invalid('Research needs a registered, published Git workspace and bot branch')
    return task['id'] if task else None, intern['code_branch']


def allowed_source(state, session, source=None, creating=False):
    item = source or current(state, session)
    if item:
        if item['sender'] != state.identity()['owner'] or (item.get('session') != session['id'] and item['role'] == 'agent'):
            raise Invalid('Only an owner instruction can authorize research wake-ups')
        from .peer_mail import binding
        if binding(state, item['seq'])[0] or state.db.execute('SELECT 1 FROM bot_mailbox WHERE event_seq=?', (item['seq'],)).fetchone():
            raise Invalid('Bot mail cannot create or manage autonomous research')
        if creating and (state.db.execute('SELECT 1 FROM research_event_scope WHERE seq=?', (item['seq'],)).fetchone()
                         or state.db.execute('SELECT 1 FROM recovery_runs WHERE seq=?', (item['seq'],)).fetchone()):
            raise Invalid('A continuation must keep its existing watch and budgets; do not create another loop')
    return item


def record(state, watch, stage, note='', seq=None):
    """Portable history is reference data; executable alarms remain private."""
    from .feature_state import get
    previous = get(state, watch['workspace'], 'research_watch', watch['id'], {})
    history = previous.get('history', [])
    entry = dict(at=time.time(), stage=stage, check=watch['checks'], note=note)
    if seq is not None and seq > 0:
        entry['turn'] = seq
    data = dict(conversation=watch['session'], watch=watch['id'], goal=watch['goal'],
                evidence=json.loads(watch['evidence']), created_at=watch['created_at'],
                due_at=watch['next_due_at'] or watch['due_at'], expires_at=watch['expires_at'],
                interval_seconds=watch['interval_seconds'], max_checks=watch['max_checks'], checks=watch['checks'],
                state=watch['state'], note=watch['last_note'], history=history + [entry])
    validate_record(data)
    revision = state.ledger.revision(watch['workspace'], 'memory', watch['id'], 'memory', conversation=watch['session'])
    state.db.execute('INSERT OR REPLACE INTO feature_documents VALUES(?,?,?,?,?)',
                     (watch['workspace'], 'research_watch', watch['id'], json.dumps(data, sort_keys=True), revision))
    state.db.execute('INSERT OR IGNORE INTO research_dirty VALUES(?)', (watch['session'],))


def validate_record(data):
    from .records import ID
    fields = {'conversation','watch','goal','evidence','created_at','due_at','expires_at','interval_seconds','max_checks','checks','state','note','history'}
    if (not isinstance(data, dict) or set(data) != fields or data['state'] not in STATES
            or any(not isinstance(data[k], str) or not ID.fullmatch(data[k]) for k in ['conversation','watch'])
            or not isinstance(data['goal'], str) or not 1 <= len(data['goal']) <= 3000
            or not isinstance(data['note'], str) or len(data['note']) > 2000
            or not isinstance(data['evidence'], list) or len(data['evidence']) > 8
            or any(not valid_path(p) for p in data['evidence'])
            or type(data['max_checks']) is not int or not 1 <= data['max_checks'] <= 48
            or type(data['checks']) is not int or not 0 <= data['checks'] <= data['max_checks']
            or type(data['interval_seconds']) is not int or not 60 <= data['interval_seconds'] <= MAX_HORIZON
            or any(type(data[k]) not in {int,float} or not 0 < data[k] < 1e12 for k in ['created_at','due_at','expires_at'])
            or not 0 < data['expires_at'] - data['created_at'] <= MAX_HORIZON
            or not isinstance(data['history'], list) or not 1 <= len(data['history']) <= 1000):
        raise Invalid('Malformed research history')
    for event in data['history']:
        if (not isinstance(event, dict) or set(event) not in ({'at','stage','check','note'}, {'at','stage','check','note','turn'})
                or type(event['at']) not in {int,float} or not 0 < event['at'] < 1e12
                or not isinstance(event['stage'], str) or not 1 <= len(event['stage']) <= 50
                or not isinstance(event['note'], str) or len(event['note']) > 2000
                or type(event['check']) is not int or not 0 <= event['check'] <= data['max_checks']
                or ('turn' in event and (type(event['turn']) is not int or event['turn'] < 1))):
            raise Invalid('Malformed research history event')


def valid_path(path):
    from .attachments import private_path
    return (isinstance(path, str) and 0 < len(path) <= 500 and not any(ord(c) < 32 for c in path)
            and '\\' not in path and not PurePosixPath(path).is_absolute()
            and '..' not in PurePosixPath(path).parts and bool(PurePosixPath(path).parts)
            and not private_path(PurePosixPath(path).parts))


def start(state, session, after, goal, *, every='1h', checks=12, horizon='72h', evidence=None, key=None, source=None, now=None):
    now = time.time() if now is None else now
    if not session or session['state'] != 'ready' or not session.get('thread_id') or not session.get('chat_id'):
        raise Invalid('Choose a ready, bound research conversation')
    item = allowed_source(state, session, source, creating=True)
    identity = state.identity()
    if not identity['owner'] or not identity['app_id']:
        raise Invalid('Pair the owner first')
    goal = validate_text(state, goal)
    delay, interval, lifetime = duration(after), duration(every), duration(horizon)
    if type(checks) is not int or not 1 <= checks <= 48 or delay >= lifetime:
        raise Invalid('Use 1-48 checks and a horizon later than the first check')
    evidence = sorted(set(evidence or []))
    if len(evidence) > 8 or any(not valid_path(p) for p in evidence):
        raise Invalid('Use up to eight relative, nonprivate workspace evidence paths')
    spec = json.dumps(dict(after=delay, goal=goal, every=interval, checks=checks, horizon=lifetime, evidence=evidence), sort_keys=True)
    key = key or hashlib.sha256((str(item['seq'] if item else 'local') + ':' + spec).encode()).hexdigest()
    if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', key):
        raise Invalid('Choose a stable research key using letters, digits, dots, underscores or hyphens')
    state.db.execute('BEGIN IMMEDIATE')
    try:
        existing = state.db.execute('SELECT * FROM research_watches WHERE session=? AND client_key=?', (session['id'], key)).fetchone()
        if existing:
            if existing['spec'] != spec:
                raise Invalid('This watch key belongs to a different plan; inspect it instead of overwriting it')
            state.db.execute('COMMIT')
            return public(dict(existing))
        if state.db.execute("SELECT count(*) FROM research_watches WHERE session=? AND state IN ('waiting','queued','running','paused','review_needed')", (session['id'],)).fetchone()[0] >= 8:
            raise Invalid('At most eight unfinished watches per conversation')
        task, branch = selection(state, session)
        ident = 'watch_' + uuid.uuid4().hex[:16]
        state.db.execute('''INSERT INTO research_watches(id,session,workspace,thread_id,app_id,owner,chat_id,
            task_id,branch,source_seq,client_key,spec,goal,evidence,state,due_at,expires_at,interval_seconds,max_checks,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,'waiting',?,?,?,?,?,?)''',
            (ident, session['id'], session['workspace'], session['thread_id'], identity['app_id'], identity['owner'],
             session['chat_id'], task, branch, item['seq'] if item else None, key, spec, goal, json.dumps(evidence),
             now + delay, now + lifetime, interval, checks, now, now))
        watch = row_for(state, session, ident)
        record(state, watch, 'scheduled', 'First check at ' + utc(watch['due_at']))
        state.db.execute('COMMIT')
        return public(watch)
    except BaseException:
        state.db.execute('ROLLBACK')
        raise


def change(state, session, ident, action, *, after=None, note='', source=None, now=None):
    now = time.time() if now is None else now
    item = allowed_source(state, session, source)
    note = validate_text(state, note, 2000) if note else ''
    state.db.execute('BEGIN IMMEDIATE')
    try:
        watch = row_for(state, session, ident)
        scope = state.db.execute('SELECT watch FROM research_event_scope WHERE seq=?', (item['seq'],)).fetchone() if item else None
        if scope and (scope['watch'] != ident or action in {'resume','pause','cancel'}):
            raise Invalid('A scheduled check may only finish or defer its own watch')
        if watch['state'] in {'completed','cancelled','exhausted','superseded'}:
            raise Invalid('This watch has ended; create a new owner-authorized plan')
        due = watch['next_due_at'] or watch['due_at']
        if action == 'defer':
            due = max(due, now + duration(after))
            if due >= watch['expires_at'] or watch['checks'] >= watch['max_checks']:
                raise Invalid('Deferral cannot extend the watch\'s check or time budget')
            field = 'next_due_at' if watch['state'] in {'queued','running'} else 'due_at'
            state.db.execute('UPDATE research_watches SET ' + field + '=? WHERE id=?', (due, ident))
        elif action == 'resume':
            if watch['state'] not in {'paused','review_needed'} or now >= watch['expires_at'] or watch['checks'] >= watch['max_checks']:
                raise Invalid('Only a paused/reviewed watch within its original budget can resume')
            if not pinned(state,watch,session) or selection(state, session) != (watch['task_id'], watch['branch']):
                raise Invalid('Restore the watch\'s original task and branch before resuming')
            state.db.execute("UPDATE research_watches SET state='waiting',due_at=?,next_due_at=NULL,errors=0,retry_at=0 WHERE id=?", (max(now, due), ident))
        elif action in {'finish','pause','cancel'}:
            state.db.execute('UPDATE research_watches SET state=? WHERE id=?', ({'finish':'completed','pause':'paused','cancel':'cancelled'}[action], ident))
            cancel_queued(state, ident)
        else:
            raise Invalid('Use research finish, defer, pause, resume or cancel')
        state.db.execute('UPDATE research_watches SET last_note=?,updated_at=? WHERE id=?', (note, now, ident))
        watch = row_for(state, session, ident)
        record(state, watch, action, note, item['seq'] if item else None)
        state.db.execute('COMMIT')
        return public(watch)
    except BaseException:
        state.db.execute('ROLLBACK')
        raise


def cancel_queued(state, ident):
    state.db.execute("UPDATE events SET state='cancelled',result='Research alarm cancelled before dispatch.' WHERE state='queued' AND seq IN (SELECT seq FROM research_event_scope WHERE watch=?)", (ident,))
    state.db.execute("UPDATE recovery_episodes SET state='cancelled',updated_at=? WHERE state IN ('waiting','queued','running') AND root_seq IN (SELECT seq FROM research_event_scope WHERE watch=?)", (time.time(), ident))


def cancel_session(state, session, pause=False):
    count = 0
    for row in state.db.execute("SELECT id FROM research_watches WHERE session=? AND state IN ('waiting','queued','running','paused','review_needed')", (session['id'],)).fetchall():
        change(state, session, row['id'], 'pause' if pause else 'cancel', source=dict(seq=0, sender=state.identity()['owner'], role='control', session=session['id']), note='Owner paused the conversation.' if pause else 'Owner stopped the conversation.')
        count += 1
    return count


def physical_busy(relay, session):
    if session['id'] in relay.host.active:
        return True
    native = relay.terminal.binding(session)
    for bridge in (relay.terminal, getattr(relay.host, 'observer', None)):
        if bridge and bridge.binding(session):
            snapshot = bridge.poll(session)
            if snapshot.get('active') or snapshot.get('pending'):
                return True
    if not native and hasattr(relay.host, 'turn_state'):
        latest = relay.host.turn_state(session)
        return bool(latest and latest['status'] == 'inProgress')
    return False


def owner_pending(state, session, excluding=None):
    return bool(state.db.execute('''SELECT 1 FROM events WHERE session=? AND seq!=? AND state IN ('queued','running')
        AND role IN ('control','urgent','agent') AND seq NOT IN (SELECT seq FROM background_event_scope) LIMIT 1''',
        (session['id'], excluding or -1)).fetchone())


def pinned(state, watch, session):
    identity = state.identity()
    return (session and session['thread_id'] == watch['thread_id'] and session['chat_id'] == watch['chat_id']
            and session['workspace'] == watch['workspace'] and identity['app_id'] == watch['app_id']
            and identity['owner'] == watch['owner'])


def guard(relay, item):
    scope = relay.state.db.execute('SELECT * FROM research_event_scope WHERE seq=?', (item['seq'],)).fetchone()
    if not scope:
        return
    state = relay.state
    session = state.session(item['session'])
    watch = row_for(state, session, scope['watch'])
    from .auto_recovery import RecoverySkipped
    if not pinned(state, watch, session) or watch['state'] not in OPEN or session['state'] != 'ready':
        raise RecoverySkipped('Research alarm is no longer active in this conversation')
    if selection(state, session) != (watch['task_id'], watch['branch']):
        end(state, watch, 'paused', 'Task or code branch changed.')
        raise RecoverySkipped('Research alarm paused because its task or branch changed')
    if time.time() >= watch['expires_at']:
        end(state, watch, 'exhausted', 'Research time budget reached.')
        raise RecoverySkipped('Research alarm reached its original time budget')
    if item['seq'] in {r['seq'] for r in state.db.execute('SELECT seq FROM research_runs WHERE watch=?',(watch['id'],))} and time.time()<watch['due_at']:
        state.db.execute('UPDATE research_runs SET not_before=? WHERE seq=?',(watch['due_at'],item['seq']))
        raise Deferred('Research check waits for its original deadline')
    from .peer_mail import pending_information
    if owner_pending(state, session, item['seq']) or pending_information(state,session) or physical_busy(relay, session):
        state.db.execute('UPDATE research_runs SET not_before=? WHERE seq=?', (time.time() + 30, item['seq']))
        raise Deferred('Research check waits for the existing turn or owner message')
    # Reserve the short write transaction before reading its decision. A
    # deferred read-to-write upgrade can fail immediately against another
    # writer, even when the SQLite busy timeout is configured.
    state.db.execute('BEGIN IMMEDIATE')
    try:
        watch=row_for(state,session,watch['id'])
        if watch['state'] not in OPEN or state.ledger.stopped(item['seq']):
            raise RecoverySkipped('Research alarm was cancelled before dispatch')
        if owner_pending(state,session,item['seq']) or pending_information(state,session):
            state.db.execute('UPDATE research_runs SET not_before=? WHERE seq=?',(time.time()+30,item['seq']))
            state.db.execute('COMMIT')
            raise Deferred('Owner input or pending peer information has priority')
        if watch['state'] != 'running':
            state.db.execute("UPDATE research_watches SET state='running',updated_at=? WHERE id=?", (time.time(), watch['id']))
            watch = row_for(state, session, watch['id'])
            record(state, watch, 'dispatched', seq=item['seq'])
        state.db.execute('COMMIT')
    except Deferred:
        raise
    except BaseException:
        state.db.execute('ROLLBACK'); raise


def end(state, watch, status, note):
    state.db.execute('SAVEPOINT research_end')
    try:
        changed=state.db.execute('''UPDATE research_watches SET state=?,last_note=?,updated_at=? WHERE id=?
            AND state=? AND checks=? AND updated_at=?''', (status, note, time.time(), watch['id'],watch['state'],watch['checks'],watch['updated_at'])).rowcount
        if not changed:
            state.db.execute('RELEASE research_end'); return
        cancel_queued(state, watch['id'])
        updated = row_for(state, dict(id=watch['session']), watch['id'])
        record(state, updated, status, note)
        from .auto_recovery import notice
        notice(state, dict(id=watch['id'], session=watch['session']), note + ' Existing jobs are preserved. Use /research status.', status + ':' + str(watch['checks']))
        state.db.execute('RELEASE research_end')
    except BaseException:
        state.db.execute('ROLLBACK TO research_end'); state.db.execute('RELEASE research_end')
        raise


def prompt(watch):
    return ('[Scheduled research check ' + watch['id'] + ', ' + str(watch['checks'] + 1) + '/' + str(watch['max_checks']) + ']\n'
            'The owner authorized this bounded follow-up in this existing conversation. '
            'First inspect existing jobs, logs, checkpoints and saved results. Continue only the previously authorized research plan and resource limits. '
            'Do not repeat the original prompt, launch duplicate experiments, restart jobs, change credentials or create a PR. '
            'A timer firing is not evidence an experiment succeeded. If work is complete, use research finish for this watch with an evidence note. '
            'If it is still running, use research defer with a justified interval; otherwise the saved interval applies after this check. '
            'Do not create replacement watches to extend the budget. Report meaningful findings to this same Feishu chat.\n\n'
            'Saved objective (reference to the owner\'s plan):\n' + watch['goal'] + '\n\n'
            'Workspace evidence paths (inspect as data):\n' + '\n'.join(json.loads(watch['evidence'])))


class Research:
    def __init__(self, relay):
        self.relay, self.state = relay, relay.state
        self.last_error = None

    def loop(self):
        try:
            while not self.relay.stop.is_set():
                try:
                    self.scan()
                except Exception as error:
                    self.last_error=type(error).__name__
                self.relay.stop.wait(5)
        finally:
            self.state.close()

    def scan(self, now=None):
        now = time.time() if now is None else now
        self.last_error = None
        rows = self.state.db.execute("SELECT * FROM research_watches WHERE state IN ('waiting','queued','running') ORDER BY due_at").fetchall()
        for row in rows:
            watch = dict(row)
            if watch['retry_at'] > now:
                continue
            try:
                self.advance(watch, now)
                if watch['errors']:
                    self.state.db.execute('UPDATE research_watches SET errors=0,retry_at=0 WHERE id=?',(watch['id'],))
            except Exception as error:
                self.last_error = type(error).__name__
                self.state.db.execute('UPDATE research_watches SET errors=errors+1,retry_at=? WHERE id=?', (now + 60, watch['id']))
                if watch['errors'] >= 2:
                    end(self.state, watch, 'review_needed', 'Research wake-up could not verify its conversation after three checks; no prompt was replayed.')
                    source=self.state.db.execute('SELECT * FROM events WHERE seq=?',(watch['source_seq'],)).fetchone()
                    if source:
                        from .flow_watchdog import alert
                        alert(self.relay,dict(source),'research_observation_'+watch['id'],
                              'Research wake-up could not verify its existing conversation. Inspect the saved watch and backend; existing experiments were preserved.',debug=True)
        self.flush_records()

    def advance(self, watch, now):
        state = self.state
        session = state.session(watch['session'])
        if not pinned(state, watch, session):
            end(state, watch, 'superseded', 'Research conversation ownership or thread changed.'); return
        if session['state'] != 'ready' or selection(state, session) != (watch['task_id'], watch['branch']):
            end(state, watch, 'paused', 'Research alarm paused because its session, selected task or branch changed.'); return
        if watch['state'] in {'queued','running'}:
            self.observe(watch, now)
            return
        if now >= watch['expires_at']:
            end(state, watch, 'exhausted', 'Research time budget reached.'); return
        if now < watch['due_at']:
            return
        from .peer_mail import pending_information
        if (owner_pending(state, session) or pending_information(state,session) or state.ledger.busy(session['id'])
                or state.db.execute("SELECT 1 FROM events WHERE session=? AND role='agent' AND state='queued'", (session['id'],)).fetchone()
                or state.db.execute("SELECT 1 FROM recovery_episodes WHERE session=? AND state IN ('waiting','queued','running')", (session['id'],)).fetchone()
                or physical_busy(self.relay, session)):
            return
        state.db.execute('BEGIN IMMEDIATE')
        try:
            latest = row_for(state, session, watch['id'])
            if latest['state'] != 'waiting' or latest['due_at'] > now or owner_pending(state, session):
                state.db.execute('COMMIT'); return
            if latest['checks'] >= latest['max_checks']:
                state.db.execute('COMMIT'); end(state, latest, 'exhausted', 'Research check budget reached.'); return
            key = 'research:' + watch['id'] + ':' + str(latest['checks'] + 1)
            cursor = state.db.execute('''INSERT INTO events(app_id,message_id,chat_id,sender,role,workspace,session,text,state)
                VALUES(?,?,?,?,'agent',?,?,?,'queued')''',
                (watch['app_id'], key, watch['chat_id'], watch['owner'], watch['workspace'], watch['session'], prompt(latest)))
            seq = cursor.lastrowid
            state.db.execute('INSERT INTO research_runs(seq,watch,number) VALUES(?,?,?)', (seq, watch['id'], latest['checks'] + 1))
            state.db.execute("UPDATE research_watches SET state='queued',checks=checks+1,errors=0,retry_at=0,updated_at=? WHERE id=?", (now, watch['id']))
            item = dict(state.db.execute('SELECT * FROM events WHERE seq=?', (seq,)).fetchone())
            state.ledger.ensure_turn(item, now, 'local')
            record(state, row_for(state, session, watch['id']), 'queued', seq=seq)
            state.db.execute('COMMIT')
        except BaseException:
            state.db.execute('ROLLBACK'); raise

    def observe(self, watch, now):
        state = self.state
        runs = state.db.execute('''SELECT e.* FROM research_event_scope r JOIN events e ON e.seq=r.seq
            WHERE r.watch=? AND r.number=? ORDER BY e.seq DESC''', (watch['id'], watch['checks'])).fetchall()
        if not runs:
            end(state, watch, 'review_needed', 'Research dispatch record is missing; no replacement prompt was sent.'); return
        item = dict(runs[0])
        outcome = state.db.execute('SELECT status FROM codex_outcomes WHERE session=? AND event_seq=?', (watch['session'], item['seq'])).fetchone()
        if not outcome:
            if item['state']=='queued' and now>=watch['expires_at']:
                end(state,watch,'exhausted','Research time budget reached before dispatch.'); return
            terminal = state.db.execute('SELECT status FROM terminal_turns WHERE session=? AND event_seq=?', (watch['session'], item['seq'])).fetchone()
            if terminal:
                outcome = {'status': {'task_complete':'completed','task_failed':'failed','turn_aborted':'interrupted'}[terminal['status']]}
        if not outcome:
            if item['state'] in {'uncertain_execution','uncertain_delivery','record_blocked','cancelled','cancelled_policy_changed'}:
                end(state, watch, 'review_needed', 'Research check requires execution/preflight review; its saved request is retained.')
            return
        if outcome['status'] == 'inProgress':
            return
        if outcome['status'] == 'failed' and state.db.execute("SELECT 1 FROM recovery_episodes WHERE root_seq=? AND state IN ('waiting','queued','running')", (item['seq'],)).fetchone():
            return
        if outcome['status'] != 'completed':
            end(state, watch, 'paused' if outcome['status'] == 'interrupted' else 'review_needed', 'Research check ended without a verified completion; existing recovery remains separate.'); return
        # A definitive completion permits a future check, even if Git publication
        # is pending. Normal dispatch/preflight still gates that future request.
        if watch['checks'] >= watch['max_checks'] or now >= watch['expires_at']:
            end(state, watch, 'exhausted', 'Research check/time budget reached.'); return
        due = watch['next_due_at'] or (now + watch['interval_seconds'])
        state.db.execute('SAVEPOINT research_observe')
        try:
            current=row_for(state,dict(id=watch['session']),watch['id'])
            if current['state'] in {'queued','running'} and current['checks']==watch['checks']:
                due=current['next_due_at'] or (now+current['interval_seconds'])
                state.db.execute("UPDATE research_watches SET state='waiting',due_at=?,next_due_at=NULL,updated_at=? WHERE id=?", (due, now, watch['id']))
                record(state, row_for(state, dict(id=watch['session']), watch['id']), 'checked', 'Next check at ' + utc(due), item['seq'])
            state.db.execute('RELEASE research_observe')
        except BaseException:
            state.db.execute('ROLLBACK TO research_observe'); state.db.execute('RELEASE research_observe'); raise

    def flush_records(self):
        for row in self.state.db.execute('SELECT session FROM research_dirty').fetchall():
            session = self.state.session(row['session'])
            if not session or self.state.ledger.busy(session['id']):
                continue
            intern = self.state.db.execute('SELECT metadata_mode FROM tracked_interns WHERE session=?', (session['id'],)).fetchone()
            if intern and intern['metadata_mode'] == 'bot_branch':
                self.state.db.execute('INSERT OR IGNORE INTO bot_tracking_pending VALUES(?,NULL,0)', (session['id'],))
            else:
                self.state.db.execute('INSERT OR IGNORE INTO tracking_pending VALUES(?,NULL,0)', (session['workspace'],))
            self.state.db.execute('DELETE FROM research_dirty WHERE session=?', (session['id'],))


def execute(state, session, tokens, source=None):
    parser = Parser(add_help=False)
    sub = parser.add_subparsers(dest='action', required=True)
    begin = sub.add_parser('start', add_help=False)
    begin.add_argument('after'); begin.add_argument('goal')
    begin.add_argument('--every', default='1h'); begin.add_argument('--checks', type=int, default=12)
    begin.add_argument('--for', dest='horizon', default='72h')
    begin.add_argument('--evidence', action='append', default=[]); begin.add_argument('--key')
    show = sub.add_parser('status', add_help=False); show.add_argument('ident', nargs='?')
    recovered=sub.add_parser('restore',add_help=False);recovered.add_argument('ident');recovered.add_argument('after')
    for action in ['pause','resume','cancel','finish','defer']:
        p = sub.add_parser(action, add_help=False); p.add_argument('ident')
        if action == 'defer': p.add_argument('after')
        p.add_argument('--note', default='')
    args = vars(parser.parse_args(tokens or ['status']))
    action = args.pop('action')
    if action == 'status':
        return listing(state, session, args['ident'])
    if action == 'start':
        return start(state, session, source=source, **args)
    if action=='restore':
        from .feature_state import get
        data=get(state,session['workspace'],'research_watch',args['ident'])
        if (not data or data['conversation']!=session['id'] or data['state'] in {'completed','cancelled','exhausted','superseded'}
                or data['checks']>=data['max_checks']):raise Invalid('Choose an unfinished restored watch from this conversation')
        if state.db.execute('SELECT 1 FROM research_watches WHERE id=?',(args['ident'],)).fetchone():raise Invalid('This watch still has its live schedule; use pause or resume')
        return start(state,session,args['after'],data['goal'],every=str(data['interval_seconds'])+'s',
            checks=data['max_checks']-data['checks'],horizon='30d',evidence=data['evidence'],
            key='restored-'+args['ident'],source=source)
    ident = args.pop('ident')
    return change(state, session, ident, action, source=source, **args)
