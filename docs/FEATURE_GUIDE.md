# Intern Agent feature tutorial

## Installable product 0.2.5

Summary progress now starts a fresh editable message after a question card is
delivered or its submitted answer is confirmed. New commentary stays below the
card while the exact conversation, thread and turn remain unchanged. This
position survives restart; uncertain sends are not replayed and final-delivery
requirements remain intact. Separate-message mode keeps its existing behavior.

The product repository and releases stay private until the owner explicitly asks
for public visibility. Install the versioned product VSIX with its matching isolated runtime and verify
SHA256SUMS/provenance first. The public identity/namespace is independent of
the development preview. Source and complete recovery/file/install/contributor
skills are attached alongside the VSIX. Use [installation](INSTALL.md),
[portable recovery](PORTABLE_RECOVERY.md) and [release workflow](DISTRIBUTION.md).
Skills include referenced resources; both bundled and standalone installation
refuse linked destinations and preserve modified user files. Each verified
feature handoff gets a fresh release version; private bot history is never a
public source branch. Account setup and live receiver acceptance are separate
from package installation.


Start here to learn the available features. The detailed guides linked below
are part of the same tutorial; they contain setup, command syntax and recovery
details. Use [the change record](CHANGELOG.md) to see what changed, and
[the development workflow](DEVELOPMENT_WORKFLOW.md) when adding or fixing a feature.

This checkout is the stable product source. The maintained development preview uses
a separate identity. The published product uses
a stable extension identity, licensed clean source and versioned VSIX/skills
artifacts; see [the release workflow](PUBLISHED_RELEASE.md). “Implemented” does
not imply every account permission, editor installation or live operation has
been accepted.

## Persistent goals and questions

**Purpose:** continue a verified owner objective across turns, and answer real
Codex choices in Feishu instead of losing them in a terminal.

**Use:** `/goal OBJECTIVE`, `/goal`, `/goal pause|resume|clear` and
`/goal auto on|off`. Semantic selection uses the current owner request without
rewriting it. Codex records evidence and remaining work; each continuation waits
for the prior Git upload and Feishu reply. `/stop` also pauses the goal.

Resuming also respects unfinished work and questions in that conversation.
An old Fast-command review with an exact verified receipt cannot silently
block a new goal iteration; its original record remains preserved.

Question cards include options, descriptions and custom text, preserving each
question independently. Use `/questions` or `/answer CODE TEXT` as a fallback.
In summary mode, the next real progress update after a card or confirmed
submission starts below that card, rather than editing an earlier message.
The app needs the published `card.action.trigger` long-connection callback.
VS Code adds **Manage Persistent Goal** and **Show Pending Codex Questions**.
See [the goal and question tutorial](GOALS_AND_QUESTIONS.md) for budgets,
native compatibility, recovery boundaries and account setup.

## Git uploads and local research output

**Purpose:** publish code, useful research artifacts and validated history while
keeping declared local-only raw journals out of Git.

**Use:** keep large raw output local through the project's explicit ignore rules,
and version a bounded archive or a useful summary with hashes as appropriate.
The relay honors those rules even when the raw file was visible at the start of
the turn. It does not delete the journal or rewrite the saved starting baseline.
Tracked changes, staged owner work, linked replacements and unreviewed large
publishable files still require their normal checks.

**Expected result:** excluded raw journals do not create a false missing-draft
review. Final replies and turn completion wait for confirmed Git publication;
`/status` shows model activity, Git status and delivery separately. A retry uses
the saved result and never reruns an executed prompt. Once upload and delivery
finish, the original queued owner message proceeds through fresh preflight.
See [reply and Git recovery](REPLY_DELIVERY.md) for genuine review/network errors.

## Reconnect or recover on another computer

**Purpose:** transfer project progress through Git and reuse a Linux host's
existing sessions from another Remote SSH editor.

**Use:** install the released VSIX on the Linux host and run setup. A private
host profile reconnects an existing installation. Fresh setup can detect the
GitHub account from masked PAT input. Use **Recover Workspace from GitHub** with
a repository name or URL, review/apply the archive plan, then **Reconnect
Restored Conversation** after missing account setup/pairing.

**Expected result:** the exact existing bot is reused on the same server.
After total local-state loss, uploaded code, readable history, tasks, notes,
team/reference records and saved model/Fast/progress/trigger/recovery preferences
return from validated Git archives. The saved controller can reuse the new
management group; native/manual Debug stays manual. No old request or experiment
is automatically replayed. Install the shipped Codex skills with **Install
Bundled Codex Skills**; modified user skills remain untouched.

**Verification:** isolated real-Git tests delete the original checkout/state,
retain its bare remote, restore by name and reconnect the exact bot. Checks
cover multiple repositories, stale plans, identity collisions, controller/manual
roles, research definitions, preferences, host profiles and private PAT handling.

**Limits:** a PAT does not restore Codex/Feishu authentication, lost processes,
unpublished files or unbacked external artifacts. New chats/threads are needed
after total routing loss. Research definitions are shown as paused references;
`/research restore WATCH 10h` creates a deliberate new monitor. Old archives
cannot supply preferences their old writer never saved. See
[portable recovery](PORTABLE_RECOVERY.md).

## Keep the published VSIX synchronized with development

**Purpose:** maintain a separate installable public product while preserving
private development/history repositories.

**Use:** the owner configures one exact source bot, a separate Open Source bot,
public destination and license through `release-sync configure`. **Show Release
Synchronization** and the dashboard expose its jobs; `/doctor` checks worker
health. It is disabled for installations without an explicit private policy.

**Expected result:** a new uploaded public-source fingerprint queues one durable
owner-authorized release task. Records-only commits and receiver restarts do not
duplicate it. A busy operator finishes its current work first. The operator builds
and publishes a fresh stable VSIX, source/skills archives and checksum/provenance
manifest in the separate release repository. It reports verified URLs/hashes.
It does not send automatic work to manual Debug.

**Verification:** checks exercise committed-versus-unpublished source, repeated
polls, restart, records-only changes, busy operators, explicit policy, separate
repository enforcement and cancellation after policy changes. Stable packaging
rejects preview/unlicensed identities, and linked directories cannot import
private files into a package.

**Limits:** an operator's completed task is not proof of a public artifact.
Uploaded asset hashes and a remote source receipt are required. Marketplace
publishing is separate. See [the published-release contract](PUBLISHED_RELEASE.md).

## Install on another machine

**Purpose:** let any GitHub user install the selected Intern Agent release on
their own Linux machine with independent accounts and private state. Baseline
reproduction is one verification mode; private machine migration is separate.

**Prerequisites:** reviewed candidate, Linux x86_64 CPython 3.12 reference profile,
a new install prefix and separate short private state path. The new public
profile pins patched application dependencies and a venv-only pip bootstrap;
the historical advisory-affected profile requires explicit reproduction choice.
The final verified source/license handoff remains a release gate.

**Use:** run `python3 tools/check_release_runtime.py`, then preview
`python3 tools/install_runtime.py --prefix /path/to/new/install --state /short/private/state`.
Read [installation](INSTALL.md) for locked-wheel download and explicit apply.

**Expected result:** verified selected baseline fingerprint, a separate virtual environment,
fixed install receipt and backend path; no relay, account pairing or state copied
from this machine. An altered runtime or lock fails rather than drifting silently.

**Verification:** parity/installer regressions and an isolated public-profile
installation, pip check, SDK/transport/native imports and synthetic message/reply
results are recorded in the installation verification receipt. The optional
[install-on-linux skill](../skills/install-on-linux/SKILL.md) is validated.

**Limits:** installed frontend/live account/upgrade acceptance remain separate;
read [deployment parity](DEPLOYMENT_PARITY.md) and [frontend readiness](FRONTEND_READINESS.md).

## Build the installable public product

**Use:** read [distribution](DISTRIBUTION.md). From a clean public checkout, run
`python3 tools/build_release.py --expected-export-commit COMMIT --output-dir /path/outside/source/new-release`,
replacing COMMIT with the exact checked-out public commit. Version 0.2.5 uses
maintained source c21ea4419894d892c5c518ca9da3cac11dd3db7c and MIT.

**Expected result:** deterministic source, VSIX, complete skills, standalone skill
installer, provenance and SHA256SUMS. The public internAgentPublic namespace
separates preview settings. Complete resources install only after inventory,
checksum and destination checks; building/installing skills starts no receiver.

**Verification and limits:** frontend behavior, identity, reproducibility and
release/skill guards have offline checks. Uploaded source and artifact hashes
are independently verified in the release receipt. Remote SSH activation, live
accounts and receiver upgrades remain separate. New feature handoffs require a
fresh version; private history is never exported.

## Collaborate with separate branches and PRs

**Purpose:** let the owner and collaborator develop independently, while other
GitHub users can contribute from forks.

**Prerequisites:** a clean source checkout, confirmed integration branch and
appropriate remote access. The public product uses MIT; contributors submit only
changes they have rights to license.

**Use:** follow [contributing](CONTRIBUTING.md) and optionally load the
[collaboration skill](../skills/collaborate-on-source/SKILL.md). The example
layout uses owner/development and collaborator/development, with PRs to main.
A registered relay bot stays on its own permanent branch; public PR heads contain
source changes, not that bot's operational records.

**Expected result:** a focused contribution with explicit base/head, checks and
review by the other maintainer. No push, PR, merge or invitation happens just
because the guide or skill is read.

**Verification:** skill validation, local branch-isolation rehearsal and package
input checks are offline evidence; no GitHub PR or protection rule was created.

**Limits:** real repository/branch names and access follow the selected public
target. Actual PR creation requires an explicit instruction; merge is separate.

## Review an open-source candidate

**Purpose:** prepare the full editable extension and relay for collaborative
review without publishing operational history or personal state.

**Prerequisites:** the locally prepared candidate, Python 3.10+ and modern Node
for frontend tests; release changes require explicit source/rights review.

**Use:** read [the handoff](OPEN_SOURCE_HANDOFF.md), then run
`python3 tools/check_source_candidate.py` and `python3 tools/check_distribution.py`
from the candidate. Source and VSIX archive inputs have separate manifests.

**Expected result:** exact file hashes, exclusions and approved rights are
reviewable. The maintained commit is source_commit in source-distribution.json;
the runtime baseline inventories all backend modules, file-transfer guidance and
top-level Markdown guides. Local product/install changes and portable docs have
explicit provenance; running-deployment equality is not claimed. See
[the baseline contract](DEPLOYMENT_PARITY.md). File batching, live follow-ups,
peer diagnostics, throughput, cold recovery and reusable records are included.
The --release check requires approved ownership, licensing and dependency gates.

**Verification:** current checks and precise limits are in
[the audit](OPEN_SOURCE_AUDIT.md). This workflow does not launch a relay or upload.

**Limits:** clean-host/editor/live/upgrade/uninstall acceptance remains planned.
See [frontend acceptance](FRONTEND_READINESS.md) and
[license decisions](LICENSING_PROVENANCE.md). Existing changelog live claims are
historical evidence, not fresh verification of this candidate.

## Features available in every bot

The shared relay supplies file transfer, commands, progress, quota, history,
independent consoles, recovery and bot communication to registered conversations.
New bots inherit the same backend. Model/Fast choices, team assignments and
enabled skills remain per conversation; tutorial reminders select only Debug
and Control. See [the session feature matrix](SESSION_FEATURES.md) for coverage,
native hook requirements, paused/legacy exceptions and verification limits.
On a normal relay start, ready conversations' hook references refresh to the
installed runtime without sending a message to Codex. Attachment references for
the same thread and bot-specific choices are preserved. Runtime snapshots now
include the public guides, so Recovery Sentinel can read its pinned runbook
from any project. `/doctor` reports a failed startup reference refresh.

## Keep history storage bounded

Recovery snapshots use compact UTF-8 JSON while retaining every recorded value
and the existing restore schema. The publisher keeps required fragments, prunes
unchanged obsolete Git-backed parts, and saves verified private compressed backups
before collecting untracked generated leftovers. Markdown conversations and Git
history remain intact. Cleanup is bounded and preserves staged or modified data;
see [history storage and restoration](RECORD_STORAGE.md).

## Keep workspace knowledge useful

**Purpose:** retain reusable decisions, findings, methods and evidence without
duplicate reports or retry chatter. The shared hook guides all registered bots;
it does not alter your message or enable the Debug/Control tutorial elsewhere.

**Use:** read conversation/task knowledge indexes for source links, outcome and
Git status. Pin explicit knowledge with `/memory add|rule|error TEXT` or
`/task note TEXT`. Repeating an identical note in the same scope adds nothing.

**Expected result:** full source history remains; indexes no longer copy recent
replies, and recent hook memory stays within the selected task or idle chat.
Useful negative results and short constraints are retained. Structure is checked
automatically; reported claims still need evidence. See [record quality, checks
and limits](RECORD_QUALITY.md).

## Return to background research automatically

Before relying on an “ended” turn, see [turn completion and GitHub uploads](GIT_COMPLETION.md).
The official final reply now waits for confirmed code/history publication, and
the thinking reaction stays until that finishes. `/status` shows the actual Git
blocker and retry timing. Interim live-follow-up answers remain progress.

For an owner-authorized experiment that outlives the current turn, Codex can
save a durable alarm and finish normally. Example:
`/research start 10h "Inspect the existing experiment" --every 1h --checks 12 --for 48h`.
`/research status` shows the saved next check; `/help research` explains controls.
The same thread wakes only at/after its deadline and when idle. Owner input has
priority; existing jobs are inspected before any next step. `/stop` cancels its
alarms. Research checks, memory, Git history and Feishu replies share the normal
pipeline. This is separate from network/capacity recovery and does not activate
an unlimited goal loop. See [the research pipeline](AUTORESEARCH.md).
If Codex completed a check but its answer is missing, inspect saved delivery and
console acknowledgement; do not rerun that experiment. Research references are
recognized separately from the exact request by both console observers.

## Transfer PDF, ZIP and other files

Upload **file1, file2, file3, then your message** in the bot's Feishu chat.
Files wait without starting Codex; your next ordinary message submits them
together in upload order, with unchanged text. PDF, Word, Markdown, TXT, images
and other ordinary incoming files are accepted, up to 20 MiB each. Commands do
not consume uploads; `/files pending` lists yours and `/files clear` discards
them. The inbox survives restarts and stays separate for each sender and chat.
A busy Codex finishes its current turn before receiving the combined batch.
Codex receives private local files through the workspace hook. To get a result back, ask “Generate the report as
PDF and send it here” or “Send the results as a ZIP.”

`/send-file RELATIVE_PATH` sends the current workspace file, including
uncommitted results; `--committed` selects Git HEAD. `/files` shows delivery and
`/files permissions` checks the live app's upload/read/send grants without
changing settings. `/files retry TRANSFER_ID` retries saved bytes after repair. `/help files` opens
the tutorial in Feishu. Delivery runs independently of Git uploads.

The live app needs file-read/upload permissions before the first round-trip;
installed code or a queued file is not proof of delivery.
See [file transfer, permissions and recovery](FILE_TRANSFER.md) and the bundled
[agent skill](../skills/feishu-file-transfer/SKILL.md).

## Talk to a bot

Message routing uses each bot's own terminal and input worker. Another bot
working in the same repository does not block an idle Debug Bot or Control.
Ordinary owner text steers the same active Codex turn while its work continues.
Use `/queue TEXT` for a separate later task and `/stop` to cancel. The relay
confirms accepted live input and forwards its public answer when it is written,
even if the same turn keeps monitoring a background job. Earlier owner work can
be finished while a new automatic monitoring turn is active. See
[live follow-ups](CONVERSATIONS.md#live-follow-ups) for native
terminal safeguards, attachment handling and uncertain-send recovery.

A failed relay preflight must not leave an idle terminal marked as running.
Delivered reports awaiting Git bookkeeping do not need to drain before the next
owner question: their Git-only retries yield and resume afterward. A current
publisher keeps its lock, and the new request still passes normal Git preflight.
Proven unstarted requests retry their original saved event with bounded backoff;
owner messages take priority over a waiting research alarm. Once Codex dispatch
has been attempted, uncertain work requires reconciliation and is never blindly
replayed. You do not need to resend a saved request after a preflight repair.

Idle delivery prioritizes owner input, then peer information, then research
alarms. A waiting timer does not prevent another bot's question from reaching
the mailbox. Completed, delivered Git reviews remain saved without blocking
older queued alarms. Active turns are preserved and every new turn still passes
its own Git preflight. See [the bot communication protocol](BOT_COMMUNICATION.md).

Progress notes stay passive until read; they do not start a Codex turn. For
ongoing research collaboration, the owner can bind a finite review watch to the
recipient's existing conversation. It reads the selected peer's notes, checks
their evidence, saves an applied/rejected/deferred decision in Markdown, then
acknowledges each handled note. See [the review workflow](BOT_COMMUNICATION.md#review-progress-notes-during-an-authorized-research-plan).
The watch does not renew an old experiment budget or authorize new compute.
If you see incoming notes but cannot find the recipient's working updates, use
`/progress messages` in that bot's chat. The default `/progress summary` edits
one earlier message; separate-message mode makes each new public update a new
chat entry. A note receipt alone does not mean the recipient finished reviewing.

## Start a project with /init

**Purpose:** create a new private GitHub repository and its project bot without
manually creating a repository, worktree, Codex console and Feishu group.

**Prerequisites:** a configured personal GitHub owner, working GitHub API and
SSH authentication, a paired owner Control group, and the live relay dependencies.
The repository name must be unused. Existing repositories use the attachment
workflow instead.

In Intern Agent Control, send:

```text
/init my-project
/init status PLAN_ID
/init apply PLAN_ID
/init status PLAN_ID
```

Replace `PLAN_ID` with the identifier returned by the first command. Inspect its
destination and steps before applying it. The preview creates no repository or
bot. Applying the reviewed plan creates the named private repository, an empty
initial `main`, a registered workspace, and a bot with one permanent working
branch, its own Codex/tmux console, and an owner-only Feishu project group.

In VS Code, use **Intern Agent: Initialize New Private Project**. The dashboard
shows saved stages and blockers. **Open Initialized Bot Workspace** opens the
bot's worktree and working branch after completion.

**Verification:** disposable Git and simulated service tests cover creation,
retry and failure paths; frontend tests and read-only GitHub authentication
checks passed. A real new-project creation remains an owner-selected pilot;
this tutorial does not claim that pilot has happened.

**Limits:** private personal repositories only; no existing-repository adoption,
automatic PR, merge, force push or collaborator grant. An uncertain creation
response is inspected rather than blindly repeated. See
[project initialization](PROJECT_INIT.md) for exact stages and recovery.

## Connect an existing GitHub project

**Use:** in the trusted VS Code workspace, select **Attach Workspace to GitHub**,
then **Connect Workspace to Feishu**. Use `/new REGISTERED_WORKSPACE` in Control
for a registered workspace, or `/new-session REGISTERED_WORKSPACE` for another
conversation.

**Expected result:** each new bot has its own worktree, permanent code/history
branch, Codex process and visible tmux console. Named tasks stay on that branch.
Existing legacy bots keep their previous layout unless explicitly migrated.
Conversation language follows the owner's current message unless the owner
explicitly chooses another language. Setup does not infer a language from the
target job market or a document's requested language; bilingual deliverables
keep their own language requirements.

**Verification and limits:** branch isolation and workspace binding are tested;
tracked GitHub upload and Feishu text round-trips have passed. Unknown folders
cannot launch agents. See [live setup](LIVE.md),
[conversations](CONVERSATIONS.md) and [terminal attachment](TERMINAL.md).

## Talk to the controller or a project bot

**Use:** ordinary text goes to that conversation's Codex session; `/help` shows
commands. Intern Agent Control supports both natural language and slash commands.
`/list`, `/status` and `/consoles` show the management overview.

**Expected result:** owner text stays unchanged. Reference context arrives
separately through hooks. Control has its own conversation and console.

**Verification and limits:** routing, provenance checks and unchanged-message
delivery are tested. Control requires a paired owner and an explicit controller
binding. See [Control](CONTROLLER.md) and [prompt delivery](PROMPT_DELIVERY.md).

## Ask another bot or send a progress note

**Purpose:** exchange information through a fixed, durable protocol without
interrupting either bot's current work or relying on a human to forward replies.

**Use:** `/mail list`, `/mail ask BOT_HANDLE QUESTION`, `/mail note BOT_HANDLE TEXT`,
`/mail status MESSAGE_ID`, `/mail read MESSAGE_ID`, `/mail ack MESSAGE_ID` and
`/mail process MESSAGE_ID` (one owner-requested read-only note review), plus
`/help mail`. The owner enables the channel with `/mail on`; portable defaults
are off. Agents receive a context-bound CLI and stable-key instructions through
their existing submit hook. VS Code offers **Intern Agent: Bot Communication**.

**Expected result:** B's question waits until B is idle. Actual Codex acknowledgement
creates a received receipt; the completed answer is saved in A's inbox and
announced with the same message ID. A reads while working or gets one idle
follow-up if its original thread/task/request still match. Native Debug stays
manual. Notes enter the inbox without starting a turn. Both sides get validated
Markdown correspondence on their own registered branches.
An explicitly requested note review waits for idle, saves an acknowledgement
and a `note_handled` receipt, and does not start a reply loop. A Feishu note
notice alone is not evidence that Codex processed it.
Historical control-command uncertainty (for example `/fast`) is kept for review
without blocking an idle peer or new Codex input. Actual uncertain agent work
still blocks dispatch. `/mail status` includes a `waiting_reason`; `/doctor`
checks the mailbox worker. One exchange's worker error does not starve others.

**Verification:** queue/receipt tests cover active work, duplicate/restarted sends,
unknown execution, newer turns, thread/task changes, owner priority, read-only
questions, cycles, bounded inboxes, correlated notices and immutable records.
Regression checks also cover control reviews versus genuine agent uncertainty,
legacy workspace mismatches, research/reply/note queues and worker isolation.
Peer answers do not count as implementation-task delivery.

**Limits:** these are same-owner information exchanges. New work assignments,
permissions, publication, PRs and merges retain their owner/team rules. Review
states never trigger blind prompt replay. See the [communication protocol](BOT_COMMUNICATION.md).

## Automatically recover transient Codex failures

**Purpose:** keep a registered bot's existing conversation going after a
confirmed network, capacity or transient server failure.

**Use:** when the owner enables the private recovery policy, the monitor handles
failures automatically. `/recovery status` and `/recovery inbox` show attempts
and Recovery Sentinel incidents; `/stop`, `/pause` or `/recovery off` cancels pending
continuations. VS Code shows the same recovery state and offers controls.

**Expected result:** three separate continuation attempts after 30, 120 and
300-second delays, preserving the thread, workspace and task. After exhaustion,
Recovery Sentinel receives a durable incident when idle and its final reply returns to
the source chat. The original prompt stays unchanged and is never replayed.

**Verification and limits:** tests cover exact dispatch guards, cancellation,
durability and both directions of the mailbox. Healthy running turns, approvals,
permanent errors and uncertain execution/delivery are excluded. Background jobs
are preserved; Git review remains enforced. Debug Bot is reserved for manual
work, with automatic continuations disabled on the personal installation.
The operator has its own Feishu output and reads the [recovery runbook](RECOVERY_OPERATOR.md).
Its own exhausted recovery requires owner review, without a fallback to Debug Bot.
If the operator's own preflight/review blocks dispatch, it and the source chat
receive one explicit blocked-operator notice instead of implying investigation
has started. Resolved diagnostics that never reached Codex leave the queue with
an audit copy and an appended resolution; their original review and baseline
remain. A queue claim alone is not recovery: Codex acceptance or delivery must
be observed. Legacy reviews without a dispatch witness require a manual
canonical-transcript audit; uncertain or executed requests are never replayed.
See [automatic recovery](AUTO_RECOVERY.md).

## Recover a silent or stale working bot

**Purpose:** distinguish Codex execution from a saved reply waiting for Git or
Feishu delivery.

**Use:** send `/status` in the affected bot chat, or `/doctor` in Control.
Observed failures clear the working flag and save a failure notice. Git retries
resume synchronization in queue order without rerunning the prompt. Completed
answers remain saved until Git publication succeeds; receipts prevent duplicates. Progress
must be enabled to receive observation warnings.

Healthy silent work sends no periodic generic working/completion reminders.
Actual public commentary, approval requests, ended failures and final replies
still reach the chat. Use `/status` when you want a current status report.

**Expected result:** failed execution is labelled failed, saved replies show
their preparation/synchronization stage, and a real ongoing terminal turn is
preserved while its Git snapshot waits.

Queued owner messages appear in `/status` before dispatch. Delivered Git reviews
from known ended turns stop holding new messages, even after repeated reviews.
The final reply requires confirmation of the exact uploaded records commit.
Older already-delivered reviews and fresh preflight checks remain in place. Diagnostics and
Recovery Sentinel incidents explain actual blockers. Do not resend a received message.
See [reply delivery and flow diagnostics](REPLY_DELIVERY.md).

**Verification:** regression checks cover failed native and managed turns,
missing retry entries, ordered claims, restart boundaries, unchanged prompts,
typing preservation, and research artifacts through actual Git guards.

**Limits:** unknown execution or API delivery still needs explicit review.
Network outages can delay replies; an observation warning is diagnostic and
does not start, stop or retry Codex. See [recovery details](RECOVERY.md).

## Name work, inspect status and stop a turn

**Use:** `/task start Improve the relay`, `/task status`, `/task pause`,
`/task use TASK_ID`, `/task finish`, and `/chat`. `/stop` interrupts this bot's
running turn and cancels its queued prompts; it keeps the session open.

**Expected result:** idle conversation and named work are both recorded. A task
name persists across messages. Finishing records owner acceptance and requires
verified uploaded delivery; it does not open a PR.

**Verification and limits:** lifecycle, interruption, queue and delivery tests
exist; a real interruption pilot passed. Completed actions are not undone by
stopping. See [conversations and stop](CONVERSATIONS.md).

## Choose a model and see public progress

**Use:** `/model`, `/model MODEL [EFFORT]`, `/reasoning EFFORT`, `/help model`,
`/fast`, `/fast on|off|status`,
`/progress summary`, `/progress messages`, or `/progress off`.

**Expected result:** model settings apply to that bot. Public natural-language
commentary updates one progress message by default; function calls, tool output
and private reasoning are omitted. There is no “Working on your message” reply.
When thinking reactions are available, they remain until actual completion and
delivery or reconciliation.

`/model MODEL EFFORT` saves that conversation's explicit choice and asks Codex
to confirm it for subsequent turns. It is handled while ordinary work is busy;
it neither stops the current turn nor starts another model prompt. `/model`
distinguishes the saved choice from the last observed console setting. An active
turn can still show its previous effort until it ends. Stale observations,
reconnects and Git recording cannot reset the saved choice. `/model default`
explicitly restores the configured default. A native attached Debug terminal
keeps its original terminal-controlled model workflow.

Host-wide defaults come from the private relay configuration's `model` and
`reasoning_effort`. They seed newly created sessions and `/model default`;
existing conversations keep their own choices. They are personal setup values,
not hardcoded account or model choices in the distributable templates.

Saved answers that already reached Feishu retry Git publication without new
chat notices. An unresolved Git incident produces at most one pending-upload
alert per bot, including in `/progress messages` mode. Research commentary and
final replies remain visible. Use `/status` to inspect the saved publication
backlog; notification suppression does not clear a review or certify an upload.
Shared-turn completion preserves any starting Git baseline already captured for
the follow-up. A missing head is recovered from its retained baseline, never
replaced by a different parent head.

**Verification and limits:** progress filtering, ordering and reaction lifetime
are tested, including repeated Git stages, restart and cross-bot isolation.
Models must support the chosen effort. Reactions require the relevant
Feishu permission; availability depends on the app. See
[restored features](RESTORED_FEATURES.md) and [live commands](LIVE.md).
Model regressions cover stale snapshots, busy settings commands, saved-command
adoption, restart, invalid choices, uncertain acknowledgement and exact-thread
backend confirmation. A settings acknowledgement is not an inference/access test.
Fast changes the service tier and preserves model/effort. Managed bots apply
it on the next turn; native Debug Bot changes require an idle empty prompt and
an observed acknowledgement. Explicit `/fast ON` is case-insensitive and confirms
an already observed setting without another API request. If the API acknowledgement
is missed, exact-conversation typed settings can confirm it; otherwise the reply
describes an unconfirmed Fast setting, without a false Codex-execution review.
See [Fast mode](FAST.md) for targeting and retries.

## See output speed and quota after each completed turn

**Use:** send a normal message. The completed Feishu reply includes a line such
as `Quota (account): 5h: 25% used, 75% left | 7d: 10% used, 90% left`.
A speed line appears immediately before it, for example
`Speed (turn avg): 20.0 output tok/s (500 tokens / 25.00 s; includes tools).`
This divides this turn's reported output tokens by Codex turn time. Reasoning
is included in output; input/cache tokens and Git/delivery time are excluded.
Tool waits remain in turn time, so this is not raw model decoding speed.

**Expected result:** each reported account window shows usage and percentage
left. This works for managed bots, Control natural language and attached native
conversations. The quota belongs to the account and is shared across its bots.

**Verification and limits:** managed/native observation and stable delivery
retries are tested; real quota fields have been checked read-only. Unsupported,
stale or expired data is shown as unavailable. No quota footer is added to
progress or management command acknowledgements. Invalid/reset/missing speed
telemetry is labelled unavailable. Counts and timing are scoped to the exact
thread/turn, and retries preserve their frozen payload. See
[output throughput](THROUGHPUT.md) and [quota replies](QUOTA.md).

## Run without execution approval prompts

**Use:** an owner-selected YOLO relay profile uses `approval_policy: "never"`
and `sandbox: "danger-full-access"`. It applies to existing managed
conversations and newly created bots. Standalone Codex CLI defaults and isolated
homes must use the equivalent TOML settings.

**Expected result:** shell/file execution does not produce repeated `/approve`
messages. Owner prompts, task ownership, Git validation and the explicit PR rule
remain unchanged. Missing-information questions still require an answer.

**Verification and limits:** policy and configuration preservation are tested.
Thread changes apply to subsequent turns; active managed-client restrictions
cannot be removed by rewriting user config. See [execution permissions](PERMISSIONS.md)
for setup, transition behavior and the active Debug Bot limitation.

## Use memory, attachments, teams and shared skills

| Feature | How to start | Expected behavior and limits | Detailed tutorial |
| --- | --- | --- | --- |
| Workspace memory | `/memory show`, `/memory add TEXT`, `/memory error TEXT` | Bounded, sanitized reference memory; past notes grant no new permission | [Recovery and memory](RECOVERY.md) |
| File transfer | Upload files, then send one message for all of them; `/files pending` and `/files clear`; request returned files or use `/send-file RELATIVE_PATH` | Any ordinary incoming extension, including Word; private 20 MiB files, safe ZIP checks and durable delivery; no automatic Git import | [File transfer](FILE_TRANSFER.md) |
| Bounded teams | `/team create "NAME" WORKSPACE LEAD_SESSION [1-3]`, then explicit assignment/run commands | Separate workers, acceptance criteria and verification reports; no recursive spawning or automatic merging | [Teams](EDITOR_TEAMS.md) and [task briefs](RESTORED_FEATURES.md) |
| Group triggers | `/trigger all` or `/trigger mentions` | Owner-selected trigger mode; controls remain available | [Group triggers](RESTORED_FEATURES.md) |
| Pinned skills | `/skills list`; explicitly add, inspect and enable a pinned GitHub skill | Per-bot enablement, verified text resources; no dependency installation or extra authority | [Pinned skills](RESTORED_FEATURES.md) |
| Setup diagnostics | `/doctor`; **Intern Agent: Review Setup Repairs** | Concrete checks and reviewed repairs; no silent account or dependency changes | [Setup and repairs](RESTORED_FEATURES.md) |

These interfaces have automated coverage. Account-dependent attachment, reaction
and collaborator behavior requires the relevant live permissions and acceptance.
See each detailed guide for its remaining limits.

## Read history or recover a failed upload

The relay records requests, final results, status, task references and delivery
evidence in validated Markdown under `.intern_workspace/`. This is operational
history; the feature tutorial explains how to use the product.

Git preflight waits for any background history commit/push on this workspace
before comparing local and published heads. An unexecuted request blocked by
an earlier publication race can be reconciled through the normal queue after
its Codex association is verified; existing reviews remain recorded. See
[reply and preflight recovery](REPLY_DELIVERY.md).

Use `/status` before retrying. A saved result awaiting Git upload is not a reason
to rerun Codex. Workspace status now shows its blocked turns and recovery steps.
An observed interrupted turn can contain partial files: `/resolve TURN deliver`
in Control uploads eligible saved work and delivers its notice without rerunning
the request. Send a separate continuation only after reconciliation.
Recovery validates history and restores records without executing old prompts.
See [recovery](RECOVERY.md) for inspected, explicit recovery actions and the
owner-configured execution limit. New setups have no automatic 15-minute cutoff;
`/stop` remains available.

## Develop a feature or fix

Only the explicitly selected Debug Bot and Control conversations receive the
automatic tutorial reminder. Other sessions do not inherit it, even when they
share a workspace. The selection stays in private host configuration, outside
Git and the VSIX. The docs remain readable by everyone.

For those two sessions, follow [the development workflow](DEVELOPMENT_WORKFLOW.md):
inspect, reproduce or define behavior, implement, verify, update this tutorial
and its detailed guide, then record the change and remaining limitations.
