# Install Intern Agent on another machine

Each user installs independent accounts, private state and projects. No access to
the original operator's machine is needed. Source development uses
[contributor onboarding](CONTRIBUTING.md).

Intern Agent 0.2.1 is the separate MIT-licensed product at
[the product repository](https://github.com/JZSAWYER/intern-agent).
Select its [versioned release](https://github.com/JZSAWYER/intern-agent/releases/tag/v0.2.1)
and verify checksums/provenance, rather than using a private operational branch.
The repository and release are private until the owner explicitly requests
public visibility. Authorized access is required to fetch them.
See [distribution](DISTRIBUTION.md) for reproducible packaging and release guards.

## Supported profile and artifact verification

The current installer profile is Linux x86_64, CPython 3.12, with Python venv and
ensurepip available. Git, tmux and an authenticated supported Codex CLI are
separate system prerequisites. The installer reports their presence without
installing them. Other backend OS/Python combinations and native macOS/Claude
contributions need their own acceptance; use a Linux Remote SSH workspace host
for the supported path.

Download the selected release's source/VSIX/skills, standalone skill installer,
release-provenance.json and SHA256SUMS. In that download directory, run:

```bash
sha256sum --check SHA256SUMS
```

Compare the release version/source commit with provenance. Checksums establish
byte equality to the selected receipt; they do not select which source or owner
to trust. Extract the source ZIP into a new directory. It contains editable
runtime, frontend, tools, tests, guides and skills, without source Git history or
private state. Tools below run from that source root, or from the installed
VSIX's backend root. The source tree and installed backend are independent of a private tracking checkout.

`release-runtime.json` pins runtime inputs and frontend source. The product
VSIX records its generated frontend identity separately from preview source. Installed checks recognize only VS Code's reserved `__metadata`
object without accepting changes to product identity, commands or script bytes;
[baseline details](DEPLOYMENT_PARITY.md) explain code versus documentation and
frontend comparisons.

## Public dependency profile

`requirements/public-linux-py312.lock` and `public-profile.json` select a new
wheel-hashed public profile. It has 14 application distributions plus a pinned
pip bootstrap tool, rather than inheriting the host environment. The profile
records exact versions, wheel hashes, versioned PyPI sources, dated OSV screening
and compatibility evidence. No findings at that check is not a future security
guarantee. Review the profile again for a later release.

The older `deployed-linux-py312.lock` is retained unchanged for deliberately
selected reproduction. It has recorded advisories and is never the default.
No deployed dependencies are upgraded by installing into a new prefix.

## Plan and install an isolated runtime

Choose a new installation prefix, a separate short private state path and a
wheelhouse. Replace the example paths deliberately:

```bash
python3 tools/check_release_runtime.py
python3 tools/install_runtime.py --prefix /path/to/new/install --state /short/private/state
python3 -m pip --isolated download --index-url https://pypi.org/simple \
  --only-binary=:all: --no-deps --require-hashes \
  --requirement requirements/public-linux-py312.lock --dest /path/to/wheelhouse
python3 tools/install_runtime.py --prefix /path/to/new/install --state /short/private/state \
  --apply --wheelhouse /path/to/wheelhouse
```

Plan writes nothing. Apply verifies the same profile/lock and runtime, creates a
venv without system packages, installs only hash-verified wheels from the named
wheelhouse with no index, runs pip check, verifies installed application/SDK,
transport and Crypto imports, copies the backend allowlist and saves its receipt.
The lock upgrades pip **inside the new venv**; global Python is unchanged.
It starts no relay, creates no private state/binding and copies no credentials.
The receipt reports the installed Python and backend paths. Set the editor's
Python setting to that venv executable. CLI users run the installed module from
its backend directory.

Existing prefixes, overlapping state/install paths, symlinked paths, non-private
existing state and excessive socket path lengths fail before installation.
Failures preserve only that install's partial prefix/receipt for inspection.
Do not retry by overwriting an existing prefix. Keep state outside the extension
and installation directories so upgrades do not own or erase user data.

For a deliberately selected historical environment, add
`--reproduce-deployed-dependencies` to both plan and apply and download the
**reference** lock instead. The plan/receipt explicitly identifies that profile;
its reproduction success does not clear the public security gate.

## Install the separate VSIX and set up fresh accounts

Use VS Code's **Install from VSIX** on the intended Linux workspace/Remote SSH
extension host, selecting the matching versioned VSIX. The extension ID is
`intern-agent.intern-agent`; its commands/settings/views use `internAgentPublic`.
It can be distinguished from `intern-agent-local.intern-agent-personal`, whose
settings remain under `internAgent`. No automatic preview-state migration occurs.

Set `internAgentPublic.pythonExecutable` to the installed venv Python. Use setup
to reuse the same Linux account's discovered host profile, or choose your own
short private state path. Fresh PAT setup detects your GitHub account through
masked input; advanced setup accepts explicit Codex/model/effort choices. The current UI uses dialogs; actual cancellation/resume,
remote activation and dashboard acceptance are tracked in
[frontend readiness](FRONTEND_READINESS.md).

Provision your own Codex login, GitHub API and Git transport access. Enter new
Feishu app credentials only through masked UI/protected handoff; never paste
secrets into chat, argv, Git or example configuration. Publish the self-built
app with the documented bot/event/chat scopes, start **one** relay and pair the
owner group using its one-time capability. Confirm the actual reply and health.
File transfer and reactions require their own published permission checks.
Follow [live setup](LIVE.md), then attach a clean user-owned GitHub checkout or
review a selected new-project initialization plan.

To reconnect another client to the same host, or recover uploaded projects on a
replacement host by repository name, follow [portable recovery](PORTABLE_RECOVERY.md)
and the bundled recover-intern-agent skill. A PAT grants repository access; it
does not recover Codex/Feishu credentials or unpushed data. Historical requests
remain archived and are not replayed.

Each runtime bot keeps its own permanent worktree/branch/thread/chat. Human
contributors use separate branches and PRs; they do not share a bot's operational
branch. Reopening a conversation reuses its binding; creating another is an
explicit action. See [contributing](CONTRIBUTING.md) and the optional
[installation skill](../skills/install-on-linux/SKILL.md).

## Install the Codex skills bundle

The skills ZIP is separate from the runtime and includes entrypoints plus required
reference resources. Installing it does not activate a relay or log in to any
account. Use the version-matched standalone installer whose own checksum was
verified above (or reviewed tools/install_skill_bundle.py from source).

Choose the installing user's Codex skills directory explicitly. Inspect the plan,
then apply only an approved released bundle, with its external SHA-256:

```bash
python3 tools/install_skill_bundle.py /path/to/intern-agent-0.2.1-skills.zip \
  --destination /path/to/your/codex/skills
python3 tools/install_skill_bundle.py /path/to/intern-agent-0.2.1-skills.zip \
  --destination /path/to/your/codex/skills --sha256 "${IA_BUNDLE_SHA256:?Set the verified skills ZIP hash}" --apply
```

Planning writes nothing. Preparation bundles cannot be applied. The installer
checks exact inventory, hashes, size/CRC/UTF-8 limits, names and symlinks, and
preflights all resources and existing destinations before writing. Modified or
conflicting skills are preserved and reported; identical reapplication changes
nothing. It keeps per-file provenance and the validated MIT notice beside its
private per-version receipt. It executes no downloaded script and performs no
network calls. Do not point it at the build bot's own Codex home.

## Persistent goals and owner questions

Version 0.2.1 adds tracked goal continuation and owner question cards. Use
[goals and questions](GOALS_AND_QUESTIONS.md) for controls, pause/recovery
boundaries, callback setup and /answer fallback. A release job or attachment
cannot authorize a new persistent goal. Real card interaction requires the app's
published card.action.trigger long-connection callback; no callback/account
configuration is changed by installing this release.

## Acceptance, upgrade and uninstall

Inspection, offline package/editor tests and isolated dependency installation
are different evidence from real account/Remote SSH acceptance. On the selected
pilot verify unchanged owner input, exact thread/branch/chat, incoming PDF/ZIP,
returned-file receipt, Git-confirmed final reply, progress/model/task controls,
stop/reconnect and uncertain-send reconciliation.

Compare the installed extension and active receiver versions separately. A VSIX
update alone does not switch the receiver. Guarded upgrades wait for active work
and preserve private state, queues, receipts, threads and worktrees. Test rollback
and interrupted upgrades on an isolated pilot. Removing the extension preserves
projects/state by default; relay shutdown and private deletion are separate user
operations. Never kill a shared tmux server or start a competing receiver. See
[installation lifecycle](../skills/install-on-linux/references/installation-lifecycle.md).
