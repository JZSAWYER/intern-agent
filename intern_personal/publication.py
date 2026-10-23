"""Delivered Git-only retries yield to new owner input in the same chat."""
import time


# Receipt plus terminal activity distinguishes publication from uncertain work.
# Internal timers, peer traffic and terminal echoes are not owner requests.
OWNER="""owner.role='agent' AND owner.state IN ('queued','running','awaiting_terminal','result_ready','preparing_reply','sending')
    AND (substr(owner.message_id,1,3)='om_' OR substr(owner.message_id,1,7)='queued:')
    AND owner.app_id=(SELECT app_id FROM identity WHERE singleton=1)
    AND owner.sender=(SELECT owner FROM identity WHERE singleton=1)"""


def schema(db):
    db.executescript('''CREATE TRIGGER IF NOT EXISTS yield_delivered_publication AFTER INSERT ON events
        WHEN NEW.role='agent' AND NEW.state='queued'
            AND (substr(NEW.message_id,1,3)='om_' OR substr(NEW.message_id,1,7)='queued:')
            AND NEW.app_id=(SELECT app_id FROM identity WHERE singleton=1)
            AND NEW.sender=(SELECT owner FROM identity WHERE singleton=1)
        BEGIN
            UPDATE events SET state='sync_pending' WHERE session=NEW.session AND seq<NEW.seq
                AND role='agent' AND state='result_ready' AND receipt IS NOT NULL AND result IS NOT NULL
                AND EXISTS(SELECT 1 FROM turn_records r WHERE r.seq=events.seq
                    AND r.activity IN ('completed','failed','stopped'));
        END;
        CREATE TRIGGER IF NOT EXISTS hold_delivered_publication_retry_v2 BEFORE UPDATE OF state ON events
        WHEN OLD.role='agent' AND OLD.state='sync_pending' AND NEW.state='result_ready'
            AND OLD.receipt IS NOT NULL AND OLD.result IS NOT NULL
            AND EXISTS(SELECT 1 FROM turn_records r WHERE r.seq=OLD.seq AND r.activity IN ('completed','failed','stopped'))
            AND EXISTS(SELECT 1 FROM events owner WHERE owner.session=OLD.session AND owner.seq>OLD.seq AND '''+OWNER+''')
        BEGIN
            SELECT RAISE(IGNORE);
        END;''')


def yield_to_owner(state):
    """Also adopt pre-upgrade backlogs, without touching an in-flight publisher."""
    state.db.execute('BEGIN IMMEDIATE')
    try:
        state.db.execute('''UPDATE events SET state='sync_pending' WHERE role='agent' AND state='result_ready'
            AND receipt IS NOT NULL AND result IS NOT NULL
            AND EXISTS(SELECT 1 FROM turn_records r WHERE r.seq=events.seq AND r.activity IN ('completed','failed','stopped'))
            AND EXISTS(SELECT 1 FROM events owner WHERE owner.session=events.session AND owner.seq>events.seq AND '''+OWNER+')')
        state.db.execute('''INSERT OR IGNORE INTO sync_retries(seq,retry_at)
            SELECT e.seq,? FROM events e JOIN turn_records r ON r.seq=e.seq
            WHERE e.role='agent' AND e.state='sync_pending' AND e.receipt IS NOT NULL
                AND e.result IS NOT NULL AND r.activity IN ('completed','failed','stopped')''',(time.time()+30,))
        state.db.execute('COMMIT')
    except BaseException:
        state.db.execute('ROLLBACK');raise
    # Lifecycle updates are separate from the intake transaction and remain
    # format-checked by the ordinary projector. No completion or sync is faked.
    rows=state.db.execute('''SELECT e.* FROM events e JOIN turn_records r ON r.seq=e.seq
        WHERE e.role='agent' AND e.state='sync_pending' AND e.receipt IS NOT NULL
            AND r.phase!='sync_pending' AND r.activity IN ('completed','failed','stopped') LIMIT 128''').fetchall()
    for row in rows:
        state.ledger.update(dict(row),'sync_pending',sync='pending')
        state.ledger.evidence(row['seq'],'Delivered result retained for Git-only retry; newer owner input has dispatch priority. No Codex replay.')
