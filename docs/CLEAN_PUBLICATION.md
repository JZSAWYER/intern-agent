# Clean branches for manual publication review

## Purpose

A bot's permanent branch contains code and recoverable management history.
Uploading that branch does not authorize a merge or deployment. A project that
deploys from its default branch can instead receive a site-only export on a
separate review branch, with the owner creating the PR on GitHub.

## Prerequisites

The owner must authorize the exact project, reviewed source revision and new
destination branch. This is a temporary operator procedure, not a general bot
permission or an implemented Feishu publication command. Ordinary bot uploads
remain restricted to the bot's permanent branch. Direct agent pushes to `main`
or `master`, automatic PR creation, merging and force-pushing are excluded.

## Use

1. Read the bot's actual history and project instructions. Pin the approved
   revision and current remote default-branch revision. A local Git refusal
   does not establish a token or SSH permission failure.
2. Prepare an isolated checkout on the configured data volume. Preserve the
   running bot's checkout, branch, index, origin and installed guard unchanged.
3. Start a new review branch from the pinned default-branch revision. Apply only
   the reviewed project paths or exact binary patch. Do not merge or cherry-pick
   the bot's code/history commits. Keep management records, private attachments,
   preview archives and operational guides out of the exported changes.
4. Install the existing code-only Git guard in the isolated checkout, allowing
   just the exact review branch. Check the outgoing diff and new history,
   credential/path/asset checks, project build and relevant rendered content.
   An inherited default-branch file is not permission to add private artifacts.
5. Recheck remote heads. If the default branch moved or the destination already
   exists, stop for reconciliation; do not overwrite it. Push only the explicit
   new branch ref, with tags/submodule pushes disabled. Verify its remote head
   and that the default branch is unchanged. Close the temporary write route.
6. Give the owner the GitHub comparison URL, for example
   `https://github.com/OWNER/REPOSITORY/compare/main...REVIEW_BRANCH?expand=1`.
   The owner reviews the diff, creates the PR and decides whether to merge it.

## Expected result

The review branch has a single project-only commit directly above the pinned
default branch. The bot continues using its existing permanent branch; no
second agent workspace or ongoing publication permission is introduced.
Management history stays recoverable on the bot branch and does not enter the
website's PR. A branch upload is not a deployed website.

## Verification

The Homepage pilot used an isolated checkout and the normal installed guards.
Its exact reviewed patch contained 25 site files, including font licenses and
five referenced figures. Eight negative checks rejected default/unrelated/tag
refs, a mismatched local ref, branch deletion, management records and private
paths. The export's sole parent is the reviewed default-branch revision, and
the outgoing tree contains no management directory. The original bot guard
was not expanded. Live upload and build receipts belong in private operational
checkpoints; do not infer completion from preparation alone. The pilot's fresh
Jekyll production build, content verifier, 21 local asset references and exact
font/license checks passed. Its clean remote branch was verified, the default
branch was unchanged and the temporary route was closed. No PR was created.

## Limits and recovery

This procedure does not grant other sessions publication access or allow the
same export to be rerun after an uncertain push. Inspect the exact remote head
first; preserve the attempt and receipts. A later revision or another export
needs a new bounded authorization. Keep original prompts and failed-operation
history intact. Do not ask the owner to bypass the guard in another terminal or
push directly to the default branch as a workaround.

Jekyll build checks using available pinned tooling do not prove the complete
GitHub Pages dependency bundle or production deployment. The owner should
review GitHub checks and the website after their eventual merge. See
[turn completion](GIT_COMPLETION.md) and [Git recovery](RECOVERY.md) for the
separate ordinary code/history upload path.

For distributing Intern Agent itself, follow the separately authorized
[product release procedure](PUBLISHED_RELEASE.md). This guide governs scoped
project review branches and does not replace the product release policy.
