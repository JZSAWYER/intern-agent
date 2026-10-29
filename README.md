# Intern Agent

Work on GitHub projects from Feishu, with persistent Codex sessions and controls
in VS Code. Send a task, follow its progress, answer questions and receive the
result in the project's chat. Reopen the same conversation in the editor when
you want to work directly.

Intern Agent includes an installable VS Code extension (VSIX), a Python relay,
maintained source and optional Codex skills. Each installation uses its own
accounts and private state.

**Current release:** [v0.2.6](https://github.com/JZSAWYER/intern-agent/releases/tag/v0.2.6).
The repository and downloads are currently private; access is required.

## What you can do

- **Keep project conversations running.** Each bot has its own Git worktree,
  permanent branch, Codex session and Feishu chat. Named tasks stay on that branch.
- **Work from chat or the editor.** Follow progress, send follow-ups during a
  turn, open the conversation's console and inspect status in VS Code.
- **Exchange files.** Send files and PDFs as a batch with your next message,
  and request generated files back in Feishu.
- **Guide longer work.** Track persistent goals, answer question cards, and
  manage model, reasoning, Fast and quota settings.
- **Coordinate bots.** Use peer requests and the documented recovery and
  research workflows while keeping each conversation separate.
- **Reconnect or recover.** Reuse an existing Linux host profile, or restore
  uploaded project records from GitHub on a replacement host.

See the [feature guide](docs/FEATURE_GUIDE.md) for commands and examples.

## Get started

The supported installation host is **Linux x86_64 with Python 3.12**. You can
connect from a Windows or macOS VS Code client through Remote SSH to that Linux
host. Native macOS and Claude Code support are outside this release's accepted
installation profile.

Before setup, you need:

- Python 3.12 with `venv` and `ensurepip`, Git and tmux on the Linux host.
- A VS Code client, with Remote SSH when connecting to a remote Linux host.
- A separately installed, authenticated Codex CLI.
- Your own GitHub access and a self-built Feishu app with the required bot,
  event and chat permissions.

Follow the [installation guide](docs/INSTALL.md) for the commands and account
setup. The sequence is:

1. **Download and verify the release.** Get the matching source ZIP, VSIX,
   provenance and checksums. Verify `SHA256SUMS`, then extract the source.
2. **Check and install the Python runtime.** Run the installation doctor, review
   the plan and use the optional download step to create an isolated environment
   without system pip. Keep private state
   outside the installation directory.
3. **Install the VSIX.** In VS Code, use **Install from VSIX** on the Linux
   workspace host. Select the installed runtime's Python executable.
4. **Set up the relay.** Run **Intern Agent: Set Up Personal Relay**, select or
   create your host profile, and configure your own accounts through the setup UI.
5. **Pair and test.** Follow [live setup](docs/LIVE.md) to pair your Feishu group,
   attach a GitHub project and verify a real message, reply and Git upload.

The stable extension ID is `intern-agent.intern-agent`; its settings use
`internAgentPublic`. The development preview has a separate identity.

Package installation and offline checks have been verified in isolated Linux
environments. New-account setup, Remote SSH activation and production upgrades
still need acceptance on the chosen installation; see
[installation acceptance](docs/INSTALL.md#acceptance-upgrade-and-uninstall).

### Try the offline demo

From the extracted source directory, run:

```bash
TMPDIR=/tmp python3 -m intern_personal simulate
```

This sends a synthetic event through a fake agent and reply adapter, then removes
its temporary state. It needs no Feishu account and starts no relay or Codex session.

### Optional Codex skills

The release includes skills for installation, recovery, file transfer and source
collaboration. Follow [skills installation](docs/INSTALL.md#install-the-codex-skills-bundle)
to inspect and install the complete bundle. Each skill includes its required
reference files; installing skills does not configure accounts or start the relay.
Verify the installed files, confirm the skill appears in your Codex client, then
explicitly invoke `$install-on-linux` with the selected product root. The guide
separates file verification, discovery and actual execution evidence.

## Working on a project

1. Open a project's bound Feishu conversation and describe the task in your own words.
2. Follow progress, answer any questions and send follow-ups in the same chat.
   Use the VS Code dashboard or console when you need direct access.
3. Review the result and uploaded changes. Final completion waits for confirmed
   Git publication and reply delivery; returned files have separate delivery receipts.

For another computer or a lost installation, use
[portable recovery](docs/PORTABLE_RECOVERY.md). Recovery restores uploaded
records; new account credentials and unpushed local work need separate handling.

## Find the right guide

| I want to… | Guide |
| --- | --- |
| Install on my own machine | [Installation](docs/INSTALL.md) |
| Configure Feishu and connect a project | [Live setup](docs/LIVE.md) · [New projects](docs/PROJECT_INIT.md) |
| Use the dashboard, tasks and terminals | [Editor workflow](docs/EDITOR_TEAMS.md) · [Conversations](docs/CONVERSATIONS.md) |
| Use goals, question cards or attachments | [Goals and questions](docs/GOALS_AND_QUESTIONS.md) · [File transfer](docs/FILE_TRANSFER.md) |
| Reconnect or troubleshoot saved work | [Portable recovery](docs/PORTABLE_RECOVERY.md) · [Recovery](docs/RECOVERY.md) |
| Understand the implementation | [Architecture](docs/ARCHITECTURE.md) |
| Check release contents and provenance | [Distribution](docs/DISTRIBUTION.md) · [Release baseline](docs/DEPLOYMENT_PARITY.md) |
| See recent changes | [Changelog](docs/CHANGELOG.md) |

## Contribute

Use a separate checkout and your own development branch. The owner and each
collaborator work on their own branches and propose changes through pull requests
to the integration branch. Contributors without write access can use a fork.
Runtime bots keep their registered permanent branches.

Start with [CONTRIBUTING.md](CONTRIBUTING.md) for the branch workflow, relevant
checks and review expectations. Coding agents can also use the
[source collaboration skill](skills/collaborate-on-source/SKILL.md).

## License

First-party source, frontend, documentation and skills use the [MIT license](LICENSE).
External dependencies and tools retain their own terms; see [NOTICE](NOTICE) and
[licensing and provenance](docs/LICENSING_PROVENANCE.md).
