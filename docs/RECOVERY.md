# Git recovery and workspace memory

For the recommended VSIX/repository-name workflow and shared SSH clients, see
[portable recovery](PORTABLE_RECOVERY.md). The low-level single-branch procedure
below remains available for explicit inspection.

Every projected workspace update writes `.intern_workspace/recovery.json` on
each new bot's same code/history branch (legacy bots use a management branch).
It carries conversation identities, named tasks,
acceptance receipts, saved requests/results, history revisions and owner notes.
New checkpoints also carry whitelisted conversation preferences and role hints.
The publisher and Git guards validate it against the Markdown in the same
tree. Credentials, Feishu identities, thread IDs, structured machine paths, transcript
cursors and pre-turn dirty-file baselines are excluded. Historical message text
can retain old path references; those are not current machine bindings.

The launcher snapshots backend Python modules, the file-transfer skill and
public guides into a private, content-hashed runtime release. Agent branch
switches cannot change that deployed code. Updates
take effect only when the relay is deliberately relaunched with a new release;
Git validators are installed from that same release.

Recovery uses a **fresh private state directory and independent clean clone**.
Fetch the bot branch through your own GitHub authentication. A mixed archive
that includes legacy bots also requires their recorded management/code branches.
Preview first; the preview does not change Git refs or import records:

```bash
python3 -m intern_personal.feishu_live --state /data/intern-agent-fresh \
  restore /data/project-clone --repo https://github.com/<github-username>/<repository> \
  --branch intern-agent/<workspace>/<bot-id>
```

`--branch` names the branch containing the archive; the older
`--metadata-branch` spelling remains an alias. Add `--apply` to restore records.
Recovery preserves each bot's recorded layout. New combined branches do not
require a second metadata checkout or create another published branch.
The workspace name comes from its validated archive. Existing live ledgers are
rejected rather than merged or overwritten. The new repository recovery plans
can append disjoint histories and reject conflicts without renumbering. Sequence numbers and revisions
remain stable. Historical events are marked `archived`, conversations are
marked `restored`, and no historical request is placed in an execution queue.
Unknown or interrupted work remains historical evidence, never an automatic retry.

Configure the fresh state's GitHub username, Codex executable and Feishu app
credentials, then explicitly start and pair it. Do not run two receivers for
the same app concurrently. `/new WORKSPACE` reconnects a restored conversation
to a new chat and new Codex thread. It reuses its published home/task branches
and loads saved knowledge. Old credentials, chat routes and native terminal
attachments require deliberate fresh setup. Use `/new-session WORKSPACE` for
a separate conversation and `/task use ID` to take an unfinished saved task.

Memory notes are explicit owner input. `/memory add TEXT` records a decision,
`/memory error TEXT` records a failure to avoid, and `/memory rule TEXT` records
a project constraint. `/task note TEXT` stores task-specific knowledge.
Recent completed agent reports are included automatically with source links and
historical-report labels; commands and failed/interrupted executions do not
displace them. A selected task uses that task's reports; idle chat uses its own
unassigned reports. Tool output and internal reasoning are excluded. Context is
capped at 18,000 characters. Long-term transcript files remain private. See
[reusable records](RECORD_QUALITY.md) for index and duplicate-note behavior.

An explicit Codex protocol rejection clears only that message's console dispatch
marker. A timeout or disconnect preserves the marker because execution may have
started; use the normal observed/explicit recovery path, never automatic replay.
Cancelling a queue record does not itself prove that its console turn stopped.
The thinking reaction remains until observation confirms completion/abort,
eligible code/history publication is confirmed and the saved result is delivered
or explicitly reconciled. Public interim answers and requested file delivery do
not establish this completion.

Current limits: recovery is for the versioned personal pipeline, not an automatic
import of enterprise state-v1. It requires the recorded code branches to remain
available. Individual metadata files are capped at 2 MiB. Large recovery archives
already use verified parts with a 64 MiB total limit; exceeding a limit blocks
upload rather than silently dropping records.

Snapshots now use compact UTF-8 JSON with the same restore schema. Unreferenced
untracked fragments receive verified private backups before bounded collection;
required parts, staged edits and readable history stay intact. See
[history storage and backup restoration](RECORD_STORAGE.md).

See [the editor, message and team workflow guide](EDITOR_TEAMS.md) for the 0.5.0 dashboard, setup diagnostics, session controls, bounded teams, workspace access and paged recovery.

## Connected receiver with no final reply

A live Feishu connection does not prove that a Codex turn is still running or
that its saved reply has been delivered. `/status` shows execution activity
separately from the reply's Git and delivery stages. `/doctor` also checks
blocked synchronization and console observation.

Once a turn has observably ended, its saved final answer waits for confirmed
code/history upload. Git failures keep the result and thinking reaction pending;
`/status` shows the actual blocker and retry timing. Retrying uses the saved
result without another Codex run. Public interim follow-up answers can appear
during ongoing work, but do not establish completion. See
[Git completion](GIT_COMPLETION.md).

Known ended turns with delivered answers do not hold the next owner message
behind their Git reviews, including consecutive reviews. Every new dispatch
still verifies the registered branch, published head and its own baseline,
preserving preexisting dirty files. Older reviews are retained and do not become
verified task deliveries. The leased final outbox waits for a Git publication
receipt. Unknown execution/delivery retains explicit review.
`/status` shows queued messages, reply transport and Git separately; the flow
monitor produces durable diagnostics and Recovery Sentinel incidents for unanswered
messages. See [reply delivery](REPLY_DELIVERY.md).

An observed failed completion, including a native completion with an error and
no final answer, clears the active-turn marker and saves a bounded failure
notice. It retains the original prompt, files and session. It does not resend
the request, interrupt another turn, or report task success. Private backend
error details remain local. Persistent observation failures produce one
sanitized progress warning after 30 seconds when progress is enabled.

With the owner-selected [automatic recovery policy](AUTO_RECOVERY.md), a
confirmed transient failure schedules a separate continuation in the same
thread after bounded backoff. It never replays the original prompt. Three failed
attempts escalate to the configured recovery operator through a durable mailbox; its
answer returns to the affected chat. `/stop` and `/pause` cancel pending recovery.
Unknown execution and ambiguous sends retain the explicit review path.

Saved replies use `preparing_reply` while validating and uploading Git, and
`sending` only immediately before the Feishu API call. A receiver restart can
resume Git preparation safely; an interrupted API send still requires explicit
reconciliation. Missing synchronization retry entries are rebuilt, and retries
rejoin the normal queue with its ordering and exclusive claims. An independent
console turn defers the Git snapshot without tying up a worker or stopping Codex.

Research PDF, PNG, JPEG, GIF and WOFF2 site fonts can pass the Git guards when their
signatures match their extensions. The existing size, private-path, credential
and branch checks remain in force. Markdown, JSON, Python and management records
retain strict text/format validation. WOFF2 fonts also require a complete header,
matching file length and bounded declared blocks; this is a format check rather
than font rendering or decompression. Unrecognized binary artifacts need review.
There is no fallback to another repository host.

## Blocked history and help replies

Run `/status` in the affected workspace chat. It shows that conversation's
blocked or running turn numbers, observed agent activity and recovery steps;
Control shows the whole queue. Conversation `Mode: idle` only means no named
task is selected. It does not prove that all earlier turns were delivered.

If a turn was observed interrupted, any saved interruption notice is not a
completed project result. Inspect the worktree. In Control, use
`/resolve TURN deliver` to publish eligible saved files and deliver the saved
notice, without replaying Codex. The normal code and Markdown guards still run.
After reconciliation, send a new continuation request in the original bot chat
to finish the existing draft. Do not resend the initial creation request.
`/resolve TURN cancel` retains history and files, but does not publish the
partial work; existing dirty files then require review before another tracked
turn modifies them.

New configurations use `turn_timeout_seconds: 0`: there is no default elapsed-time
execution cutoff. `/stop` still interrupts the current turn. A positive value
explicitly enables a limit in seconds; transport and approval timeouts remain
separate. Existing configurations retain their saved setting until it is
changed deliberately. For example, a saved value of `900` can interrupt healthy
research after 15 minutes. Inspect that setting when a turn
stops at a repeatable duration. New failure records preserve a safe category,
including an execution cutoff, without saving exception text.

`/doctor` checks unresolved delivery records as well as receiver connectivity.
A connected receiver can still have queued messages behind a `record_blocked`
turn. Repair the validation failure first, then use `/resolve TASK retry`; saved
results resume recording/delivery without rerunning Codex. Tutorial `CODE`
placeholders remain visible. Real pairing/approval/answer codes are redacted
consistently before Markdown quoting and in the recovery archive; repeated
redaction leaves the existing marker unchanged.
