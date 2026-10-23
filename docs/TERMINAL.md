# Existing terminal attachment

An attached terminal chat uses the exact running tmux pane and Codex thread.
It never launches another Codex, resumes that thread in app-server, or imports
its old transcript. The ordinary app-server sessions continue independently.
Use a private GitHub repository when tracking a personal debug conversation.

Provision a clean project on a new code branch, publish that branch, and register
the GitHub repository with the normal workspace setup. New attachments keep
code and management records on this one branch. From tools
inside the existing Codex tmux conversation, run:

```bash
python3 -m intern_personal.feishu_live --state "$INTERN_STATE" attach-terminal project \
  --code-branch '<new-code-branch>' --display-name 'Debug Bot'
```

The registry binds project work to that repository directory. An existing
terminal's process working directory stays as originally launched; injected
requests state the bound project directory. Files elsewhere on the machine
are not uploaded. Git objects and private state can live on a data volume.

## Turn completion and Git checks

A durable watcher reads the current CLI's `task_started`, visible user message,
and `task_complete`/`turn_aborted` records. Only turns from the attachment
boundary onward produce Markdown task records and Feishu final replies. Tool
output, reasoning, old conversation history, identities and credentials are
excluded from Git records. Requests/results undergo credential redaction.
Native terminal completions enter the existing durable publication and reply
pipeline. The official final reply and thinking-reaction removal wait for
confirmed code/history upload and reply delivery. Interim answers to accepted
live follow-ups can arrive earlier as progress; requested file returns have a
separate outbox. Task acceptance still requires verified uploaded work.
Feishu-injected turns have a private deduplication marker and produce one
acknowledged final response. A restart never re-injects an ambiguous request.
See [Git completion](GIT_COMPLETION.md).

Explicitly installed pre-commit and pre-push hooks validate Python syntax,
JSON parsing, the legacy Markdown status marker/table and workspace manifest.
They reject private files, credential literals, linked files, large artifacts,
unregistered destinations and default-branch pushes. New attachments validate
code and records together; existing attachments retain their code/management
branch boundaries. Pre-push scans new history, including removed secrets.
The relay verifies the reviewed hook scripts and validator hash before Git
operations. Arbitrary repository hooks remain disabled. Editing a hook
definition requires explicitly reviewing/reinstalling it before upload.
No PR is created without explicit user instruction.

This watcher supplies the completion integration for an already running TUI.
It does not silently alter user-level Codex hooks or bypass their trust review.
Codex's [native hooks documentation](https://learn.chatgpt.com/docs/hooks)
requires review/trust for non-managed hooks and describes transcript paths as
unstable. The transcript adapter is tested against CLI 0.162.0's actual records;
unknown sequences, replacement/truncation and malformed records fail closed.
Revalidate it when updating Codex.

## Controls and operating limits

Send ordinary text in the attached group. A new request waits for idle; an owner
follow-up can steer the exact active turn without stopping its work. Use
`/queue TEXT` when a separate later request is intended. File-bearing messages
retain normal batch preparation and wait for the current turn to finish.
The bridge pins pane PID, native Codex PID/start time, process ancestry, thread
and rollout inode. It pastes literal text through stdin, then presses Enter,
only at an empty pinned input prompt. Active-turn steering also requires exact
transcript acceptance; uncertain input is retained for review. Typed drafts/menus are rejected. Do not
type into the pane at the instant a Feishu request is being dispatched; tmux
and a human keyboard do not share an atomic input lock. End the current turn
before starting more terminal work so its files can be committed consistently.

`/help`, `/status`, `/pause`, `/resume`, `/stop` and reconciliation use the relay.
`/model` reports observed terminal settings and `/models` lists the catalog.
Change model/reasoning and handle terminal-native approvals in the original
pane. The attachment explicitly rejects relay model mutations rather than
pretending they changed the terminal. App-server sessions retain their full
existing model/approval commands. Thinking reactions share the normal handler
and require the separate Feishu reaction permission to be published.

Liveness of the SDK/attachment is not a human incoming round-trip test. After
attaching, send a harmless message from Feishu and verify its final reply in
both that group and the original terminal, plus its permanent code/history branch records.
