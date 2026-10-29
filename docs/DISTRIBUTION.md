# Intern Agent product and release workflow

Intern Agent 0.2.6 uses MIT and maintained source
`c21ea4419894d892c5c518ca9da3cac11dd3db7c`. Its separate product repository is
`https://github.com/JZSAWYER/intern-agent`. The owner has explicitly placed this
repository under a **private visibility hold** until a later explicit request to
go public. Feature synchronization and inherited public-release wording do not
lift that hold. No release tool changes repository visibility.

## Product inputs and identity

`source-distribution.json` inventories exact reviewed source files, original hashes
and local product deltas. `distribution.json` independently selects VSIX backend
inputs. Git history from private tracking, `.intern_workspace`, enterprise snapshots,
credentials, account/chat/native identifiers and machine state are excluded.

The stable identity is `intern-agent.intern-agent`, version 0.2.6, with 39 commands
and settings/views under `internAgentPublic`. This namespace remains stable across
repository visibility changes and is distinct from the development preview's
`intern-agent-local.intern-agent-personal` / `internAgent`. It does not migrate
preview bindings or start a receiver. Source/VSIX byte checks and a separate
canonical installed-manifest hash accept only VS Code's reserved `__metadata`;
functional manifest fields and script content remain pinned.

`release-product.json` locks version, skills, public dependency profile and the
verified maintained-source handoff. `release_channel=private`,
`public_visibility_hold=true` and `final_handoff.channel=private` pin this release.
The public channel is rejected while the hold is true. Switching to public needs
the owner's explicit new request and a reviewed policy change; it is never an
API side effect of publishing a release. MIT licensing is independent of whether
the repository is currently visible publicly.

## Reproduce versioned assets

From a clean product checkout with the exact credential-free origin:

```bash
PRODUCT_COMMIT="$(git rev-parse HEAD)"
python3 tools/build_release.py --check-only --expected-export-commit "$PRODUCT_COMMIT"
python3 tools/build_release.py --expected-export-commit "$PRODUCT_COMMIT" \
  --output-dir /path/outside/source/release-0.2.6
```

The destination must be new and outside source. The six files are the stable
VSIX, exact source ZIP, complete skills ZIP, standalone skill installer,
release-provenance.json and SHA256SUMS. Fixed ZIP modes, order and timestamps
make two builds byte-identical. No existing output, tag or asset is overwritten.
The optional --prepare mode creates unlicensed, unpublished tooling exercises;
it is not a way to bypass final release approval.

Provenance records maintained_source_commit, product_source_commit, compatibility
export_commit, channel, hold, inventories, dependency hashes and asset SHA-256.
For a private release public_source_commit is null. The product Git commit is
supplied externally to avoid a self-referential tracked hash. The builder makes
no network changes; its published=false means publication is a separate step.

The four selected skills are recover-intern-agent, feishu-file-transfer,
install-on-linux and collaborate-on-source. Their entrypoints and inert references
ship together with MIT notice. The separate installer is byte-identical to its
reviewed source and requires an explicit destination/checksum/apply; it preserves
modified user files. The bundled installer also retains full resources/notices
and rejects linked ancestors. Skill installation starts no relay or agent.

The packaged installation doctor is read-only and independent of relay state.
Its default preflight and strict installed-runtime checks are separate from
operational `/doctor`. Optional wheel downloading uses only reviewed profile
URLs/hashes without system pip. Skills metadata and installed bytes are checked;
actual client discovery and invocation require their own evidence.

## Synchronize committed feature updates

The maintained development checkout stays read-only to the release operator.
Import only the verified source pin, reconcile local product/installation tooling,
review per-file provenance and update FEATURE_GUIDE/CHANGELOG. Goals/questions in
this version retain their owner, task, native publication and cold-recovery gates;
release jobs cannot authorize goals. See [goals and questions](GOALS_AND_QUESTIONS.md).

The product history begins with an audited source-only export. Later product
commits extend that clean history; never merge private operational refs. Every
feature handoff uses a fresh immutable version and consistent source/runtime pins,
rights/license, dependency and installation gates. Keep all earlier versions.

The existing relay release worker can queue handoffs from its explicitly selected
source conversation. A queued or completed operator event is not proof of upload.
Receipts require exact remote source and downloaded asset hashes. The visibility
hold remains authoritative regardless of an automatic handoff's inherited prose.

## GitHub workflow and publication

The root `.github/workflows/release.yml` tests and builds versioned main updates
only for the exact product repository. It requires an approved channel/handoff,
clean tracked inventory and source commit. Event/API visibility must match the
chosen channel; private source updates cannot publish publicly under the hold.
No workflow creates a repository, changes visibility, invites a person or changes
account permissions.

Before mutation, verify the current main commit and unused tag/release version.
Upload a new draft, download every asset with repository-scoped authentication
and verify exact bytes/digests before completing the release. The private channel
uses authenticated downloads after completion as well. Credentials stay in memory
and are not forwarded to redirected CDN hosts. A partial draft/tag remains for
review; never replace its assets blindly. Recheck repository visibility before
upload and completion. The first local publisher for a version uses [skip ci]
to avoid a competing workflow; later approved updates can use CI directly.

## Installation and acceptance limits

[Installation](INSTALL.md) uses the patched Linux x86_64 CPython 3.12 hash lock.
The older dependency reference is reproduction-only, with dated advisories.
No third-party wheels or tools are bundled. [Portable recovery](PORTABLE_RECOVERY.md)
explains same-host discovery and cold GitHub recovery with independent accounts.

Offline tests, mocked editor checks, isolated sideload/venv installation and remote
asset verification are distinct from real Feishu/Codex accounts, card callbacks,
Remote SSH activation and receiver upgrade/rollback/uninstall acceptance. This
release does not change a production receiver or research job. No PR, merge,
Marketplace publication or existing-repository visibility change is performed.
