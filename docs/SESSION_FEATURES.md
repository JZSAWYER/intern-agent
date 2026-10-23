# Features shared across conversations

Use this guide to distinguish a feature installed in the shared relay from a
per-conversation preference, a management action or a live acceptance result.
The same backend serves project bots, Control, Recovery Sentinel and an attached
native Debug Bot. New registered bots inherit that backend; an unrelated Codex
terminal does not gain a Feishu chat or workspace binding automatically.

## Shared behavior

| Feature | Existing and future registered bots | Scope and remaining checks |
| --- | --- | --- |
| Files followed by a message | Incoming ordinary files, including PDF, Word, Markdown, TXT and images, wait for the same sender's next message; one ordered batch, up to 20 MiB per file | All registered bots share a durable inbox per chat/thread/sender. Commands do not consume uploads. `/files pending` and `/files clear`; busy turns continue, then receive the whole batch. Existing outgoing workspace snapshots and app grants remain. See [files](FILE_TRANSFER.md). |
| Natural language and slash commands | Project and Control conversations accept both | Owner text stays unchanged; references arrive separately. Management actions remain scoped to Control or an explicit authorized target. See [prompt delivery](PROMPT_DELIVERY.md). |
| Live owner follow-ups | Text can steer an active managed or attached native turn | Public answers are delivered when written, even while monitoring continues; authoritative completion still governs Git/task acceptance. Exact turn/pane checks and durable acceptance; `/queue TEXT` deliberately waits. Files keep normal attachment dispatch. Peer mail and timers cannot steer. See [follow-ups](CONVERSATIONS.md#live-follow-ups). |
| `/help`, `/model`, `/reasoning`, `/fast` | Shared command implementations | Model, effort and Fast selections belong to the conversation. An active turn keeps its current settings. See [commands](LIVE.md) and [Fast](FAST.md). |
| Immediate `/stop` | Interrupts the targeted conversation and cancels its pending input | Does not stop another bot or undo completed actions. See [conversations](CONVERSATIONS.md). |
| Public progress and thinking reactions | Shared managed/native observation and delivery | Public prose only; no tool dumps or private reasoning. The official final reply and reaction removal wait for confirmed code/history upload and delivery. Interim follow-up answers are progress. No generic working acknowledgement or healthy-work timer reminder. See [Git completion](GIT_COMPLETION.md). |
| Quota footer | Completed managed, Control and native replies | Reports observed account usage; unavailable data is labelled unavailable. Old delivered replies are not rewritten. See [quota](QUOTA.md). |
| Output speed | Completed managed, Control and native replies, including new bots | Exact-turn reported output delta / Codex turn time, before quota. Includes reasoning and tool waits; excludes relay queue/Git/delivery. Missing/reset data stays unavailable. See [throughput](THROUGHPUT.md). |
| Workspace, tasks and Git history | Registered workspace, named task state and validated Markdown records | GitHub only; no automatic PR. New bots keep code and records on one permanent branch. Existing legacy layouts are preserved. See [history](CONVERSATIONS.md). |
| Separate consoles and input | Each conversation has its own Codex thread and tmux console | Independent registered checkouts dispatch independently; later input to the same busy terminal waits. Uncertain execution is never blindly replayed. See [routing](CONVERSATIONS.md#codex-consoles). |
| Network/capacity recovery | Shared monitor when the owner enables the recovery policy | Same-thread continuation has bounded retries; exhausted incidents go to Recovery Sentinel. Manual Debug is excluded on the personal installation. See [recovery](AUTO_RECOVERY.md). |
| Bot communication | Shared durable ask/note/reply protocol when enabled | Owner work has priority; busy bots are not interrupted. Control-command reviews do not masquerade as Codex work; genuine execution uncertainty stays protected. Per-exchange isolation, waiting reasons and worker health cover all registered bots. Native Debug uses its inbox without automatic turns. See [mail](BOT_COMMUNICATION.md). |
| Persistent research wake-ups | Available to every ready tracked bot; requires an explicitly registered owner-authorized research watch | Same thread/task/branch, delayed first check, finite check/horizon budgets, unchanged owner messages and normal tracked delivery. No ordinary conversation, peer message or recovery incident creates a research loop. `/stop` cancels alarms. See [research](AUTORESEARCH.md). |
| Memory, task briefs, teams and pinned skills | Available through the shared backend | Memory grants no authority. Teams need explicit assignments; pinned skills need selection and enablement. They are not automatically activated for every bot. See [teams](EDITOR_TEAMS.md) and [restored features](RESTORED_FEATURES.md). |
| New private GitHub projects | `/init` preview/apply in Control and the editor | Creation needs authorization for the named plan. This does not automatically create a repository for every terminal. See [initialization](PROJECT_INIT.md). |

## Preferences and intentional exceptions

Tutorial/development reminders select only the exact Debug Bot and Control
conversations in private host policy. Other bots can read the guides, but do
not inherit this reminder. Automatic recovery incidents belong to Recovery
Sentinel; they must not take over manual Debug work.

An owner-selected YOLO profile supplies managed conversations and future local
CLI defaults. A currently running native client can have additional host-managed
restrictions that relay configuration cannot remove. See [permissions](PERMISSIONS.md).
Do not flatten model, Fast, progress, trigger, skill or team preferences when
updating shared features.

Both native and managed console observers and the Codex RPC connection accept
bounded records up to 32 MiB, allowing the documented 20 MiB attachment size
plus JSON/base64 encoding. A large tool result does not become public progress
or hide the final response behind it. Final replies retain their separate 1 MiB limit. A record
above the observation bound still needs review; do not truncate the transcript,
reset the conversation or replay the request to clear it.

Paused historical conversations stay paused. A historical console without a
tracked Git workspace is not eligible for file export or normal new work until
the owner registers a workspace. Older bots using separate management branches
are compatible; moving their records is a separate migration, not a prerequisite
for file transfer.

## Hooks, upgrades and verification

All registered bots also share [bounded history storage](RECORD_STORAGE.md):
compact lossless recovery snapshots and verified private backups before collecting
untracked obsolete fragments. Each bot keeps its existing branch, Markdown history
and recovery data; source/inactive checkouts require explicit maintenance.

The relay builds current application context for each new submission. File
commands use a private context pinned to that bot's exact thread and route to
its existing Feishu chat. Native conversations use the corresponding
`UserPromptSubmit` hook. Codex must trust that exact native hook before its
automatic context injection can be claimed; declaring a hook or writing a
context file alone is insufficient. See [official hook trust documentation](https://learn.chatgpt.com/docs/hooks).

On a normal relay start, ready conversations' hook references refresh from the
installed immutable release before workers or event reception begin. This
submits no Codex prompt and preserves same-thread attachment references and
bot-specific settings. An older/replaced thread's file references cannot cross
into the new one. Paused and unbound conversations are skipped. A malformed
context stays intact for inspection; `/doctor` reports it under `feature_context`
without disabling other bots. Updating a file does not steer an already running
managed turn; its next submission receives current application context.

Runtime snapshots include the public Markdown guides. Sentinel's hook names an
absolute runbook path within that release, so recovery does not depend on a
project containing a possibly older copy of the relay docs.

For an installation audit, check the running receiver's source, published app
permissions, each ready conversation's context/console/branch binding, and the
installed Git guard hashes. Preserve paused sessions, active work and settings.
Check `/files permissions` for account access and `/files` for actual delivery.
Do not send an artifact to every chat merely to prove that they share an app.

Cross-role regression checks exercise incoming private PDFs, unchanged prompts,
outgoing files delivered to the correct chat, rejected foreign-thread contexts,
and feature inheritance for a newly created bot. Other automated modules cover
commands, recovery, progress, quota, scheduling, history and packaging. These
tests do not establish every feature's live acceptance in every conversation.

A live installation audit must separately verify distinct thread/chat bindings,
independent consoles, current hook references, registered GitHub origins/branches
and installed guard hashes. Check published app grants and actual outgoing
receipts, and fetch/download an explicitly requested test file to compare its
bytes. Verify speed/footer ordering in the saved delivery payload. Keep account
inventories and message identities in private evidence rather than source docs.
Preserve each conversation's effort/Fast choices and legacy branch layout;
new conversations still use configured defaults and one bot branch. Historical
reviews and uncertain execution remain evidence to inspect, not prompts to
replay merely for an audit.

An outgoing PDF receipt and matching downloaded bytes do not establish incoming
PDF/ZIP ingestion into a real Codex turn; test that separately in the selected
installation. Neither automated coverage nor another operator's live results
establishes permissions or delivery for a fresh account. The VSIX remains a
preview; backend feature inheritance does not install or upgrade the extension
in another editor. See [distribution readiness](DISTRIBUTION.md).
