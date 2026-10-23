---
name: collaborate-on-source
description: Develop Intern Agent with separate owner and collaborator branches, prepare a focused PR, or review a contribution. Use for source collaboration; deployment and public release are separate operations.
---

# Collaborate on Intern Agent source

Read repository AGENTS.md and docs/CONTRIBUTING.md. Establish the checkout, remote,
integration base and branch owner before changing Git state. The example public
layout uses main for integration, owner/development for the owner and
collaborator/development for the collaborator; actual selected names take
precedence. GitHub contributors without write access use a fork. Do not infer
access from chat membership or from this skill.

An active relay bot stays on its registered permanent branch. Never switch its
worktree to another contributor's branch, rename its registration or publish its
private operational history as a PR. Use a separate clean source checkout for
human/fork development and an audited source-only export when crossing from bot
work to a public repository. Do not create that public target implicitly.

Develop only on the actor's own branch; preserve other people's changes. Inspect
the focused diff, run relevant backend/frontend checks and update FEATURE_GUIDE
and CHANGELOG. Review source inventory and runtime deltas explicitly rather than
blindly accepting changed hashes. Follow the contributor test command that clears
inherited CODEX_THREAD_ID only for synthetic offline fixtures. Keep credentials,
runtime state and private records out of commits, test evidence and PR attachments.

For a product update, read docs/DISTRIBUTION.md and include the runtime/dependency,
VSIX and complete skill-resource deltas. A verified source handoff and fresh
release-product.json version drive the separate public release workflow. Do not
mark release gates approved or upload the private bot repository/history merely
to trigger it. CI packages the reviewed source, VSIX, skills and checksums together;
ordinary source development does not grant publication authority.

Prepare a PR with the confirmed integration branch as base and the contributor's
branch as head, including the problem, resulting behavior, checks and limits.
Push only the intended branch when uploading is authorized. Create the actual
PR only on an explicit instruction to do so; learning or documenting this workflow
does not itself request a PR. Review the other person's change and resolve
conflicts on the submitting branch. Merge only when separately requested and
review/check requirements are satisfied. Do not force-push another person's
branch or deploy as a side effect of merging.

Report branch/base, changed files, verification and whether the PR is merely
prepared, actually opened or merged. If target/access is missing, finish local
reviewable work and identify the specific missing information.
