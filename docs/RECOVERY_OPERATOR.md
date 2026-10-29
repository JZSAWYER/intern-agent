# Recovery Sentinel operator runbook

Recovery Sentinel owns automatic network/capacity recovery and relay flow
incidents. It has its own registered workspace, permanent code/history branch,
Codex thread, tmux console and owner-only Feishu group. Debug Bot handles manual
debugging only. Do not ask it to repair an automatic incident or queue a
continuation in its conversation.

Use the absolute runbook path supplied by the current workspace hook. The
immutable relay runtime includes this guide and the linked public guides even
when the affected project has no relay documentation. A normal receiver start
refreshes ready-session references without waking or restarting Codex.

Act within the owner's configured recovery policy and current incident
authorization. Authorized work can include inspecting saved state and the
affected session, preserving user work, preparing and testing a focused relay
repair, and guarded reconciliation or bounded same-thread recovery. Reading
this runbook does not authorize activation or unrelated work. Do not change account credentials, Feishu app
permissions/publication, default branches, or unrelated services. Do not reset
research jobs, replay accepted requests, force-push, merge, or create a PR.
Report repairs and limits in your Feishu group; the relay returns incident
answers to their source chats. An account/provider limitation goes to the owner
in this chat or Control, without falling back to Debug Bot.

## Start with evidence

Read the incident's event and `turn_records`, `reply_outbox`, `codex_outcomes`,
console/native snapshot, recovery episode/run and mailbox. Follow the current
workspace hook's local relay CLI rather than guessing a state root or reading
credentials. Keep raw transcripts, account/native thread IDs, resource keys and
authenticated URLs out of Feishu reports. Save sensitive checkpoints only in
private 0700 directories on the configured data volume, with 0600 files.

Check the same registered thread using read-only `thread/read` and the latest
turn listing. Inspect exact `terminal_turns.event_seq` / pending association,
not just similar text: automatic continuation text is intentionally reused.
For example, a completed continuation and a later undispatched continuation
can contain identical text while belonging to different events.
Keep failed turns and every earlier review/result/receipt intact.

Classify the state before acting:

| Evidence | Meaning | Action |
| --- | --- | --- |
| Healthy active turn or active jobs | Ongoing work, even if quiet | Preserve it; inspect progress/observer health and report status |
| Waiting approval/input | User decision needed | Surface the existing request; do not interpret it as a network failure |
| Authoritative failed turn with network/capacity/service category | Eligible transient failure | Existing monitor schedules bounded same-thread continuations |
| Authentication, billing/quota, context or policy error | Owner/provider/configuration action | Explain the specific blocker; no blind retry or credential change |
| No result/outbox/associated Codex turn, preflight failed | Request was never dispatched | Repair preflight, then restore only that saved request through normal guarded dispatch |
| Saved final answer, no receipt | Publication or delivery is pending | Inspect the confirmed Git upload state, outbox and transport; do not rerun Codex |
| Unknown execution or unknown API send | Outcome uncertain | Inspect exact thread/message evidence; retain review until absence or completion is proved |
| Historical completed answer with receipt, Git blocked | Earlier execution/delivery finished | Repair publication separately; keep historical reviews and task acceptance gates |

## Transient network and capacity

The normal policy permits three attempts with 30/120/300-second backoff. Only
an authoritative ended failure can start a continuation. Verify the current
failed turn, workspace, task, branch and stop state before dispatch. A new owner
request or independent turn supersedes an old continuation. Keep the exact
thread, model/reasoning/Fast settings and saved work; check existing jobs before
resuming. Never launch duplicate monitors, training runs or experiments.

Check receiver heartbeat, existing app-server socket/process, transcript
observer cursor and confirmed error category. A capacity error often belongs to
the provider: a working socket/proxy is not evidence of spare model capacity.
For connectivity, inspect the configured proxy's availability using redacted
diagnostics; do not dump environment variables, authentication stores or token
files. Preserve a live backend turn when the relay connection drops.

The receiver heartbeat is a private health timestamp, not a chat reminder.
Healthy silent turns send no periodic generic completion notices. Do not
reinstate them or infer failure from a missing public prose update. Read exact
backend status and fresh transcript timestamps; actual failures still use the
normal bounded recovery and incident paths.

After repairing an escalated cause, the supported `/recovery retry INCIDENT`
control rearms one bounded cycle, with cumulative attempt numbers. Use the
hook's local `control` CLI targeting the source session. Do not reset the
counter or repeat this command indefinitely. If Recovery Sentinel itself
exhausts recovery, report to the owner; never escalate to itself or Debug Bot.

## Git preflight and history

Check the registered GitHub origin, permanent branch, validated hooks, published
head and index without editing research files. Take the same workspace lock
used for commit/push before comparing local and remote heads. Background
metadata commits can temporarily be ahead of GitHub while a push is in flight.
`WorkspaceTracking.begin_turn` holds that lock. Genuine
unpublished code or divergent branches remain a review, not a reason to bypass
guards or discard work.

Use the deployed immutable relay release for Git validation. If executable hook
hashes differ, establish whether a reviewed deployment changed them; do not
disable hooks or edit cached releases. Only reinstall reviewed guards as part
of a concrete repair. Preserve pre-existing staged work and never commit
unknown research changes merely to clear a queue.

A recorded `code_start` is immutable. Do not clear it when restoring preflight.
For a proved undispatched request, a private dirty snapshot may be refreshed
only after verifying the published code tree has not changed relative to its
recorded head, excluding management records. Preserve the original marker and
record the proof. A different code tree needs explicit reconciliation.

Running research jobs legitimately change logs, results and checkpoints.
Pre-existing file changes are deliberately reviewed; do not kill jobs, freeze
their outputs or alter data to satisfy a Git baseline. Capture a private
checkpoint and inspect who owns each change. Code and history for a new bot
stay on one permanent branch, and diagnostic code fixes stay on your own
branch. Published artifacts/Markdown must pass format, path and credential
checks. No automatic PR, merge or default-branch update.

## Safe queue and delivery repair

Before reconciling an undispatched event, check its original text hash, absence
of result/outbox/receipt and absence of an associated Codex turn. Recheck the
latest failed turn and recovery cycle. Preserve the same event and attempt;
do not create another copy. If a preflight-only failure escalated its cycle,
restore the existing queued-cycle state atomically with the retry, without
increasing or resetting attempts. Use `LiveState.resolve(seq, 'retry')` for
`record_blocked` and let the normal claim, preflight, hooks and host dispatch it.
Verify the exact event is observed running or ended afterward. Keep every
other review row unchanged.

The final-answer outbox and file-transfer outbox have independent leases and
stable message keys. The official final answer waits for confirmation of its
code/history upload; requested files and public interim live-follow-up answers
can arrive earlier without establishing task completion. The thinking reaction
remains until authoritative completion, confirmed publication and reply delivery
or explicit reconciliation. Retrying Git must not resend an answer. An interrupted
or transport-failed send is uncertain; it may have arrived, and Feishu's
deduplication window is finite. Reconcile it explicitly rather than blindly
repeating. File upload failures can retry their private snapshot; uncertain
file sends require inspection. Missing file permissions are an owner account
action, not a Codex resume failure. Do not grant them yourself.

Only the receiver can be reloaded after existing managed turns and send leases
are safely idle. Preserve app-server/TUI processes, tmux identities, routes,
pairing, credentials and research processes. A stuck receiver is not a reason
to restart the affected Codex thread. Keep receipts and watch for lease
recovery after any approved operational change.

Your own incident turn is also a managed turn. Do not reload the receiver from
inside it: the receiver can interrupt its owned turns during shutdown. Finish
testing and publish a prepared repair on your own branch first. Activation must
use an independent guarded operation after your turn and other managed turns
end. If that operation is unavailable, report the prepared repair and concrete
activation blocker to the owner or Control; do not route it to Debug Bot.

A separately attached native monitoring terminal can survive a receiver reload.
An approved activation operation may preserve that exact native turn using its
registered configuration, pane/process identity and event association. This is
not an exception for managed or unidentified active turns. Every nested hook
repair must enforce the same precise boundary; never hide activity by changing
saved events or terminal snapshots to idle.

Compare semantic settings when guarding an activation: service tier and model
choices must stay fixed, while an observation's updated_at is not a changed
choice. Capture semantic values at preparation. For older raw snapshots, require
independent evidence proving the narrow timestamp-only difference before
adjusting expectations. Preserve the original snapshot and all other guards.

Tested, published, scheduled, active and delivered are separate milestones.
Verify activation receipts and the original source reply before reporting a
repair complete. If a follow-up was queued, verify its exact dispatch and result;
a prepared helper or a completed operator turn alone does not prove recovery.

## Verify and report

Check your own dispatch health as well as the affected bot. A durable incident
is received work, not proof that your Codex started it. An unresolved preflight
review can hold later incidents even when the terminal is idle. The incident
lifecycle check reports that blocker once to you and the source chat; it never
routes an automatic turn to Debug Bot or to another recovery operator.

Obsolete flow incidents may leave the queue only with a never-claimed queued
request or a durable preflight witness and no transport/result/outbox evidence.
The original event and review are archived in the private incident resolution
audit and a resolution is appended to Markdown evidence; old failure phases,
immutable baselines and receipts remain intact. Legacy failed preflights without
a witness need an owner-authorized manual audit of the exact pinned canonical
history before cancellation. Active or uncertain work must remain untouched.
Do not close a queue incident just because its request was claimed: a database
lock or Git preflight can still fail before any Codex input was attempted.

State the diagnosed cause, evidence, concrete action, current execution/delivery
state and remaining owner/provider action. Verify input hashes, thread/branch,
attempt counts, prior reviews, process identities and receiver health. Never
claim research completed because a request was queued, or a file arrived
because it was uploaded. Do not rerun one-shot repair scripts after dispatch.
An unresolved incident needs a specific owner-facing result in your own chat
and its source reply, not repeated escalation or a request to Debug Bot.

References: [automatic recovery](AUTO_RECOVERY.md),
[preflight and delivery](REPLY_DELIVERY.md), [Git recovery](RECOVERY.md),
[existing terminal observation](TERMINAL.md),
[file transfer](FILE_TRANSFER.md), and [feature tutorial](FEATURE_GUIDE.md).
