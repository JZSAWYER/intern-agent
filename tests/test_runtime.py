import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from intern_personal.core import Invalid, Store
from intern_personal.runtime import (Ingress, Policy, Queue, Sessions,
                                     SimulatedAgent, SimulatedReplies, Worker, simulate)


class Crash(BaseException):
    pass


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ia-test-")
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))
        self.store.initialize("example-personal-account")
        self.store.add_workspace("pilot", "https://github.com/example-personal-account/pilot.git")
        self.policy = Policy("cli_test", "ou_owner", {"oc_pilot": "pilot"})
        self.queue = Queue(self.store)
        self.addCleanup(lambda: self.queue.close())
        self.sessions, self.agent, self.replies = Sessions(self.store), SimulatedAgent(), SimulatedReplies()
        self.ingress = Ingress(self.policy, self.queue, self.sessions, clock=lambda: 1000)
        self.worker = Worker(self.policy, self.queue, self.sessions, self.agent, self.replies)

    def event(self, identifier="om_first"):
        return {"header": {"app_id": "cli_test", "event_type": "im.message.receive_v1"},
                "event": {"sender": {"sender_type": "user", "sender_id": {"open_id": "ou_owner"}},
                          "message": {"message_id": identifier, "chat_id": "oc_pilot", "chat_type": "group",
                                      "message_type": "text", "create_time": "1000000",
                                      "content": json.dumps({"text": "literal $(never-run) ; text"})}}}

    def test_complete_roundtrip_and_duplicate_delivery(self):
        with patch("socket.socket", side_effect=AssertionError("network forbidden")), \
             patch("subprocess.Popen", side_effect=AssertionError("processes forbidden")):
            self.assertEqual(self.ingress.receive(self.event()), "accepted")
            self.assertEqual(self.ingress.receive(self.event()), "duplicate")
            self.assertEqual(self.worker.run_once(), "complete")
            self.assertEqual(self.worker.run_once(), "idle")
        self.assertEqual(len(self.agent.calls), 1)
        self.assertEqual(len(self.replies.calls), 1)
        self.assertEqual(self.agent.calls[0][1], "literal $(never-run) ; text")
        self.assertEqual(self.replies.calls[0]["chat_id"], "oc_pilot")
        self.assertEqual(self.queue.summary(), {"complete": 1})

    def test_wrong_app_sender_chat_and_bot_never_enqueued(self):
        changes = [(["header", "app_id"], "cli_other"),
                   (["event", "sender", "sender_id", "open_id"], "ou_other"),
                   (["event", "sender", "sender_type"], "app"),
                   (["event", "message", "chat_id"], "oc_other")]
        for path, value in changes:
            event = self.event()
            node = event
            for key in path[:-1]:
                node = node[key]
            node[path[-1]] = value
            self.assertNotEqual(self.ingress.receive(event), "accepted")
        self.assertEqual(self.queue.summary(), {})
        self.assertEqual(self.worker.run_once(), "idle")

    def test_malformed_oversized_old_future_and_unsupported_events(self):
        for key, value in [("content", "not-json"), ("content", json.dumps({"text": "x" * 33000})),
                           ("create_time", "1"), ("create_time", "2000000"),
                           ("create_time", "9" * 400), ("message_type", "file"),
                           ("chat_type", "p2p"), ("message_id", "../../unsafe")]:
            event = self.event()
            event["event"]["message"][key] = value
            self.assertNotEqual(self.ingress.receive(event), "accepted")
        for value in [None, [], {}, {"header": []}]:
            self.assertNotEqual(self.ingress.receive(value), "accepted")
        self.assertEqual(self.queue.summary(), {})

    def test_policy_requires_explicit_identifiers_and_bindings(self):
        for args in [("<app-id>", "ou_owner", {"oc_pilot": "pilot"}),
                     ("cli_test", "<owner-open-id>", {"oc_pilot": "pilot"}),
                     ("cli_test", "ou_owner", {}),
                     ("cli_test", "ou_owner", {"oc_pilot": "../legacy"})]:
            with self.assertRaises(Invalid):
                Policy(*args)

    def test_missing_workspace_fails_closed(self):
        bad = Policy("cli_test", "ou_owner", {"oc_pilot": "missing"})
        self.assertEqual(Ingress(bad, self.queue, self.sessions, clock=lambda: 1000).receive(self.event()),
                         "rejected_malformed_or_unconfigured")
        self.assertEqual(self.queue.summary(), {})

    def test_policy_change_after_enqueue_cancels_task(self):
        self.ingress.receive(self.event())
        changed = Policy("cli_test", "ou_new_owner", {"oc_pilot": "pilot"})
        worker = Worker(changed, self.queue, self.sessions, self.agent, self.replies)
        self.assertEqual(worker.run_once(), "cancelled_policy_changed")
        self.assertEqual(self.agent.calls, [])
        self.assertEqual(self.replies.calls, [])

    def test_fifo_per_workspace(self):
        self.ingress.receive(self.event("om_one"))
        self.ingress.receive(self.event("om_two"))
        self.worker.run_once()
        self.worker.run_once()
        self.assertEqual([call["reply_to"] for call in self.replies.calls], ["om_one", "om_two"])

    def test_execution_crash_is_not_replayed_and_blocks_later_task(self):
        self.ingress.receive(self.event("om_one"))
        self.ingress.receive(self.event("om_two"))
        with patch.object(self.agent, "run", side_effect=Crash()):
            with self.assertRaises(Crash):
                self.worker.run_once()
        self.queue.close()
        self.queue = Queue(self.store)
        worker = Worker(self.policy, self.queue, self.sessions, self.agent, self.replies)
        self.assertEqual(worker.run_once(), "idle")
        self.assertEqual(self.queue.summary(), {"queued": 1, "uncertain_execution": 1})
        self.assertEqual(self.agent.calls, [])

    def test_persisted_result_is_delivered_without_reexecuting_agent(self):
        self.ingress.receive(self.event())
        job = self.queue.claim()
        self.queue.transition(job["seq"], "result_ready", result="saved result")
        self.assertEqual(self.worker.run_once(), "complete")
        self.assertEqual(self.agent.calls, [])
        self.assertEqual(self.replies.calls[0]["text"], "saved result")

    def test_delivery_disconnect_is_not_blindly_retried(self):
        self.ingress.receive(self.event())
        with patch.object(self.replies, "send", side_effect=ConnectionError("synthetic transport failure")):
            self.assertEqual(self.worker.run_once(), "uncertain_delivery")
        self.assertEqual(self.worker.run_once(), "idle")
        self.assertEqual(len(self.agent.calls), 1)
        self.assertEqual(self.replies.calls, [])

    def test_crash_during_delivery_is_reconciled_not_resent(self):
        self.ingress.receive(self.event())
        with patch.object(self.replies, "send", side_effect=Crash()):
            with self.assertRaises(Crash):
                self.worker.run_once()
        self.assertEqual(self.worker.run_once(), "idle")
        self.assertEqual(self.queue.summary(), {"uncertain_delivery": 1})
        self.assertEqual(len(self.agent.calls), 1)

    def test_only_one_worker_can_execute(self):
        self.ingress.receive(self.event())
        other = Worker(self.policy, self.queue, self.sessions, SimulatedAgent(), SimulatedReplies())
        def work(session, text):
            self.assertEqual(other.run_once(), "worker_busy")
            return "result"
        with patch.object(self.agent, "run", side_effect=work):
            self.assertEqual(self.worker.run_once(), "complete")

    def test_session_paths_and_environment_are_isolated(self):
        with patch.dict(os.environ, {"WORK_AGENTS_ROOT": "/legacy", "CODEX_HOME": "/legacy/codex",
                                     "LB_API_KEY": "synthetic-legacy-value", "TMUX": "legacy-socket"}):
            spec = self.sessions.prepare("pilot")
        for path in [spec.cwd, spec.tmux_socket, spec.environment["HOME"]]:
            self.assertTrue(Path(path).is_relative_to(self.store.state))
        self.assertTrue(spec.tmux_session.startswith("ia-personal-"))
        self.assertNotIn("CODEX_HOME", spec.environment)
        self.assertNotIn("LB_API_KEY", spec.environment)
        self.assertNotIn("TMUX", spec.environment)
        self.assertEqual(self.queue.path.stat().st_mode & 0o777, 0o600)

    def test_symlink_workspace_cannot_target_existing_deployment(self):
        parent = self.store.state / "workspaces"
        parent.mkdir()
        (parent / "pilot").symlink_to("/tmp", target_is_directory=True)
        with self.assertRaises(Invalid):
            self.sessions.prepare("pilot")

    def test_simulation_in_a_different_installation_path(self):
        with tempfile.TemporaryDirectory(prefix="ia-portable-") as directory, \
             patch("socket.socket", side_effect=AssertionError("network forbidden")), \
             patch("subprocess.Popen", side_effect=AssertionError("processes forbidden")):
            result = simulate(directory)
        self.assertEqual(result["outcome"], "complete")
        self.assertEqual(result["repeat_event"], "duplicate")
        self.assertEqual(result["agent_calls"], 1)


if __name__ == "__main__":
    unittest.main()
