# Fast and Ultrafast from Feishu

Use `/fast` in a bot's Feishu group to control Codex's Fast service tier without
changing its model, reasoning effort, workspace, branch or conversation.
Use `/ultrafast` for the distinct Ultrafast tier. Both preferences belong to the
selected conversation; adding the command does not enable a paid tier globally.

## Commands

```text
/fast
/fast on
/fast off
/fast status
/ultrafast
/ultrafast on
/ultrafast off
/ultrafast status
```

`/fast` toggles the selected bot. `on` and `off` set an explicit preference;
repeat them when you want an idempotent command. `status` reads the last observed
Codex tier and any saved preference for the next turn. It changes no setting.
Commands are case-insensitive: `/fast ON` is the same as `/fast on`.
`/ultrafast` toggles Ultrafast; explicit `on` selects Ultrafast and `off` selects
Standard. `/fast on` selects Fast instead. These are three separate speed states:
Standard, Fast and Ultrafast. Turning either command off selects Standard, even
if the other speed was selected. Status distinguishes observed Codex settings
from a saved next-turn preference; an unobserved setting is labelled unobserved.

In Intern Agent Control these commands default to Control's own conversation.
To manage another bot, copy its session identifier from `/list`:

```text
/fast SESSION on
/fast SESSION off
/fast SESSION status
/ultrafast SESSION on
/ultrafast SESSION off
/ultrafast SESSION status
```

Only the owner may change speed. A workspace viewer/operator may inspect
`/fast status` and `/ultrafast status`; this does not grant access to Control or
other workspaces. A bot created later inherits command support, with its own
preference and account eligibility checks.

## Expected result

Managed app-server bots update future-turn settings through Codex's dedicated
`thread/settings/update` API and verify its exact-thread settings notification.
The bridge also sends the saved `serviceTier` on turn start. A resume response
alone does not verify an effective tier change on an already loaded thread.
The running turn is unchanged. Other bots keep their settings.
Direct console service-tier changes are observed through typed Codex settings.
If exact-conversation recorded settings already match an explicit `on` or `off`,
the relay confirms that state without requesting another change or looking up
the model catalogue. A saved preference alone is not proof of the current setting.

An attached native Debug Bot executes the real `/fast` toggle in its pinned
terminal. The relay checks the current tier, verifies the exact process/pane,
requires an idle empty prompt for a change, and waits for Codex's typed settings
acknowledgement. An already selected preference is verified without sending
keys. Busy turns and typed drafts are preserved; wait for completion and retry.

Fast support and its protocol ID come from the current model catalogue. In the
installed Codex catalogue, `priority` is advertised as **Fast**. Unsupported
models are rejected. Ultrafast requires an advertised `ultrafast` tier; the
legacy additional-speed list is supported as well. The relay does not guess an
entitlement, substitute Fast, lower reasoning effort or switch the model.
When a speed is selected, changing to a model without that tier requires
selecting a supported speed first. The preference is retained across turns,
receiver restarts and portable Git recovery. The legacy portable settings field
is still called `fast`; its tier value can now be `ultrafast`.

The attached native CLI 0.162.0 has a verified `/fast` toggle adapter but no
verified arbitrary-tier input adapter. For that attachment, select Ultrafast in
the original Codex model/speed picker when available, then inspect
`/ultrafast status` in Feishu. An already selected tier can be confirmed without
keys; changing to or from Ultrafast through that native adapter gives a precise
limitation and sends no invented slash command. Managed project bots and Control
use the settings API directly. Busy native turns and typed drafts stay intact.

## Speed and usage

The model catalogue supplies speed descriptions. Model processing speed is not
a guarantee that Git, tools, networking or the whole task will finish at that
rate. Fast and Ultrafast use more account allowance or credits. The official
guidance checked on 2026-11-16 lists Ultrafast for GPT-6 Astra and GPT-6.1 Sol on
Pro $500 and eligible Enterprise/Edu plans. It lists included usage at 8x Standard
and purchased-credit/pay-as-you-go usage at 6x Standard for Ultrafast. Account,
workspace and model access still determine actual availability. See
[official OpenAI speed guidance](https://learn.chatgpt.com/docs/agent-configuration/speed)
for current availability and usage rules.

## Retry and verification

Accepted toggle commands have durable receipts with their intended target.
Retrying the same event does not compute another toggle. Before a native toggle
is sent, its dispatch is persisted. If an acknowledgement is lost, the relay
checks observed settings and never blindly sends a second toggle. An unresolved
native command blocks another change until its outcome can be reconciled by
settings evidence or owner inspection. `/fast status` remains available.

For managed bots, both settings RPCs have five-second response limits. If the
API acknowledgement is unavailable, the relay checks typed settings from the
pinned conversation before reporting success. Matching evidence confirms the
setting without replaying the command. Unknown or mismatched evidence produces
a speed-specific “not confirmed” result; it does not claim that an inference
prompt was submitted or that the current Codex task is stuck.
Fast's `priority`/`fast` aliases may match each other. Ultrafast must match
Ultrafast; it can never satisfy a Standard or Fast acknowledgement. Speed
commands save their records for background Git publication, so a subsequent
command does not wait for a synchronous history push. Normal agent final replies
retain their Git completion gate.

An older control-command review can be reconciled from this evidence while its
original event and warning remain in history. A verified Fast receipt no longer
counts as unresolved execution in diagnostics or conversation lifecycle checks.
Queued commands, delivery/Git reviews, unverified commands and genuinely
uncertain Codex turns retain their protections.

Automated checks cover routing, owner boundaries, model/effort preservation,
restart persistence, unchanged prompt bytes, actual bridge fields, native busy
and draft protection, acknowledged changes and ambiguous-dispatch recovery.
Regression checks also cover uppercase commands, repeated explicit settings,
lost acknowledgements, unavailable catalogues, foreign-thread observations and
preserved historical reviews.
The installed CLI schema and real account catalogue were inspected. A disposable
Git-bound pilot returned its expected reply and Codex acknowledged its Fast tier.
The real settings API was checked on idle Control and its original Standard tier
restored. These checks verify the setting, not a measured 2x performance increase.
Live acceptance is recorded separately in the change record.

Ultrafast regression checks cover Control/explicit-target/new-bot routing,
owner boundaries, catalogue rejection, exact settings acknowledgements, retry
idempotency, lost acknowledgements, Standard/Fast/Ultrafast transitions,
unchanged owner prompts, next-turn fields, portable preferences, model changes,
native observation and preserved busy turns. The maintained development record
dated 2026-11-16 reports a later catalogue refresh advertising Ultrafast for its
selected model, plus managed/native status replies independently read back from
Feishu without changing speed preferences. These are upstream installation
observations, not live checks performed by the public release operator or proof
for a new account. An Ultrafast inference and a measured speed increase remain
separate live acceptance work; catalogue availability and adapter tests do not
establish them.

If a newly documented command returns “Unsupported command”, check the running
relay version. Uploading source or publishing a VSIX alone does not activate a
new receiver. Deployment must reconcile any already-uploaded completion-hook
commit against the exact tested source, wait for a safe receiver boundary, and
verify a new command reply after activation. A preliminary busy check must not
mistake an attached native follow-up for managed work; the final Codex, delivery
and process-preservation checks still govern receiver replacement. Do not replay
the original unsupported command or clear its history to test the new handler.
