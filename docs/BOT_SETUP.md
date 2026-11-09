# Choose your first bot and optional management roles

Start with **one installation, one paired management group and one project
conversation**. You do not need to reproduce the original operator's Control,
Debug and recovery conversations. These are roles within an installation, not
separate software products or a required fleet of Feishu applications.

## What “bot” means here

| Component | Purpose | Needed to start? |
| --- | --- | --- |
| Feishu app bot | The app identity that receives messages and sends replies in your groups | Yes: configure your own app once |
| Relay receiver | Routes messages and coordinates your installation's private state | Yes: one receiver for that installation/app |
| Paired management group | Owner-only administrative entry point for relay commands | Yes for the supported live onboarding flow |
| Project bot/conversation | One coding conversation with its own Codex thread, worktree, permanent branch and project chat | Start with one |
| Controller conversation | Adds ordinary-language Codex assistance inside the existing management group | Optional |
| Manual Debug conversation | A separate place for a maintainer's deliberate troubleshooting or product development | Optional |
| Recovery operator | A configured conversation that investigates escalated incidents | Optional; configure deliberately later |
| Doctor | Diagnostic commands, not an AI conversation or another bot | Available without creating any of the optional roles |

One Feishu app bot can appear in the management group and several project groups.
Creating another project conversation does not mean creating another Feishu app.
Likewise, a new message or named task normally continues the existing conversation;
it does not allocate a new bot or switch its permanent branch.

```text
Your Feishu app bot + one relay installation
    |
    +-- Paired management group: /help, /list, /status, /new, /init
    |       +-- Optional controller Codex conversation for ordinary language
    |
    +-- Project conversation: your coding requests and this project's progress
    +-- Additional project conversations when you actually need them
    +-- Optional manual Debug / configured recovery-operator conversations
```

## First installation with an existing project

1. Follow [installation](INSTALL.md): install the supported runtime and VSIX,
   configure your own GitHub/Codex/Feishu accounts and select private state.
   Reuse an existing host profile when connecting another editor to the same host.
2. Start one receiver. Create a Feishu group containing yourself and your app bot,
   then use **Intern Agent: Create Owner Pairing Code** and send the displayed
   one-time `/pair CODE` command in that group. This is your management group;
   its name does not have to be “Control”. See [live pairing](LIVE.md#feishu-setup-and-owner-pairing).
3. Open a clean checkout of an existing GitHub repository in a trusted VS Code
   workspace. Use **Intern Agent: Attach Workspace to GitHub**, then
   **Intern Agent: Connect Workspace to Feishu**. Repository access must be yours.
4. Follow the resulting project-chat link and send a small coding request there.
   Inspect its worktree/branch in the dashboard and verify a reply plus the
   intended Git publication. Use a private project repository for sensitive records.
5. Keep using that project chat for its work. Use the management group's slash
   commands or the dashboard when you need to inspect or manage conversations.

For a repository that does not exist yet, use **Initialize New Private Project**
or `/init` in the paired management group. Review and explicitly apply its plan;
see [project initialization](PROJECT_INIT.md). This path does not require a
controller Codex conversation. It does not automatically create a Debug or
recovery operator, invite collaborators, create a PR or merge anything.

## Management group versus optional controller

The paired management group already accepts relay commands before any controller
is attached. For example:

```text
/help
/list
/status
/doctor
/new WORKSPACE
```

Replace `WORKSPACE` with an attached workspace name. `/new WORKSPACE` reuses its
existing conversation when available. `/new-session WORKSPACE`, or **New Workspace
Conversation** in VS Code, requests a separate one. Use it when independent
context, worktree and branch ownership are useful, rather than for each new task.

Without a controller binding, ordinary prose in the management group is not a
coding conversation. Send coding instructions in the project chat instead. If
you want natural-language management, attach the paired group to a trusted,
tracked workspace using the configured runtime's Python on the Linux host:

```text
python3 -m intern_personal.feishu_live --state STATE attach-controller WORKSPACE
```

Replace `STATE` and `WORKSPACE`; keep the state private. This creates the
controller's own Codex thread/worktree/branch in the existing management group,
not another app or management group. The controller uses model capacity like
other conversations. Ordinary text there now reaches that conversation; explicit
relay commands still use their administrative path. See [Control](CONTROLLER.md)
for targeting another session and permission boundaries.

## Doctor and Debug are different

Use **Intern Agent: Check Setup** before or after setup. Before account setup it
runs installation diagnostics; after configuration it also checks the selected
installation. The standalone installation doctor is:

```text
python3 tools/doctor_install.py
```

Run it from the product source or installed backend; see [installation checks](INSTALL.md).
`/doctor` in the paired management group reports runtime diagnostics. `/status`
in an affected project chat separates its execution, Git and delivery states.
These commands do not allocate a Debug bot or a recovery operator.
A missing controller can appear as a nonblocking informational check; it does
not mean that project conversations require a controller to work.

A manual Debug conversation is useful if you maintain Intern Agent itself or
want a separate place for deliberate troubleshooting. It is not required for
normal project work. The original operator's manual Debug terminal is not a
portable dependency, and a fresh installation must not try to adopt it.
Calling a conversation “Debug” does not configure its automation policy.
Choose its policy explicitly; use the [terminal guide](TERMINAL.md) if attaching
an existing native terminal rather than creating a normal project conversation.

## Three different meanings of recovery

| Situation | Where to start | What it does not mean |
| --- | --- | --- |
| Model work ended, but code/history publication is blocked | The affected project `/status`, [Git completion](GIT_COMPLETION.md) and [reply delivery](REPLY_DELIVERY.md) | Resubmit the coding request or restart an experiment |
| A confirmed Codex turn failed transiently | Inspect `/recovery status`; optionally enable the bounded policy in [automatic recovery](AUTO_RECOVERY.md) | Retry a healthy/unknown turn, ignore an approval, or fix account permissions automatically |
| You moved machines or lost installation state | [Portable recovery](PORTABLE_RECOVERY.md), including preview, apply and explicit reconnection | Start here for every fresh project, restore lost RAM, or replay historical commands |

For failed Git publication, retain the saved answer and exact starting evidence.
Inspect the actual Git error, ownership and current index. Git-only retry is
different from executing the original request again. If delivery or execution
is uncertain, reconcile it before retrying. Do not clear a review, disable hooks,
stage someone else's work or create another bot merely to make a queue move.

An enabled automatic-recovery policy schedules bounded continuations only for
eligible confirmed failures. Portable installations start with that policy off.
A dedicated incident operator is optional: register a suitable separate
conversation and explicitly configure its actual `incident_session` if wanted.
Calling a chat “Recovery Sentinel” does not register it. Without a configured
operator, unresolved incidents require owner review; there is no automatic
fallback to a manual Debug conversation. See the [operator runbook](RECOVERY_OPERATOR.md)
before taking that role. The runbook itself grants no new operational permission.

## Grow only when the workflow needs it

Start with your management group and one project conversation. Add a controller
when natural-language administration helps, another project bot when you need an
independent working context, and a recovery operator when you deliberately want
incident handling. Keep Debug manual if you add it. Teams, peer mail, research
watches and release synchronization are separate features to configure later,
not prerequisites for the first coding request.

Another editor on the same Linux account should reuse its host profile rather
than start another receiver. A collaborator on another machine uses their own
accounts and private state; GitHub collaboration permissions do not copy your
live chats, credentials or terminals. For contributing to Intern Agent's source,
follow [contributor onboarding](CONTRIBUTING.md) on separate human branches.
