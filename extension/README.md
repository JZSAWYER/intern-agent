# Intern Agent — Linux workspace extension

Connect Linux workspace folders to dedicated Feishu chats using one application
bot and persistent Codex conversations. Install the released VSIX on your Linux
workspace host. Windows and macOS VS Code clients can connect through Remote SSH;
the backend and agent run on Linux.

The supported installer profile is **Linux x86_64 with Python 3.12**. It uses a
separate, hash-locked Python environment; see the bundled `docs/INSTALL.md`.
Python 3.10+ compatibility of some offline source commands does not make it a
supported live installation profile. Git, tmux and an authenticated Codex CLI
must also be available on the host. The extension does not silently install
dependencies or configure your Feishu account.

Run **Intern Agent: Check Setup** before setup. It checks the installation,
Python, host tools and bundled skill resources without creating relay state,
starting a service or contacting accounts. Review each warning or failure in the
Intern Agent output. With an existing real relay configuration, it requires the
installed runtime dependencies to pass before showing operational diagnostics.
These checks do not prove account login or a real Feishu message round-trip.

Use the Command Palette:

1. **Intern Agent: Set Up Personal Relay** — choose a private state directory on
   a data volume. A host profile reuses existing server sessions. Fresh PAT
   setup detects the GitHub account; advanced setup accepts explicit host choices.
2. **Intern Agent: Set Feishu Credentials** — store your new app's credentials
   privately on the Linux host, or use a protected SSH handoff.
3. **Intern Agent: Start Relay** — launch the isolated tmux receiver.
4. **Intern Agent: Create Owner Pairing Code** — copy its one-time command into
   a Feishu management group containing you and the bot.
5. **Intern Agent: Show Status** — verify pairing and enable automatic workspace
   connection when prompted.
6. **Intern Agent: Attach Workspace to GitHub** — register a clean checkout
   after confirming the destination. Each new bot publishes one permanent branch.
7. **Intern Agent: Connect Workspace to Feishu** — open the attached project.
   The management group posts its workspace-chat link.

To recover an uploaded project instead of creating one, choose **Recover Workspace
from GitHub**, supply its repository name/URL, and review/apply the pinned plan.
After missing account setup and pairing, **Reconnect Restored Conversation**
selects the exact saved bot/branch, including the saved Control conversation.
Use **Set GitHub Authentication**, **Show Git Recovery Status** and **Install
Bundled Codex Skills** for the related setup. Restored history is not an execution
queue. See the bundled `docs/PORTABLE_RECOVERY.md` and `recover-intern-agent` skill.

For skills, choose an absolute directory that the running Codex actually scans
on this host; current Codex uses `~/.agents/skills`, while older installations
may use another configured location. The installer preserves modified user
instructions and checks the copied instructions, references and license. Reload
Codex and confirm the skill appears, then explicitly invoke it, such as
`$install-on-linux`. Ask it to follow the skill and report its checks. File
verification, session discovery and execution are separate steps; installation
alone does not show that a skill was executed.

For a project whose repository does not exist, use **Intern Agent: Initialize
New Private Project** or `/init WORKSPACE [REPOSITORY]` in Feishu Control.
Review the private repository, data-volume storage and creation steps before
applying the plan. The dashboard shows progress and blockers; on completion,
**Open Initialized Bot Workspace** opens its permanent working branch in VS Code.
Its initial `main` remains an empty baseline. See the bundled project-init guide.
The bundled `docs/FEATURE_GUIDE.md` is the tutorial entry for all features;
`docs/CHANGELOG.md` records fixes and additions. The development reminder is
enabled only for explicitly selected Debug Bot and Control sessions.
In Feishu, `/fast` toggles Codex Fast mode; `/fast on|off|status` sets or inspects
it, and `/fast SESSION ...` in Control targets another bot. See `docs/FAST.md`.

The app must be published/available with Bot, long connection, the
`im.message.receive_v1` event and the message/chat permissions documented in
the bundled backend docs. Credentials, transcripts and personal account names
are never bundled. Your account is an onboarding input, not a default.

Automatic connection activates only after opt-in and in trusted workspaces.
Reopening a bot worktree reuses that exact bot; an ordinary source folder selects
its registered workspace conversation. **New Workspace Conversation**
creates a separate chat and Codex history. Independent registered checkouts
dispatch independently; shared checkouts or the same busy terminal remain exclusive. Each new bot keeps code and validated Markdown history on one branch;
named tasks do not create branches. Existing bots retain their former tracking
layout until explicitly migrated. Existing default branches are preserved.
No force push or PR is performed automatically. Unknown folders cannot start agents.
Non-GitHub remotes are rejected. Credentials and private runtime stay outside Git.

Use `/list`, `/new NAME`, `/new-session NAME`, `/status`, `/pause SESSION`,
`/resume SESSION`, `/cancel SESSION`, `/approve CODE`, `/deny CODE` and
`/resolve TASK cancel|deliver` in the management group. Send ordinary text in a
workspace group to talk to its Codex session. One editable public-prose progress
message accompanies final text. **Manage Persistent Goal** and **Show Pending
Codex Questions** expose tracked goal continuation and owner question cards.
Questions accept choices and custom text; card clicks need the published
`card.action.trigger` long-connection callback. `/answer` is the text fallback.
See [goals and questions](../docs/GOALS_AND_QUESTIONS.md). Use **Set Group Trigger**, **Set
Progress Mode**, **Manage Pinned Skills** and **Review Setup Repairs** for the
restored controls. Task and team commands also support structured briefs and
attributed verification reports; see the bundled `docs/RESTORED_FEATURES.md`.

This stable extension uses `intern-agent.intern-agent` and `internAgentPublic`
commands/settings, distinct from the development preview. Install the matching
isolated runtime using bundled `docs/INSTALL.md` before setup. A stable package
is built with `--release` after clean-export checks. Updating the VSIX does not
switch an active receiver; private state is preserved. Marketplace publication
is separate.
**Show Release Synchronization** displays the configured operator's release jobs.

See [the editor, message and team workflow guide](../docs/EDITOR_TEAMS.md) for dashboard diagnostics, session controls, bounded teams, workspace access and paged recovery.

The editor includes **Bot Communication** and mailbox counts; use the bundled
BOT_COMMUNICATION and SESSION_FEATURES guides for account policy and coverage.
