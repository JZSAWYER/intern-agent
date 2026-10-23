"""Bound Git housekeeping notices, including senders running an older release.

These guards affect optional notices only. Results, publication retries, reviews,
assistant commentary and final-reply receipts are never changed here.
"""


def schema(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS progress_milestones(
            seq INTEGER NOT NULL,stage TEXT NOT NULL,created_at REAL NOT NULL,
            PRIMARY KEY(seq,stage));
        CREATE TABLE IF NOT EXISTS progress_sync_incidents(
            session TEXT PRIMARY KEY,seq INTEGER NOT NULL,created_at REAL NOT NULL);
    ''')
    # A sender may still use the old UPSERT that alternates uploading and
    # sync_pending. Filter the queued row atomically, before it can be read.
    body='''
        UPDATE progress_notices SET state='suppressed' WHERE seq=NEW.seq AND (
            EXISTS(SELECT 1 FROM events e JOIN turn_records r ON r.seq=e.seq
                WHERE e.seq=NEW.seq AND e.result IS NOT NULL
                    AND e.receipt IS NOT NULL AND e.receipt!=''
                    AND r.activity IN ('completed','failed','stopped'))
            OR EXISTS(SELECT 1 FROM progress_milestones h WHERE h.seq=NEW.seq
                AND (h.stage=NEW.stage OR (NEW.stage='uploading' AND h.stage='sync_pending')))
            OR (NEW.stage='sync_pending' AND EXISTS(
                SELECT 1 FROM progress_sync_incidents i JOIN events e ON e.session=i.session
                WHERE e.seq=NEW.seq)));
        INSERT INTO progress_milestones VALUES(NEW.seq,NEW.stage,NEW.created_at)
            ON CONFLICT(seq,stage) DO NOTHING;
        INSERT INTO progress_sync_incidents(session,seq,created_at)
            SELECT e.session,NEW.seq,NEW.created_at FROM events e
            JOIN progress_notices p ON p.seq=e.seq
            WHERE e.seq=NEW.seq AND e.session IS NOT NULL
                AND NEW.stage='sync_pending' AND p.state='pending'
            ON CONFLICT(session) DO NOTHING;
    '''
    for action in ('INSERT','UPDATE'):
        db.executescript('CREATE TRIGGER IF NOT EXISTS bound_git_notice_'+action.lower()+
            ' AFTER '+action+''' ON progress_notices
            WHEN NEW.state='pending' AND NEW.stage IN ('uploading','sync_pending')
            BEGIN '''+body+' END;')
    # A new failure can notify again only after the previous publication backlog
    # actually clears. An unrelated success must not re-arm an open incident.
    remaining='''SELECT 1 FROM events e JOIN turn_records r ON r.seq=e.seq
        WHERE e.session=progress_sync_incidents.session AND e.role='agent'
            AND e.result IS NOT NULL AND e.state NOT IN ('complete','cancelled','cancelled_policy_changed')
            AND r.sync!='synced' '''
    db.executescript('''CREATE TRIGGER IF NOT EXISTS close_git_notice_event
        AFTER UPDATE OF state ON events WHEN NEW.state='complete'
        BEGIN DELETE FROM progress_sync_incidents WHERE session=NEW.session
            AND NOT EXISTS('''+remaining+'''); END;
        CREATE TRIGGER IF NOT EXISTS close_git_notice_record
        AFTER UPDATE OF sync ON turn_records WHEN NEW.sync='synced'
        BEGIN DELETE FROM progress_sync_incidents WHERE session=NEW.conversation
            AND NOT EXISTS('''+remaining+'''); END;''')
    db.execute('BEGIN IMMEDIATE')
    try:
        # Adopt legacy attempted sends once. History prevents a later restart
        # from recreating an already resolved incident from an old notice row.
        db.execute('''INSERT OR IGNORE INTO progress_sync_incidents(session,seq,created_at)
            SELECT e.session,p.seq,p.created_at FROM progress_notices p
            JOIN events e ON e.seq=p.seq JOIN turn_records r ON r.seq=e.seq
            WHERE p.state='claimed' AND p.stage='sync_pending' AND e.session IS NOT NULL
                AND e.state NOT IN ('complete','cancelled','cancelled_policy_changed') AND r.sync!='synced'
                AND NOT EXISTS(SELECT 1 FROM progress_milestones h WHERE h.seq=p.seq AND h.stage=p.stage)
            ORDER BY p.created_at,p.seq''')
        db.execute('''INSERT OR IGNORE INTO progress_milestones
            SELECT seq,stage,created_at FROM progress_notices
            WHERE state='claimed' AND stage IN ('uploading','sync_pending')''')
        db.execute("UPDATE progress_notices SET state='pending' WHERE state='pending' AND stage IN ('uploading','sync_pending')")
        db.execute('COMMIT')
    except BaseException:
        db.execute('ROLLBACK');raise
