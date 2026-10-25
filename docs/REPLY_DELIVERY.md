# Replies, Git review and messages that need attention

Codex execution, Feishu reply transport and Git publication have separate
durable records. A Git upload requiring review does not mean that Codex is still
running, and it must not permanently silence the next owner message.

## Git preflight and background history publication

Before dispatching a request, the relay captures its code baseline under the
same workspace lock used for history publication. A background history commit
and its push finish before the local/GitHub comparison, so their temporary
difference cannot strand an unexecuted request in review. Actual unpublished
code, changed branches and unverified work still require reconciliation.

A previously blocked request may return to the normal queue only after checking
its saved result/outbox, exact Codex turn association and current preflight.
Identical automatic continuation text can occur in different turns; a text
match alone does not establish which event Codex received. Keep other review
records and failed turns intact, and never replay an event already dispatched.

If Git already records that request's `code_start`, preserve it: the format
guard treats this starting marker as immutable. Do not clear it when retrying
preflight. A private pre-dispatch dirty snapshot can be refreshed for a proven
unexecuted request only after verifying that the current published tree differs
from the recorded head solely in management records. Changed committed code
requires its own reconciliation.

If a formerly visible untracked journal is now explicitly ignored, its local
bytes are not a proposed Git upload. The draft checker uses Git's actual ignore
rules for those existing regular files, without changing the original baseline
or forcing large raw data into the repository. Tracked files matching an ignore
pattern, deleted drafts, linked replacements and staged owner work retain their
reviews. Publish useful bounded archives/summaries under the project's declared
artifact policy; this rule does not waive size or credential checks.

## Completed replies

After execution is observed completed, the relay saves the result and publishes
eligible code and validated history. The official final reply waits until the
exact uploaded records commit is confirmed on GitHub. A failed upload retains
the saved result and thinking reaction; `/status` shows the blocker and next
Git-only retry. An unrelated later console turn does not erase a saved result.
Failed, stopped and explicitly reconciled turns retain their distinct outcomes.
See [Git completion](GIT_COMPLETION.md).

The private `reply_outbox` freezes the complete payload, observed speed/quota footers and message
key before sending. The sender and publication worker share an atomic send lease,
so they cannot both send the same answer. The receipt is saved independently of
Git status. A later retry uses that receipt and does not resend Codex's answer
or execute the original prompt again. The thinking reaction ends only after
authoritative completion, confirmed publication and reply delivery or explicit
reconciliation.

Public answers to accepted live owner follow-ups can arrive while the same turn
continues working. They are interim progress and do not certify Git publication
or task completion. The eventual completion is correlated with an identical
already-sent answer to avoid duplicates; uncertainty still needs review.
Requested file returns use their own durable outbox and remain independent of
code/history publication. See [live follow-ups](CONVERSATIONS.md#live-follow-ups)
and [file transfer](FILE_TRANSFER.md).

Task acceptance still requires verified and uploaded work. Reply delivery alone
does not mark blocked Git work accepted or fabricate a starting baseline.

## The next message

An older Git review with a delivered reply and a known ended execution does not
hold the next owner message. This applies even when several consecutive turns
need Git review; it does not depend on an intervening successful upload.
Every new turn still checks the registered workspace, branch and published Git
head and captures its own starting baseline. Existing staged or dirty files
retain their normal protection and upload review.

Unknown execution remains a barrier. An ambiguous Feishu send remains a delivery
incident requiring confirmation; it is never blindly repeated. A receipt for
an error/diagnostic is not proof that an unknown Codex turn ended. Earlier active
turns, ordered queued prompts and actual Git operations keep their usual guards.

## Diagnostics instead of silence

The five-second flow monitor observes unanswered messages separately from the
model's optional progress mode:

- A failed preflight immediately records a diagnostic stating that Codex never
  received the request. The request stays saved and Recovery Sentinel gets an incident.
- A message waiting behind active work gets a concise explanation after a minute.
  A paused conversation explains how to resume it.
- An idle dispatch delay persisting for three minutes gets a durable Recovery Sentinel
  incident after confirmation across multiple monitor scans.
- Unknown execution or delivery, and a saved answer waiting over three minutes
  after completion, get a diagnostic and Recovery Sentinel incident.
- Healthy work stays quiet when Codex has no public commentary. Elapsed time
  alone produces no repeated “not reported completion” or generic working
  notices. Real commentary, approval requests and final/failure messages retain
  their existing delivery. `/status` is available on demand. There is no initial
  “Working on your message” response.

The database also blocks legacy `heartbeat_` notices while an older receiver
finishes an active turn. Pending legacy heartbeats are marked suppressed; their
text and previous delivery receipts are retained. This takes effect without
restarting Codex or the receiver and does not suppress recovery or fault notices.

Incidents use the existing private bot mailbox. They wait while Recovery Sentinel is
busy; its final diagnosis returns to the source chat. Stale incidents that have
not started are resolved when the message proceeds. Diagnostics also appear in
Control for faults. Source prompts, private backend errors and credentials are
not included in those incident messages.

Use `/status` to see queued/running messages, Feishu delivery state and Git review
separately. `/recovery inbox` includes both Codex recovery and flow incidents.
VS Code also shows Feishu delivery separately from Git synchronization.

## Restart and transport uncertainty

A pending outbox job survives a receiver restart. A send interrupted before its
receipt was persisted becomes `uncertain`; it is not assumed to have failed and
is not automatically repeated. Recovery Sentinel can inspect and reconcile it. The owner
can use `/resolve TURN deliver` after confirming that delivering the saved body
is appropriate. The frozen body and key are retained, including legacy replies
that were sent before quota footers existed.

These safeguards remove the recurring Git-review queue deadlock and keep
diagnostics durable. They cannot guarantee Feishu or Codex availability during
an external outage; the pending answer and incident remain saved for recovery.

## Verification

Tests cover withholding the official final reply before confirmed Git upload,
stable saved results across failed publication, interim follow-up answers,
simultaneous sender/publisher claims, restarts during sends, frozen payload
reconciliation, unknown and active turns, independent later terminal work,
repeated historical Git reviews, ignored raw journals and retained draft
protections, fresh preflight checks, waiting diagnostics,
Recovery Sentinel replies, quiet healthy work and suppression of legacy
heartbeats without losing actual fault notices. Offline checks do not establish
a live account's connectivity, permissions or message delivery.
