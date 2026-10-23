# Initialize a new private project

Use **Intern Agent: Initialize New Private Project** in VS Code, or `/init` in the owner Control group. This is project onboarding; the local `init --github-owner ...` command still configures the relay itself.

The feature creates a GitHub repository that does not exist yet. Existing repositories, including empty ones, use the explicit attachment workflow and are never silently adopted. The GitHub owner comes from private relay configuration; shared source and examples contain no personal default.

```text
/init my-project
/init my-workspace repository-name
/init apply PLAN_ID
/init status PLAN_ID
/init status
```

The first command creates a review plan, without creating a repository, pushing Git, making project files, opening a group or launching Codex. Applying that plan authorizes its named private repository, one empty initial `main` commit, and the project's branch, group and console.

```mermaid
flowchart LR
    UI[VS Code wizard or Feishu /init] --> P[Review private repo and storage]
    P --> A[Owner applies plan]
    A --> G[Create GitHub repository]
    G --> M[Empty initial main commit]
    M --> W[Register workspace]
    W --> B[One bot code/history branch]
    B --> C[Dedicated Codex and tmux console]
    C --> F[Owner-only project Feishu group]
```

`main` is an empty bootstrap baseline, created once for this new repository. The bot works on one permanent `intern-agent/...` branch containing code and validated Markdown. Named tasks stay on that branch. The workflow creates no PR, merge, force push, public repository, collaborator grant or Feishu app change.

## Frontend

The Explorer dashboard has an **Initialize New Private Project** action. Enter workspace and repository names, inspect the destination, storage path and five steps, then select **Apply reviewed plan**. Dismissing that dialog leaves the preview unchanged.

The **Project initialization** tree shows the phase, private repository, storage and any blocker. Context actions show current status or review the same plan again. On completion, **Open Initialized Bot Workspace** opens the bot's worktree and permanent branch in a new VS Code window. It does not open the read-only source checkout on `main`. The completed row also opens the project Feishu chat. Mutating actions require workspace trust; dashboard refresh performs no creation.

The controller is labelled **Intern Agent Control** rather than an opaque conversation ID. Other conversation and team actions retain their existing behavior. The preview still needs to be installed as a VSIX on the Linux Remote SSH host to appear in the editor.

## Authentication and storage

No GitHub CLI or additional package is installed. The API client uses the host's existing builtin Git `store`/`cache` credential helper, or `GH_TOKEN`/`GITHUB_TOKEN` already present in its process environment. It rejects arbitrary credential-helper scripts. Tokens stay in memory and are not included in command arguments, project files, output, Git or VSIX artifacts. Standard isolated relay launch does not inherit arbitrary token environment variables; configure the host helper for that launcher, or explicitly launch the receiver in the securely provisioned environment.

SSH authentication uploads Git, but repository creation also needs an authenticated GitHub API account. The API user must match the configured personal owner. Organization creation is outside this flow. The feature uses GitHub's authenticated repository API with an explicitly pinned, supported version. See [GitHub repository API](https://docs.github.com/en/rest/repos/repos?apiVersion=2022-11-28) and [supported API versions](https://docs.github.com/en/rest/about-the-rest-api/api-versions). Actual creation permission is determined when the owner applies the plan; a successful account read does not prove write permission.

Source storage is under the private relay data volume at `runtime/projects/WORKSPACE/source`. The bot worktree is under `runtime/worktrees`; its Git guards are installed before publication. Initialization journals and operation markers remain private runtime data. Credentials are never copied into them.

Generated intern identifiers reserve space for their unique suffix, so the existing maximum workspace-name length remains compatible with Markdown recovery validation. Existing intern identifiers are preserved.

## Retries and history

Every step is saved before its external action. A successful retry verifies the repository's immutable identity, private visibility and operation marker. A timed-out creation request is never blindly posted again. A definite rejected request can be retried after authorization is repaired. The initial commit is reused after a push disconnect, and an existing `main` with other content is never overwritten.

Creation of an incomplete bot conversation requires inspection of its existing session. The workflow does not create a duplicate group or thread to work around an ambiguous response. `/init status PLAN_ID` reports the last saved stage and blocker. Applying an already complete plan returns the existing project.

An established controller records commands on its existing history branch. For first-project onboarding without a controller archive, original init preview/apply commands and their receive/completion receipts are retained privately and projected into validated Markdown on the first project bot branch. Original bodies and timestamps are preserved. Initialization does not rewrite the owner's prompt or rerun an agent to retry Git.

## Validation limits

The implementation is tested with disposable Git repositories, simulated GitHub/Feishu/Codex adapters, real read-only host GitHub authentication, frontend command tests and VSIX packaging checks. Creating an actual new repository requires the owner to choose its name and apply its concrete plan. This development milestone does not create a demonstration repository or install/publish the extension automatically.
