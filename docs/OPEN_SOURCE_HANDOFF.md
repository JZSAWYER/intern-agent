# Intern Agent product release handoff

Current release channel: **private**, version **0.2.0**, maintained source
`56edf2640439c25d088ee844063f41faf6a99f70`. The owner's private-visibility hold
supersedes earlier public-release prose. Only an explicit later owner request
can authorize going public. Source updates may upload new private releases;
automation must not change repository visibility. See [distribution](DISTRIBUTION.md).

The owner authorized synchronizing the clean, separate Intern Agent product at
`JZSAWYER/intern-agent`, under MIT, from verified maintained-source commit
`56edf2640439c25d088ee844063f41faf6a99f70`. The current version is 0.2.0, provided that version is unused. Version 0.1.0
is retained; this update remains private under the owner hold. The existing private tracking repository, its
branches, operational records and Git history remain private.

This document defines the reviewed release handoff. Offline and isolated install
checks are recorded in the audit; the independently verified product commit,
release URL and uploaded asset hashes belong in the completed release receipt.

## Product contents

The public frontend identity is `intern-agent.intern-agent`, version 0.2.0, with
39 commands in the `internAgentPublic` namespace. The maintained development
extension's 0.8.0 preview identity is separate. Public settings, views and context
values use the public namespace; packaging records generated frontend metadata
and hashes explicitly.

The source includes committed recovery by repository name, shared SSH host-profile
discovery, portable settings and roles, safe skill installation and durable release
handoffs, alongside the existing conversation, file/PDF, peer, recovery, research,
model/Fast, quota, Git completion and reusable-record features. See the complete
[feature guide](FEATURE_GUIDE.md) and [session guide](SESSION_FEATURES.md).

Release assets comprise the stable VSIX, exact source archive, complete skills
archive, standalone skills installer, provenance and SHA-256 checksums. The four
skills are `recover-intern-agent`, `feishu-file-transfer`, `install-on-linux` and
`collaborate-on-source`; their reviewed references must be included. Skills alone
do not install dependencies, authenticate an account or start a relay.

## Verification and publication sequence

1. Reconcile committed inputs from the verified pin, preserving separately
   attributed distribution tooling and portable documentation. Never import
   uncommitted source, private Git objects, enterprise reference snapshots,
   operational state or account bindings.
2. Refresh the source and runtime inventories to the same maintained-source pin.
   Check source ownership, MIT metadata, third-party notices, dependency security
   and an isolated clean installation. Approval flags record completed reviews;
   a successful build does not justify clearing an unexplained failure.
3. Complete backend, editor, recovery, skill-resource and packaging checks. Build
   deterministic assets with new filenames and a fresh immutable version. The
   patched public dependency profile uses a separate virtual environment; the
   historical deployed dependency lock remains explicit reproduction only.
4. Update the existing private product repository from its reviewed source tree.
   The first export starts with fresh Git history. Verify the exact product commit
   and tracked inventory, then build the final assets with that commit recorded.
5. Upload a new release without replacing existing assets. Independently verify
   remote source and every uploaded artifact before reporting publication.

[Distribution](DISTRIBUTION.md) documents automation and
[source policy](SOURCE_MANIFEST.md) separates source, VSIX and skills inventories.
The release workflow requires the exact product repository, approved source pin,
version and review gates. Initial manual publication and CI must use one publisher
for a version; later verified updates use fresh versions.

## Installation and acceptance limits

[Installation](INSTALL.md) covers a new Linux host and independent account setup;
[portable recovery](PORTABLE_RECOVERY.md) covers reconnection and state loss.
Every user supplies their own GitHub, Codex and Feishu authentication. Shared-host
profile discovery must reuse the existing relay rather than start another receiver.
Cold recovery restores uploaded project continuity; it cannot recover lost RAM,
unpushed work, expired credentials or private transcripts that were not backed up.

Final-candidate test results remain separate from historical preparation results.
An isolated editor-directory installation proves package acceptance, not extension
activation. No parity with a currently running receiver, real account/message
round-trip, Remote SSH activation, upgrade/rollback or uninstall/reinstall acceptance
is claimed here. No production relay or research action is part of publication.

Continue maintenance on separate contributor branches using the
[contributor guide](CONTRIBUTING.md). Bot-created PRs, merges, Marketplace publication,
existing-repository visibility changes and account permission changes require their
own explicit authorization. Private development chats remain private coordination
channels; public installation does not depend on access to them.
