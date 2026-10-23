# Fast mode from Feishu

Use `/fast` in a bot's Feishu group to control Codex's Fast service tier without
changing its model, reasoning effort, workspace, branch or conversation.

## Commands

```text
/fast
/fast on
/fast off
/fast status
```

`/fast` toggles the selected bot. `on` and `off` set an explicit preference;
repeat them when you want an idempotent command. `status` reads the last observed
Codex tier and any saved preference for the next turn. It changes no setting.
Commands are case-insensitive: `/fast ON` is the same as `/fast on`.

In Intern Agent Control these commands default to Control's own conversation.
To manage another bot, copy its session identifier from `/list`:

```text
/fast SESSION on
/fast SESSION off
/fast SESSION status
```

Only the owner may change Fast mode. A workspace viewer/operator may inspect
`/fast status`; this does not grant access to Control or other workspaces.

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
models are rejected. If Fast is selected, changing to a model without that tier
requires disabling Fast first. Turning it off selects standard/default processing.

## Speed and usage

The current installed catalogue describes GPT-6.1 Sol and GPT-6 Astra Fast as
“2x speed, increased usage”; other models advertise different increases. This
is model processing speed, not a guarantee that Git, tools, networking or the
whole task will finish twice as quickly. Fast uses more account allowance or
credits. See [official OpenAI speed guidance](https://learn.chatgpt.com/docs/agent-configuration/speed)
for availability and current usage rules.

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
a Fast-specific “not confirmed” result; it does not claim that an inference
prompt was submitted or that the current Codex task is stuck.

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
