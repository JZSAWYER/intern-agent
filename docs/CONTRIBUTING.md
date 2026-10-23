# Contributing to Intern Agent

Use [installation on another machine](INSTALL.md) to run Intern Agent with your
own accounts. This guide covers developing its relay and VS Code extension,
whether you are the owner, an invited collaborator or a GitHub fork contributor.
The public product at JZSAWYER/intern-agent is licensed under MIT; contributions
must be compatible with [source rights and licensing](LICENSING_PROVENANCE.md).
Repository access is managed separately; this guide grants no permissions.

## Separate development branches

The shared integration branch is `main` in the examples below. Confirm the
actual base branch in the public repository before using commands.
The owner develops on `owner/development`; the collaborator develops on
`collaborator/development`. These are example public source branch names,
not existing remote branches. Each person uses their own checkout/worktree.
Both submit PRs to the integration branch and review the resulting changes.
Neither pushes to the other person's branch or directly to `main`.

GitHub users without write access create a fork and use their own development
or topic branch. A PR's base is the upstream integration branch; its head is the
contributor's branch. See GitHub's
[fork PR guide](https://docs.github.com/en/pull-requests/how-tos/create-pull-requests/creating-a-pull-request-from-a-fork).
For small independent changes, a topic branch can be used in the contributor's
own source checkout. An active relay bot always keeps its registered branch;
do not switch that bot's worktree to create a human topic branch.

From a clean, dedicated source checkout with the intended remote configured:

```bash
git status --short
git remote -v
git fetch origin
git switch -c owner/development origin/main
```

The collaborator substitutes `collaborator/development`. For a fork checkout,
fetch the configured upstream remote and create the branch from upstream/main.
If your branch already exists, switch to it instead of recreating it. Inspect
remotes privately: never paste URLs containing authentication into issues or PRs.
An assistant operating this live bot remains on its registered permanent branch;
the example does not authorize changing branches here or uploading its history.

## Develop and verify

Read repository AGENTS.md if present, README, [architecture](ARCHITECTURE.md),
[feature tutorial](FEATURE_GUIDE.md) and [development workflow](DEVELOPMENT_WORKFLOW.md).
State the symptom or feature example and acceptance criteria. Preserve existing
edits. Use synthetic inputs and disposable repositories for tests; do not point
them at a running user's state, queue or workspace. The extension uses VS Code
and Node builtins; no npm dependency installation is needed.

```bash
env -u CODEX_THREAD_ID TMPDIR=/tmp PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -q
node tests/extension.test.js
TMPDIR=/tmp python3 -m intern_personal simulate
python3 tools/check_distribution.py
git diff --check
```

Run the checks relevant to the change; the full Python command is available for
broader verification. The reference environment used Python 3.12 and Node 24;
see INSTALL.md for actual platform/dependency limits. Use a short temporary root
to leave room for Unix sockets. Offline simulation starts no real Codex session.
When running inside a coding agent, clear only CODEX_THREAD_ID for the offline
test subprocess, as shown above. These fixtures create synthetic conversations;
inheriting the real session identity correctly triggers ownership guards. Do not
change those guards or unset identity for live relay/file/mail operations.
Record live installation/editor/Feishu results separately from mocked checks.
Update FEATURE_GUIDE and CHANGELOG for implemented behavior or corrected guidance.

The source review manifest pins exact files. After reviewing a changed/new file,
update its inventory entry with provenance; do not refresh unexplained changes.
The runtime baseline intentionally rejects behavior drift. A backend change must
include its proposed baseline delta and acceptance plan, then obtain maintainer
review before changing the accepted release fingerprint. See
[deployment parity](DEPLOYMENT_PARITY.md).

## Open and review a PR

Review the diff and stage only the intended source files. Exclude credentials,
private app/chat bindings, conversation records, databases, logs, caches and
packaged reference snapshots. Commit to your own branch, then push only that
branch when the repository and upload scope have been authorized. Do not use
push --all or publish this bot's operational refs.

Open a PR in GitHub with the confirmed integration branch as base and your own
branch as head. Describe the concrete problem, resulting behavior, relevant
checks, frontend evidence if applicable, and remaining limits. Request review
from the other maintainer. A PR is a proposal; deployment and merge are separate
decisions. An agent creates an actual PR only when explicitly instructed.
This guide and its skill describe the workflow without creating one now.

Fetch integration updates and merge them into your own development branch as
needed; resolve conflicts locally and rerun affected checks. For a fork, fetch
the upstream remote. Avoid force-pushing shared long-lived branches. After the
reviewed PR is merged, synchronize with the resulting integration head before
the next change, especially after a squash merge. Do not delete another person's
branch or merge your own unreviewed work as a side effect of finishing a task.

The proposed policy is to protect the integration branch with PR review and
required checks. GitHub protection settings, maintainer identities, security
contact and contribution terms are release setup decisions; none are configured
by this document. This document does not invite a collaborator or configure access.

## Relay bot branches and public source history

A permanent branch per registered bot is a runtime ownership rule, not a rule
that all contributors share one branch. This assistant continues managing its
registered worktree. A future collaborator manages their own branch independently.
Operational bot branches contain private management records and must never be
used as public PR heads. Submit reviewed source changes through a dedicated
clean source checkout and separately authorized push/PR workflow.

The optional [collaboration skill](../skills/collaborate-on-source/SKILL.md)
captures branch ownership, PR preparation and review. No skill grants account
access, changes repository rules, starts another relay or implicitly merges work.
