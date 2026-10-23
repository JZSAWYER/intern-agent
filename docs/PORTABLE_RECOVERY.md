# Portable recovery and shared SSH clients

The recoverable unit is a GitHub project and its bot branches, not a particular
Windows installation. Keep the installable product separate from private agent
records. A release contains the relay, VSIX, instructions and skills; each bot's
project branch contains its code and validated `.intern_workspace` checkpoint.

## What is recovered

| Situation | Result | Setup still required |
|---|---|---|
| Another computer connects to the same Linux account/server | Reuse the existing private state, chats, exact Codex sessions, tasks and progress | Install the VSIX on the Remote SSH host and select/reuse its host profile |
| Relay restart with its state intact | Existing durable queues, routes and observation survive | Start one receiver for that state; normal uncertainty checks still apply |
| Server/state loss with Git available | Restore the last published code, requests/results, notes, named tasks, acceptance, team records, feature references and saved conversation preferences | Codex login and Feishu credentials/pairing when unavailable; reconnect desired bots |
| Data never uploaded to Git | Not reconstructed by this feature | Recover it from its own artifact store or filesystem backup |

A GitHub PAT provides repository access. It cannot recreate a Codex login,
Feishu App Secret, RAM, GPU processes, unpushed edits or a lost private Codex
transcript. Recovery restores project continuity. A cold recovery creates a new
Codex context using the saved task/memory; it does not pretend an old process
survived. Datasets, large training checkpoints, external services and ignored
artifacts need their own backups. Record their versions, checksums, locations
and resume instructions with the task's reusable knowledge.

## Reuse this server from another client

**Purpose:** see the same progress from another Windows/SSH editor.

The setup flow writes one small private host pointer under
`$XDG_STATE_HOME/intern-agent/host.json`, or the standard user state directory.
It contains only the chosen state directory and Python executable. It contains
no token. A fresh VSIX client on the same Linux account discovers this pointer
and reuses the existing state. The large data remains on the selected data
volume; the VSIX installation directory is not a state directory.

Use **Intern Agent: Set Up Personal Relay**. Existing host discovery precedes
new setup. When opening an existing bot worktree, the editor reconnects that
exact registered bot rather than selecting the last bot in its workspace.
The receiver's existing exclusive lock prevents a second client from starting
another receiver for the same state. An editor disconnect does not own or end
its server's Codex jobs.

This shares one Linux account's installation. Different Linux accounts do not
automatically gain access to another user's private files or GitHub repositories.
Collaborators use their granted repository/workspace permissions.

## Restore a project by name

**Prerequisites:** a supported Linux/Remote SSH host, Git, Python, the relay
runtime dependencies and an installed Codex executable. Codex and Feishu
accounts must be configured before live reconnection. Fresh PAT setup detects
the GitHub account and uses the release's model/effort defaults. Advanced setup
can choose another executable/model or existing SSH authentication.

1. Install the released VSIX on the Linux workspace host. Run setup and choose
   a private state directory on a data volume. If a host profile exists, reuse
   it instead of creating another installation.
2. For fresh setup, select **GitHub PAT**. Input is masked and passes through
   stdin, never argv, Git URLs, logs or packages. It is verified and saved in
   an owner-private `0600` file. **Set GitHub Authentication** can update it
   explicitly later. A PAT must have access to each requested repository;
   account login alone does not certify repository read/write permissions.
3. Run **Recover Workspace from GitHub** and supply `REPOSITORY`,
   `OWNER/REPOSITORY` or a credential-free GitHub URL. Existing host workspaces
   are reused. Otherwise preview clones an independent checkout, discovers
   recovery archives, selects the newest published workspace revision and pins
   all recorded code/task branches. Review the repository and record counts.
4. Apply the saved plan. Historical events become archived records, not queued
   prompts. Conversations remain restored until explicitly connected. Repeated
   application is idempotent, including after a restored bot advances.
5. Repeat for the other desired projects. Non-conflicting archives can share
   one installation. Published turn/revision identities are preserved. A collision
   from a different installation requires a separate state profile; import
   never renumbers or overwrites history to force a merge.
6. Supply missing Feishu credentials, publish/configure the app if necessary,
   start one receiver and pair the owner management group. Before moving app
   reception to another host, establish that the old receiver will not continue
   receiving the same app's events. Recovery does not stop unrelated jobs.
7. Use **Reconnect Restored Conversation**. It chooses the exact saved bot and
   branch. The saved controller can bind to the new paired management group;
   it does not need another control group. Saved model, effort, Fast, progress,
   trigger and automatic-recovery preferences follow the conversation. A
   recovered native/manual Debug inbox remains manual; its old tmux pane is
   not guessed. Reattach a native terminal explicitly if desired.
8. Verify a new message round-trip and Git publication. Install the bundled
   Codex recovery/file-transfer skills through **Install Bundled Codex Skills**
   if wanted. Existing modified skills are preserved for review.

Equivalent backend commands, from the installed backend or a source checkout:

```text
python3 -m intern_personal.host_profile
python3 -m intern_personal.feishu_live --state STATE recover-repository preview OWNER/REPOSITORY
python3 -m intern_personal.feishu_live --state STATE recover-repository apply PLAN_ID
python3 -m intern_personal.feishu_live --state STATE recover-repository status
python3 -m intern_personal.feishu_live --state STATE install-skills ABSOLUTE_SKILLS_DIRECTORY
```

Use the configured Python executable. Replace uppercase placeholders; no personal
account is a distributable default. A PAT handoff uses the masked editor command
or the `github-auth`/`bootstrap` CLI's JSON stdin, never a token-valued flag.
In the paired owner Control group:

```text
/reconnect SESSION
/reconnect SESSION controller
```

`/new WORKSPACE` chooses a conversation; it does not reconnect every bot in a
multi-bot workspace. Existing `/restore`-style low-level Git import remains the
`restore PATH --repo URL --branch BRANCH [--apply]` backend command. That older
single-branch command still defaults to a fresh ledger.

## Restore ongoing workflows deliberately

Research definitions/history remain recoverable as feature records. On a cold
installation, `/research status` exposes archived watches as `restored_paused`.
After reconnecting and inspecting the actual experiment/evidence, use
`/research restore WATCH_ID 10h` to create a new bounded monitor from the saved
goal, evidence, interval and remaining check count. It schedules checks on the
new host and does not restart a training run. Old watch history remains intact.
Completed/cancelled/exhausted watches are not automatically rearmed.

Peer notes, prior requests and replies remain readable in Git. Executable old
mail, approvals, file transfers and unconfirmed prompts are not replayed.
Reauthorize/reissue an intended new action against current evidence. Pinned skill
catalogues preserve their references; their local caches must be fetched and
verified again. The recovery operator role can reconnect its future escalation
route without replacing an already configured operator.

## Verification and limits

Regression tests delete an isolated original state and checkout, retain only a
real Git bare remote, clone by repository name, restore two bots, preserve task
selection/preferences/history, then reconnect the exact bot through fake Feishu
and Codex adapters. Other tests cover multiple repositories, repeated plans,
changed remote branches, identity conflicts, private PAT/helper boundaries,
shared host discovery, manual roles, controller routing and user-owned skills.
This is an offline transport test, not a fresh real Feishu-account acceptance.

Old archives remain valid but cannot contain preferences/roles never exported
by their writer version. New checkpoints include the added portable settings.
A changed remote between preview and apply requires a fresh plan. Corrupt
archives, missing required branches, oversized records and history collisions
fail without erasing existing work. The current archive size and single-file
limits remain; see [Git recovery](RECOVERY.md) and [storage](RECORD_STORAGE.md).

Recovery restores the last successfully published checkpoint. The normal turn
pipeline still waits for confirmed code/history Git upload before final delivery.
The portable archive deliberately excludes account credentials, old Feishu
identities, machine paths, process/thread bindings and executable queues.

Preference/lifecycle changes receive a monotonic workspace journal revision and
matching per-conversation `preferences.md` records. Recovery verifies that the
Markdown and archive agree. Discovery therefore distinguishes settings-only
updates across branches without filesystem timestamps; unchanged projections do
not add another preference revision. Paused bots reconnect paused, and archived
bots remain archived unless the owner explicitly selects them for reconnection.
A newer bot's archive can also restore an older, unused sibling branch.

Structured machine binding fields are excluded. Historical request/result text
can still mention old paths; preserve that history and use the new registered
workspace when locating project files. It is not an instruction to recreate old
machine directories or replay an old command.
