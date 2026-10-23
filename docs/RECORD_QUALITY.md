# Reusable workspace records

**Purpose:** make future work understandable and reproducible without filling
`.intern_workspace` or project directories with duplicate summaries and status
chatter. This policy applies to existing and future registered bots.

**Prerequisites:** a registered GitHub workspace and the shared relay. Native
sessions need their trusted exact-thread submit hook to receive application
context; managed sessions receive it separately from the owner's unchanged text.

## What belongs in durable knowledge

Save a finding when it helps someone make a decision, reproduce a result or
avoid a demonstrated mistake. Useful records identify:

- The question or objective, relevant task and source evidence.
- The decision, method or measured result, including useful negative results.
- Verification performed and its limits; proposed ideas remain hypotheses.
- An unresolved issue or next step when one remains.

Update the relevant guide, experiment result or existing note. Do not create a
new report just because another turn ended. Brief rules such as “GitHub only”
are useful; a long document is not automatically better. Temporary scripts,
raw tool output, repeated progress notices, retry counters and placeholder
summaries belong in private runtime diagnostics when needed for debugging.
Do not remove evidence from an experiment merely because its result was bad.

## Use and expected result

The relay owns generated `.intern_workspace` documents; agents do not hand-edit
them. It preserves each request, final response, task and necessary lifecycle
evidence in the original turn/history records. Conversation and task knowledge
indexes link directly to the latest 100 source work responses, with outcome,
Git status and a short request label. Older source documents remain in history.
Known sensitive values are sanitized before shortening index labels or context.
Indexes no longer copy recent responses into additional knowledge
files. Control commands remain in source history rather than the work index.

Read a linked response and its evidence before reusing a claim. “Completed” and
“synced” describe execution/delivery; they do not certify scientific correctness
or passing tests. Failed and stopped work remains available for analysis.

Use `/memory add TEXT`, `/memory rule TEXT`, `/memory error TEXT`, or select a
named task and use `/task note TEXT` for explicit owner knowledge. Repeating the
same sanitized note in the same workspace, kind and task adds no new note or
revision. Different kinds, tasks, measurements and internal whitespace remain
distinct. Existing duplicates remain in the recovery archive, but appear once
in knowledge views.

Recent-turn hook memory includes completed agent reports with source paths,
labelled as historical reports. Commands, failed/interrupted executions and
empty results do not displace them. A selected named task uses reports for that
task; idle conversation uses that conversation's unassigned reports. Other
tasks remain findable in their own history. Useful negative experimental
results in normally completed agent reports are retained without text-based
"success" heuristics.

## Verification and limits

Regression checks cover original request/response preservation, Git restoration,
failed and command history, useful negative findings, task isolation, short
constraints, duplicate retry idempotency, legacy duplicate preservation and
existing/new session hooks, including redaction before truncation. Unchanged projection writes already avoid replacing
identical files. No historical turn is deleted or rewritten to enforce quality.
Storage cleanup remains the separate [backup-first policy](RECORD_STORAGE.md).

The shared hook guides agent-authored artifacts; it cannot mechanically prove
that every paragraph is useful or every reported result is true. Markdown
validation checks structure, links identify provenance, and owner review or
task-specific verification establishes correctness. Full source history remains
available to investigate claims instead of relying on an automatic summary.
