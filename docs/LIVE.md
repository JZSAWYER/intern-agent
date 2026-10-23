# Live Feishu workspace relay

For named tasks, conversation modes, Markdown validation, recovery and priority
`/stop`, see [conversation lifecycle](CONVERSATIONS.md).

The opt-in live relay adds one management group and one group per registered
Codex conversation. This module uses the installed Feishu Python SDK and a
Codex app-server on a private Unix socket. A separate tmux server hosts it;
legacy tmux sessions, enterprise services and migration records are independent.

The implementation supports real transports. A deployment is accepted only
after an authorized Feishu message receives its actual Codex response in the
same workspace group. An offline test, SDK connection or Codex smoke test alone
does not establish that result.

## Requirements and private state

Use Linux, Python 3.10+, tmux, an authenticated Codex CLI, `lark-oapi`, and
`websockets` with `websockets.sync.client.unix_connect`. The implementation was
developed against Codex CLI 0.162.0. App-server is a versioned experimental
interface; validate its generated protocol schema when changing CLI versions.
A local preview VSIX can be built with `tools/build_vsix.py`; its installation
was verified in an isolated VS Code profile. It runs on Linux workspace hosts,
including Remote SSH. Live account and editor activation tests are still needed.

Choose an absolute, private directory on a data volume. Its permissions must be
0700. Credentials and configuration must be 0600. Runtime databases, transcripts,
pairing codes and Codex login material belong here, outside the source repository.
The launcher accepts a configurable executable, model and GitHub owner; no
personal account is a distributable default.

```bash
INTERN_STATE=/path/to/private/intern-agent-state
python3 -m intern_personal.feishu_live --state "$INTERN_STATE" init \
  --github-owner '<github-username>' --model '<available-model>' \
  --codex-executable /path/to/codex
```

Replace every placeholder. The `--auth-home` option selects an existing Codex
login directory. The isolated Codex home initially links its `auth.json` without
reading or displaying token contents; subsequent CLI credential refresh may
persist the login privately there. Enterprise configuration is not imported.
`--proxy` selects the existing loopback proxy at port 17890 for Codex only.
It does not alter the proxy service or apply that route to Feishu.

Securely provision `credentials.json` inside the private directory:

```json
{"app_id":"<new-app-id>","app_secret":"<new-app-secret>"}
```

The file must contain exactly these keys. Transfer over SSH stdin or enter
through a local secret input; never paste the secret into chat, arguments,
Git, an example file, or a log. Placeholder credentials cannot start the relay.

## Feishu setup and owner pairing

The app needs Bot, long connection, `im.message.receive_v1`, and the approved
group/message/chat permissions listed in the existing personal template.
Configure the app's availability/publication for the intended users. Start with
the owner only. Extra people in a group are not automatically allowed to run
machine actions. Direct-message control is not enabled by this group-only pilot.

1. Launch the isolated receiver:

   ```bash
   python3 -m intern_personal.feishu_live --state "$INTERN_STATE" launch
   ```

2. Manually create a management group containing yourself and the app bot.
3. Generate a pairing code:

   ```bash
   python3 -m intern_personal.feishu_live --state "$INTERN_STATE" pair-code
   ```

4. Read the returned private file path in your own terminal, then send
   `/pair CODE` in that group. The code expires in 15 minutes, is hashed in the
   database, and is removed after use. Possession of that explicitly generated
   code binds the authenticated event sender and group; an arbitrary first
   message cannot claim ownership.
5. Check `health.json` for a recent timestamp and `receiver_connected: true`.
   Run `status` to verify pairing. Connection status does not substitute for
   receiving the bot's pairing reply.

SDK logging is disabled because signed WebSocket URLs can contain credentials.
Routine output contains fixed event labels and exception types, never prompts,
credentials or full URLs. Private SQLite state contains task and result text.

## Workspace launch and conversation routing

Register a clean GitHub checkout, then queue its chat creation. Each new bot
publishes one branch containing both code and validated Markdown history:

```bash
python3 -m intern_personal.feishu_live --state "$INTERN_STATE" attach project /path/to/project \
  --repo 'https://github.com/<owner>/<repository>'
python3 -m intern_personal.feishu_live --state "$INTERN_STATE" open project
```

Replace all placeholders. Git 2.34+ is required for staging new files in sparse worktrees.
The repository must already exist and its origin must
match the supplied GitHub URL. Existing branches and dirty checkouts are not
overwritten. The default base is `main`; supply `--base-branch` for another base.
No PR is created, and the base branch is never pushed or merged by the relay.
GitHub authentication is supplied by the host's existing SSH setup.
Deliberate sparse-checkout coverage is inherited by new worktrees; excluded
tracked artifacts remain in Git. Materialize any required excluded datasets
before running project experiments.

The preview extension provides **Attach Workspace to GitHub** with a publication
confirmation, then **Connect Workspace to Feishu**. Automatic connection uses
only previously attached workspaces, including their intern worktrees.

In management, `/new project` reuses the latest conversation; `/new-session
project` creates another. Unknown or merely registered local directories cannot
launch interns. Before a chat or Codex thread starts, the relay creates a separate
Git worktree/code branch and uploads its initial identity and Markdown records.
The chat introduction and `/status` show the repository, directory and branches.

Management lives in `.intern_workspace` on the bot's same permanent branch. It uses
legacy-compatible intern `status.md`/`knowledge.md` and task `README.md`,
`history_log.md` and `task_knowledge.md`. Private chat identifiers, credentials
and queue state remain outside Git. Prompts and results become repository records;
use a private repository when those contents should not be public.

Before execution, task metadata is uploaded. After execution, code changes are
committed to the intern branch and uploaded, and task records are saved before
the final reply. Code containing recognized credentials, private state, linked
files or new blobs larger than 10 MiB is blocked for review. Existing public base
files are not scanned as newly authored work. These guards do not replace a
review of sensitive project contents. Upload failures preserve local results;
`/resolve TASK deliver` retries delivery and publication without rerunning Codex.
Post-delivery metadata failures enter a durable upload retry queue, shown by
management `/status`. No force push or PR is performed.

For a bounded live smoke test in an existing tracked conversation:

```bash
python3 -m intern_personal.feishu_live --state "$INTERN_STATE" agent-smoke --workspace project
```

This queues a normal tracked task and posts its result in that workspace's chat;
it never creates an untracked temporary agent.

New conversations complete a read-only initialization turn so the CLI persists
their history before the connection closes. Ordinary messages resume the saved
thread. Conversation IDs and Feishu chat IDs remain associated across relay
restarts. Each conversation has its own input worker. Registered conversations
with different checkouts and branches can run concurrently, including within
one workspace. Only a shared conversation, checkout or branch serializes input;
another bot's long task cannot occupy this terminal's worker.

Available commands are `/help`, `/help model`, `/model`, `/models`,
`/reasoning`, `/list`, `/status`, `/new NAME`, `/new-session NAME`,
`/pause SESSION`, `/resume SESSION`, `/cancel SESSION` (alias `/stop`),
`/approve CODE`, `/deny CODE`, `/answer CODE TEXT`, and
`/resolve TASK cancel|deliver`.
Workspace groups can control their own session; the management group can control
all registered sessions. Ordinary workspace text is agent input, never a shell
command. The pilot returns final text responses, split into bounded messages.

### Model commands and tutorial

`/help` returns a tutorial directly in Feishu, with setup steps, workspace versus
management syntax, approvals, cancellation and reconciliation. `/help model`
shows focused model guidance. Supported commands are handled by the relay;
unimplemented terminal slash commands get an explicit unsupported-command reply
and are never passed to Codex as ordinary prompts.

In a workspace chat:

```text
/model
/models
/model MODEL
/model MODEL EFFORT
/reasoning
/reasoning EFFORT
/model default
```

Replace MODEL and EFFORT with values in the bot's `/model` reply. The relay
uses Codex app-server `model/list`, including pagination and advertised reasoning
efforts, rather than a hardcoded model list. An unlisted model or unsupported
effort leaves settings unchanged. Catalog membership is not proof of account
entitlement; successful inference confirms access for that request.

Selections persist in private state per conversation. Changing one chat does
not change other chats or the global `live.json`. New chats use the configured
defaults. Changing the model without an effort keeps the current effort for the
same model if supported, or selects the new model's advertised default.
`/model default` restores the configured model and effort. An already running
turn retains its settings; the saved selection is used on the next turn.
Both thread resume and turn start receive the selected model and effort.

In management, `/model` shows the configured defaults and catalog. Use `/list`
to get a target session, then `/model SESSION [MODEL [EFFORT]]` or
`/reasoning SESSION [EFFORT]`. A workspace chat can change only its own
conversation. `/cancel`, `/stop`, `/pause`, and `/resume` without arguments
operate on the current workspace; management requires a session argument.
No extra Feishu permission or publication is needed for these text commands.

For each authorized Feishu message being processed, the bot adds Feishu's
`Typing` keyboard reaction to that original message. It remains through Codex
execution and delivery of every reply chunk, then is removed. Management
commands use the same behavior; locally queued extension commands have no
incoming Feishu message to react to. Queued or paused tasks are not marked as
actively thinking until processing starts.

This feature additionally needs the tenant scope
`im:message.reactions:write_only` (send/delete message reactions), followed by
publication of the permission change. The original seven scopes suffice for
text replies. No reaction-read scope or extra event subscription is required.
See the [official reaction permissions](https://github.com/larksuite/cli/blob/main/skills/lark-im/SKILL.md)
and [Feishu emoji list](https://open.feishu.cn/document/server-docs/im-v1/message-reaction/emojis-introduce).
An unavailable reaction API does not prevent the normal reply; reaction failures
back off for 60 seconds. Cleanup checks run throughout the turn and after
completion. Returning from a worker or reloading the relay does not end the emoji:
an attached terminal still running keeps the same reaction until its completion
is observed and the final reply is delivered. Waiting for approval, Git retries
and unresolved execution/delivery failures also keep it visible; use `/status`
and resolve the blocked turn rather than treating disappearance as success.
Cancellation clears it only after a known active terminal turn has stopped.
Known reaction receipts persist privately and are reused after restart without
rerunning Codex or replacing the emoji. Failed
removals retry after 60 seconds. A process crash between the API's creation of
a reaction and local receipt persistence may require manual reaction cleanup.

## 0.5.0 attachment permission proposal

An authorized read-only check against the published app's message-resource API
returned `99991672`, listing `im:message`, `im:message.history:readonly` or
`im:message:readonly` as alternatives. The code does not grant any of them.
The proposed read permission for the supported attachment pilot is
`im:message:readonly`; the original seven scopes do not authorize this download.
Upload requires a resource permission: the official CLI documents `im:resource`
for file/image creation. Inspect whether the console/API offers the narrower
`im:resource:upload` alternative before requesting the broader combined scope.
Thinking reactions separately need `im:message.reactions:write_only`.

Account approval must identify the exact available scopes, preserve owner-only
visibility, keep external sharing off and publish only the approved scope change.
Then test one small image, one UTF-8 file, one PDF/ZIP input and output, one committed-file export and
reaction removal after completion/cancellation. These account grants, publication
and human attachment pilots have not been completed by local feature tests.
See [the official IM permission table](https://github.com/larksuite/cli/blob/main/skills/lark-im/SKILL.md)
and [the new workflow guide](EDITOR_TEAMS.md).

The 2026-10-14 file-transfer update extends input to PDF/ZIP and adds independent
durable result-file delivery, current-file snapshots and `/help files`. A fresh
read-only granted-scope check confirmed the read/upload scopes above are still
absent. Follow [the file-transfer guide](FILE_TRANSFER.md) for the exact grants
and pilot; this implementation does not change app permissions or publication.

Approval codes are bound to the original session and expire. Approval applies
to one request; the relay never grants persistent rules or blanket permissions.
Single-question input requests support `/answer`. Multi-question requests and
unsupported approval methods require a terminal client or a future richer UI.
Each managed conversation now has its own Codex app-server process, private
Unix socket and Codex home. Its tmux session is visible on the default socket:

```bash
tmux ls
tmux attach -t ia-WORKSPACE-STABLE_SUFFIX
```

Each contains a `Codex` window (interactive TUI attached to that same thread)
and a `host` window (that conversation's app-server). Use `/console` in its
Feishu chat or `/consoles` in management to obtain the exact attach command.
The dashboard also reports the console. Two conversations in the same workspace
still get distinct processes, sockets, homes and tmux session names. Opening a
console never starts an extra model turn or forks its saved thread. Authenticated
sessions share the existing login through a private link, not copied tokens.

The existing Debug Bot retains its original native terminal attachment. The
historical untracked pilot can display its history in a read-only console while
paused; binding it to a GitHub workspace is still required for project work.
Directly typed console conversations are observed and recorded separately from
Feishu dispatch, with completion deduplication. Dirty code from a directly typed
turn without a verified starting Git baseline blocks upload for review, as with
the existing native Debug Bot. It is never silently uploaded. Use tracked Feishu
messages for automated code delivery. Workspace memory for those messages stays
in the separate context channel; console observation does not rewrite prompts.

A dead/unreachable host or mismatched history fails visibly rather than starting
a replacement thread. Inspect only that conversation's `host` window. Closing
a live host stops its conversation; detaching from tmux leaves it running.
The old shared rollout directory is retained during migration, and only the
exact bound rollout is copied to each new home. No unrelated history is imported.

Existing unrelated terminal sessions are not automatically attached or resumed.
They require an explicit future adoption flow. The management registry covers
sessions created through this relay.

## Recovery and operation

One file lease owns the receiver/worker set. App and message IDs deduplicate
incoming tasks. A restart changes interrupted execution/delivery to uncertain
states. Those states block later work in the affected workspace; management
status and reconciliation remain available. Reconnecting the SDK does not rerun
accepted tasks. A disconnected known Codex turn is interrupted best-effort.

Review uncertain tasks before choosing `/resolve TASK cancel` or
`/resolve TASK deliver`. Deliver only resends a saved response; it does not run
Codex again. Message chunks use deterministic API UUIDs, but API idempotency is
not an unlimited exactly-once guarantee. Unfinished group creation is not
automatically retried; inspect the created group and local session record first.

`launch` creates `ia-personal-relay` on the private tmux socket. Each conversation
uses its own `ia-WORKSPACE-STABLE_SUFFIX` session on the default socket. A separate
`ia-personal-models` catalog host, if needed, runs no conversation turns. This keeps them alive after SSH disconnect;
boot-time startup is not installed. To stop the receiver, send SIGTERM to the
PID in a fresh `health.json`. It interrupts known active turns. Do not kill an
unrelated tmux server or service. The isolated Codex host can remain available
for terminal access and future relay restarts.

Run `agent-smoke` to test real Codex without Feishu messages or repository actions.
The test's actual rollout remains private for diagnosis; its temporary registry
entry is removed. Run `python3 -m unittest discover -s tests -q` for offline tests.

## Pilot acceptance

- Published/available app, fresh receiver connection, successful owner pairing.
- `/new pilot` creates a group containing the owner and bot.
- Text in that group reaches its saved Codex thread and gets a real reply.
- Another group's message reaches its own conversation.
- Reopening reuses the group; a new session has a separate history.
- Approval/cancellation controls work, duplicates are ignored, and restart
  recovery does not silently repeat a task.

Reference: [Codex app-server protocol](https://learn.chatgpt.com/docs/app-server),
[Feishu group automation](https://open.feishu.cn/solutions/detail/groups?lang=zh-CN).
