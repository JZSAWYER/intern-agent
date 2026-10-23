"""Observe remote TUI turns without changing the relay's structured transport."""
import hashlib
import json
from pathlib import Path

from .core import Invalid
from .live_state import write_private
from .terminal_bridge import TerminalBridge


class ConsoleObserver(TerminalBridge):
    def __init__(self, state, tracker):
        super().__init__(state, tracker, table='console_views')

    def register(self, session, spec):
        with self.lock:
            row = self.binding(session)
            if row:
                previous = self.config_path(row)
                if previous['thread_id'] == session['thread_id']:
                    return
                snapshot=json.loads(row['snapshot'])
                if snapshot.get('active') or snapshot.get('pending'):
                    raise Invalid('Finish the remote terminal turn before replacing its view')
            paths = list((spec['codex_home'] / 'sessions').glob('**/rollout-*' + session['thread_id'] + '.jsonl'))
            if len(paths) != 1:
                raise Invalid('The exact console rollout is unavailable')
            rollout = paths[0]
            if rollout.is_symlink() or not rollout.resolve().is_relative_to(spec['codex_home'].resolve()):
                raise Invalid('Console history is outside its private home')
            path = spec['base'] / ('console-'+hashlib.sha256(session['thread_id'].encode()).hexdigest()[:16]+'.json')
            write_private(path, dict(thread_id=session['thread_id'], rollout=str(rollout),
                                    rollout_inode=rollout.stat().st_ino))
            cursor = rollout.stat().st_size
            self.state.db.execute('INSERT OR REPLACE INTO console_views VALUES(?,?,?,?)',
                                 (session['id'], str(path), cursor, json.dumps({'progress_after':cursor})))

    @staticmethod
    def config_path(row):
        from .live_state import private_json
        return private_json(row['config_path'])

    def prepare(self, session, text, seq):
        with self.lock:
            snapshot = self.poll(session)
            if snapshot.get('active') or snapshot.get('pending'):
                from .codex_bridge import TurnBusy
                raise TurnBusy(snapshot.get('active'))
            snapshot['pending'] = dict(seq=seq, prompt_sha256=hashlib.sha256(text.encode()).hexdigest(),
                                       cursor=self.binding(session)['cursor'])
            self.state.db.execute('UPDATE console_views SET snapshot=? WHERE session=?',
                                 (json.dumps(snapshot), session['id']))

    def cancel_pending(self, session, seq):
        with self.lock:
            row = self.binding(session)
            if not row:return
            snapshot = json.loads(row['snapshot'])
            if (snapshot.get('pending') or {}).get('seq') == seq:
                snapshot['pending'] = None
                self.state.db.execute('UPDATE console_views SET snapshot=? WHERE session=?',
                                     (json.dumps(snapshot),session['id']))
