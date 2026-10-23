# Feature and debugging workflow

This process applies to Intern Agent source development by the owner,
collaborators and GitHub fork contributors. Read [contributing](CONTRIBUTING.md)
for separate branches and PRs. The optional relay reminder described below is
limited to selected bot conversations; it does not define human branch policy.
This guide grants no new account, publication or merge permissions.
Ordinary conversation and read-only questions do not require a change entry.

## Follow the evidence

1. Read the repository's `AGENTS.md` if present, the relevant implementation,
   existing tests, and its current feature guide. Preserve user-owned rules.
2. Identify the requested behavior. For a bug, reproduce the symptom and record
   the expected and actual result. For a feature, define a concrete usage example
   and acceptance criteria. Investigate before choosing a fix.
3. Make the smallest coherent change on your own source branch. The owner
   and collaborator have separate branches; public contributors can use forks.
   If working through a registered relay bot, keep that bot on its permanent
   branch and preserve runtime isolation. Do not migrate bots or switch their
   worktrees as a side effect of preparing a public contribution.
4. Run relevant verification. Add a regression test when it protects behavior
   that could fail again; documentation-only edits need link/format checks.
   Describe what the checks actually establish. Do not label mocked or offline
   tests as a live pilot. For a shared relay feature, verify existing session
   roles and a newly created bot, including correct chat/thread isolation.
   Check the actual running release and stale private context files, not just
   the source checkout. Preserve active turns, attachments and each bot's
   preferences. Keep intentional role exceptions such as the two-session
   tutorial policy and manual Debug behavior; see [session coverage](SESSION_FEATURES.md).
   When adding an application-context fragment, register its key in the shared
   producer/observer context registry. Exercise the actual published fragments
   as serialized transcript references through native and managed observation,
   exact request acknowledgement and saved reply delivery. A direct mocked
   Codex completion alone does not verify that full path.
5. Update [the feature tutorial](FEATURE_GUIDE.md) and reuse or extend the
   relevant detailed guide. Record the feature/fix in [the change record](CHANGELOG.md)
   in the same change. Keep the stable tutorial readable; put chronology in the
   change record and conversation history in the relay's Markdown records.
6. Review the diff, formatting and private-data exclusions. For public source
   development, prepare a PR from your branch to the confirmed integration base;
   push/open it only within the requested scope. For registered bot operations,
   let its guards and relay deliver operational records separately. Never publish
   private operational refs as PR heads. Report checks and remaining limits.
   Saved-result retries must not rerun the original execution.
7. For the owner's configured canonical development source, verify the durable
   Open Source release handoff after a feature is uploaded. A records-only
   commit is not a new product version. The separate release operator packages
   source, stable VSIX and skills, then verifies public URLs and asset hashes.
   Keep development/operational history private; do not claim a release from
   a queued/completed bot task alone. See [published releases](PUBLISHED_RELEASE.md).
   This does not authorize PRs, merges or copying private state into that product.

## Documentation format

Use this structure when adding or substantially changing a tutorial section:

```markdown
## Feature name

**Purpose:** what problem this solves.

**Prerequisites:** workspace, account or configuration needed.

**Use:** exact commands or UI steps, with explicit placeholders.

**Expected result:** what the reader should observe.

**Verification:** checks actually run and their outcomes.

**Limits:** remaining gaps, unsupported behavior and safe recovery.
```

For a fix, the change record also states the symptom, cause, resulting behavior
and regression evidence. Correct outdated tutorial instructions when behavior
changes; do not merely append contradictory instructions.

Examples must use placeholders, not personal GitHub accounts, chat/session IDs,
tokens, pairing commands or private paths. Never copy raw transcripts or tool
output into public tutorials. Relative links should lead to existing repository
files. Dates use `YYYY-MM-DD`; Markdown uses a title and balanced code fences.
Use clear labels for implemented, locally verified, live-verified and planned work.

## Where the reminder lives

The relay's existing submit hook adds this process to application context only
when the session's exact ID appears in private `tutorial-policy.json`. The
allowlist holds at most two registered sessions. It has no role/name/workspace
matching and no automatic enablement for new bots or replacement controllers.
Missing policy means no reminders. The file is stored with permission `0600`;
it is not exported in Git history or packages.

Trusted host setup uses `intern_personal.tutorial_workflow.configure(state, ids)`;
the selected IDs stay private. An empty list disables the policy. This is not a
new Feishu command or an automatic permission grant. Repository-local `AGENTS.md`
is not rewritten to impose the reminder on every bot.

App-server turns receive the reminder as separate application context on each
submission. Native sessions use a `UserPromptSubmit` hook with an exact-thread
check. New native hook definitions must be reviewed and trusted using Codex
`/hooks`; until then, publishing a context file does not activate native injection.
See [official OpenAI hook documentation](https://learn.chatgpt.com/docs/hooks).
The hook does not change the owner prompt or rewrite
history. Normal relay records remain format-checked; the agent still authors
the tutorial and judges whether its content matches the implementation. The
reminder is not a claim that tutorial completeness can be certified mechanically.
