# Licensing and dependency provenance review

Current release channel: **private**, version **0.2.2**, maintained source
`efa10678221efe96e47047eda9f2cd42c489263b`. The owner's private-visibility hold
supersedes earlier public-release prose. Only an explicit later owner request
can authorize going public. Source updates may upload new private releases;
automation must not change repository visibility. See [distribution](DISTRIBUTION.md).

## Approved MIT source release

The owner explicitly authorized the separate clean Intern Agent product
at `JZSAWYER/intern-agent`, using MIT for maintained source commit
`efa10678221efe96e47047eda9f2cd42c489263b`. [LICENSE](../LICENSE) covers the exported
first-party Python, frontend, guides, tests, templates and skills, including local
release/install improvements. [NOTICE](../NOTICE) records scope and external tools.
The source inventory records every reviewed input, its original hash where
applicable, local changes and approved distribution rights. Enterprise snapshots
and private history are excluded, rather than relicensed.

Repository privacy does not change the license grant.

The stable source manifest and VSIX declare MIT with the separate identity
`intern-agent.intern-agent`. This is a sideloadable extension identity, not a
Marketplace publisher account. No account permission or repository visibility
changes are involved. Contributions retain attribution through the clean public
history; contributors must have rights to submit their changes under MIT.

## Historical dependency inventory

The VSIX contains first-party Python/JavaScript/text inputs, not third-party
Python wheels, Codex, tmux, Git, Node or VS Code binaries. There are no npm package
dependencies. Installed Python metadata is environment evidence, not a locked
or vulnerability-cleared dependency policy.

| Dependency | Observed version and metadata license | Role and remaining work |
| --- | --- | --- |
| lark-oapi | 1.7.1; MIT | Feishu SDK; verify versioned source/license and distribution notices |
| websockets | 15.0.1; BSD-3-Clause | sync Unix app-server connection; retain upstream license if redistributed |
| requests | 2.32.2; Apache-2.0 | Direct Feishu upload import as well as SDK dependency; make direct dependency explicit |
| SDK transitives | requests_toolbelt, pycryptodome, httpx; websockets/requests | Resolve exact versions, hashes and license texts from intended clean install |
| HTTP transitives | charset-normalizer, idna, urllib3, certifi; httpcore/h11; anyio/sniffio as resolved | Complete dependency SBOM/notice review; optional extras are not assumed installed |
| Python/Git/tmux | Python 3.12.4 and Git 2.34.1/tmux 3.2a observed | External runtime tools; do not redistribute binaries in the VSIX |
| Codex/VS Code/Remote SSH | CLI 0.162.0 is documented transport baseline; VS Code ^1.90.0 manifest | Separate installation/account terms; no authentication or editor bundle included |
| Node | v24.20.0 used for harness; default v12.22.9 obsolete for this source | VS Code supplies runtime in production; record test baseline, no npm lock needed |

Upstream source locations for independent review:
[lark SDK](https://github.com/larksuite/oapi-sdk-python),
[websockets 15.0.1 license](https://github.com/python-websockets/websockets/blob/15.0.1/LICENSE),
[requests v2.32.2 license](https://github.com/psf/requests/blob/v2.32.2/LICENSE).
The version-specific SDK license URL was not retrieved successfully during this
audit; local installed metadata is the evidence for its MIT label. The exact 15-distribution installed closure is recorded in
[dependency inventory](DEPENDENCY_INVENTORY.json); it includes typing_extensions
as selected on Python 3.12. Transitive
license decisions and advisories remain pending, rather than silently inferred.

## Release evidence and external dependency notices

The exported source and package include LICENSE/NOTICE. Final builds enforce
approved component rights, public metadata and dependency/installation checks;
source and runtime manifests pin the verified maintained commit. The public
profile below provides exact wheel provenance, notice hashes and dated advisory
screening. Wheels remain external rather than vendored, and their own license
files are retained by pip in the new isolated environment. The product MIT grant
does not relicense those dependencies, Codex or editor/runtime tools.

## Historical 2026-10-15 reference dependency profile

A 15-distribution wheel-hashed reference lock/profile is now prepared under
requirements/. It matches the observed deployment versions; no wheels are
vendored in source/VSIX. Version queries recorded on 2026-10-15 flagged four packages, so this
profile is explicitly reproduction-only and cannot clear the public dependency
security gate. A patched profile, advisories/notices review and compatibility/live
acceptance are still required. See [installation](INSTALL.md) and
[version query](DEPENDENCY_ADVISORIES.json).

## Prepared public profile — 2026-10-23

requirements/public-profile.json records a separate public Linux x86_64 CPython
3.12 closure: 14 application distributions plus pip 26.2.2 in the new venv. Exact
wheel hashes, versioned PyPI provenance, per-version OSV results and upstream
notice-file hashes are recorded there. All selected versions returned no OSV
findings on that date; this is a dated screen, not future vulnerability clearance.
The SDK constrains websockets below 16, so selection respects compatibility rather
than blindly choosing the newest major. Native crypto and SDK/transport imports,
pip check and an offline synthetic round-trip were exercised in an isolated prefix.

Wheels and third-party binaries are external, not embedded into source/VSIX/skills.
Installed wheel notices remain with their distributions. The separate skills
installer retains the approved product notice beside its private receipt. The final source handoff is approved; clean-host/editor/live acceptance has the
separate limits documented in INSTALL.md. The old
reference lock and its recorded advisories remain explicitly historical.

The same 15 selected versions were queried again on 2026-10-24 with no version
advisories returned. This release reuses the exact wheel-hashed lock and repeats
application/SDK installation checks against its own maintained-source pin.
