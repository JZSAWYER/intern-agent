"""Owner follow-ups join an existing turn; never interrupt or replay unknown input."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import threading
import time

from .core import Invalid


def schema(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS steered_inputs(
        seq INTEGER PRIMARY KEY,session TEXT NOT NULL,thread_id TEXT NOT NULL,turn_id TEXT NOT NULL,
        parent_seq INTEGER,transport TEXT NOT NULL,state TEXT NOT NULL,
        cursor INTEGER,inode INTEGER,accepted_at REAL,created_at REAL NOT NULL,updated_at REAL NOT NULL,
        error_type TEXT);
        CREATE VIEW IF NOT EXISTS steering_targets AS
        SELECT s.id AS session,s.thread_id,
            COALESCE(json_extract(n.snapshot,'$.active'),json_extract(c.snapshot,'$.active'),
                CASE WHEN o.status='inProgress' AND o.thread_id=s.thread_id THEN o.turn_id END) AS turn_id,
            CASE WHEN n.session IS NOT NULL THEN 'native' ELSE 'managed' END AS transport,
            CASE WHEN json_extract(n.snapshot,'$.active') IS NOT NULL THEN json_extract(n.snapshot,'$.event_seq')
                 WHEN json_extract(c.snapshot,'$.active') IS NOT NULL THEN json_extract(c.snapshot,'$.event_seq')
                 WHEN o.status='inProgress' AND o.thread_id=s.thread_id THEN o.event_seq END AS parent_seq
        FROM sessions s JOIN tracked_interns i ON i.session=s.id AND i.published=1
        LEFT JOIN terminal_attachments n ON n.session=s.id LEFT JOIN console_views c ON c.session=s.id
        LEFT JOIN codex_outcomes o ON o.session=s.id
        WHERE s.state='ready' AND s.thread_id IS NOT NULL;
        CREATE TRIGGER IF NOT EXISTS reserve_owner_steering AFTER INSERT ON events
        WHEN NEW.role='agent' AND NEW.state='queued' AND substr(NEW.message_id,1,3)='om_'
            AND NEW.sender=(SELECT owner FROM identity WHERE singleton=1)
            AND NEW.app_id=(SELECT app_id FROM identity WHERE singleton=1)
        BEGIN
            INSERT OR IGNORE INTO steered_inputs(seq,session,thread_id,turn_id,parent_seq,transport,state,created_at,updated_at)
            SELECT NEW.seq,t.session,t.thread_id,t.turn_id,t.parent_seq,t.transport,'pending',
                unixepoch('now'),unixepoch('now') FROM steering_targets t
                WHERE t.session=NEW.session AND t.turn_id IS NOT NULL AND t.turn_id!='';
            UPDATE events SET state='awaiting_terminal' WHERE seq=NEW.seq
                AND EXISTS(SELECT 1 FROM steered_inputs WHERE seq=NEW.seq AND state='pending');
        END;
        CREATE TRIGGER IF NOT EXISTS reserve_explicit_queue AFTER INSERT ON events
        WHEN NEW.role='control' AND NEW.state='queued' AND lower(substr(NEW.text,1,6))='/queue'
            AND substr(NEW.text,7,1) IN (' ',char(9),char(10))
            AND NEW.sender=(SELECT owner FROM identity WHERE singleton=1)
            AND NEW.app_id=(SELECT app_id FROM identity WHERE singleton=1)
            AND EXISTS(SELECT 1 FROM sessions WHERE id=NEW.session AND state='ready')
        BEGIN
            UPDATE events SET state='awaiting_terminal' WHERE seq=NEW.seq;
        END;''')
    from .owner_answers import schema as answer_schema
    answer_schema(db)
    from .publication import schema as publication_schema
    publication_schema(db)
    from .busy_inputs import schema as busy_schema
    busy_schema(db)


def queue_later(state,item):
    session=state.session(item.get('session'))
    if not session or item['sender']!=state.identity()['owner'] or not state.authorized(item):
        raise Invalid('Use /queue TEXT in the owner workspace chat')
    parts=item['text'].split(maxsplit=1)
    if len(parts)!=2 or not parts[1].strip():raise Invalid('Use /queue TEXT for a separate later task')
    state.db.execute('BEGIN IMMEDIATE')
    try:
        state.db.execute('''INSERT OR IGNORE INTO events(app_id,message_id,chat_id,sender,role,workspace,session,text,state)
            VALUES(?,?,?,?,'agent',?,?,?,'queued')''',(item['app_id'],'queued:'+item['message_id'],session['chat_id'],
                item['sender'],session['workspace'],session['id'],parts[1]))
        child=dict(state.db.execute('SELECT * FROM events WHERE app_id=? AND message_id=?',
            (item['app_id'],'queued:'+item['message_id'])).fetchone())
        state.ledger.ensure_turn(child,time.time(),'feishu')
        state.db.execute('COMMIT')
    except BaseException:
        state.db.execute('ROLLBACK');raise
    return 'Saved as a separate later turn. It will start after this Codex turn finishes.'


def reserve_waiting(state):
    """The same intake boundary also adopts proven unclaimed legacy messages."""
    db = state.db
    db.execute('BEGIN IMMEDIATE')
    try:
        db.execute('''INSERT OR IGNORE INTO steered_inputs
            (seq,session,thread_id,turn_id,parent_seq,transport,state,created_at,updated_at)
            SELECT e.seq,t.session,t.thread_id,t.turn_id,t.parent_seq,t.transport,'pending',?,?
            FROM events e JOIN steering_targets t ON t.session=e.session JOIN identity id ON id.singleton=1
            WHERE e.role='agent' AND e.state='queued' AND substr(e.message_id,1,3)='om_'
                AND e.sender=id.owner AND e.app_id=id.app_id AND t.turn_id IS NOT NULL AND t.turn_id!=''
                AND NOT EXISTS(SELECT 1 FROM dispatch_attempts d WHERE d.seq=e.seq)
                AND NOT EXISTS(SELECT 1 FROM background_event_scope b WHERE b.seq=e.seq)''', (time.time(),time.time()))
        db.execute("UPDATE events SET state='awaiting_terminal' WHERE state='queued' AND seq IN (SELECT seq FROM steered_inputs WHERE state='pending')")
        db.execute('COMMIT')
    except BaseException:
        db.execute('ROLLBACK'); raise


class Steering:
    def __init__(self, state, host=None, terminal=None, api=None):
        from .codex_bridge import CodexHost
        from .terminal_bridge import TerminalBridge
        from .workspace_tracking import WorkspaceTracking
        self.state = state
        self.api = api
        self.host = host or CodexHost(state)
        self.terminal = terminal or TerminalBridge(state, WorkspaceTracking(state))

    def dirty(self, session):
        self.state.db.execute('INSERT OR IGNORE INTO bot_tracking_pending VALUES(?,NULL,0)', (session['id'],))

    def notify(self, row, stage, text):
        from .auto_recovery import notice
        notice(self.state, dict(id='steer-'+str(row['seq']),session=row['session']), text, stage)

    def fallback(self, row, reason):
        db = self.state.db
        db.execute('BEGIN IMMEDIATE')
        try:
            stopped=self.state.ledger.stopped(row['seq']) or db.execute("SELECT 1 FROM events WHERE seq=? AND state='cancelled'",(row['seq'],)).fetchone()
            changed = db.execute("UPDATE steered_inputs SET state=?,error_type=?,updated_at=? WHERE seq=? AND state IN ('pending','sending')",
                                 ('cancelled' if stopped else 'fallback',reason,time.time(),row['seq'])).rowcount
            if changed:
                db.execute("UPDATE events SET state=? WHERE seq=? AND state='awaiting_terminal'", ('cancelled' if stopped else 'queued',row['seq']))
            db.execute('COMMIT')
        except BaseException:
            db.execute('ROLLBACK'); raise
        if changed:
            event = dict(db.execute('SELECT * FROM events WHERE seq=?',(row['seq'],)).fetchone())
            self.state.ledger.update(event,'cancelled' if stopped else 'received',activity='stopped' if stopped else 'waiting')

    def accepted(self, row):
        db = self.state.db
        db.execute('BEGIN IMMEDIATE')
        try:
            changed = db.execute("UPDATE steered_inputs SET state='accepted',accepted_at=COALESCE(accepted_at,?),updated_at=?,error_type=NULL WHERE seq=? AND state IN ('pending','sending','review_needed')",
                                 (time.time(),time.time(),row['seq'])).rowcount
            if changed:
                db.execute("UPDATE events SET state='awaiting_terminal' WHERE seq=? AND state='uncertain_execution'", (row['seq'],))
                self.notify(row,'accepted','Delivered to Codex without stopping its current work.')
            db.execute('COMMIT')
        except BaseException:
            db.execute('ROLLBACK');raise
        if changed:self.record_accepted(row)

    def record_accepted(self,row):
        db=self.state.db
        event = dict(db.execute('SELECT * FROM events WHERE seq=?',(row['seq'],)).fetchone())
        self.state.ledger.bind_turn(event)
        self.state.ledger.update(event,'awaiting_terminal',activity='running')
        self.state.ledger.evidence(row['seq'],'Owner follow-up accepted into the existing Codex turn; shared completion, not a new task.')
        self.dirty(self.state.session(row['session']))

    def typing(self,event):
        if not self.api or self.state.db.execute('SELECT 1 FROM message_reactions WHERE seq=?',(event['seq'],)).fetchone():return
        try:
            reaction=self.api.add_typing(event['message_id'])
            if reaction:self.state.db.execute('INSERT OR IGNORE INTO message_reactions(seq,reaction_id) VALUES(?,?)',(event['seq'],reaction))
        except Exception:pass  # Optional reaction cannot block the owner's update.

    def unknown(self, row, error):
        changed=self.state.db.execute("UPDATE steered_inputs SET state='review_needed',error_type=?,updated_at=? WHERE seq=? AND state='sending'",
                              (type(error).__name__,time.time(),row['seq'])).rowcount
        if not changed:return
        self.state.db.execute("UPDATE events SET state='uncertain_execution' WHERE seq=? AND state='awaiting_terminal'",(row['seq'],))
        event = dict(self.state.db.execute('SELECT * FROM events WHERE seq=?',(row['seq'],)).fetchone())
        self.state.ledger.update(event,'uncertain_execution',activity='unknown',sync='blocked')
        self.notify(row,'review','The live follow-up could not be confirmed. Its original text is saved; it will not be resent automatically. The existing Codex turn was not stopped.')

    def boundary(self, row, bridge, session):
        """Pin a transcript boundary before transport; never scan historical matches."""
        try:
            config = bridge.config(session)
            path = Path(config['rollout'])
            info = path.stat()
            if info.st_ino != config['rollout_inode']: return False
            self.state.db.execute('UPDATE steered_inputs SET cursor=?,inode=? WHERE seq=? AND state=\'pending\'',
                                  (info.st_size,info.st_ino,row['seq']))
            return True
        except (OSError,ValueError,KeyError,Invalid):
            return False

    def dispatch(self, row):
        from .codex_bridge import RequestRejected
        state = self.state
        event = dict(state.db.execute('SELECT * FROM events WHERE seq=?',(row['seq'],)).fetchone())
        session = state.session(row['session'])
        if event['state'] == 'cancelled' or state.ledger.stopped(row['seq']) or state.ledger.session_stopped(row['session']):
            state.db.execute("UPDATE steered_inputs SET state='cancelled',updated_at=? WHERE seq=?",(time.time(),row['seq']))
            state.transition(row['seq'],'cancelled',result='Cancelled before live steering.')
            state.ledger.update(event,'cancelled',activity='stopped',completed_at=time.time());return
        if not session or session['state']!='ready' or session['thread_id'] != row['thread_id'] or not state.authorized(event):
            self.fallback(row,'binding_changed'); return
        if state.db.execute('SELECT 1 FROM attachments WHERE seq=?',(row['seq'],)).fetchone():
            self.fallback(row,'attachment_uses_normal_dispatch'); return
        if row['parent_seq'] and state.db.execute("SELECT 1 FROM peer_event_scope WHERE seq=? AND phase='request'",(row['parent_seq'],)).fetchone():
            self.fallback(row,'read_only_peer_turn'); return
        self.typing(event)
        bridge = self.terminal if row['transport']=='native' else self.host.observer
        bound=self.boundary(row,bridge,session)
        if row['transport']=='native':
            if not bound:
                self.fallback(row,'native_transcript_unavailable'); return
            state.ledger.bind_turn(event)
            self.native(row,session,event); return
        connection = self.host.connect(session=session)
        try:
            thread = connection.request('thread/read',{'threadId':row['thread_id'],'includeTurns':False},timeout=5)['thread']
            if thread.get('id') != row['thread_id']:
                self.fallback(row,'binding_changed'); return
            if thread.get('status',{}).get('type') != 'active':
                self.fallback(row,'turn_ended_before_dispatch'); return
            if state.ledger.stopped(row['seq']) or state.ledger.session_stopped(row['session']):
                self.fallback(row,'stopped_before_dispatch'); return
            state.ledger.bind_turn(event)
            claimed=state.db.execute("UPDATE steered_inputs SET state='sending',updated_at=? WHERE seq=? AND state='pending'",(time.time(),row['seq'])).rowcount
            if not claimed: return
            try:
                result = connection.request('turn/steer',{'threadId':row['thread_id'],'expectedTurnId':row['turn_id'],
                    'input':[{'type':'text','text':event['text']}]},timeout=10)
                if result.get('turnId') != row['turn_id']:
                    raise ValueError('Uncorrelated steering receipt')
            except RequestRejected:
                self.fallback(row,'explicit_rejection_no_input_accepted')
            except Exception as error:
                self.unknown(row,error)
            else:
                self.accepted(row)
        finally:
            connection.close()

    def native(self,row,session,event):
        from .terminal_bridge import tmux, verify_pane, empty_prompt
        # Preserve drafts and use Enter's native steering behavior. Never Escape,
        # Ctrl-C, a replacement process, or an overwritten terminal input.
        with self.terminal.input_lock:
            config=self.terminal.config(session)
            try:
                verify_pane(config,timeout=1); empty_prompt(config)
            except Exception:
                self.fallback(row,'native_prompt_not_empty'); return
            if self.state.ledger.stopped(row['seq']): return
            changed=self.state.db.execute("UPDATE steered_inputs SET state='sending',updated_at=? WHERE seq=? AND state='pending'",(time.time(),row['seq'])).rowcount
            if not changed:return
            name='ia-steer-'+str(row['seq'])
            try:
                tmux(config,'load-buffer','-b',name,'-',data=event['text'].encode(),timeout=1)
                verify_pane(config,timeout=1)
                tmux(config,'paste-buffer','-p','-d','-b',name,'-t',config['pane'],timeout=1)
                tmux(config,'send-keys','-t',config['pane'],'Enter',timeout=1)
            except Exception as error:
                self.unknown(row,error)
            finally:
                try:tmux(config,'delete-buffer','-b',name,timeout=1)
                except Exception:pass
            # Native acceptance requires the exact post-boundary transcript;
            # successful keystrokes alone are not an acknowledgement.

    def observed(self, row):
        if row['cursor'] is None or row['inode'] is None: return False
        from .terminal_bridge import native_context_message, MAX_LINE
        session=self.state.session(row['session'])
        bridge=self.terminal if row['transport']=='native' else self.host.observer
        try:
            path=Path(bridge.config(session)['rollout'])
            if path.stat().st_ino!=row['inode'] or path.stat().st_size<row['cursor']:return False
            event=self.state.db.execute('SELECT text FROM events WHERE seq=?',(row['seq'],)).fetchone()
            expected=hashlib.sha256(event['text'].replace('\r\n','\n').strip().encode()).hexdigest()
            with path.open('rb') as stream:
                stream.seek(row['cursor']); active=row['turn_id']
                for _ in range(2000):
                    line=stream.readline(MAX_LINE+1)
                    if not line or len(line)>MAX_LINE or not line.endswith(b'\n'):return False
                    value=json.loads(line);payload=value.get('payload',{})
                    if value.get('type')=='event_msg':
                        if payload.get('type')=='task_started':active=payload.get('turn_id')
                        if payload.get('type') in {'task_complete','turn_aborted'} and payload.get('turn_id')==active:return False
                    if (active==row['turn_id'] and value.get('type')=='response_item'
                            and payload.get('type')=='message' and payload.get('role')=='user'
                            and not native_context_message(payload)):
                        text='\n'.join(x.get('text','') for x in payload.get('content',[]) if x.get('type')=='input_text')
                        if hashlib.sha256(text.replace('\r\n','\n').strip().encode()).hexdigest()==expected:
                            # Identical later input cannot certify an earlier
                            # uncertain send. Acceptance receipts stay separate.
                            others=self.state.db.execute('''SELECT e.text FROM steered_inputs s JOIN events e ON e.seq=s.seq
                                WHERE s.session=? AND s.turn_id=? AND s.seq!=? AND s.cursor<=?
                                    AND s.state IN ('sending','accepted','review_needed','completed')''',
                                (row['session'],row['turn_id'],row['seq'],stream.tell())).fetchall()
                            if any(hashlib.sha256(other['text'].replace('\r\n','\n').strip().encode()).hexdigest()==expected for other in others):return False
                            return True
        except (OSError,ValueError,KeyError,Invalid):pass
        return False

    def complete(self,row):
        """Follow the shared authoritative completion; never rerun/publish a child."""
        state=self.state
        terminal=state.db.execute('SELECT * FROM terminal_turns WHERE session=? AND turn_id=?',(row['session'],row['turn_id'])).fetchone()
        observed=state.db.execute('''SELECT 1 FROM codex_outcomes WHERE session=? AND thread_id=? AND turn_id=?
            AND status IN ('completed','failed','interrupted')''',(row['session'],row['thread_id'],row['turn_id'])).fetchone()
        if not terminal and not observed:return
        parent_seq=terminal['event_seq'] if terminal else row['parent_seq']
        if not parent_seq:return
        parent=state.db.execute('SELECT * FROM events WHERE seq=?',(parent_seq,)).fetchone()
        if not parent or not parent['receipt'] or parent['state'] not in {'complete','record_blocked','result_ready','preparing_reply','sync_pending'}:return
        outcome=state.db.execute('SELECT * FROM turn_records WHERE seq=?',(parent_seq,)).fetchone()
        if not outcome or outcome['activity'] not in {'completed','stopped','failed'}:return
        ack=state.db.execute('SELECT receipt FROM recovery_notices WHERE id=?',('steer-'+str(row['seq'])+':accepted',)).fetchone()
        if not ack or not ack['receipt']:return
        event=dict(state.db.execute('SELECT * FROM events WHERE seq=?',(row['seq'],)).fetchone())
        # A delivered manual result can still await Git because this very
        # follow-up holds the session's dispatch lane. Release execution now;
        # retain the parent's unsynced history without scheduling a child run.
        phase=('complete' if parent['state']=='complete' and outcome['phase']=='complete' and outcome['sync']=='synced'
               else 'record_blocked' if parent['state']=='record_blocked' else 'reply_ready')
        current=state.db.execute('SELECT sync,code_end,code_start,baseline FROM turn_records WHERE seq=?',(row['seq'],)).fetchone()
        # A request can acquire a real preflight snapshot before a busy refusal
        # routes it into shared execution. Never erase/replace that immutable
        # baseline with its parent's (possibly absent) snapshot. Legacy clears
        # can recover the same head from the request's retained baseline.
        captured=json.loads(current['baseline']).get('head') if current and current['baseline'] else None
        code_start=(current['code_start'] if current else None) or captured or outcome['code_start']
        if row['state']=='completed' and event['state']==phase:
            if current and current['sync']==outcome['sync'] and current['code_end']==outcome['code_end'] and current['code_start']==code_start:return
        if event['state']=='cancelled':
            state.db.execute("UPDATE steered_inputs SET state='cancelled' WHERE seq=?",(row['seq'],));return
        final='Follow-up handled in the shared Codex turn. See the final reply for turn '+str(parent_seq)+'.'
        state.ledger.evidence(row['seq'],'Shares execution and final delivery with parent turn '+str(parent_seq)+'; any captured Git baseline is retained. Not a separate Codex run.')
        for proof in json.loads(outcome['evidence']):
            if 'task_complete' in proof or 'turn/completed status completed' in proof:
                state.ledger.evidence(row['seq'],'Shared parent turn '+str(parent_seq)+': '+proof)
        state.transition(row['seq'],phase,result=final,receipt=ack['receipt'])
        state.ledger.update(event,phase,activity=outcome['activity'],sync=outcome['sync'],
            completed_at=outcome['completed_at'],code_start=code_start,code_end=outcome['code_end'])
        state.db.execute("UPDATE steered_inputs SET state='completed',parent_seq=?,updated_at=? WHERE seq=?",(parent_seq,time.time(),row['seq']))
        self.dirty(state.session(row['session']))

    def scan(self):
        if self.api:
            from .model_preferences import poll
            poll(self)
        from .busy_inputs import recover
        recover(self)
        from .publication import yield_to_owner
        yield_to_owner(self.state)
        from .incident_lifecycle import reconcile
        reconcile(self.state)
        # Old receivers can keep running: intake reserves this command before
        # their command worker sees it, and that worker delivers the saved reply.
        for command in self.state.db.execute("SELECT * FROM events WHERE role='control' AND state='awaiting_terminal' AND lower(substr(text,1,6))='/queue' ORDER BY seq LIMIT 16").fetchall():
            item=dict(command)
            result=queue_later(self.state,item)
            self.state.transition(item['seq'],'result_ready',result=result)
        reserve_waiting(self.state)
        rows=self.state.db.execute("""SELECT s.* FROM steered_inputs s
            WHERE (s.state='pending' AND NOT EXISTS(SELECT 1 FROM steered_inputs earlier
                WHERE earlier.session=s.session AND earlier.seq<s.seq
                    AND earlier.state IN ('pending','sending','review_needed')))
            OR s.state IN ('sending','review_needed','accepted')
            OR (s.state='completed' AND EXISTS(SELECT 1 FROM events e JOIN events p ON p.seq=s.parent_seq
                WHERE e.seq=s.seq AND e.state IN ('record_blocked','reply_ready')
                    AND (e.state!=p.state OR EXISTS(SELECT 1 FROM turn_records c JOIN turn_records r ON r.seq=p.seq
                        WHERE c.seq=e.seq AND (c.sync!=r.sync OR c.code_end IS NOT r.code_end
                            OR (c.code_start IS NULL AND c.baseline IS NOT NULL))))))
            ORDER BY CASE s.state WHEN 'pending' THEN 0 WHEN 'sending' THEN 1 WHEN 'review_needed' THEN 1 ELSE 2 END,s.seq LIMIT 128""").fetchall()
        self.last_error=None
        for item in rows:
            row=dict(item)
            if row['state']=='pending' and row['error_type'] and time.time()-row['updated_at']<5:continue
            try:
                if row['state']=='pending':
                    self.dispatch(row)
                elif row['state'] in {'sending','review_needed'}:
                    if self.observed(row):self.accepted(row)
                    elif row['state']=='sending' and time.time()-row['updated_at']>20:
                        self.unknown(row,TimeoutError())
                elif row['state'] in {'accepted','completed'}:
                    ledger=self.state.db.execute('SELECT phase FROM turn_records WHERE seq=?',(row['seq'],)).fetchone()
                    if ledger and ledger['phase'] in {'received','running','uncertain_execution'}:self.record_accepted(row)
                    self.complete(row)
                    if row['state']=='accepted':
                        from .owner_answers import collect
                        collect(self,row)
            except Exception as error:
                self.last_error=type(error).__name__
                self.state.db.execute('UPDATE steered_inputs SET error_type=?,updated_at=? WHERE seq=?',
                                      (self.last_error,time.time(),row['seq']))


def loop(state, stop, *, handoff=False):
    lock=os.open(state.runtime/'steering.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    held=False
    from .live_state import private_json,write_private
    original=private_json(state.root/'health.json')['pid'] if handoff else None
    try:
        from .feishu_live import FeishuAPI
        import lark_oapi
        lark_oapi.logger.disabled=True
        worker=Steering(state,api=FeishuAPI(state.credentials()))
        adopted=False
        while not stop.is_set():
            if not held:
                try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);held=True
                except BlockingIOError:stop.wait(1);continue
            if not adopted:
                from .model_preferences import adopt,configure_defaults
                adopt(state);configure_defaults(state);adopted=True
            if handoff:
                receiver=private_json(state.root/'health.json')
                if receiver['pid']!=original and receiver.get('steering_worker_alive') is True:return
            try:
                worker.scan()
                error=worker.last_error
            except Exception as failure:error=type(failure).__name__
            write_private(state.runtime/'steering-health.json',dict(pid=os.getpid(),updated_at=time.time(),error_type=error))
            stop.wait(.5 if error is None else 2)
    finally:
        os.close(lock);state.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state',required=True);parser.add_argument('--handoff',action='store_true')
    args=parser.parse_args()
    from .live_state import LiveState
    state=LiveState(args.state);stop=threading.Event()
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:stop.set())
    loop(state,stop,handoff=args.handoff)


if __name__=='__main__':main()
