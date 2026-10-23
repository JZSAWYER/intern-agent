"""Owner model choices survive observation; settings commands do not run Codex."""
import re
from types import SimpleNamespace

from .core import Invalid


COMMAND="lower(substr(trim(NEW.text),1,instr(trim(NEW.text)||' ',' ')-1)) IN ('/model','/models','/reasoning')"


def schema(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS model_preferences(
        session TEXT PRIMARY KEY,model TEXT NOT NULL,effort TEXT NOT NULL,seq INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS model_controls(seq INTEGER PRIMARY KEY,state TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS model_control_policy(singleton INTEGER PRIMARY KEY,enabled INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS session_model_defaults(singleton INTEGER PRIMARY KEY,model TEXT NOT NULL,effort TEXT NOT NULL);
        INSERT OR IGNORE INTO model_control_policy VALUES(1,0);
        CREATE TRIGGER IF NOT EXISTS seed_session_model_defaults AFTER INSERT ON sessions
        BEGIN
            INSERT OR IGNORE INTO session_settings(session,model,effort)
                SELECT NEW.id,model,effort FROM session_model_defaults WHERE singleton=1;
        END;
        CREATE TRIGGER IF NOT EXISTS reserve_model_control AFTER INSERT ON events
        WHEN NEW.role='control' AND NEW.state='queued'
            AND (SELECT enabled FROM model_control_policy WHERE singleton=1)=1
            AND NEW.app_id=(SELECT app_id FROM identity WHERE singleton=1)
            AND NEW.sender=(SELECT owner FROM identity WHERE singleton=1) AND '''+COMMAND+'''
        BEGIN
            INSERT OR IGNORE INTO model_controls VALUES(NEW.seq,'pending');
            UPDATE events SET state='awaiting_terminal' WHERE seq=NEW.seq;
        END;''')
    # Older observers call INSERT OR REPLACE on every poll, including polls
    # with no new settings event. These guards protect a running old receiver.
    for action in ('INSERT','UPDATE'):
        db.executescript('CREATE TRIGGER IF NOT EXISTS retain_model_preference_'+action.lower()+
            ' BEFORE '+action+''' ON session_settings
            WHEN NOT EXISTS(SELECT 1 FROM terminal_attachments WHERE session=NEW.session)
                AND EXISTS(SELECT 1 FROM model_preferences p WHERE p.session=NEW.session
                    AND (NEW.model!=p.model OR NEW.effort!=p.effort))
            BEGIN SELECT RAISE(IGNORE); END;''')


def configure_defaults(state):
    """Seed future sessions only; never replace an existing conversation choice."""
    config=state.config()
    state.db.execute('''INSERT INTO session_model_defaults VALUES(1,?,?)
        ON CONFLICT(singleton) DO UPDATE SET model=excluded.model,effort=excluded.effort''',
        (config['model'],config.get('reasoning_effort','high')))


def save(state,session,model,effort,seq):
    if (not state.session(session) or not isinstance(model,str)
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}',model)
            or not isinstance(effort,str) or not re.fullmatch(r'[a-z]{1,32}',effort)):
        raise Invalid('Invalid model preference')
    state.db.execute('BEGIN IMMEDIATE')
    try:
        previous=state.db.execute('SELECT * FROM model_preferences WHERE session=?',(session,)).fetchone()
        state.db.execute('''INSERT INTO model_preferences VALUES(?,?,?,?)
            ON CONFLICT(session) DO UPDATE SET model=excluded.model,effort=excluded.effort,seq=excluded.seq
            WHERE model_preferences.seq<excluded.seq''',(session,model,effort,seq))
        preference=state.db.execute('SELECT * FROM model_preferences WHERE session=?',(session,)).fetchone()
        state.set_model(session,preference['model'],preference['effort'])
        if not previous or (previous['model'],previous['effort'])!=(preference['model'],preference['effort']):
            state.db.execute('''INSERT OR IGNORE INTO bot_tracking_pending(session,error_type,retry_at)
                SELECT ?,NULL,0 WHERE EXISTS(SELECT 1 FROM tracked_interns WHERE session=? AND published=1)''',(session,session))
        state.db.execute('COMMIT')
        return dict(preference)
    except BaseException:
        state.db.execute('ROLLBACK');raise


def adopt(state):
    """Adopt only successful owner acknowledgements, never an unexecuted request."""
    rows=state.db.execute('''SELECT e.* FROM events e JOIN identity i ON i.singleton=1
        WHERE e.role='control' AND e.app_id=i.app_id AND e.sender=i.owner
            AND e.result LIKE 'Model selected: %' ORDER BY e.seq''').fetchall()
    for row in rows:
        words=row['text'].split()
        if not words or words[0].lower() not in {'/model','/reasoning'}:continue
        target=words[1] if len(words)>1 and words[1].startswith('s_') and row['chat_id']==state.identity()['management_chat'] else row['session']
        session=state.session(target)
        if not session or session['state']!='ready' or state.db.execute('SELECT 1 FROM terminal_attachments WHERE session=?',(target,)).fetchone():continue
        lines=row['result'].splitlines()
        if len(lines)<2 or not lines[0].startswith('Model selected: ') or not lines[1].startswith('Reasoning effort: '):continue
        save(state,target,lines[0][len('Model selected: '):],lines[1][len('Reasoning effort: '):],row['seq'])


def pending(state):
    # Adopt only unclaimed commands. Running commands and uncertain deliveries
    # retain their existing worker/outbox; no work is reset to make a slot.
    state.db.execute('BEGIN IMMEDIATE')
    try:
        state.db.execute('''INSERT OR IGNORE INTO model_controls
            SELECT NEW.seq,'pending' FROM events NEW WHERE NEW.role='control' AND NEW.state='queued'
                AND NEW.app_id=(SELECT app_id FROM identity WHERE singleton=1)
                AND NEW.sender=(SELECT owner FROM identity WHERE singleton=1) AND '''+COMMAND)
        state.db.execute("UPDATE events SET state='awaiting_terminal' WHERE state='queued' AND seq IN (SELECT seq FROM model_controls WHERE state='pending')")
        state.db.execute('COMMIT')
    except BaseException:
        state.db.execute('ROLLBACK');raise


def poll(worker):
    """Use the existing leased reply outbox; never dispatch an agent prompt."""
    from .feishu_live import Relay
    from .delivery import send
    state=worker.state
    state.db.execute('UPDATE model_control_policy SET enabled=1 WHERE singleton=1 AND enabled=0')
    pending(state)
    class Records:
        def queue_record(self,session,**kwargs):
            if session:worker.dirty(session)
            return False  # The ordinary publisher handles Git independently.
    relay=SimpleNamespace(state=state,api=worker.api,host=worker.host,terminal=worker.terminal,tracker=Records(),last_error=None)
    for row in state.db.execute('''SELECT e.* FROM events e JOIN model_controls c ON c.seq=e.seq
        WHERE c.state='pending' AND e.state IN ('awaiting_terminal','sending') ORDER BY e.seq LIMIT 8''').fetchall():
        item=dict(row)
        if not state.authorized(item):
            state.transition(item['seq'],'cancelled_policy_changed')
            state.db.execute("UPDATE model_controls SET state='complete' WHERE seq=?",(item['seq'],));continue
        if not item['result']:
            try:
                command=item['text'].split()[0].lower().lstrip('/')
                result=Relay.model_command(relay,item,command,item['chat_id']==state.identity()['management_chat'])
            except Invalid as error:result=str(error)
            except Exception:result='Codex model settings are unavailable. No selection was confirmed; retry /model when the console is connected.'
            state.db.execute('UPDATE events SET result=? WHERE seq=? AND result IS NULL',(result,item['seq']))
        if not worker.api:continue
        outcome=send(relay,item,'complete')
        if outcome in {'complete','uncertain_delivery','cancelled_policy_changed'}:
            state.db.execute("UPDATE model_controls SET state=? WHERE seq=?",(outcome,item['seq']))
            state.ledger.observe(dict(state.db.execute('SELECT * FROM events WHERE seq=?',(item['seq'],)).fetchone()),outcome)
