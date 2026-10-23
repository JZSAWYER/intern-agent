# Restored personal workflow features

The 0.6.0 local preview restores five useful workflows from the enterprise
implementation. GitHub is the only supported repository host. Each new bot keeps
one permanent branch and its own Codex/tmux console. No automatic PRs, merges,
recursive workers or Codeup integrations are introduced.

## Public progress

`/progress summary` is the default: public assistant commentary and upload
milestones update one text message per turn. `/progress messages` keeps separate
messages, and `/progress off` disables progress. In management, add the conversation
identifier before the mode. The relay omits reasoning, tools, raw function calls
and start acknowledgements. Final answers remain separate.

The progress sender has its own queue. Ambiguous message creation is never
replayed; failed edits cannot create duplicate panels or rerun Codex. `/doctor`
reports recent edit failures. A panel closes after final delivery, independently
of the typing reaction. The typing reaction retains its existing rule: actual
turn completion and delivery/reconciliation. A failed optional panel edit does
not block a final answer or `/stop`.

## Group triggers

The owner can select `/trigger all` (existing default) or `/trigger mentions` in
each workspace group. Management syntax is `/trigger SESSION MODE`.
Mention-only mode fetches this app's bot identity and checks the actual message
mention identifiers. Mentioning a person or another bot does not start Codex.
Missing identity fails closed. Slash controls, including `/help` and `/stop`,
remain available and retain their normal authorization checks. This affects new
group messages, not previously accepted work or explicit local/team submissions.

## Pinned shared skills

The workspace catalog is shared, while enablement is explicit per bot:

```text
/skills add review-v1 https://github.com/YOUR_GITHUB_USERNAME/YOUR_SKILLS_REPOSITORY FULL_40_CHARACTER_COMMIT_SHA skills/review
/skills inspect review-v1
/skills enable review-v1
/skills disable review-v1
/skills list
/skills refresh review-v1
```

In management, use `/skills SESSION ACTION ...`. These are owner-only settings.
The catalog fetches exactly the supplied GitHub commit using the host's configured
GitHub SSH identity. It disables global URL rewrites and credential helpers;
there is no enterprise fallback. A skill directory must contain `SKILL.md` with
plain one-line `name` and `description` frontmatter. Only bounded UTF-8 text
resources are accepted: at most 64 files, 64 KiB each, and 1 MiB total. Symlinks,
submodules, private paths and credential literals are rejected.

Definitions enter the existing reference-context hook, separately from the
owner's unchanged prompt. Resources are exposed through a private, hash-verified
cache. The catalog never installs dependencies or runs downloaded scripts.
Enabled definitions grant no additional authority. Context is bounded; oversized
definitions receive an explicit unavailable/budget notice rather than partial
instructions. This is a relay reference catalog, not a global Codex skill install.

Version pins are immutable. Add a new key for an update, inspect it, then disable
the old version and enable the new one explicitly. Catalog provenance and
per-conversation enablement are stored in validated Git Markdown/recovery data;
credentials, account routes and cache contents are excluded. Recovery never
fetches or executes a skill automatically. Refresh a missing pinned cache
explicitly after recovery.

## Reviewed setup repairs

**Intern Agent: Review Setup Repairs** shows exact actions before an explicit
confirmation. The CLI provides the same workflow:

```sh
python -m intern_personal.feishu_live --state /PRIVATE/DATA/DIRECTORY repair preview
python -m intern_personal.feishu_live --state /PRIVATE/DATA/DIRECTORY repair apply PLAN_ID
python -m intern_personal.feishu_live --state /PRIVATE/DATA/DIRECTORY repair rollback PLAN_ID
```

Repairs normalize owned private configuration-file permissions and reinstall
registered Git guards only when their existing GitHub/branch policy can be
verified. They do not install packages, change credentials, remotes or accounts,
restart services, or replay work. Invalid or missing policies remain manual
diagnostic items. Plans pin the reviewed source and file state; stale plans fail
closed. Private rollback receipts store old modes and guard definitions, never
credential contents. Rollback refuses changed targets instead of overwriting
later edits. Dependency/account/network repairs remain explicit manual steps.

## Task briefs and bounded teams

Select a named task, then record the relevant details:

```text
/task criteria Parser behavior preserved and regression tests pass
/task brief background Why this change matters
/task brief goal Desired result
/task brief scope Files and behavior included or excluded
/task brief verification Commands/checks to run and expected results
/task brief risks Remaining uncertainty
/task report python -m unittest | Passed | Offline fixtures; live editor not checked
```

`/task brief` and `/task report` display the current documents. In management,
use `/task SESSION brief ...` or `/task SESSION report ...`. Briefs and reports
are injected as reference context through the existing hook and are projected
into readable validated Markdown. Reports are attributed and append-only;
they are reported evidence, not independently certified command execution.
Use another report to correct an earlier claim.

New `/team assign` requires acceptance criteria and a verification plan. Owner
acceptance also requires a report for these new handoffs, alongside the existing
observed completion, clean worktree and verified GitHub delivery checks. Existing
assignments remain compatible. Workers remain limited to 1–3 explicit members,
each with its own registered workspace/branch/console. No workers are launched
automatically, and branch delivery never creates a PR or merges anything.

## Distribution status

The local preview bundles these interfaces and backend modules using the existing
portable file allowlist. It excludes credentials, runtime data and personal
account defaults. Clean editor install/upgrade testing, license/provenance review
and a public publisher remain prerequisites for an open-source public release.
