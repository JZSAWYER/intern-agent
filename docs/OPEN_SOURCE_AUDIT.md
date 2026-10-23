# Source and release readiness audit

## Current candidate — 2026-10-23

The owner authorized the separate MIT Intern Agent 0.1.0 product from maintained
commit `c0aa1748223011e244080a7cd4ec58228988beff`. The local public candidate now
contains that committed implementation and separately attributed distribution
work. The public extension identity is `intern-agent.intern-agent`; its 37 commands
use `internAgentPublic`. The maintained 0.8.0 development preview is a separate
extension. Public upload and release publication are established by independently verified
GitHub URLs and asset hashes, rather than by this local audit alone.

The existing private tracking repository and source history are unsuitable public
history inputs. Publication uses a fresh allowlisted export, preserving the private
repositories and their refs. Source tests and release tooling are reviewed separately
from the smaller VSIX backend allowlist. Enterprise reference material, credentials,
private records, queue databases, transcripts, native/chat/account identifiers and
machine-specific configuration are excluded. See [source policy](SOURCE_MANIFEST.md).

The committed feature import includes repository-name cold recovery, shared-host
profile discovery, portable settings/roles, safe Codex skill installation and durable
feature-to-release handoffs. Existing file/PDF batching and returns, independent
consoles, live follow-ups, peer/recovery/research, model/Fast controls, throughput,
Git completion and recovery storage remain in the reviewed feature inventory.
[Feature guide](FEATURE_GUIDE.md), [session guide](SESSION_FEATURES.md) and
[changelog](CHANGELOG.md) describe behavior; they do not substitute for verification.

## Release evidence requirements

The complete offline corpus passed: 885 discovered test cases, zero failures,
errors or skips, with the combined test-ID multiset verified across four workers.
Source review covers 206 files, 113 runtime inputs and 255 local Markdown links.
The stable MIT VSIX installed into isolated VS Code 1.143.0 directories; its
bundled installer created a fresh isolated runtime with hash-locked wheels,
pip check, imports and one deduplicated fake event/reply. Complete skills/resources
and notices passed isolated installation and idempotence checks. Final artifact
hashes and repeated-build comparison are recorded in release provenance and the
independent publication receipt. Live acceptance remains separate.

| Evidence class | Required check | What it can establish |
| --- | --- | --- |
| Source inspection | Verified maintained pin, per-file provenance, portable examples, exact reviewed inventory | Which committed inputs and local distribution changes are included |
| Backend tests | Complete discovery under the isolated public dependency profile, with private Codex thread identity unset and a short temporary path | Offline behavior, synthetic event/reply, recovery, publication and storage semantics |
| Editor tests | Mocked routing/trust/setup/recovery/skills behavior and public namespace/metadata checks | Frontend logic under the harness, without a live extension host |
| Package tests | Exact source, VSIX and skills inventories, hashes/CRC, resource links, privacy checks and repeat builds | Artifact boundaries, integrity and reproducible local bytes |
| Clean installation | New isolated venv, hash-locked wheels, pip check, SDK/native/WebSocket smoke checks, offline simulation and isolated editor-directory sideload | Supported local installation paths, without global package or account changes |
| Release guards | Matching maintained-source pins, approved ownership/MIT/security/install gates, clean public Git tree and unused version | Eligibility to create a new release; not proof of upload |
| Remote verification | Public commit plus downloaded asset bytes and SHA-256/provenance checks | Actual synchronization and release publication |
| Live acceptance | Explicit real-account, Remote SSH, activation, upgrade/rollback and uninstall/reinstall pilot | Not established by this release audit |

The public profile contains 14 runtime distributions plus a hash-locked pip
bootstrap tool. Version-specific OSV queries dated 2026-10-23 returned no findings
for those 15 selected versions; upstream wheel and notice hashes are recorded in
`requirements/public-profile.json`. This is dated dependency evidence, not a claim
against future vulnerabilities. The older deployed reference lock retains its
historical advisories and is not the public installation default.

The four selected Codex skills include recovery and file-transfer instructions plus
installation and contributor guidance. Their archives contain reviewed entrypoints
and inert references. The standalone installer checks external SHA-256, license,
exact inventory, links, destination ownership and conflicts before an explicit apply.
No script inside the skills archive is executed automatically.

## Historical preparation evidence

Earlier audits and private synchronization runs used older source revisions,
preview identities and running-backend snapshots. Those observations established
specific local comparisons at their recorded time; they do not describe the current
public candidate or certify today's running receiver.

Preparation resolved several concrete blockers: an advisory-affected default
profile, preview/public namespace collisions, filesystem-dependent ZIP metadata,
incomplete skill references and installed VS Code manifest metadata differences.
Earlier isolated dependency installs, smoke checks, simulations and VS Code CLI
sideloads passed for their exact preparation artifacts. A sideload did not activate
an extension or start a relay. This import repeats relevant checks against its own artifacts rather than
inheriting those historical results.

The release process retains exact private diagnostics and completed receipts outside
distributable source. It imports no private Git history and changes no deployed
service, account permission, research job or existing repository visibility.
See [handoff](OPEN_SOURCE_HANDOFF.md), [baseline contract](DEPLOYMENT_PARITY.md)
and [frontend acceptance](FRONTEND_READINESS.md) for the remaining boundaries.
