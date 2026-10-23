# Published product and development separation

The owner requires a separate, installable Intern Agent product. Development
and operational tracking remain in their existing private workspaces. Public
source starts from a clean, allowlisted export, never private Git history.
The published repository contains maintained source, a stable VSIX, Codex skills,
installation/recovery guides and the tooling needed to reproduce its artifacts.
A source-review folder or an unlicensed preview VSIX is not a published release.

## Release contents and identity

Use a stable product extension identity distinct from the local preview. Its
manifest must declare the selected public license, product name and publisher;
keep the development manifest's preview identity separate. The local builder
supports `--release` and rejects preview/unlicensed stable inputs. GitHub release
publication does not imply publication in the VS Code Marketplace.

Every published version should attach:

- An installable stable `.vsix`, containing the allowlisted backend and skills.
- A source archive containing only the public project tree.
- A skills archive, including `recover-intern-agent` and file-transfer guidance.
- A machine-readable release manifest and SHA-256 checksums for the artifacts.
- Installation, recovery, dependency and upgrade/uninstall instructions.

The manifest records the exact maintained-source commit, public-source commit,
product version and artifact filenames/hashes. Verify uploaded bytes before
claiming synchronization. Use new immutable release versions; do not silently
replace an existing release asset or publish development tracking refs.
Uninstall/upgrade preserves private state and user-owned skills.

## Permanent feature-to-release workflow

The normal per-turn Git hook remains responsible for private project history.
A separate release worker watches only the owner's explicitly selected Debug
source conversation. It is disabled without private `release-policy.json`.
Trusted host configuration selects a distinct registered Open Source operator,
public destination and license. Nothing is inferred from bot names or enabled
for ordinary research bots.

The worker fingerprints committed public inputs and first verifies that the
source branch's HEAD is actually uploaded. A new content fingerprint creates
one durable owner-authorized handoff in the existing Open Source bot's queue.
If that bot is busy, it finishes its current work first. The handoff survives
receiver restarts; repeat polls and records-only commits do not create duplicate
release tasks. No source file or prompt is sent into a busy terminal directly.
Debug remains a manual debugging bot, without automatic reply tasks from releases.

The release operator uses its own worktree/branch to:

1. Read the verified commit and reconcile only missing maintained changes into
   the clean public export. Keep examples portable, with explicit GitHub account
   onboarding rather than a personal destination default.
2. Run backend/editor/distribution/recovery checks and the release project's
   dependency, source-provenance and artifact-content checks. Complete a real
   isolated editor installation check for the exact VSIX where available.
3. Build the stable VSIX, source/skills archives and checksum/provenance manifest.
4. Upload the clean source to the separate public repository and publish the
   versioned GitHub release. Creating this new clean public repository and
   release is authorized by the owner's release request. It does not authorize
   exposing an existing private repository/history, PRs, merges, invitations,
   Marketplace publication, account permission changes or production services.
5. Independently check remote source and uploaded asset hashes. Report actual
   public URLs and limits in its own Feishu chat. Save a secret-free private
   receipt under `runtime/release-receipts/SOURCE_COMMIT.json` for coordination.

The public repository should also provide a GitHub Actions release workflow
that builds and attaches artifacts for its versioned source updates. Its CI uses
repository-scoped GitHub credentials and public input allowlists, not personal
PATs copied from the private relay. Branch/release policies must preserve the
owner's no-PR rule; initialize a new repository from the clean export and never
merge private development history into it.

## Status and failures

The dashboard exposes release jobs and the operator's execution state.
`/doctor` checks the enabled worker's health. `operator_completed` means its
Codex task completed; it is not independent proof that an artifact was uploaded.
The release URL, exact source commit and verified asset hashes establish that.
Git/network/review failures remain visible, with no automatic replay of an
already executed publication prompt. Normal relay recovery can continue a
confirmed failed operator turn in its existing session under its configured
policy. The release worker does not interrupt research, alter credentials,
start another receiver or hold ordinary Feishu delivery while building a VSIX.

## Recovery boundaries

Public product files and private recovery data have separate purposes. Never
bundle `.intern_workspace`, authentication files, queue databases, transcripts,
private inventories, personal account/chat/native identities, machine paths,
enterprise reference snapshots or live configuration in the public export.
A PAT is entered privately on each replacement host. Codex/Feishu credentials
are separate account setup; see [portable recovery](PORTABLE_RECOVERY.md).

Changing computers while keeping the same Linux account/server reuses its host
profile and exact sessions. Total state loss uses the published recovery skill
and uploaded project archives. Lost RAM, unpushed edits and unbacked external
artifacts cannot be recreated by packaging a VSIX.

Trusted host setup exposes the explicit policy through the backend:

```text
python3 -m intern_personal.feishu_live --state STATE release-sync configure --source SOURCE_SESSION --operator RELEASE_SESSION --repo PUBLIC_GITHUB_URL --license MIT
python3 -m intern_personal.feishu_live --state STATE release-sync status
python3 -m intern_personal.feishu_live --state STATE release-sync off
```

Disabling policy preserves job history and cancels undispatched handoffs at their
execution guard. A changed source/operator/destination/license also invalidates
an old queued policy. Already executed publication work is never blindly replayed.

The public package includes a LICENSE file as recommended by the
[VS Code publishing documentation](https://code.visualstudio.com/api/working-with-extensions/publishing-extension).
GitHub's [release API](https://docs.github.com/en/rest/releases/releases)
provides published asset metadata, including SHA-256 digests, for independent
verification of the release receipt.
