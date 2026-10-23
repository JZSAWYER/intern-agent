# Owner messages and lifecycle hooks

The relay forwards the owner's text as the Codex user message. It does not add
task IDs, a workspace preamble, previous outcomes, or a rewritten request.
Feishu's leading bot-mention token is routing syntax and is removed. Text
whitespace and punctuation are otherwise preserved at the relay boundary;
slash commands are parsed separately. Attachment-only messages still need an
explicit default request because they contain no owner-written text.

The receiver lifecycle performs tracking outside the prompt:

1. Receive and deduplicate the original message in durable private state.
2. Bind the workspace and bot's permanent branch; record the turn as running.
   Existing legacy bots retain their selected task-branch behavior.
3. Publish bounded, sanitized reference context separately from the user text.
4. Submit the unchanged message and observe Codex completion or interruption.
5. Save the outcome, validate Markdown/code and upload the registered branches.
6. Deliver the Feishu reply. Failed Git uploads retry saved records, not Codex.

App-server sessions use Codex's `turn/start.additionalContext` with explicit
experimental API negotiation. Workspace binding is application context; memory
and attachment references are untrusted data. This was verified against Codex
0.162.0 with a real resumed thread and an unchanged persisted user message.
Unsupported servers fail visibly; the relay never falls back to modifying text.

## Scoped feature tutorial reminder

A private, exact-session allowlist enables the
[feature/debugging workflow](DEVELOPMENT_WORKFLOW.md) only for the selected
Debug Bot and Control conversations. The existing submit hook carries a short
development checklist in application context, separately from owner text.
Other sessions do not receive it, including bots in the same workspace; newly
created bots and controllers are not enabled automatically. The hook asks the
selected agents to update the [tutorial](FEATURE_GUIDE.md) and
[change record](CHANGELOG.md) after feature/fix verification. It neither copies
user text into a new prompt nor treats past history as new authorization.

## Existing terminal sessions

Terminal delivery uses a durable transcript byte boundary and prompt digest to
associate completion with its incoming event. Identical repeated requests remain
separate turns. Ambiguous dispatch is not replayed. Previously dispatched tagged
messages remain readable during upgrades; past transcripts are not rewritten.
Codex's TUI trims outer whitespace in its own history. Acknowledgement accepts
that verified normalization (including CRLF line endings) without modifying
the relay's original message, pasted bytes or Markdown request record.

The submit hook publishes a private file under
`STATE/runtime/prompt-context/SESSION.json`. An optional native hook command is:

```text
PYTHON -m intern_personal.prompt_context PRIVATE_CONTEXT_FILE
```

Configure this command for `UserPromptSubmit` in a trusted Codex hook source,
using an immutable backend snapshot or installed backend as its module path.
It returns `hookSpecificOutput.additionalContext` only for the exact bound
Codex thread. It does not edit the incoming prompt, run Git, grant permissions,
or start services. The relay observer continues recording completion, including
messages typed directly in the attached terminal.

Native hook definitions require review through Codex `/hooks`. **The existing
Debug Bot terminal did not have this hook configured.** Its raw-message delivery
and external recording hooks can be activated without restarting that terminal;
automatic native memory injection must be enabled and verified separately.
The relay does not change global Codex configuration or bypass hook trust.

## Meaning of pending

An unfinished feature or acceptance check is different from a queued message.
Use `/status` for actual event states and Git synchronization. A `sync_pending`
turn has a saved result waiting for upload; `awaiting_terminal` is an observed
in-flight terminal turn; uncertain execution needs reconciliation and is never
automatically rerun. Named tasks pause only through an explicit task command.

Remaining release checks include actual editor/collaborator pilots, account
permissions for attachments/thinking reactions, and an owner-selected license.
These do not pause working text conversations. Account grants, publication and
collaborator access are separate changes; this prompt fix does not perform them.
