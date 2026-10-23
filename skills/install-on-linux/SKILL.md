---
name: install-on-linux
description: Install or verify Intern Agent on another Linux machine from reviewed source or VSIX with independent accounts and private state. Use for new user installation, setup diagnostics, upgrade or uninstall planning.
---

# Install Intern Agent on Linux

Locate the selected source release or installed VSIX backend using its installation
receipt. Tools and product docs below are relative to that root; installing this
skill alone does not install the runtime. Read docs/INSTALL.md, verify the release
SHA256SUMS/provenance and run tools/check_release_runtime.py. Version labels alone
do not establish byte parity or live acceptance.

Use the hash-locked public Linux x86_64 CPython 3.12 profile selected by
requirements/public-profile.json. Review its dated advisory/import evidence and
any remaining acceptance limits. The older deployed-reference-only profile has
known advisories and requires the user's explicit reproduction choice; never
select it as a public default.

Plan with tools/install_runtime.py before applying to a fresh prefix and separate
short private state path. Apply uses an isolated environment and predownloaded,
hash-verified wheels; it starts no relay. Preserve an existing installation and
its receipt instead of overwriting it. Use the independent internAgentPublic
settings for the public VSIX; do not migrate preview state/settings implicitly.

For setup, provision the installing user's Codex/GitHub/Feishu authentication,
app scopes and owner pairing. Never import another machine's credentials, queue,
chat bindings, Codex history, tmux identity or personal project checkouts.
Read [installation lifecycle](references/installation-lifecycle.md) for setup,
upgrade, uninstall or a separately requested private recovery operation.

Record source/package/runtime and dependency fingerprints, local tests, actual
editor activation and live account acceptance separately. Reuse a selected
installation's relay without starting a competing receiver. Follow the user's
scope for service and account changes; this skill does not authorize publication,
invitations, state recovery or replaying an uncertain execution.
