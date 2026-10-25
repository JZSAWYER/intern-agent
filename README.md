# Intern Agent

**Intern Agent** connects GitHub workspaces and persistent Codex conversations to
Feishu. The MIT-licensed public product includes maintained source, an installable
VSIX and Codex skills, with independent private state for each installation.
The product repository is currently **private for review**. It stays private until
the owner explicitly requests public visibility; routine feature-release handoffs
do not lift that hold. Repository access is managed separately.

Start with [installation](docs/INSTALL.md),
[the 0.2.2 release](https://github.com/JZSAWYER/intern-agent/releases/tag/v0.2.2),
[portable recovery](docs/PORTABLE_RECOVERY.md) and
[contributor onboarding](docs/CONTRIBUTING.md).

For installation on another machine, start with [the installation guide](docs/INSTALL.md)
and optional [installation skill](skills/install-on-linux/SKILL.md). Every user
provisions independent accounts and state; private machine migration is separate.

This project supplies a GitHub-only Feishu workspace relay, a Linux workspace
VS Code extension, and offline migration tools. One management bot routes each
GitHub-attached workspace conversation to its own group and Codex thread. Historical source evidence records a real
Codex transport and owner-only Feishu round-trip; this source sync runs offline
checks and does not repeat that live acceptance.
Every registered relay bot uses its own permanent branch for code and validated
Markdown history; named tasks stay on that bot's branch. Human source development
uses separate owner and collaborator branches with PRs to the integration branch;
GitHub users can contribute from forks. See [contributing](docs/CONTRIBUTING.md)
and the [collaboration skill](skills/collaborate-on-source/SKILL.md). A running
bot does not create actual PRs without an explicit instruction.
See [live setup](docs/LIVE.md) and [distribution requirements](docs/DISTRIBUTION.md).
For another computer or a lost installation, use [portable recovery](docs/PORTABLE_RECOVERY.md).
Private development/tracking history stays separate from this clean public
product; see [published releases](docs/PUBLISHED_RELEASE.md).
The owner Control group also supports unchanged natural-language messages and
slash commands through its own [tracked controller conversation](docs/CONTROLLER.md).
Create a new private repository and project bot through [project initialization](docs/PROJECT_INIT.md).
See the [frontend comparison](docs/FRONTEND_COMPARISON.md) for differences from the original extension.
Start with the [feature tutorial](docs/FEATURE_GUIDE.md) for commands and expected
results. The [change record](docs/CHANGELOG.md) tracks features and fixes; the
[development workflow](docs/DEVELOPMENT_WORKFLOW.md) keeps those guides current
in the explicitly selected Debug Bot and Control conversations.
Use [persistent goals and Feishu questions](docs/GOALS_AND_QUESTIONS.md) for
multi-iteration work, selectable answers and custom input.

The offline commands need only Python 3.10+ on Linux and `/usr/bin/git` for
repository inspection. The live relay additionally needs tmux, an authenticated
Codex CLI, lark-oapi and websockets; setup does not silently install packages.

## Release source and scope

Version 0.2.2 imports verified maintained source
`efa10678221efe96e47047eda9f2cd42c489263b`. It includes file/PDF batches, independent
consoles and live follow-ups, peer/recovery/research workflows, model/Fast settings,
quota/throughput, Git-confirmed completion, reusable records, cold GitHub recovery,
shared SSH host profiles and portable preferences/roles. This patch lets explicitly ignored local journals stay outside Git while
retaining draft protections and Git-confirmed completion. Packaging adds a distinct
public extension identity, a patched hash-locked dependency profile and safe
complete-resource skills installation. Provenance records both the maintained
source commit and the clean product commit, with every artifact's SHA-256.

Installation is verified in isolated Linux/editor directories. Fresh real account,
Remote SSH activation and upgrade/rollback acceptance remain separate; this
release does not deploy or change an existing receiver. See
[distribution](docs/DISTRIBUTION.md) and [baseline scope](docs/DEPLOYMENT_PARITY.md).

## Commands

```bash
python3 -m intern_personal doctor
TMPDIR=/tmp python3 -m intern_personal simulate
python3 -m intern_personal migration-plan --legacy-root "$LEGACY_WORK_ROOT"
env -u CODEX_THREAD_ID TMPDIR=/tmp python3 -m unittest discover -s tests -v
python3 tools/check_distribution.py
```

Set `LEGACY_WORK_ROOT` to the old work-agents directory before requesting a
migration preview. No environment file is sourced automatically.

The GitHub username is configurable and belongs only in private `personal.json`.
There is no account default in code. Supply your username during initialization
or change it later with `set-owner`. Changing it never changes Git remotes and is
rejected if existing workspace destinations belong to a different owner.
For a separate project, set `GITHUB_OWNER` to your chosen username, then run:

```bash
python3 -m intern_personal --project /tmp/intern-personal-example init --github-owner "${GITHUB_OWNER:?Set your GitHub username}"
python3 -m intern_personal set-owner "${GITHUB_OWNER:?Set your GitHub username}"
```

To register a repository in the new personal registry without cloning or
contacting GitHub:

```bash
python3 -m intern_personal workspace-add demo "https://github.com/${GITHUB_OWNER:?}/demo.git"
python3 -m intern_personal workspace-add metadata-demo "https://github.com/${GITHUB_OWNER:?}/metadata-demo.git" --metadata-mode metadata_branch --metadata-branch intern_workspace
```

Registration checks owner restrictions when configured and rejects Codeup,
other hosts, embedded credentials, URL options, path traversal, duplicate IDs,
duplicate destinations and unsupported metadata layouts. Updates use a file
lock and atomic replacement so simultaneous registrations do not lose data.
No existing repository or legacy registry is changed.

## Migration preview

The generated report is `reports/migration-preview.json`. To supply proposed
destinations, create a secret-free JSON mapping of workspace-directory IDs:

```json
{
  "ws_example": "https://github.com/<github-username>/<repository-name>.git"
}
```

```bash
python3 -m intern_personal migration-plan --legacy-root "$LEGACY_WORK_ROOT" --destinations destinations.json
```

Replace the example placeholders before use; the validator rejects them.
The planner reads legacy records but never uses their repository URLs or
provider credentials. Old GitHub records are not assumed to belong to the
desired personal account; each destination needs an explicit mapping.
Unknown workspace IDs, non-GitHub targets and duplicate destinations are
rejected or flagged. Preview entries always remain `ready: false`: destination
access, LFS/submodule completeness, a complete worktree/ref audit, a secret scan,
and migration authorization are not established by this offline inspection.

Discovery covers `source`, the metadata checkout, and immediate intern
clone/worktree directories. It is bounded, not an exhaustive disk scan.
Missing checkouts mean **not found by this scan**, not proven lost.
Uncommitted work is flagged and left unchanged. Branch counts are local;
the old server is never contacted and unavailable server-only history cannot
be reconstructed from registry records alone.

## Isolation

- Active code: `intern_personal/`; no imports of the old extension/backend.
- Config/state: this project's `personal.json` and `runtime/`; no `.bashrc` edits,
  legacy env loading, or credentials in the supported configuration schema.
- Local Git inspection uses a constructed environment, disables optional
  locks, hooks, filesystem monitor, credential helpers, global/system Git
  configuration, lazy fetch and transport protocols. No network operations or
  clone/fetch/pull/push/checkout commands exist in the offline inspection tools.
  The live Codex agent can perform actions explicitly requested by its owner.
- Historical packaged reference snapshots are excluded from this candidate,
  version control exports and the VSIX. Their former local presence establishes
  no permission to redistribute them.
- Reports, personal configuration, runtime state and reference snapshot are
  excluded by `.gitignore`. Live workspace adoption and GitHub uploads require
  explicit authorization.

## Offline runtime

`simulate` creates a temporary synthetic profile, delivers one event twice,
runs the fake agent once, records one fake reply, and removes its temporary
profile. It uses no network, tmux session or agent process and does not use
the private account configuration. See [runtime behavior](docs/RUNTIME.md).

The core enforces an explicit app ID, owner open ID and chat/workspace binding.
It stores tasks in a private SQLite queue and uses a single-worker lease.
Uncertain execution or delivery blocks later work in that workspace; it is
never silently retried. A saved result can be delivered after restart without
reexecuting the task. No message body is emitted in routine status output.

Templates under `templates/` contain explicit placeholders. They are examples,
not working configurations. Unreplaced placeholders fail validation.

## Next implementation/deployment work

Named tasks, independent conversation/activity status, validated per-turn
Markdown and priority `/stop` are implemented. See
[conversation lifecycle](docs/CONVERSATIONS.md) for commands and recovery.

An opt-in live relay now exists alongside the offline preparation commands.
See [live setup and workspace chats](docs/LIVE.md). Its real Codex transport has
passed an isolated create/persist/resume round-trip and an owner-only Feishu
message pilot. A new account still needs its own credential handoff, app
availability, pairing and message verification.
Live private state is independent of the offline project's registry and should
be placed on a data volume. The preview extension provides private setup,
credential input, pairing, status, Git recovery and optional workspace connection
on Linux, including Remote SSH. A VSIX was built and installed in an isolated
VS Code profile. Release acceptance for another host/account remains distinct
from this installation's live pilot.

Build a local preview without installing build packages:

```bash
python3 tools/build_vsix.py --output /path/to/intern-agent-preview.vsix
```

The output must not already exist. The builder uses the backend allowlist and
rejects private configuration, credentials and the locally configured username.
See [extension setup](extension/README.md).

1. Verify access to the configured GitHub account and select a pilot; finish its
   worktree, branch, LFS, submodule and secret inventory.
2. Complete the live Feishu pilot using the SDK receiver, chat API and structured
   Codex bridge. Offline transport tests do not establish account capability.
3. Validate operator reconciliation, network reconnection and live authorization
   before treating the relay as deployed.
4. Verify GitHub authentication on each installation. Each new bot keeps code
   and management records on one branch; PRs require an explicit user request.
5. Complete explicit account onboarding and the authorized message pilot;
   obtain authorization before repository uploads/remotes or public releases.

The seven proposed Feishu tenant scopes are in the example configuration.
The typing reaction additionally needs `im:message.reactions:write_only`;
see [live setup and reaction behavior](docs/LIVE.md).
Account-specific grants, publication and connection are setup steps, not
properties of this distributable code.

See [the editor, message and team workflow guide](docs/EDITOR_TEAMS.md) for the 0.5.0 dashboard, setup diagnostics, session controls, bounded teams, workspace access and paged recovery.
