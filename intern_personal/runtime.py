"""Offline runtime core. All external I/O is injected; no SDK/process startup."""
from __future__ import annotations

from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Protocol

from .core import Invalid, Store, WORKSPACE, github_url


@dataclass(frozen=True)
class Policy:
    app_id: str
    owner_open_id: str
    chat_workspaces: dict[str, str]
    max_age_seconds: int = 600
    max_text_bytes: int = 32768

    def __post_init__(self):
        for value, prefix in [(self.app_id, "cli_"), (self.owner_open_id, "ou_")]:
            if not isinstance(value, str) or not re.fullmatch(prefix + r"[A-Za-z0-9_]+", value):
                raise Invalid("Explicit application and owner identifiers are required")
        if not isinstance(self.chat_workspaces, dict) or not self.chat_workspaces or any(
            not isinstance(chat, str) or not isinstance(workspace, str)
            or not re.fullmatch(r"oc_[A-Za-z0-9_]+", chat) or not WORKSPACE.fullmatch(workspace)
            for chat, workspace in self.chat_workspaces.items()
        ):
            raise Invalid("Explicit chat-to-workspace bindings are required")
        if (type(self.max_age_seconds) is not int or type(self.max_text_bytes) is not int
                or not 1 <= self.max_age_seconds <= 86400 or not 1 <= self.max_text_bytes <= 1048576):
            raise Invalid("Invalid event limits")


@dataclass(frozen=True)
class SessionSpec:
    workspace_id: str
    cwd: str
    tmux_socket: str
    tmux_session: str
    environment: dict[str, str]


class Sessions:
    """Build isolated launch specifications; never run tmux or an agent."""
    def __init__(self, store: Store):
        self.store = store

    def prepare(self, workspace):
        if not WORKSPACE.fullmatch(workspace):
            raise Invalid("Invalid workspace")
        config = self.store.load()
        if not config["github_owner"]:
            raise Invalid("Configure a GitHub owner before preparing a runtime")
        registry = self.store.contained(self.store.state / "workspaces.json")
        records = json.loads(registry.read_text())
        item = records.get(workspace)
        if not isinstance(item, dict) or item.get("provider") != "github":
            raise Invalid("Unknown or non-GitHub workspace")
        url = github_url(item.get("repo_url"))
        if url.split("/")[3].casefold() != config["github_owner"].casefold():
            raise Invalid("Workspace does not belong to the configured GitHub owner")
        root = self.store.contained(self.store.state / "workspaces" / workspace)
        home = self.store.contained(self.store.state / "agent-home")
        socket = self.store.contained(self.store.state / "tmux.sock")
        if len(os.fsencode(socket)) >= 104:
            raise Invalid("Personal state path is too long for an isolated tmux socket")
        return SessionSpec(workspace, str(root), str(socket), "ia-personal-" + workspace, {
            "PATH": "/usr/bin:/bin", "HOME": str(home),
            "WORK_AGENTS_ROOT": str(self.store.state),
            "XDG_CONFIG_HOME": str(home / ".config"),
        })


class Agent(Protocol):
    def run(self, session: SessionSpec, text: str) -> str: ...


class Replies(Protocol):
    def send(self, *, chat_id: str, reply_to: str, text: str, idempotency_key: str) -> str: ...


class Queue:
    def __init__(self, store: Store):
        self.store = store
        directory = store.contained(store.state / "queue")
        directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        # Durable task content is private state, never a distributable asset.
        if directory.stat().st_mode & 0o077:
            raise Invalid("Queue directory must be private (0700)")
        self.path = store.contained(directory / "events.sqlite3")
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        if self.path.stat().st_mode & 0o077:
            raise Invalid("Queue database must be private (0600)")
        self.db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute('''CREATE TABLE IF NOT EXISTS events (
            seq INTEGER PRIMARY KEY AUTOINCREMENT, app_id TEXT NOT NULL,
            message_id TEXT NOT NULL, chat_id TEXT NOT NULL, workspace TEXT NOT NULL,
            sender TEXT NOT NULL, text TEXT NOT NULL, state TEXT NOT NULL,
            result TEXT, reply_id TEXT,
            UNIQUE(app_id, message_id))''')

    def close(self):
        self.db.close()

    def enqueue(self, app_id, message_id, chat_id, workspace, sender, text):
        changed = self.db.execute('''INSERT OR IGNORE INTO events
            (app_id,message_id,chat_id,workspace,sender,text,state) VALUES (?,?,?,?,?,?,'queued')''',
            (app_id, message_id, chat_id, workspace, sender, text)).rowcount
        return bool(changed)

    def summary(self):
        return dict(self.db.execute("SELECT state,COUNT(*) FROM events GROUP BY state").fetchall())

    def recover(self):
        # Called only while holding the sole worker lease. External completion
        # cannot be inferred after a crash: never automatically replay it.
        self.db.execute("UPDATE events SET state='uncertain_execution' WHERE state='running'")
        self.db.execute("UPDATE events SET state='uncertain_delivery' WHERE state='sending'")

    def claim(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            item = self.db.execute('''SELECT e.* FROM events e
                WHERE e.state IN ('queued','result_ready')
                AND NOT EXISTS (SELECT 1 FROM events b WHERE b.workspace=e.workspace
                    AND b.seq != e.seq AND (b.state IN
                    ('running','sending','uncertain_execution','uncertain_delivery')
                    OR (b.seq<e.seq AND b.state IN ('queued','result_ready'))))
                ORDER BY e.seq LIMIT 1''').fetchone()
            if item:
                state = "running" if item["state"] == "queued" else "sending"
                self.db.execute("UPDATE events SET state=? WHERE seq=?", (state, item["seq"]))
            self.db.execute("COMMIT")
            return dict(item) if item else None
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def transition(self, seq, state, *, result=None, reply_id=None):
        self.db.execute("UPDATE events SET state=?,result=COALESCE(?,result),reply_id=COALESCE(?,reply_id) WHERE seq=?",
                        (state, result, reply_id, seq))


class Ingress:
    def __init__(self, policy: Policy, queue: Queue, sessions: Sessions, clock=time.time):
        self.policy, self.queue, self.sessions, self.clock = policy, queue, sessions, clock

    def receive(self, payload):
        """Return fixed reason labels only; never log incoming content."""
        try:
            header, event = payload["header"], payload["event"]
            if header.get("app_id") != self.policy.app_id:
                return "rejected_app"
            if header.get("event_type") != "im.message.receive_v1":
                return "ignored_event"
            sender, message = event["sender"], event["message"]
            if sender.get("sender_type") != "user":
                return "ignored_sender_type"
            owner = sender["sender_id"].get("open_id")
            if owner != self.policy.owner_open_id:
                return "rejected_sender"
            chat = message.get("chat_id")
            workspace = self.policy.chat_workspaces.get(chat)
            if not workspace:
                return "rejected_chat"
            if message.get("message_type") != "text" or message.get("chat_type") != "group":
                return "ignored_message_type"
            message_id = message["message_id"]
            if not isinstance(message_id, str) or not re.fullmatch(r"om_[A-Za-z0-9_]+", message_id):
                return "rejected_message_id"
            timestamp = int(message["create_time"]) / 1000
            age = self.clock() - timestamp
            if age > self.policy.max_age_seconds or age < -60:
                return "rejected_timestamp"
            content = message["content"]
            if not isinstance(content, str) or len(content.encode()) > self.policy.max_text_bytes * 8:
                return "rejected_size"
            text = json.loads(content)["text"]
            if not isinstance(text, str) or not text.strip() or len(text.encode()) > self.policy.max_text_bytes:
                return "rejected_text"
            self.sessions.prepare(workspace)
            accepted = self.queue.enqueue(self.policy.app_id, message_id, chat, workspace, owner, text)
            return "accepted" if accepted else "duplicate"
        except (KeyError, TypeError, ValueError, AttributeError, OSError, OverflowError):
            return "rejected_malformed_or_unconfigured"


class Worker:
    def __init__(self, policy: Policy, queue: Queue, sessions: Sessions, agent: Agent, replies: Replies):
        self.policy, self.queue, self.sessions, self.agent, self.replies = policy, queue, sessions, agent, replies

    def run_once(self):
        path = self.queue.store.contained(self.queue.path.parent / "worker.lock")
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as lease:
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return "worker_busy"
            self.queue.recover()
            item = self.queue.claim()
            if item is None:
                return "idle"
            # Re-check authorization after restarts or binding changes.
            if (item["app_id"] != self.policy.app_id or item["sender"] != self.policy.owner_open_id
                    or self.policy.chat_workspaces.get(item["chat_id"]) != item["workspace"]):
                self.queue.transition(item["seq"], "cancelled_policy_changed")
                return "cancelled_policy_changed"
            if item["state"] == "queued":
                try:
                    session = self.sessions.prepare(item["workspace"])
                    result = self.agent.run(session, item["text"])
                    if not isinstance(result, str) or not result.strip() or len(result.encode()) > 1048576:
                        raise Invalid("Invalid agent result")
                    self.queue.transition(item["seq"], "result_ready", result=result)
                    item["result"] = result
                except Exception:
                    self.queue.transition(item["seq"], "uncertain_execution")
                    return "uncertain_execution"
            self.queue.transition(item["seq"], "sending")
            identity = hashlib.sha256((item["app_id"] + ":" + item["message_id"]).encode()).hexdigest()[:32]
            try:
                reply_id = self.replies.send(chat_id=item["chat_id"], reply_to=item["message_id"],
                                             text=item["result"], idempotency_key=identity)
                if not isinstance(reply_id, str) or not reply_id:
                    raise Invalid("Missing reply receipt")
            except Exception:
                self.queue.transition(item["seq"], "uncertain_delivery")
                return "uncertain_delivery"
            self.queue.transition(item["seq"], "complete", reply_id=reply_id)
            return "complete"


class SimulatedAgent:
    def __init__(self):
        self.calls = []

    def run(self, session, text):
        self.calls.append((session, text))
        return "Offline simulated result. No agent was launched."


class SimulatedReplies:
    def __init__(self):
        self.calls = []

    def send(self, **payload):
        self.calls.append(payload)
        return "om_simulated_reply_" + str(len(self.calls))


def simulate(project):
    """Complete an isolated synthetic round-trip without network or processes."""
    store = Store(Path(project))
    store.initialize("example-personal-account")
    store.add_workspace("pilot", "https://github.com/example-personal-account/pilot.git")
    policy = Policy("cli_simulation", "ou_simulation", {"oc_simulation": "pilot"})
    queue = Queue(store)
    try:
        sessions, agent, replies = Sessions(store), SimulatedAgent(), SimulatedReplies()
        ingress = Ingress(policy, queue, sessions)
        payload = {"header": {"app_id": policy.app_id, "event_type": "im.message.receive_v1"},
                   "event": {"sender": {"sender_type": "user", "sender_id": {"open_id": policy.owner_open_id}},
                             "message": {"message_id": "om_simulation", "chat_id": "oc_simulation",
                                         "chat_type": "group", "message_type": "text",
                                         "create_time": str(int(time.time() * 1000)),
                                         "content": json.dumps({"text": "Synthetic offline pilot"})}}}
        first, duplicate = ingress.receive(payload), ingress.receive(payload)
        outcome = Worker(policy, queue, sessions, agent, replies).run_once()
        return {"mode": "offline_simulation", "first_event": first, "repeat_event": duplicate,
                "outcome": outcome, "agent_calls": len(agent.calls), "reply_calls": len(replies.calls),
                "queue": queue.summary(), "network_used": False, "real_sessions_started": False}
    finally:
        queue.close()
