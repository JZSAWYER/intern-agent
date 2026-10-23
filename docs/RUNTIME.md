# Offline text runtime

This page describes the unchanged offline core. The separate opt-in live
implementation and its account/operation requirements are in [LIVE.md](LIVE.md).

The current runtime is a testable core, not a network service. `Ingress` consumes
the Feishu message event shape as a Python mapping. The future authenticated
SDK adapter must pass events to it; there is no public unauthenticated HTTP
endpoint. `Worker` receives injected agent/reply implementations. The only
included runnable demonstration uses `SimulatedAgent` and `SimulatedReplies`.

## Authorization and input

Each policy names one app, one owner open ID, and explicit group-to-workspace
bindings. Unknown apps, users, chats, unregistered workspaces and bot-originated
messages do not enqueue tasks. Only group text messages are supported.
Malformed, oversized, stale and excessively future-dated events are rejected.
The event sender's open ID comes from the authenticated event, never message
text. An empty owner/binding is an error; first-message ownership is not used.

The worker checks the current owner and chat binding again before processing a
queued task. Revoking those bindings cancels pending execution/delivery.
Feishu scopes control API access; these bindings separately control machine
access. `templates/bindings.example.json` must be filled explicitly and never
copied from another app's user identifiers.

## Durable state

The SQLite queue lives under the selected personal project's `runtime/queue`.
The directory is 0700 and database 0600. It stores task/result content as local
private state and is excluded from packages and Git. There is currently no
automatic retention cleanup; design retention before live deployment.

States:

```text
queued -> running -> result_ready -> sending -> complete
             |                         |
     uncertain_execution       uncertain_delivery
```

An application/message ID pair is unique, including completed events. A
single-worker file lease prevents concurrent workers and makes crash recovery
safe. Work is FIFO within each workspace. An uncertain task blocks later tasks
for that workspace; other workspaces can continue.

After acquiring the lease, recovery changes abandoned `running` and `sending`
states to their uncertain counterparts. It never assumes that an external
action failed merely because the local process died. A persisted `result_ready`
can be delivered without rerunning the agent. Replies carry a deterministic
idempotency key for a future transport, but this alone does not prove exactly-once
delivery. Operator reconciliation is required before live use.

## Session isolation

`Sessions.prepare` creates a specification, not a process. It resolves only
registered GitHub workspaces belonging to the configured owner. Work directories,
agent HOME, and the dedicated tmux socket are inside the selected personal
project. Session names start with `ia-personal-`. Legacy TMUX, provider settings,
credentials and shell initialization are not inherited. Symlink escapes and
overlong socket paths are rejected.

The real adapter must use these paths, explicit argument arrays and agent input
channels. It must not evaluate messages as shell commands, reconnect to the
legacy tmux server, scrape unrelated panes, source `.bashrc`, or resume historical
sessions. Backend process startup, structured output parsing, cancellation and
approval behavior remain to be implemented and tested with the chosen agent.

## Live work still required

- SDK event decoding/acknowledgment and reconnect adapter, bounded worker
  scheduling and shutdown. Do not acknowledge durable acceptance if queue
  persistence failed; invalid/unauthorized events can be acknowledged and ignored.
- Real agent process and response adapters, message-size splitting/rate limits,
  cancellation, approval interactions, error classification and redacted logging.
- Explicit reconciliation UI/CLI for uncertain execution/delivery; never blindly
  change these states back to queued.
- Safe owner binding during onboarding, publication and dedicated test group.
- Credential acquisition/storage, authenticated live tests, startup persistence
  and a reviewed deployment/rollback procedure.

None of those actions is performed by `simulate`.
