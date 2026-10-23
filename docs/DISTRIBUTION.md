# Intern Agent product and release workflow

The product is **Intern Agent**, first stable version **0.1.0**, at
`https://github.com/JZSAWYER/intern-agent`. This is a new, clean public destination;
the private tracking repository and its operational history stay private. The owner authorized this clean public release under MIT from maintained commit
`c0aa1748223011e244080a7cd4ec58228988beff`. The release operator verifies the
clean public commit and uploaded artifact hashes; no private repository history
or Marketplace publication is included.

## Product inputs and identity

`release-product.json` records the destination, fresh stable version, extension
identity, complete selected skills and dependency profile. Its approved handoff and release gates identify the reviewed release inputs. The reviewed source inventory remains
`source-distribution.json`; the independent VSIX backend allowlist is
`distribution.json`. Neither includes Git objects/history, `.intern_workspace`,
enterprise references, private configuration, queues, credentials or transcripts.

The public extension ID is `intern-agent.intern-agent`; commands, views, context
values and machine settings use `internAgentPublic`. The preview remains
`intern-agent-local.intern-agent-personal` with `internAgent` settings. The build
changes both the manifest and JavaScript consistently, preserving separate
settings and output channels. It does not migrate preview bindings or start a
receiver. Stable VSIX metadata sets PreRelease=false and includes content types
for every member. The package's generated frontend hashes and their source hashes
are recorded explicitly, rather than compared against unchanged preview bytes. VS Code adds its own
`__metadata` object when installing a VSIX. The installed-backend check accepts
only that reserved metadata through an additional canonical manifest fingerprint;
identity, commands/settings and all other manifest content stay pinned, as do
script bytes. Source and VSIX archive comparisons still require exact bytes.

The clean public source and stable package declare MIT and include LICENSE and
NOTICE. Final builds require approved source inventory and locked handoff. The
optional --prepare mode remains an unlicensed, unpublished tooling exercise; its
output cannot be installed as an approved skills release.

## Build and reproduce release assets

From a clean checkout of the public product source root:

```bash
PUBLIC_COMMIT="$(git rev-parse HEAD)"
python3 tools/build_release.py --check-only --expected-export-commit "$PUBLIC_COMMIT"
python3 tools/build_release.py --expected-export-commit "$PUBLIC_COMMIT" \
  --output-dir /path/outside/source/release-0.1.0
```

The destination must be new and outside the source tree. The deterministic assets
are a VSIX, exact source ZIP, complete Codex skills ZIP, standalone skill installer,
release-provenance.json and SHA256SUMS. Versioned filenames share one version.
The source ZIP uses the exact source manifest, not git archive or a repository
copy. The skills ZIP includes selected entrypoints **and referenced resources**;
it contains no automatically executed installer. The separate installer is
byte-identical to reviewed tools/install_skill_bundle.py.

ZIP timestamps, ordering and file modes are fixed. Receipts include the maintained
source commit, source inventory, dependency lock/profile hashes, package overlays
and each asset's SHA-256. Temporary paths, build-host identities and private Git
history are excluded. Building twice from identical inputs must yield identical
bytes. Existing outputs, tags and release assets are never overwritten.

## Final handoff and source updates

This release imports the committed cold recovery, SSH profile discovery, portable
settings/roles and durable release-handoff features from the pinned source. Local
product changes include the separate frontend identity, patched install profile,
complete resource-preserving skill installation and reproducible release tooling.
The source inventory records original and exported hashes for review. For a future
handoff, import only verified committed changes; update FEATURE_GUIDE and CHANGELOG
alongside implemented behavior.

Set final_handoff.source_commit to that verified pin, with matching repository,
version and explicit approval. Source and runtime manifests must pin the same
commit. Ownership, MIT/license, dependency security and clean installation gates
require actual reviewed evidence. Keep the export commit outside tracked handoff
metadata to avoid a self-referential hash; CI supplies GITHUB_SHA separately.

Use a history-free source import to establish the new public repository. Later
updates copy only audited source changes into that public history. A Debug source
feature must produce a durable verified release handoff, a fresh version and an
updated clean export. Never mirror private refs or publish unverified drafts to
force synchronization. Increment patch versions for fixes and minor versions for
new compatible feature sets; all artifacts for one handoff use the same version.

## GitHub automation

The prepared `.github/workflows/release.yml` belongs at the **root** of the clean
public repository. The private tracking wrapper keeps a copy under a source subtree; it is
inert there. It accepts pushes to main only in the exact public destination, requires
an approved source handoff and clean checkout/origin, then tests in an isolated,
hash-locked Python environment and builds every asset twice. It covers code,
frontend, tools, requirements, skills/resources, docs, manifests, workflow and
license changes. A stale handoff fails; source updates do not approve themselves.

Before external mutation it checks public visibility, exact main commit and
absence of the proposed tag/release. It creates a fresh immutable tag, uploads a
draft, downloads and compares every asset byte and checks the tag commit before
publishing. A failed upload leaves that new draft/tag for review; automation does
not replace an existing version. The initial owner-driven local release uses a [skip ci] export commit to avoid
a competing publisher. Subsequent versioned source updates use this workflow; its
remote runner acceptance is separate from local verification. Main review policy and repository settings are separate owner
operations; writing workflow files does not configure account permissions.

## Installation and remaining acceptance

[Installation](INSTALL.md) uses the patched public profile; the old deployment
lock is explicit reproduction only. Real account login, Feishu pairing/scopes,
Remote SSH/editor activation and receiver upgrade/rollback remain separate live
acceptance. Installing a VSIX does not prove that the receiver switched runtime.
No Marketplace publication, relay/research activation or existing-repository
visibility change is part of this preparation.

Official references: [VS Code packaging and VSIX installation](https://code.visualstudio.com/api/working-with-extensions/publishing-extension),
[extension manifest and host identity](https://code.visualstudio.com/api/references/extension-manifest).
