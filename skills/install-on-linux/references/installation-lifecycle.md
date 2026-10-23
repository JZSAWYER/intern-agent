# Installation lifecycle

## Install on another machine

Use the selected source/VSIX release, runtime fingerprint, dependency profile,
supported platform matrix and license/notices. A GitHub user does not need the
original operator's machine or accounts. Provision Linux tools, Codex login,
GitHub API/transport access, their own Feishu app/scopes, protected credentials
and explicit owner pairing. Runtime paths can differ; separation, private
ownership and Unix socket length are requirements. Native Windows backend
support is not implied; use a Linux Remote SSH extension host where appropriate.

Locate the installed product/backend root from its receipt, then follow
docs/INSTALL.md for plan/apply commands and staged acceptance. The public hash-locked profile is separate from the historical advisory-affected
reference lock. The latter requires explicit reproduction selection.
Record missing OS/editor/live evidence even when isolated installation passes.
Contributors separately read docs/CONTRIBUTING.md for branches and PRs.

## Upgrade and uninstall

Compare selected backend and installed frontend against the intended release.
A VSIX update alone does not prove the receiver switched backend. End active
work before a guarded receiver upgrade and preserve state, registrations,
worktrees, receipts and threads. Test interrupted upgrade/rollback in an isolated
profile, without killing a shared tmux server or replaying prompts.

Removing the editor extension and stopping its relay are distinct operations.
Preserve private state and projects by default. Installation failure preserves
the installer's own prefix/receipt; reinstall does not authorize duplicate bots
or chats. Delete private data only for the user's explicitly selected scope.

## Private recovery is separate

If the user explicitly requests their own existing-state recovery, follow
docs/RECOVERY.md and verify saved branch/thread association. Do not automatically
resume old bindings or work. Reconcile ambiguous execution/send outcomes;
saved-answer retry must not repeat the original execution. Private backups never
become source packages, example configuration or public documentation.
