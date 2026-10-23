"""Private state for the opt-in live relay; independent of legacy records."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import tempfile
import threading
import time
import uuid

from .core import Invalid, WORKSPACE, github_url, git_environment, valid_owner


IDENTIFIER = re.compile(r"(?:cli|ou|oc|om)_[A-Za-z0-9_]+")


def identifier(value, prefix):
    return isinstance(value, str) and bool(re.fullmatch(prefix + r"_[A-Za-z0-9_]+", value))


def private_json(path):
    path = Path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise Invalid("Private configuration must be owned by you and have permission 0600")
        return json.load(stream)


def write_private(path, value):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class LiveState:
    def __init__(self, root):
        original = Path(root).expanduser()
        if original.is_symlink():
            raise Invalid("Live state root must be a real private directory")
        self.root = original.resolve()
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        info = self.root.stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise Invalid("Live state root must be owned by you and have permission 0700")
        self.runtime = self.root / "runtime"
        self.runtime.mkdir(mode=0o700, exist_ok=True)
        if self.runtime.is_symlink() or self.runtime.stat().st_mode & 0o077:
            raise Invalid("Runtime directory must be private")
        self.path = self.runtime / "relay.sqlite3"
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        if self.path.stat().st_mode & 0o077:
            raise Invalid("Runtime database must be private")
        self.local = threading.local()
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS identity (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1), app_id TEXT,
                owner TEXT, management_chat TEXT, pairing_hash TEXT, pairing_until REAL);
            INSERT OR IGNORE INTO identity(singleton) VALUES(1);
            CREATE TABLE IF NOT EXISTS workspaces (
                id TEXT PRIMARY KEY, cwd TEXT NOT NULL UNIQUE);
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, workspace TEXT NOT NULL, chat_id TEXT UNIQUE,
                thread_id TEXT, state TEXT NOT NULL, creation_key TEXT NOT NULL UNIQUE);
            CREATE TABLE IF NOT EXISTS controller_routes (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1), app_id TEXT NOT NULL,
                chat_id TEXT NOT NULL, session TEXT NOT NULL UNIQUE);
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, app_id TEXT NOT NULL,
                message_id TEXT NOT NULL, chat_id TEXT NOT NULL, sender TEXT NOT NULL,
                role TEXT NOT NULL, workspace TEXT NOT NULL, session TEXT,
                text TEXT NOT NULL, state TEXT NOT NULL, result TEXT, receipt TEXT,
                UNIQUE(app_id,message_id));
            CREATE TABLE IF NOT EXISTS message_reactions (
                seq INTEGER PRIMARY KEY, reaction_id TEXT NOT NULL,
                retry_at REAL NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS session_settings (
                session TEXT PRIMARY KEY, model TEXT NOT NULL, effort TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS session_hosts (
                session TEXT PRIMARY KEY, tmux_name TEXT NOT NULL UNIQUE,
                tmux_socket TEXT NOT NULL, agent_socket TEXT NOT NULL UNIQUE,
                codex_home TEXT NOT NULL UNIQUE, view_thread TEXT);
            CREATE TABLE IF NOT EXISTS tracked_workspaces (
                workspace TEXT PRIMARY KEY, repo_url TEXT NOT NULL, source_path TEXT NOT NULL,
                metadata_path TEXT NOT NULL, metadata_branch TEXT NOT NULL, base_branch TEXT NOT NULL,
                published INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS tracked_interns (
                session TEXT PRIMARY KEY, intern_name TEXT NOT NULL UNIQUE,
                code_path TEXT NOT NULL, code_branch TEXT NOT NULL, published INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS tracking_pending (
                workspace TEXT PRIMARY KEY, error_type TEXT, retry_at REAL NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS terminal_attachments (
                session TEXT PRIMARY KEY, config_path TEXT NOT NULL,
                cursor INTEGER NOT NULL DEFAULT 0, snapshot TEXT NOT NULL DEFAULT '{}');
            CREATE TABLE IF NOT EXISTS console_views (
                session TEXT PRIMARY KEY, config_path TEXT NOT NULL,
                cursor INTEGER NOT NULL DEFAULT 0, snapshot TEXT NOT NULL DEFAULT '{}');
            CREATE TABLE IF NOT EXISTS terminal_turns (
                session TEXT NOT NULL, turn_id TEXT NOT NULL, event_seq INTEGER NOT NULL,
                result TEXT NOT NULL, status TEXT NOT NULL,
                PRIMARY KEY(session,turn_id));
            CREATE TABLE IF NOT EXISTS git_hook_bindings (
                directory TEXT PRIMARY KEY, config_path TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS named_tasks (
                id TEXT PRIMARY KEY, workspace TEXT NOT NULL, name TEXT NOT NULL,
                state TEXT NOT NULL, session TEXT NOT NULL, created_at REAL NOT NULL,
                updated_at REAL NOT NULL, revision INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS conversation_tasks (
                session TEXT PRIMARY KEY, task_id TEXT NOT NULL UNIQUE);
            CREATE TABLE IF NOT EXISTS turn_records (
                seq INTEGER PRIMARY KEY, workspace TEXT NOT NULL, conversation TEXT NOT NULL,
                task_id TEXT, source TEXT NOT NULL, received_at REAL, completed_at REAL,
                phase TEXT NOT NULL, activity TEXT NOT NULL, sync TEXT NOT NULL,
                code_start TEXT, code_end TEXT, baseline TEXT, evidence TEXT NOT NULL DEFAULT '[]',
                stop_requested INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL,
                projected_revision INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS record_events (
                revision INTEGER PRIMARY KEY AUTOINCREMENT, workspace TEXT NOT NULL,
                kind TEXT NOT NULL, record_id TEXT NOT NULL, phase TEXT NOT NULL, at REAL NOT NULL,
                conversation TEXT, task_id TEXT, turn_id TEXT);
            CREATE TABLE IF NOT EXISTS sync_retries (
                seq INTEGER PRIMARY KEY, retry_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS event_failures (
                seq INTEGER PRIMARY KEY, kind TEXT NOT NULL, at REAL NOT NULL);
        ''')
        # Additive upgrades preserve existing queues, attachment cursors and records.
        upgrades = {
            'tracked_interns': {'home_branch': "TEXT NOT NULL DEFAULT ''",'metadata_mode': "TEXT NOT NULL DEFAULT 'metadata_branch'"},
            'tracked_workspaces': {'metadata_mode': "TEXT NOT NULL DEFAULT 'metadata_branch'"},
            'named_tasks': {'code_branch': 'TEXT', 'criteria': "TEXT NOT NULL DEFAULT ''",
                            'acceptance': 'TEXT'},
        }
        for table, columns in upgrades.items():
            existing = {row['name'] for row in self.db.execute('PRAGMA table_info('+table+')')}
            for name, declaration in columns.items():
                if name not in existing:
                    self.db.execute('ALTER TABLE '+table+' ADD COLUMN '+name+' '+declaration)
        self.db.execute("UPDATE tracked_interns SET home_branch=code_branch WHERE home_branch=''")
        self.db.execute('''CREATE TABLE IF NOT EXISTS workspace_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT, workspace TEXT NOT NULL, task_id TEXT,
            kind TEXT NOT NULL, body TEXT NOT NULL, at REAL NOT NULL, revision INTEGER NOT NULL)''')
        self.db.execute('''CREATE TABLE IF NOT EXISTS restored_conversations (
            session TEXT PRIMARY KEY, code_branch TEXT, intern_name TEXT)''')
        if 'metadata_mode' not in {r['name'] for r in self.db.execute('PRAGMA table_info(restored_conversations)')}:
            self.db.execute("ALTER TABLE restored_conversations ADD COLUMN metadata_mode TEXT NOT NULL DEFAULT 'metadata_branch'")
        self.db.execute('CREATE TABLE IF NOT EXISTS bot_tracking_pending(session TEXT PRIMARY KEY,error_type TEXT,retry_at REAL NOT NULL DEFAULT 0)')
        from .auto_recovery import schema as recovery_schema
        recovery_schema(self.db)
        from .delivery import schema as delivery_schema
        delivery_schema(self.db)
        from .lifecycle import Lifecycle
        self.ledger = Lifecycle(self)
        from .teams import schema
        schema(self.db)
        from .peer_mail import schema as peer_schema
        peer_schema(self.db)
        from .draft_baselines import schema as draft_schema
        draft_schema(self.db)
        self.db.execute('CREATE TABLE IF NOT EXISTS conversation_usage (session TEXT PRIMARY KEY,usage TEXT NOT NULL)')
        from .quota import schema as quota_schema
        quota_schema(self.db)
        from .throughput import schema as throughput_schema
        throughput_schema(self.db)
        from .progress import schema as progress_schema
        progress_schema(self.db)
        from .triggers import schema as trigger_schema
        trigger_schema(self.db)
        from .feature_state import schema as feature_schema
        feature_schema(self.db)
        from .access import schema as access_schema
        access_schema(self.db)
        from .project_init import schema as initialization_schema
        initialization_schema(self.db)
        from .speed import schema as speed_schema
        speed_schema(self.db)
        self.db.execute('''CREATE TABLE IF NOT EXISTS attachments(seq INTEGER NOT NULL,ordinal INTEGER NOT NULL,
            resource_key TEXT NOT NULL,name TEXT NOT NULL,type TEXT NOT NULL,sha256 TEXT,PRIMARY KEY(seq,ordinal))''')
        from .file_transfer import schema as file_schema
        file_schema(self.db)
        from .research import schema as research_schema
        research_schema(self.db)
        from .dispatch import schema as dispatch_schema
        dispatch_schema(self.db)
        from .steering import schema as steering_schema
        steering_schema(self.db)
        from .model_preferences import schema as model_schema
        model_schema(self.db)
        from .incident_lifecycle import schema as incident_schema
        incident_schema(self.db)
        from .attachment_batches import schema as attachment_schema
        attachment_schema(self.db)
        from .turn_uploads import schema as upload_schema
        upload_schema(self.db)
        self.db.execute('''CREATE TABLE IF NOT EXISTS restore_receipts(workspace TEXT PRIMARY KEY,
            repo_url TEXT NOT NULL,branch TEXT NOT NULL,commit_id TEXT NOT NULL)''')
        self.db.execute("CREATE TABLE IF NOT EXISTS restored_roles(session TEXT PRIMARY KEY,role TEXT NOT NULL,prior_state TEXT NOT NULL DEFAULT 'ready')")
        self.db.execute('CREATE TABLE IF NOT EXISTS workspace_preference_versions(workspace TEXT PRIMARY KEY,fingerprint TEXT NOT NULL)')
        from .release_sync import schema as release_schema
        release_schema(self.db)

    @property
    def db(self):
        if not hasattr(self.local, "connection"):
            self.local.connection = sqlite3.connect(self.path, timeout=15, isolation_level=None)
            self.local.connection.row_factory = sqlite3.Row
            self.local.connection.execute("PRAGMA synchronous=FULL")
        return self.local.connection

    def close(self):
        if hasattr(self.local, "connection"):
            self.local.connection.close()
            del self.local.connection

    def config(self):
        config = private_json(self.root / "live.json")
        if (config.get("schema") != "intern-agent.live.v1" or not valid_owner(config.get("github_owner"))
                or config.get("sandbox") not in {"read-only", "workspace-write", "danger-full-access"}
                or not isinstance(config.get("model"), str) or not config["model"]
                or not isinstance(config.get("codex_command"), list) or not config["codex_command"]
                or any(not isinstance(x, str) or not x or "\0" in x for x in config["codex_command"])):
            raise Invalid("Invalid live configuration; complete the local onboarding first")
        from .turn_limits import seconds
        seconds(config)
        from .execution_policy import settings
        settings(config)
        from .auto_recovery import validate as validate_recovery
        validate_recovery(config)
        return config

    def note_failure(self, seq, error):
        # Persist only a bounded category, never exception text, prompts or tokens.
        from .codex_bridge import TurnTimedOut
        kind = 'execution_timeout' if isinstance(error, TurnTimedOut) else 'execution_unconfirmed'
        self.db.execute('INSERT OR REPLACE INTO event_failures VALUES(?,?,?)', (seq, kind, time.time()))

    def turn_status(self, session=None):
        query = '''SELECT e.seq,e.state,e.result,e.receipt,r.activity,r.completed_at,f.kind,o.state AS delivery_state,
            g.reason,g.attempts AS git_attempts,g.updated_at AS git_failed_at,x.retry_at,d.stage AS dispatch_stage,
            EXISTS(SELECT 1 FROM terminal_turns t WHERE t.event_seq=e.seq AND t.status='turn_aborted') AS interrupted
            FROM events e LEFT JOIN turn_records r ON r.seq=e.seq
            LEFT JOIN git_sync_errors g ON g.seq=e.seq AND g.resolved_at IS NULL
            LEFT JOIN sync_retries x ON x.seq=e.seq LEFT JOIN dispatch_attempts d ON d.seq=e.seq
            LEFT JOIN event_failures f ON f.seq=e.seq LEFT JOIN reply_outbox o ON o.seq=e.seq WHERE e.role='agent'
            AND e.state IN ('queued','uncertain_execution','uncertain_delivery','record_blocked','sync_pending','awaiting_terminal','running','preparing_reply','sending','result_ready')'''
        parameters = ()
        if session:
            query += ' AND e.session=?'
            parameters = (session,)
        rows = self.db.execute(query+' ORDER BY e.seq', parameters).fetchall()
        lines=[]
        files=self.db.execute("SELECT COUNT(*) FROM incoming_file_batches WHERE state='waiting'"+
            (' AND session=?' if session else ''),parameters).fetchone()[0]
        if files:lines.append(str(files)+' uploads waiting for the sender\'s next message. Use /files pending or /files clear.')
        if not rows:return '\n'.join(lines) or 'No blocked or running turns.'
        for row in rows:
            lines.append('Turn '+str(row['seq'])+': '+row['state']+' | agent '+(row['activity'] or 'unknown'))
            if row['dispatch_stage']=='preflight' and row['state']=='running':
                lines.append('Preparing the Git workspace; this message has not reached Codex yet.')
            elif row['activity'] in {'completed','failed','stopped'} and row['state'] in {'result_ready','preparing_reply','sync_pending','record_blocked'}:
                elapsed=max(0,int(time.time()-row['completed_at'])) if row['completed_at'] else None
                lines.append('Codex finished'+(' '+str(elapsed)+' seconds ago' if elapsed is not None else '')+
                    '; this turn is waiting for confirmed GitHub code/history upload.')
            if row['reason']:
                lines.append('Git blocker: '+row['reason']+' (attempt '+str(row['git_attempts'])+').')
                if row['retry_at']:lines.append('Next Git-only retry in '+str(max(0,int(row['retry_at']-time.time())))+' seconds; Codex will not run again.')
            if row['delivery_state']:lines.append('Feishu delivery: '+row['delivery_state'])
            if row['state']=='queued':
                lines.append('Received and waiting; this message has not reached Codex. Earlier work or tracking review may be holding the queue. Do not resend it.')
            if row['kind']=='execution_timeout':
                lines.append('Cause: the relay reached its configured execution time limit.')
            elif row['kind']=='execution_failed':
                lines.append('Cause: Codex reported a failed completion. The saved notice is not a completed task result.')
            if row['state'] in {'result_ready','preparing_reply','sync_pending'}:
                lines.append('Reply delivered; Git tracking is still pending.' if row['receipt'] else
                    'The reply is saved; Git validation/upload and delivery will retry without rerunning Codex.')
            elif row['state']=='record_blocked' and row['receipt']:
                lines.append('Reply delivered; Git tracking is blocked and needs review. Task acceptance remains blocked; later messages use their own Git preflight.')
            if row['interrupted']:
                lines.append('Codex stopped. Partial files may exist; the saved notice is not a completed task result.')
            if row['state']=='uncertain_delivery' and row['result']:
                lines.append('In Intern Agent Control: /resolve '+str(row['seq'])+' deliver — upload saved work and deliver the saved notice/result without rerunning Codex.')
            elif row['state']=='uncertain_execution':
                lines.append('Inspect its console before deciding. No request will be rerun automatically.')
            elif row['state']=='record_blocked':
                lines.append('Repair record validation, then in Intern Agent Control: /resolve '+str(row['seq'])+' retry. Codex is not rerun.')
            elif row['state']=='sync_pending':
                lines.append('Git recording/upload will retry; Codex is not rerun.')
        return '\n'.join(lines)

    def credentials(self):
        value = private_json(self.root / "credentials.json")
        if (set(value) != {"app_id", "app_secret"} or not identifier(value.get("app_id"), "cli")
                or not isinstance(value.get("app_secret"), str)
                or not 8 <= len(value["app_secret"]) <= 256
                or any(c.isspace() or c in "<>" for c in value["app_secret"])):
            raise Invalid("Feishu credential handoff is incomplete")
        identity = self.identity()
        if identity["app_id"] and identity["app_id"] != value["app_id"]:
            raise Invalid("Changing an application's identity requires fresh live state")
        self.db.execute("UPDATE identity SET app_id=? WHERE singleton=1", (value["app_id"],))
        return value

    def identity(self):
        return dict(self.db.execute("SELECT * FROM identity WHERE singleton=1").fetchone())

    def pairing_code(self, now=None):
        if self.identity()["owner"]:
            raise Invalid("An owner is already paired")
        code = secrets.token_urlsafe(24)
        now = time.time() if now is None else now
        self.db.execute("UPDATE identity SET pairing_hash=?,pairing_until=? WHERE singleton=1",
                        (hashlib.sha256(code.encode()).hexdigest(), now + 900))
        path = self.root / "pairing-code.txt"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(code + "\n")
        return path

    def pair(self, app, sender, chat, code, now=None):
        now = time.time() if now is None else now
        self.db.execute("BEGIN IMMEDIATE")
        try:
            identity = self.identity()
            valid = (not identity["owner"] and app == identity["app_id"]
                     and identifier(sender, "ou") and identifier(chat, "oc")
                     and isinstance(code, str) and identity["pairing_hash"]
                     and now <= (identity["pairing_until"] or 0)
                     and secrets.compare_digest(hashlib.sha256(code.encode()).hexdigest(), identity["pairing_hash"]))
            if valid:
                self.db.execute("UPDATE identity SET owner=?,management_chat=?,pairing_hash=NULL,pairing_until=NULL WHERE singleton=1",
                                (sender, chat))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        if valid:
            (self.root / "pairing-code.txt").unlink(missing_ok=True)
        return bool(valid)

    def register_workspace(self, name, cwd):
        if not isinstance(name, str) or not WORKSPACE.fullmatch(name):
            raise Invalid("Use a workspace name containing letters, numbers, hyphens or underscores")
        cwd = Path(cwd).expanduser().resolve(strict=True)
        if not cwd.is_dir():
            raise Invalid("Workspace must be an existing directory")
        # Inspect local configuration only. No fetch/push, helpers, shell startup or remote changes.
        if (cwd / ".git").exists():
            import subprocess
            result = subprocess.run(["/usr/bin/git", "-C", str(cwd), "config", "--get-regexp", r"^remote\..*\.url$"],
                                    env=git_environment(), capture_output=True, text=True, timeout=10)
            if result.returncode not in {0, 1}:
                raise Invalid("Could not inspect the workspace's Git configuration")
            for line in result.stdout.splitlines():
                github_url(line.split(None, 1)[1])
        existing = self.workspace(name)
        if existing:
            if existing["cwd"] != str(cwd):
                raise Invalid("Workspace is already registered with a different directory")
            return existing
        try:
            self.db.execute("INSERT INTO workspaces(id,cwd) VALUES(?,?)", (name, str(cwd)))
        except sqlite3.IntegrityError:
            raise Invalid("This directory is already registered under another name") from None
        return self.workspace(name)

    def workspace(self, name):
        row = self.db.execute("SELECT * FROM workspaces WHERE id=?", (name,)).fetchone()
        return dict(row) if row else None

    def session(self, session_id):
        row = self.db.execute('''SELECT s.*,p.model,p.effort FROM sessions s
            LEFT JOIN session_settings p ON p.session=s.id WHERE s.id=?''', (session_id,)).fetchone()
        return dict(row) if row else None

    def chat_session(self, chat):
        row = self.db.execute('''SELECT s.*,p.model,p.effort FROM sessions s
            LEFT JOIN session_settings p ON p.session=s.id WHERE s.chat_id=?''', (chat,)).fetchone()
        return dict(row) if row else None

    def controller_session(self):
        identity=self.identity()
        if not identity['owner']:return None
        route=self.db.execute('SELECT * FROM controller_routes WHERE singleton=1').fetchone()
        if not route or route['app_id']!=identity['app_id'] or route['chat_id']!=identity['management_chat']:return None
        session=self.session(route['session'])
        return session if session and session['chat_id']==route['chat_id'] else None

    def reserve_controller(self, name,session_id=None):
        identity=self.identity()
        if not identity['owner'] or not identity['management_chat']:
            raise Invalid('Pair the owner management group first')
        if not self.workspace(name):raise Invalid('Register the controller Git workspace first')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            route=self.db.execute('SELECT * FROM controller_routes WHERE singleton=1').fetchone()
            if route:
                session=self.controller_session()
                if not session or session['workspace']!=name or session_id and session['id']!=session_id:
                    raise Invalid('Existing controller binding changed; inspect it before rebinding')
                self.db.execute('COMMIT');return session,False
            if self.chat_session(identity['management_chat']):
                raise Invalid('Management group already belongs to another conversation')
            if session_id:
                saved=self.session(session_id)
                if (saved and saved['workspace']==name and saved['state']=='archived'
                        and self.db.execute('SELECT 1 FROM restored_conversations WHERE session=?',(session_id,)).fetchone()
                        and not self.db.execute('SELECT 1 FROM tracked_interns WHERE session=?',(session_id,)).fetchone()):
                    self.db.execute("UPDATE sessions SET state='restored' WHERE id=?",(session_id,))
                    saved=self.session(session_id)
                if not saved or saved['workspace']!=name or saved['state']!='restored':raise Invalid('Choose a restored controller from this workspace')
                self.db.execute("UPDATE sessions SET chat_id=?,state='creating' WHERE id=?",(identity['management_chat'],session_id))
            else:
                session_id='s_'+uuid.uuid4().hex[:16]
                self.db.execute("INSERT INTO sessions(id,workspace,chat_id,state,creation_key) VALUES(?,?,?,'creating',?)",
                    (session_id,name,identity['management_chat'],uuid.uuid4().hex))
            self.db.execute('INSERT INTO controller_routes VALUES(1,?,?,?)',
                (identity['app_id'],identity['management_chat'],session_id))
            self.db.execute('COMMIT');return self.session(session_id),True
        except BaseException:self.db.execute('ROLLBACK');raise

    def reserve_session(self, name, *, new=False,session_id=None):
        if not self.workspace(name):
            raise Invalid("Register this workspace locally before opening its Feishu chat")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if session_id is not None:
                row=self.session(session_id)
                if (row and row['workspace']==name and row['state']=='archived'
                        and self.db.execute('SELECT 1 FROM restored_conversations WHERE session=?',(session_id,)).fetchone()
                        and not self.db.execute('SELECT 1 FROM tracked_interns WHERE session=?',(session_id,)).fetchone()):
                    # Explicit owner selection can reconnect a cold-restored
                    # archive. Generic workspace opening still excludes it.
                    self.db.execute("UPDATE sessions SET state='restored' WHERE id=?",(session_id,))
                    row=self.session(session_id)
                if not row or row['workspace']!=name or new or row['state'] not in {'restored','ready','paused'}:
                    raise Invalid('Choose an existing restored or ready conversation in this workspace')
                if self.db.execute('SELECT 1 FROM controller_routes WHERE session=?',(session_id,)).fetchone():
                    raise Invalid('This conversation already belongs to the Control group')
                self.db.execute('COMMIT');return row,False
            row = self.db.execute("""SELECT * FROM sessions WHERE workspace=? AND id!='controller'
                AND id NOT IN (SELECT session FROM controller_routes) AND state!='archived'
                AND id NOT IN (SELECT session FROM restored_roles WHERE role='controller')
                ORDER BY rowid DESC LIMIT 1""", (name,)).fetchone()
            if row and not new:
                self.db.execute("COMMIT")
                return dict(row), False
            session_id = "s_" + uuid.uuid4().hex[:16]
            self.db.execute("INSERT INTO sessions(id,workspace,state,creation_key) VALUES(?,?,'creating',?)",
                            (session_id, name, uuid.uuid4().hex))
            self.db.execute("COMMIT")
            return self.session(session_id), True
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def set_session(self, session_id, **values):
        if not values or set(values) - {"chat_id", "thread_id", "state"}:
            raise Invalid("Invalid session update")
        if values.get("chat_id") is not None and "chat_id" in values and not identifier(values["chat_id"], "oc"):
            raise Invalid("Invalid chat identifier")
        sql = ",".join(k + "=?" for k in values)
        self.db.execute("UPDATE sessions SET " + sql + " WHERE id=?", [*values.values(), session_id])

    def sessions(self):
        return [dict(row) for row in self.db.execute('''SELECT s.*,p.model,p.effort FROM sessions s
            LEFT JOIN session_settings p ON p.session=s.id ORDER BY s.rowid''')]

    def set_model(self, session_id, model, effort):
        if not self.session(session_id):
            raise Invalid("Unknown session")
        if not isinstance(model,str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}",model):
            raise Invalid("Invalid model name")
        if not isinstance(effort,str) or not re.fullmatch(r"[a-z]{1,32}",effort):
            raise Invalid("Invalid reasoning effort")
        self.db.execute("INSERT OR REPLACE INTO session_settings(session,model,effort) VALUES(?,?,?)",
                        (session_id,model,effort))

    def ingest(self, payload, now=None):
        now = time.time() if now is None else now
        try:
            header, event = payload["header"], payload["event"]
            identity = self.identity()
            if header.get("app_id") != identity["app_id"] or not identity["app_id"]:
                return "rejected_app"
            if header.get("event_type") != "im.message.receive_v1":
                return "ignored_event"
            sender, message = event["sender"], event["message"]
            if sender.get("sender_type") != "user":
                return "ignored_sender"
            owner = sender["sender_id"]["open_id"]
            chat, mid = message["chat_id"], message["message_id"]
            if not identifier(owner, "ou") or not identifier(chat, "oc") or not identifier(mid, "om"):
                return "rejected_identifiers"
            if message.get("chat_type") != "group":
                return "ignored_type"
            received_at = int(message["create_time"]) / 1000
            age = now - received_at
            if age > 600 or age < -60:
                return "rejected_timestamp"
            content = message["content"]
            if not isinstance(content, str) or len(content.encode()) > 262144:
                return "rejected_size"
            resources=[];uploads_only=False
            if message.get('message_type')=='text':text=json.loads(content)['text']
            else:
                if not identity['owner']:return 'unpaired'
                from .attachments import normalize
                try:text,resources=normalize(message)
                except Invalid as error:text='/unsupported'+(' '+str(error) if str(error)!='Unsupported message type' else '')
                except (KeyError,TypeError,ValueError):return 'rejected_malformed'
                if resources:
                    from .attachment_batches import file_only
                    uploads_only=file_only(message)
            if not isinstance(text, str) or not text.strip() or len(text.encode()) > 32768:
                return "rejected_text"
            # Remove Feishu's leading bot-mention transport token only. Preserve
            # the owner's punctuation, whitespace and multiline text otherwise.
            from .triggers import normalize as normalize_mentions
            text = normalize_mentions(self,message,text)
            command = text.strip()
            if not command:return 'rejected_text'
            if not identity["owner"]:
                if command.startswith("/pair ") and self.pair(header["app_id"], owner, chat, command[6:].strip(), now):
                    return "paired"
                return "unpaired"
            session = self.chat_session(chat)
            management = chat == identity["management_chat"]
            controller=self.controller_session() if management else None
            from .access import allows,role as access_role
            if owner!=identity['owner'] and (management or not session or not access_role(self,owner,session['workspace'])):return 'rejected_sender'
            if not management and not session:
                return "rejected_chat"
            role = "control" if command.startswith("/") else "agent"
            if command.split()[0].lower() in {'/stop','/cancel'}:
                role = 'urgent'
            if role != 'agent':text=command
            if role=='agent' and management and (not session or not controller
                    or session['id']!=controller['id']):return 'controller_unavailable'
            if role == "agent" and session["state"] != "ready":
                return "session_unavailable"
            if role=='agent' and not uploads_only:
                from .triggers import accepts
                if not accepts(self,session,message):return 'ignored_unmentioned'
            if not allows(self,dict(sender=owner,chat_id=chat,session=session['id'] if session else None,role=role,text=text)):return 'rejected_sender'
            workspace = "__control__" if role != "agent" else session["workspace"]
            self.db.execute('BEGIN IMMEDIATE')
            try:
                cursor = self.db.execute('''INSERT OR IGNORE INTO events
                (app_id,message_id,chat_id,sender,role,workspace,session,text,state)
                VALUES(?,?,?,?,?,?,?,?,'queued')''',
                    (header["app_id"], mid, chat, owner, role, workspace, session["id"] if session else None, text))
                changed = cursor.rowcount
                if changed:
                    item = dict(self.db.execute('SELECT * FROM events WHERE seq=?',(cursor.lastrowid,)).fetchone())
                    self.ledger.ensure_turn(item, received_at, 'feishu')
                    for ordinal,r in enumerate(resources):
                        self.db.execute('INSERT INTO attachments VALUES(?,?,?,?,?,NULL)',(item['seq'],ordinal,r['key'],r['name'],r['type']))
                    if role=='agent':
                        from .attachment_batches import stage,bind
                        if uploads_only:stage(self,item,received_at)
                        else:bind(self,item,received_at)
                self.db.execute('COMMIT')
            except BaseException:
                self.db.execute('ROLLBACK')
                raise
            return "accepted" if changed else "duplicate"
        except (KeyError,TypeError,ValueError,AttributeError,OverflowError):
            return "rejected_malformed"

    def enqueue_open(self, name, new=False):
        identity = self.identity()
        if not identity["owner"] or not self.workspace(name):
            raise Invalid("Pair the management group and register the workspace first")
        controller=self.controller_session()
        self.db.execute('''INSERT INTO events(app_id,message_id,chat_id,sender,role,workspace,session,text,state)
            VALUES(?,?,?,?,'control','__control__',?,?,'queued')''',
            (identity["app_id"], "local_" + uuid.uuid4().hex, identity["management_chat"], identity["owner"],
             controller['id'] if controller else None,("/new-session " if new else "/new ") + name))

    def recover(self):
        from .dispatch import recover as recover_preflight
        recover_preflight(self)
        from .file_transfer import recover as recover_files
        recover_files(self)
        self.db.execute("UPDATE reply_outbox SET state='uncertain' WHERE state='sending'")
        # An interrupted Feishu notice send cannot be assumed absent. Never
        # resend it blindly; continuation state is independent of its receipt.
        self.db.execute("UPDATE recovery_notices SET state='uncertain' WHERE state='sending'")
        # Legacy in-flight sends had no footer. Their delivery is uncertain;
        # preserve the original body rather than changing an attempted UUID.
        self.db.execute("INSERT OR IGNORE INTO reply_quota(seq,line) SELECT seq,'' FROM events WHERE role='agent' AND state='sending'")
        self.db.execute("UPDATE events SET state='uncertain_execution' WHERE state='running'")
        self.db.execute("UPDATE events SET state='uncertain_delivery' WHERE state='sending'")
        # Git preparation has not attempted an API send. Resume the saved
        # reply after a restart, never its Codex request.
        self.db.execute("UPDATE events SET state='result_ready' WHERE state='preparing_reply' AND result IS NOT NULL AND length(trim(result))>0")
        self.db.execute("UPDATE events SET state='record_blocked' WHERE state='preparing_reply'")
        self.schedule_sync_retries()
        # Only a durable, identified native turn may continue across a relay
        # restart. Its completion is observed; its prompt is never re-injected.
        for row in self.db.execute('SELECT session,snapshot FROM terminal_attachments').fetchall():
            snapshot = json.loads(row['snapshot'])
            if snapshot.get('active') and snapshot.get('event_seq'):
                self.db.execute("UPDATE events SET state='awaiting_terminal' WHERE seq=? AND session=? AND state='uncertain_execution'",
                                (snapshot['event_seq'],row['session']))

    def claim(self, role, session_id=None):
        if session_id is not None and role!='agent':raise Invalid('Conversation workers may claim only their own agent messages')
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if role == 'urgent':
                row = self.db.execute("SELECT * FROM events WHERE role='urgent' AND state='queued' ORDER BY seq LIMIT 1").fetchone()
            elif role=='urgent_reply':
                row = self.db.execute("SELECT * FROM events WHERE role='urgent' AND state='result_ready' ORDER BY seq LIMIT 1").fetchone()
            else:
                # Completed execution, reply transport and Git review are
                # separate. Ended, delivered Git reviews cannot hold the next
                # message forever. Its dispatch still requires a published head
                # and a fresh baseline; uncertain execution still blocks.
                row = self.db.execute('''SELECT e.* FROM events e WHERE e.role=?
                AND (? IS NULL OR e.session=?)
                AND e.state IN ('queued','result_ready')
                AND NOT EXISTS (SELECT 1 FROM dispatch_attempts d WHERE d.seq=e.seq AND e.state='queued' AND d.retry_at>?)
                AND NOT EXISTS (SELECT 1 FROM research_runs r WHERE r.seq=e.seq AND e.state='queued' AND r.not_before>?)
                AND (e.role='control' OR e.state='result_ready' OR EXISTS (
                    SELECT 1 FROM sessions s WHERE s.id=e.session AND s.state='ready'))
                AND NOT EXISTS (SELECT 1 FROM events c WHERE e.role='agent' AND c.role='control'
                    AND (c.session=e.session OR c.text LIKE '/task '||e.session||' %'
                      OR c.text LIKE '/compact '||e.session||'%' OR c.text LIKE '/clear '||e.session||'%'
                      OR c.text LIKE '/restart '||e.session||'%' OR c.text LIKE '/archive '||e.session||'%'
                      OR c.text LIKE '/unarchive '||e.session||'%' OR c.text LIKE '/goal '||e.session||'%'
                      OR c.text LIKE '/team %' OR c.text LIKE '/access %')
                    AND c.seq<e.seq AND c.state IN ('queued','running'))
                AND NOT EXISTS (
                SELECT 1 FROM events b WHERE (b.workspace=e.workspace OR (e.role='agent' AND b.session=e.session)) AND b.seq!=e.seq
                AND NOT (e.role='agent' AND b.role='agent' AND e.session!=b.session AND EXISTS (
                    SELECT 1 FROM tracked_interns ie JOIN tracked_interns ib ON ib.session=b.session
                    WHERE ie.session=e.session AND ie.code_path!='' AND ib.code_path!=''
                    AND ie.code_branch!='' AND ib.code_branch!=''
                    AND ie.code_path!=ib.code_path AND ie.code_branch!=ib.code_branch))
                AND b.role!='urgent' AND (b.state IN ('running','preparing_reply','sending','awaiting_terminal')
                OR (e.role='agent' AND b.seq IN (SELECT seq FROM codex_review_events)
                    AND NOT (b.receipt IS NOT NULL AND b.state IN ('record_blocked','sync_pending')
                        AND (e.state='result_ready' OR EXISTS (SELECT 1 FROM recovery_runs r JOIN recovery_episodes ep ON ep.id=r.episode
                            WHERE r.seq=e.seq AND ep.session=e.session AND ep.state IN ('queued','running'))
                            OR EXISTS (SELECT 1 FROM bot_mailbox m WHERE m.event_seq=e.seq AND m.recipient=e.session AND m.kind='incident')
                            OR (e.session=b.session AND b.role='agent'
                                AND EXISTS (SELECT 1 FROM turn_records old WHERE old.seq=b.seq
                                    AND old.activity IN ('completed','failed','stopped'))))))
                OR (b.seq<e.seq AND b.state IN ('queued','result_ready')
                    AND NOT ((b.seq IN (SELECT seq FROM background_event_scope) AND e.seq NOT IN (SELECT seq FROM background_event_scope))
                        OR (b.seq IN (SELECT seq FROM research_event_scope) AND e.seq IN (SELECT seq FROM peer_event_scope))))))
                ORDER BY CASE WHEN e.seq IN (SELECT seq FROM research_event_scope) THEN 2
                    WHEN e.seq IN (SELECT seq FROM peer_event_scope) THEN 1 ELSE 0 END,e.seq LIMIT 1''', (role,session_id,session_id,time.time(),time.time())).fetchone()
            if row:
                # Bind at dispatch, after earlier /task commands have executed.
                # Arrival records remain durable, but cannot capture an obsolete selection.
                self.ledger.bind_turn(dict(row))
                self.db.execute("UPDATE events SET state=? WHERE seq=?",
                                ("running" if row["state"] == "queued" else "preparing_reply", row["seq"]))
            item = dict(row) if row else None
            if item and role=='agent' and item['state']=='queued':
                from .dispatch import claimed
                claimed(self,item)
            self.db.execute("COMMIT")
            return item
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def transition(self, seq, state, result=None, receipt=None):
        self.db.execute("UPDATE events SET state=?,result=COALESCE(?,result),receipt=COALESCE(?,receipt) WHERE seq=?",
                        (state,result,receipt,seq))
        if state=='complete':self.db.execute('UPDATE initialization_receipts SET completed_at=COALESCE(completed_at,?) WHERE seq=?',(time.time(),seq))

    def schedule_sync_retries(self):
        # Repair interrupted scheduling without replaying uncertain work.
        self.db.execute("INSERT OR IGNORE INTO sync_retries(seq,retry_at) SELECT seq,0 FROM events WHERE state='sync_pending'")

    def authorized(self, item):
        identity = self.identity()
        from .access import allows
        if item["app_id"] != identity["app_id"] or not allows(self,item):
            return False
        if item["chat_id"] == identity["management_chat"]:
            if item['role'] in {'control','urgent'}:return True
            controller=self.controller_session()
            return bool(item['role']=='agent' and controller and controller['id']==item['session']
                and controller['state'] in {'ready','paused'})
        session = self.chat_session(item["chat_id"])
        return bool(session and session["id"] == item["session"]
                    and (item["role"] in {"control",'urgent'} or session["state"] in {"ready", "paused"}))

    def resolve(self, seq, action):
        row = self.db.execute("SELECT * FROM events WHERE seq=?", (seq,)).fetchone()
        if not row or row["state"] not in {"uncertain_execution", "uncertain_delivery",'record_blocked'}:
            raise Invalid("That event is not awaiting reconciliation")
        if action == "cancel":
            self.transition(seq, "cancelled")
        elif action == "deliver" and row["state"] == "uncertain_delivery" and row["result"]:
            self.db.execute("UPDATE reply_outbox SET state='pending',after_state=NULL WHERE seq=? AND state='uncertain'",(seq,))
            self.transition(seq, "result_ready")
        elif action == 'retry' and row['state']=='record_blocked':
            self.transition(seq,'sync_pending')
            self.db.execute('INSERT OR REPLACE INTO sync_retries(seq,retry_at) VALUES(?,0)',(seq,))
        else:
            raise Invalid("Choose cancel, or deliver for a saved uncertain reply")

    def status(self):
        return {"paired": bool(self.identity()["owner"]),
                "workspaces": [dict(r) for r in self.db.execute('''SELECT w.id,w.cwd,
                    COALESCE(t.published,0) AS tracked,t.repo_url,t.metadata_branch,t.metadata_mode
                    FROM workspaces w LEFT JOIN tracked_workspaces t ON t.workspace=w.id''')],
                "intern_worktrees": [dict(r) for r in self.db.execute('''SELECT s.workspace,i.code_path,i.code_branch
                    FROM tracked_interns i JOIN sessions s ON s.id=i.session WHERE i.published=1''')],
                "tracking_pending": [dict(r) for r in self.db.execute("SELECT workspace,error_type FROM tracking_pending")],
                "bot_tracking_pending": [dict(r) for r in self.db.execute('SELECT session,error_type FROM bot_tracking_pending')],
                "pending_reaction_cleanup": self.db.execute("SELECT COUNT(*) FROM message_reactions").fetchone()[0],
                "reply_delivery":dict(self.db.execute('SELECT state,count(*) FROM reply_outbox GROUP BY state').fetchall()),
                "flow_incidents":[dict(r) for r in self.db.execute('SELECT seq,cause,state FROM flow_incidents ORDER BY created_at DESC LIMIT 20')],
                "sessions": self.sessions(),
                "events": dict(self.db.execute("SELECT state,COUNT(*) FROM events GROUP BY state").fetchall()),
                "uncertain": [dict(r) for r in self.db.execute("SELECT seq,workspace,state FROM events WHERE state LIKE 'uncertain_%'")]}
