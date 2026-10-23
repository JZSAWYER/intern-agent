# Automatic Codex recovery and Recovery Sentinel incidents

The receiver checks registered Codex conversations every five seconds. When
Codex reports a completed turn with a transient network, capacity or server
error, an enabled recovery policy schedules a continuation in that same
conversation. This supports managed app-server conversations and registered
native terminal attachments. New registered bots inherit the global policy;
an unrelated, unregistered terminal is not automatically adopted. On the personal
installation Debug Bot is reserved for manual work: automatic continuations are
disabled there and automatic incidents go to Recovery Sentinel instead.

The owner enabled this policy on the personal installation. Portable installs
start with recovery disabled until their owner enables it. In private `live.json`,
the optional configuration is:

```json
{
  "auto_recovery": {
    "enabled": true,
    "max_attempts": 3,
    "backoff_seconds": [30, 120, 300],
    "incident_session": "s_<registered-recovery-operator>"
  }
}
```

Use the actual registered Recovery Sentinel session locally. Do not commit private
configuration, account identities, credentials or relay state into a package.
The setting accepts one to five attempts and bounded delays. The receiver does
not need another Feishu app or bot permission to recover. The recovery operator
has its own conversation, console, workspace and permanent code/history branch.
The legacy `debug_session` field is still readable for older installations;
`incident_session` takes precedence, including an explicit `null`. New setup
should use `incident_session` and a dedicated operator rather than a manual bot.

## What happens after a failure

1. Observe an authoritative failed completion. Intermediate errors, a quiet
   console or elapsed time alone do not prove failure.
2. Record an incident and send a concise recovery notice in the affected chat.
3. Wait 30 seconds before attempt one. If that turn fails transiently too, wait
   120 seconds before attempt two, then 300 seconds before attempt three.
4. Before each dispatch, verify that the same failed turn is still current and
   that the workspace, selected task and branch still match. A new owner request
   or independent terminal turn supersedes the scheduled continuation.
5. Append a separate continuation turn. It tells Codex to inspect saved work and
   background jobs, continue unfinished work, and avoid duplicate actions/jobs.
6. Stop after the configured attempts. Send a durable incident to Recovery Sentinel.
   Its final diagnosis or repair response is sent back to the source chat.

The original owner prompt and failed turn remain unchanged. Each continuation
has its own recorded request, result and evidence linking it to the incident and
original recorded turn. Normal hooks, Markdown checks, Git upload guards and
the existing one-branch-per-new-bot layout remain in effect. The continuation
instruction is a precaution, not proof that an agent can never duplicate work.

Codex reports `failed` on the completed turn and supplies its error classification.
Inspection uses `thread/read` and the latest-turn listing without resuming or
starting work. A managed continuation resumes the existing thread and appends a
new turn. See the [official Codex app-server documentation](https://learn.chatgpt.com/docs/app-server).

## Controls in Feishu and VS Code

```text
/recovery status
/recovery inbox
/recovery off
/recovery on
/recovery retry INCIDENT
/stop
```

`status` shows attempts and waiting, queued, running, resolved, escalated or
cancelled incidents. `inbox` also shows incident/reply delivery state. Control can
target another bot using `/recovery SESSION status|on|off|inbox`. In VS Code,
**Intern Agent: Automatic Recovery** offers these controls for the selected bot;
the dashboard shows recovery state and attempts.

`/stop` interrupts active work and cancels queued/waiting recovery. `/pause` also
cancels recovery. Turning recovery off cancels automatic continuations without
interrupting an already accepted Codex turn. A changed thread, task or branch
prevents a stale continuation. Model/reasoning/Fast settings remain configured
on the same session and apply through the usual next-turn behavior.

After repairing an escalated incident, the source bot, configured recovery operator or
owner in Control can use `/recovery retry INCIDENT` to rearm one bounded cycle.
The latest failed turn must still match; attempt numbers remain cumulative.
The monitor never rearms an exhausted incident on its own. `/resume` retains
its existing meaning: unpause a workspace; it does not blindly replay a request.

## Durable bot communication

The private relay database stores incidents, attempt boundaries and mailbox
entries. If Recovery Sentinel is busy or paused, its incident waits. It runs through the
normal owner-authorized queue when that exact conversation becomes available.
No second operator session is launched. Replies return as notices, not executable
prompts, so they cannot trigger a reply loop. If the operator's incident turn needs
its own successful continuation, the reply follows that proven recovery chain.

If Recovery Sentinel itself exhausts recovery, or no operator is configured, the
incident requires owner review. An explicit repair/rearm gets a separate mailbox
entry; restarts retain attempt counts and do not duplicate queued continuations.
An ambiguous Feishu notice send is retained as uncertain and is not blindly sent
again. Inspect the inbox and notice counts in `/recovery status`. There is no
fallback to Debug Bot. Read the [operator runbook](RECOVERY_OPERATOR.md) for
evidence checks, repair boundaries and known Git/delivery failure modes.

## Boundaries and verification

Authentication, exhausted account quota, context-window, policy and sandbox
errors do not start this procedure. Approvals and healthy long-running turns
stay active. A broken relay transport preserves the backend turn; the observer
must subsequently confirm its outcome before recovery can run. Existing jobs
and terminals are not killed or reset. Recovery adds a new turn only after the
prior one ended; it does not resume a lost tool call inside that prior turn.

Known delivered Git-review notices can let a guarded recovery or operator incident
reach its preflight checks. Existing blocked records remain blocked. New code
still needs a verified baseline and matching published head; Git or transport
preflight blockers go to Recovery Sentinel rather than bypassing validation. Unknown
execution and ambiguous answer delivery still require reconciliation.

Known ended turns with delivered answers allow fresh messages past their Git
reviews even without an intervening successful upload. Each gets its own
preflight and baseline; historical reviews are retained. The independent reply
outbox and flow watchdog also handle saved-answer delivery, queue/preflight
diagnostics and Recovery Sentinel incidents. See [reply delivery](REPLY_DELIVERY.md).

Automated checks cover classification, all attempt/backoff boundaries, exact
thread/branch/task guards, restarts, owner changes, stop/pause, future sessions,
thinking-reaction lifetime, native failures, transport preservation, durable
escalation, repaired operator replies, explicit rearm and uncertain notice delivery.
Operator separation checks prove that a busy manual bot is untouched and the
operator cannot self-escalate or fall back to it.
No PR, merge, repository reset or credential change is part of recovery.
