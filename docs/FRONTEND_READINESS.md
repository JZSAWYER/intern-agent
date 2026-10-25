# Frontend readiness and acceptance backlog

Refreshed 2026-10-25 for the owner-authorized MIT Intern Agent 0.2.3 candidate.
Its installable identity is `intern-agent.intern-agent`, with 39 commands in the
`internAgentPublic` namespace. The maintained 0.8.0 preview is separate. Editable
frontend source is `extension/extension.js`, using Node/VS Code builtins without
npm runtime dependencies. No compiled enterprise reference snapshot is included.

“Implemented” describes inspected source. Offline harnesses and isolated editor
sideloads have narrower scope than real activation. Final-candidate checks are in
progress; this document does not claim public upload or completed live acceptance.
P0 identifies installation and operational acceptance work; P1 improves daily
collaboration; P2 is later interface polish.

| Area | Current implementation and gap | Priority | Acceptance criteria |
| --- | --- | --- | --- |
| Setup and authentication | Trusted Linux workspace dialogs, masked GitHub PAT input and account discovery, Feishu credentials and owner pairing; no visual setup checklist. | P0 | Cancel safely at every stage; explain partial setup and missing Python/Git/tmux/Codex/SDK/app/auth/pairing. No secret in settings, logs or argv. Diagnose short private state/socket paths. |
| Shared SSH host | Host-profile discovery can adopt the existing state and Python configuration; public settings use machine scope. | P0 | A second SSH client/editor reuses one host profile and receiver. Ambiguous profiles require selection; no silent duplicate installation, relay or pairing. |
| Recovery | Repository-name preview/apply/status and Reconnect Restored Conversation actions exist; the recovery skill explains state loss boundaries. | P0 | Review pinned repository/branches before apply; preserve task/memory/model preferences and stable record IDs. Repeat safely, reject collisions, never replay old prompts/jobs. Verify real-account reconnection separately. |
| Dashboard | Explorer tree, visible polling and task/branch/model/delivery/Git/recovery/research details; release status is exposed through a command. Fault and stale states need clearer UI; root upload counts are global. | P0 | Actionable empty/loading/stale/error states and independent per-workspace activity, reply and Git state. Refresh creates no chats or agents and disposes polling. |
| Diagnostics | Doctor and reviewed repair/rollback actions exist, with JSON output. | P0 | Identify wrong permissions, missing tools, account mismatch, network and app publication failures without secret output. Review/cancel/rollback local repair; no silent account changes. |
| Remote SSH and activation | Workspace extension kind, Linux backend guard, machine-scoped settings and remote URI preservation exist. | P0 | Activate on a real Linux SSH host, preserve browser handoff, reconnect and multi-root targeting, and reuse the selected relay. Reject untrusted/virtual/unsupported local hosts with instructions. |
| Upgrade | Content-addressed backend snapshots and preference protections exist; stable/public identity is separate from preview. | P0 | Identify installed VSIX and active receiver versions independently. Preserve identities, queues, threads, skills and worktrees; test idle upgrade, interruption and rollback without duplicate reception or replay. |
| Uninstall | Extension removal and private runtime lifecycle are separate; no managed cleanup UI. | P0 | Extension removal preserves projects, private state and user skills. Explain reviewed service stop/cleanup separately; verify reinstall reuses the chosen state in an isolated pilot. |
| Project initialization | Reviewed preview/apply/status/open-workspace flow and durable stages exist. | P0 | Cancel creates no repository/chat/thread; apply only the reviewed plan, safely repeat confirmed stages and inspect uncertain remote creation. Verify the resulting permanent branch on the selected remote host. |
| Codex skills | Bundled-skill installation and pinned reference-skill management actions exist. Standalone release installer validates the complete inert bundle. | P0 | Explicit destination, ownership/link/conflict checks and per-file receipts; preserve user modifications, referenced resources and notices. Skill installation neither executes instructions nor starts a receiver. |
| Goals and questions | Manage Persistent Goal, Show Pending Codex Questions and dashboard status; durable owner cards with text fallback. | P0 | Preserve task/branch scope, pause/native publication gate and exact question correlation; publish callback configuration per account and verify real card answers separately. |
| Models and portable settings | Dashboard model and Feishu model/effort/Fast controls; per-session preferences and roles are recoverable. No editor model picker. | P1 | Discover supported options, preserve exact session/role targeting, show pending changes and acknowledgement, and keep busy/native draft restrictions visible. |
| Tasks and teams | Task/team visibility and command dialogs use structured backend briefs and permanent branches. | P1 | Add clearer assignment, criteria, blocker, handoff and acceptance forms. Accept verified uploaded work; workers require explicit assignment/run and no automatic merge. |
| Terminal and follow-ups | Dedicated consoles, native attachment and active-turn follow-ups exist; no direct exact-console editor action. | P1 | Open the registered remote console without duplicate agent/thread creation. Show busy/draft/unreachable reasons and distinguish interim answers/file returns from Git-confirmed final completion. |
| Navigation and localization | English Explorer tree and command-based management. | P2 | Improve setup discovery, skill catalog navigation and localization without implicit installs or execution. |

## Package evidence and remaining pilot

Stable packaging keeps manifest and JavaScript namespaces consistent, records
frontend overlays and uses fixed archive metadata and complete content-type
mapping. Final tests must validate the 39-command artifact rather than inherit
results from an earlier preparation VSIX. Installation into isolated extension,
user-data and server-data directories is useful package evidence; it does not
start an extension host or receiver.

No running-receiver parity, real account/message round-trip, Remote SSH activation,
upgrade/rollback or uninstall/reinstall acceptance is claimed. Use an explicitly
selected pilot for those cases after offline checks. Keep installed editor metadata
handling distinct from functional manifest/script integrity. See
[the audit](OPEN_SOURCE_AUDIT.md) and [baseline details](DEPLOYMENT_PARITY.md).

The [older interface comparison](FRONTEND_COMPARISON.md) describes a historical
preview-versus-enterprise inspection. It is neither the current command inventory
nor permission to copy enterprise implementation. The next UI improvements remain
a setup checklist, actionable dashboard errors, model picker, structured task forms
and an exact-console terminal action.
