# Intern Agent contributor context template

This template is optional repository context, not authorization to change accounts,
services or release visibility. Maintainers adapt it to the agreed contributor policy.

Read README.md, docs/CONTRIBUTING.md, docs/INSTALL.md and the relevant tests.
Keep the source and VSIX manifests distinct. Verify release-runtime.json using
tools/check_release_runtime.py; unexpected drift is a proposed behavior change,
not a reason to silently update the accepted baseline.

Development bots use their registered worktree and permanent branch. Named tasks
stay on that branch. The owner and collaborator develop on separate source branches and propose
PRs to the selected integration base; public contributors can use forks. Read
docs/CONTRIBUTING.md and skills/collaborate-on-source/SKILL.md. Access and review
permissions follow the maintainer's explicitly agreed policy. No automatic PR or merge is
implied by a completed bot task.

Use disposable offline fixtures and separately provisioned account inputs.
Update FEATURE_GUIDE.md and CHANGELOG.md with changes and actual verification.
Distinguish offline, isolated installation, editor activation and live receipt
checks. Do not copy runtime bindings, credentials, conversation history, Codex
rollouts or packaged proprietary references into source.

Reuse the existing relay for operations. Saved-result retries must not rerun the
original request; unknown execution/send outcomes require reconciliation.
Private recovery and backups are separate from a public source release.
