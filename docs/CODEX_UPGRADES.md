# Rolling Codex upgrades

## Keep Feishu connected while updating Codex

**Purpose:** update registered Codex backends and their visible tmux consoles
without restarting the shared Feishu receiver or replacing conversations.

**Prerequisites:** an owner-authorized host upgrade, a complete compatible CLI
package, existing authentication, private rollback storage and exact registered
thread/process bindings. This is an operator procedure, not a Feishu `/update`
command or an automatic global model change.

**Use:** stage and verify the package, then replace one idle conversation at a
time. Keep the receiver running. Its normal queue retains messages during each
brief worker reconnection. An active inference must finish naturally first;
do not use an in-console updater that exits an active agent.

1. Inventory only registered hosts, their console views and the shared model
   catalogue backend. Record exact threads, process start times, sockets,
   workspaces, branches, selected model, reasoning effort, speed and paused
   state. Leave unrelated Codex processes and research services alone.
2. Stage the complete CLI bundle on the data volume, keeping the previous
   version. Verify the executable and required resources, generated protocol
   schema and a separate model-catalogue probe. An npm `latest` tag alone is
   insufficient when its platform package is missing. Retain a receipt that
   distinguishes downloaded-package integrity from reuse of an existing bundle.
3. Atomically update the configured launcher for future starts. Preserve its
   existing proxy setup, authentication source and arguments. Do not relocate
   secrets into source, packages, command lines or logs.
4. Take a consistent private database reference backup. Reserve the selected
   conversation through a real local maintenance control event so ordinary
   dispatch waits while incoming messages remain durable. Verify the actual
   backend is idle, no turn/delivery is in progress and the console has no draft.
5. Stop only the pinned idle Codex view and backend. Back up that conversation's
   isolated home while its writers are stopped; preserve authentication links.
   Recreate the backend in its original tmux window and private socket.
6. Resume the exact saved thread, without forking, starting a new thread or
   sending a test prompt into its history. Restore the registered workspace,
   execution policy and model settings, then reopen the original console view.
7. Preserve speed on both sides of a remote console connection. Restoring Fast
   only on the server is insufficient: the reopening TUI can apply its Standard
   default. Carry the saved tier into the client launch as well. CLI
   `service_tier = "fast"` maps to the request tier `priority`; Ultrafast remains
   distinct. Never infer an owner preference from a new default. Require an
   exact-thread acknowledgement if a setting needs reconciliation.
8. A known startup **Resume paused goal?** dialog must use **Leave paused**.
   Confirm that exact option before pressing Enter. Do not resume a goal just
   to open its console, and do not dismiss unknown dialogs or overwrite drafts.
9. Verify the actual backend and view executable versions, exact conversation
   and branch bindings, saved preferences, observer pins and healthy unchanged
   receiver. Read the advertised models from the upgraded backend. Release the
   maintenance event through normal records, Git upload and reply delivery.
10. On failure, retain the incident and maintenance receipts. Restore only the
    stopped conversation's cold backup and previous executable; validate its
    history prefix and observer cursor before reconnecting. Never restore the
    whole old relay database over newer messages, clear a review to retry or
    replay an executed prompt.

An attached native terminal needs an additional boundary: its own upgrade
must wait until the current reply and any accepted follow-ups have completed,
uploaded and been delivered. A separately reviewed, bounded maintenance watcher may then
replace its pinned processes, atomically update its attachment process pins and
verify the same thread and transcript. The watcher must respect owner stop/pause
and configuration changes. It does not create another Codex task or inference.
Count that terminal as upgraded only after its final acceptance receipt exists.

**Expected result:** Feishu stays connected throughout. Each console briefly
reconnects to its existing conversation at an idle boundary. Pending messages,
research jobs, history, workspace branches and intentional paused goals survive.
New CLI versions and newly advertised models are separate from the selected
model: use `/models` to inspect, then explicitly select a model if desired.
Managed chats support `/model MODEL EFFORT`; native attachments retain their
original terminal model picker.

**Verification:** the maintained development record dated 2026-11-18 reports a
Codex 0.169.1 pilot across registered managed backend/console pairs and the
shared catalogue. It reports preserving registered bindings, preferences,
paused states, historical reply bytes and receiver identity, and independently
reading back model-list replies containing `gpt-6.2-sol` and `gpt-6.2-astra`.
The record also reports remote-client Fast resets reconciled with exact-thread
acknowledgements. Private account inventories and deployment receipts are
excluded here. These are attributed upstream observations; this public release
operator did not perform a live host upgrade or independently repeat them.
Attached native completion still needs its own after-reply receipt.

**Limits:** this is a bounded, checkpointed operator rollout, not a shipped
one-click upgrade controller. It does not promise compatibility with every later
CLI, uninterrupted active inference, new-model inference access, quota or speed.
Catalogue visibility is not an inference test. Keep rollback material private
until acceptance and retention review; never bundle it into the public VSIX.
The dated upstream record says the pilot used a complete 0.169.1 bundle because
the 0.170.0 npm tag lacked its Linux platform payload at that time. Recheck the
actual package before a future upgrade. That record reports no model-default
change or receiver restart. This guide ships no upgrade watcher or controller.

See the official [CLI update behavior](https://learn.chatgpt.com/docs/developer-commands),
[configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)
and [speed settings](https://learn.chatgpt.com/docs/agent-configuration/speed).
Application behavior and rollback should be revalidated for the actual version.
