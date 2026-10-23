# History and recovery storage

**Purpose:** keep complete conversation Markdown, memory and recoverable Git
history while preventing generated recovery fragments from filling the data
volume. `.intern_workspace` contains current records and a machine-readable
recovery snapshot. Git's object storage and private runtime backups are separate.
For what future records should contain, see [reusable workspace records](RECORD_QUALITY.md).

## Normal publication

Recovery snapshots use compact UTF-8 JSON with the same recovery schema. Every
request, response, event, timestamp and string value survives serialization.
Large snapshots still use verified content-addressed parts, with UTF-8 boundaries
and a 64 MiB total bound. Existing pretty/ASCII snapshots remain readable. A
snapshot remains complete; conversation Markdown stays human-readable.

The publisher holds its existing worktree lock when collecting old fragments:

1. Validate the current manifest, each required part and the complete snapshot
   digest. Keep every currently referenced fragment.
2. Preserve the existing Git-backed pruning rule: a tracked obsolete fragment
   can leave the working tree only when its bytes match both HEAD and index.
   Older commits continue to contain it.
3. For untracked obsolete fragments, validate the filename/content digest and
   generated JSON encoding. Save a private compressed backup, read every byte
   back and verify its SHA-256 before deleting any source fragment.
4. Recheck the manifest, staging, inode and file bytes. A changed or staged file,
   symlink, unknown filename or invalid fragment stays available for review.

Automatic untracked collection handles at most 512 files / 64 MiB per publication.
Optional backup/cleanup failure preserves the files and writes only a private
`STATE/runtime/recovery-storage-CHECKOUT_HASH.json` diagnostic. It does not rerun
Codex, emit repeated Feishu notices or block an otherwise valid Git publication.
Unrelated record validation errors still require their normal review.

## Backups and restoration

Backups live outside repositories at:

```text
STATE/runtime/recovery-backups/CHECKOUT_HASH/INVENTORY_DIGEST.tar.gz
```

Directories are owner-only `0700`; archive files are `0600`. Each archive has
`backup.json`, the current manifest as a witness and the exact original bytes
of the removed fragments. Inventory entries carry their relative paths, sizes
and SHA-256. No backups are uploaded to Git or expired automatically. These are
fragment backups, not substitutes for a full repository or runtime-state backup.

Inspect the archive without extraction, then verify it with
`intern_personal.record_storage.verify_backup(path, inventory)`. Restore selected
fragments first into an independent private review directory. Validate the
appropriate historical manifest before considering changes to an active checkout.
Never overwrite current records merely because an old fragment exists.

## Existing source and inactive checkouts

A source checkout can retain fragments from an earlier publisher even when the
active bot uses another worktree. Such a checkout has no regular publication to
trigger collection. Owner-authorized maintenance may inspect these registered
paths under their worktree/workspace locks, back up and verify eligible fragments,
then remove only the planned files. Tracked deletions must use their existing
registered branch and normal commit/push guards. Preserve staged work and all
non-candidate files. No PR, force push, history rewrite or research restart is
part of storage maintenance.

Measure source checkouts, active bot worktrees and legacy metadata checkouts
separately. Files no longer referenced by the current manifest can still contain
uncommitted historical information: absence from the manifest is insufficient
reason to discard them without a verified backup.

## Verification and limits

Regression checks cover byte-identical backup restoration, current and historical
Git restoration, compact Unicode snapshots, legacy ASCII snapshots, budget and
backup reuse, verification failures, incomplete manifests, links, staged/modified
files and changes after backup. Runtime snapshots and distribution include the
collector and this guide. Other bots receive the same publication behavior.

Cold backups still consume compressed disk space. Cleanup does not shrink older
Git commits, remove Markdown conversations or summarize away owner history.
Changing to a separate incremental archive schema remains a distinct migration;
this optimization preserves existing restore compatibility.
