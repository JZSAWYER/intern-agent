# Source, package and deployment baselines

Current release channel: **private**, version **0.2.5**, maintained source
`c21ea4419894d892c5c518ca9da3cac11dd3db7c`. The owner's private-visibility hold
supersedes earlier public-release prose. Only an explicit later owner request
can authorize going public. Source updates may upload new private releases;
automation must not change repository visibility. See [distribution](DISTRIBUTION.md).

## Current source baseline

The owner-authorized public candidate targets maintained-source commit
`c21ea4419894d892c5c518ca9da3cac11dd3db7c` and stable product version 0.2.5 under
MIT. `source-distribution.json`, `release-runtime.json` and the approved handoff
must record that same maintained-source pin. The per-file inventory distinguishes
committed implementation from portable documentation and distribution changes.
Final manifest refresh and verification are release gates, not implicit acceptance
of every file currently present in a working directory.

The public frontend is `intern-agent.intern-agent`, with 39 `internAgentPublic`
commands. Its version and identity differ deliberately from the maintained 0.8.0
preview. Public frontend metadata and any packaging overlays are recorded by hash;
byte equality to the preview is not a public-product requirement.

No comparison here establishes parity with a currently running receiver. No live
service, private state, installed account binding or other bot is changed by source
export and packaging. Historical comparisons against older deployed snapshots
remain historical observations and are not the baseline for this release.

## What the manifests verify

The source inventory covers every reviewed distributable input, including tests,
release automation and license/notices. The runtime baseline hashes the Python
modules, file-transfer skill and every top-level Markdown guide using the recorded
`deployment-input-order-v3` algorithm: sorted modules, then that skill, then sorted
guides. Separate frontend hashes identify the manifest and JavaScript. Unknown
algorithms, missing/extra/changed/linked modules or guides and inconsistent hashes
fail verification. Older baseline algorithms retain their historical meaning.

Documentation additions and portable edits change the runtime fingerprint even
when backend modules match a maintained revision. The public candidate therefore
has its own complete fingerprint. Never omit guides or weaken checks to make it
appear equal to an older deployed snapshot.

Stable packaging records generated frontend hashes in the VSIX's own baseline.
The release provenance records the source and packaged frontend hashes and any
baseline overlay. Check the public source and the packaged baseline independently;
a source-versus-VSIX check must not silently accept unexplained metadata changes.

## Reproducible checks

From the public source root:

```bash
python3 tools/check_source_candidate.py --release
python3 tools/check_release_runtime.py
python3 tools/build_vsix.py --release --output /path/to/new/intern-agent-0.2.5.vsix
```

The stable builder validates the complete packaged backend and generated frontend.
For a separate package check, verify the artifact checksum, extract it into a new
review directory, then run the bundled helper against its own packaged baseline:

```bash
python3 -m zipfile -e /path/to/intern-agent-0.2.5.vsix /path/to/new/review
python3 /path/to/new/review/extension/backend/tools/check_release_runtime.py --vsix /path/to/intern-agent-0.2.5.vsix
```

VS Code may add its reserved `__metadata` object during installation. The installed
manifest check recognizes only that reserved metadata while preserving functional
manifest and script validation. Source manifests containing installed metadata are
rejected. This does not accept changes to commands, settings, product identity or
JavaScript. See [distribution](DISTRIBUTION.md).

An optional `--deployed /path/to/selected/immutable/release` comparison requires
complete equality with the selected baseline. A mismatch is evidence to investigate,
not permission to drop inputs. Reading an immutable snapshot does not identify which
process is currently active or demonstrate a successful upgrade.

## Product commit and remote verification

Final release builds require a clean Git checkout whose tracked file set equals the
reviewed source inventory. Its origin must be the selected product repository. The
external expected export commit must equal checkout HEAD; it is not stored inside
the same tracked manifest, which would create a circular self-reference.

`release-provenance.json` records `maintained_source_commit` and
`product_source_commit`, channel, public-visibility hold, product version,
dependency profile, overlays and each
artifact hash. For private releases public_source_commit is null. A remote commit match establishes tracked source equality at that
instant. Downloaded asset checks establish uploaded-byte equality. Neither establishes
ignored account/state equality or running-service parity.

Real account/message delivery, Remote SSH activation, installed extension activation,
upgrade/rollback and uninstall/reinstall remain separate acceptance work. macOS and
Claude contributions are outside this Linux/Codex release baseline unless their
own accepted source and verification are included explicitly.
