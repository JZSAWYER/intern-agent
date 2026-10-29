---
name: install-on-linux
description: Install or verify Intern Agent on another Linux machine from reviewed source or VSIX with independent accounts and private state. Use for new user installation, setup diagnostics, upgrade or uninstall planning.
---

# Install Intern Agent on Linux

Locate the selected source release or installed VSIX backend using its installation
receipt. Tools and product docs below are relative to that root; installing this
skill alone does not install the runtime. Read docs/INSTALL.md, verify the release
SHA256SUMS/provenance and run tools/doctor_install.py before account setup. Report
its actual pass/warning/failure checks and exit status. Version labels alone
do not establish byte parity or live acceptance.

Use the hash-locked public Linux x86_64 CPython 3.12 profile selected by
requirements/public-profile.json. Review its dated advisory/import evidence and
any remaining acceptance limits. The older deployed-reference-only profile has
known advisories and requires the user's explicit reproduction choice; never
select it as a public default.

Plan with tools/install_runtime.py before applying to a fresh prefix and separate
short private state path. For a reviewed online install, use explicit
--apply --download --wheelhouse with the public profile; system pip is unnecessary.
For offline apply, select an existing verified wheelhouse and omit --download.
Apply uses an isolated environment and hash-verified wheels; it starts no relay.
Run the installed backend's tools/doctor_install.py --require-runtime using the
installed venv Python and inspect the installation receipt. Preserve an existing installation and
its receipt instead of overwriting it. Use the independent internAgentPublic
settings for the public VSIX; do not migrate preview state/settings implicitly.

For setup, provision the installing user's Codex/GitHub/Feishu authentication,
app scopes and owner pairing. Never import another machine's credentials, queue,
chat bindings, Codex history, tmux identity or personal project checkouts.
Read [installation lifecycle](references/installation-lifecycle.md) for setup,
upgrade, uninstall or a separately requested private recovery operation.

When verifying the skills bundle, run its installer with --verify and the external
bundle SHA-256, or use tools/doctor_install.py --skills-directory for the matching
product's files. File equality does not prove Codex discovered or invoked a skill.
Confirm the actual client's skill list and explicitly invoke $install-on-linux
with the selected product root; inspect the resulting command output. Do not
claim execution from a successful file copy or from the model's final prose alone.

Record source/package/runtime and dependency fingerprints, local tests, actual
editor activation and live account acceptance separately. Reuse a selected
installation's relay without starting a competing receiver. Follow the user's
scope for service and account changes; this skill does not authorize publication,
invitations, state recovery or replaying an uncertain execution.
