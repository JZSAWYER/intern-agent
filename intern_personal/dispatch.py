"""Prove the boundary between a claimed request and attempted Codex execution."""
import json
import time
import uuid

from .core import Invalid


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS dispatch_attempts(
        seq INTEGER PRIMARY KEY,token TEXT NOT NULL,stage TEXT NOT NULL,
        failures INTEGER NOT NULL DEFAULT 0,retry_at REAL NOT NULL DEFAULT 0,
        error_type TEXT,error_code TEXT,updated_at REAL NOT NULL)''')
    # Configuration/command delivery reviews are not evidence of Codex work.
    # All agent entry lanes use this same execution-review classification.
    db.execute('''CREATE VIEW IF NOT EXISTS codex_review_events AS
        SELECT seq FROM events WHERE role='agent'
        AND state IN ('uncertain_execution','uncertain_delivery','record_blocked','sync_pending')''')


def claimed(state, item):
    token = uuid.uuid4().hex
    state.db.execute('''INSERT INTO dispatch_attempts(seq,token,stage,updated_at)
        VALUES(?,?,'preflight',?) ON CONFLICT(seq) DO UPDATE SET
        token=excluded.token,stage='preflight',updated_at=excluded.updated_at''',
        (item['seq'], token, time.time()))
    item['_dispatch_token'] = token


def start(state, item):
    """Commit before entering either transport; ambiguity must never allow replay."""
    changed = state.db.execute('''UPDATE dispatch_attempts SET stage='dispatching',updated_at=?
        WHERE seq=? AND token=? AND stage='preflight'
        AND EXISTS(SELECT 1 FROM events e WHERE e.seq=dispatch_attempts.seq AND e.state='running'
            AND e.result IS NULL AND e.receipt IS NULL)''',
        (time.time(), item['seq'], item.get('_dispatch_token'))).rowcount
    if not changed:
        raise Invalid('The request no longer owns its preflight dispatch boundary')


def retry(state, item, error=None):
    """Retry only a failed worker which durably never entered a Codex transport."""
    state.db.execute('BEGIN IMMEDIATE')
    try:
        proof = state.db.execute('''SELECT d.* FROM dispatch_attempts d JOIN events e ON e.seq=d.seq
            WHERE d.seq=? AND d.token=? AND d.stage='preflight' AND e.state='running'
            AND e.role='agent' AND e.result IS NULL AND e.receipt IS NULL''',
            (item['seq'], item.get('_dispatch_token'))).fetchone()
        if not proof:
            state.db.execute('COMMIT'); return None
        if (state.db.execute('SELECT 1 FROM codex_outcomes WHERE event_seq=?', (item['seq'],)).fetchone()
                or state.db.execute('SELECT 1 FROM terminal_turns WHERE event_seq=?', (item['seq'],)).fetchone()
                or state.db.execute('SELECT 1 FROM event_failures WHERE seq=?', (item['seq'],)).fetchone()):
            state.db.execute('COMMIT'); return None
        for table in ['terminal_attachments', 'console_views']:
            row = state.db.execute('SELECT snapshot FROM '+table+' WHERE session=?', (item['session'],)).fetchone()
            snapshot = json.loads(row['snapshot']) if row else {}
            if ((snapshot.get('pending') or {}).get('seq') == item['seq']
                    or snapshot.get('active') and snapshot.get('event_seq') == item['seq']):
                state.db.execute('COMMIT'); return None
        failures = proof['failures'] + 1
        phase = 'queued' if failures <= 3 else 'record_blocked'
        error_type = type(error).__name__ if error else 'ReceiverRestart'
        code = getattr(error, 'sqlite_errorname', None)
        if not isinstance(code, str) or not code.startswith('SQLITE_') or len(code)>80:
            code = None
        state.db.execute('''UPDATE dispatch_attempts SET failures=?,retry_at=?,error_type=?,error_code=?,updated_at=?
            WHERE seq=? AND token=?''',
            (failures, time.time() + min(30, 2 ** failures), error_type[:80], code,
             time.time(), item['seq'], item['_dispatch_token']))
        state.db.execute('UPDATE events SET state=? WHERE seq=?', (phase, item['seq']))
        state.db.execute('COMMIT')
    except BaseException:
        state.db.execute('ROLLBACK'); raise
    state.ledger.update(item, 'waiting' if phase == 'queued' else phase,
                        activity='waiting' if phase == 'queued' else 'failed')
    state.ledger.evidence(item['seq'], 'Relay preflight failed before Codex dispatch; original request retained.')
    return phase


def recover(state):
    # Old rows and attempted transports stay uncertain. Age/idle alone is no proof.
    rows = state.db.execute('''SELECT e.*,d.token FROM events e JOIN dispatch_attempts d ON d.seq=e.seq
        WHERE e.state='running' AND d.stage='preflight' AND e.result IS NULL AND e.receipt IS NULL''').fetchall()
    for row in rows:
        retry(state, dict(row, _dispatch_token=row['token']))
