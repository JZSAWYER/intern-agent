# Source repository and archive policy

Current release channel: **private**, version **0.2.6**, maintained source
`c21ea4419894d892c5c518ca9da3cac11dd3db7c`. The owner's private-visibility hold
supersedes earlier public-release prose. Only an explicit later owner request
can authorize going public. Source updates may upload new private releases;
automation must not change repository visibility. See [distribution](DISTRIBUTION.md).

## Current release scope

The owner authorized the separate MIT Intern Agent 0.2.6 export from maintained
commit `c21ea4419894d892c5c518ca9da3cac11dd3db7c`. The existing private tracking
repository and Git history remain private. The public product uses
`intern-agent.intern-agent` with 39 `internAgentPublic` commands; it does not reuse
the maintained development preview's identity. Final checks and remote publication
must be recorded separately; this policy is not an upload receipt.

## Independent inventories

`source-distribution.json` lists exact public source paths, SHA-256 hashes,
per-file provenance and distribution rights. Its own bytes are included without a
circular self-hash. `tools/check_source_candidate.py` rejects changed, unlisted,
linked, private or unreviewed inputs. Update the inventory after a real content
review, not by automatically treating new files as approved.

`distribution.json` separately selects backend assets for `extension/backend/`.
The VSIX builder adds the editable frontend, generated XML and extension license
metadata. Source tests, `.github` workflows and development-only release tools do
not ship in the VSIX. The packaged runtime baseline records its generated frontend
metadata explicitly. The skills archive has its own exact inventory and includes
all reviewed resources beneath the four configured skill directories.

| Input | Public source archive | Stable VSIX | Reason |
| --- | --- | --- | --- |
| `intern_personal/*.py` | All reviewed maintained modules | Backend | Runtime, lifecycle, recovery and validation |
| `extension/{extension.js,package.json,README.md}` | Editable public frontend | Extension root | Maintained JavaScript and stable MIT product metadata |
| Root `README.md`, `docs/*.md` | Reviewed portable guides | Backend documentation | Installation, operation, recovery and contribution |
| `CONTRIBUTING.md`, tests and release-only tooling | Include | Exclude | Contributor verification and reproducible release maintenance |
| Placeholder templates | Include | Backend | No real account configuration or machine bindings |
| Four selected skill directories and inert references | Include | Allowlisted entrypoints/references | Recovery, file transfer, installation and collaboration |
| Public dependency lock/profile, runtime installer, installation doctor, parity helper and safe skills installer | Include | Backend | Isolated supported installation and verification |
| Historical deployed reference lock/profile | Include with explicit scope | Backend reference inputs | Optional reproduction, never the public installation default |
| Product/source/runtime/backend manifests and `.gitignore` | Include reviewed inventory/configuration | Only backend/product/runtime manifests selected by allowlist | Separate archive and runtime boundaries |
| `.github/workflows/release.yml`, `tools/build_release.py` | Include | Exclude | Guarded public source-to-release automation |
| `LICENSE`, `NOTICE`, reviewed dependency observations | Include | Required license/notices and selected observations | MIT source grant plus separate third-party terms |
| Downloaded wheels, VSIX/ZIP outputs, legacy architecture HTML and snapshot capture tooling | Exclude | Exclude | External dependencies/build artifacts or obsolete reference material |

The skills ZIP contains no automatically executed installer. The standalone
`intern-agent-VERSION-install-skills.py` release asset is byte-identical to its
reviewed source file. It checks the bundle's external SHA-256, exact resources,
MIT notice, ownership, links and conflicts before explicit apply. User-modified
skills are preserved.

## Always excluded

Exclude `.git/`, private refs and historical Git objects; `.intern_workspace/`
conversation/history/recovery records; runtime, reports and reference snapshots;
private configuration/bindings and release policy; queue databases/WAL, logs,
transcripts and health/status records; credentials/tokens/SSH keys; personal
account/chat/native/thread/pairing identifiers; Codex homes and rollouts; sockets,
tmux state, editor/machine settings, caches, node_modules and personal checkouts.
Build outputs and private review diagnostics are not source archive inputs.

Synthetic fixtures are test data, not personal bindings. Any credential-shaped
fixture exception is explicit in the source manifest and pinned by the reviewed
file hash. Other plaintext credential literals fail review. Symlinks, linked
ancestors, traversal and paths outside the candidate are rejected.

An ignored private file beside source is never an archive input. `.gitignore`
cannot remove already tracked records or historical blobs. Never publish the
private tracking repository by a visibility toggle, all-branch push or history
merge. The first public repository starts from the reviewed tree with fresh
history; later public commits contain only that public development history.

## Review and build commands

From the public source root:

```bash
python3 tools/check_source_candidate.py --release
python3 tools/check_distribution.py
python3 tools/check_release_runtime.py
python3 tools/build_vsix.py --release --output /path/to/new/intern-agent-0.2.6.vsix
python3 tools/build_release.py --expected-export-commit PUBLIC_COMMIT --output-dir /path/to/new/release-assets
```

Replace `PUBLIC_COMMIT` with the full clean public checkout commit. Output paths
must be new and outside source where required. Final release builds require
matching source/runtime/handoff pins, ownership/license/dependency/install gates,
MIT frontend metadata, the selected public origin and a tracked Git file set
identical to the reviewed source payload. A successful local build still does
not prove remote publication.

Review source, VSIX and skills boundaries independently: exact entries, CRC,
source and artifact hashes, privacy patterns, referenced resources, metadata and
license/notices. The release receipt records both maintained-source and public-
source commits plus asset checksums. Download and verify uploaded bytes before
claiming synchronization. See [distribution](DISTRIBUTION.md) and
[baseline details](DEPLOYMENT_PARITY.md).
