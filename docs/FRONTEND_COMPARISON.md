# Frontend comparison

This compares the current **0.7.0 preview** against the surviving **1.6.1 extension from the 1.6.0 enterprise line**. An exact 1.6.0 package is unavailable. The original manifest, translated command titles and compiled extension were inspected; the legacy system was not restarted. The original declares 52 commands; the current preview declares 31. Command counts describe interface surface, not backend reliability or complete feature parity.

| Area | Original interface | Current interface | Remaining work or recommendation |
| --- | --- | --- | --- |
| Navigation | Dedicated Activity Bar container and setup/agent views | Intern Agent Explorer tree | A dedicated view and clearer empty-state setup page would improve discovery. |
| Setup | Webview checklist, refresh/repair, project selection and client upgrade | Input dialogs, diagnostics and reviewed repairs | Restore a visual checklist. Package installation and account changes should remain explicit. |
| Workspace creation | Workspace management and setup workflow | Attach existing GitHub checkout; new reviewed project-init wizard | The new wizard fills new-repository onboarding. Existing empty repository initialization remains explicit separate work. |
| Agent controls | Start/switch Copilot, Claude and Codex; restart/delete controls | Codex conversations; stop/pause/resume, compact, restart context, archive | Native editor chat and multi-provider support remain absent. Keep archival safer than destructive deletion. |
| Controller | Coordinator creation and team assignment actions | Working owner Control conversation, now named in the tree | A guided controller-binding action and deeper coordinator controls remain useful. |
| Tasks and teams | Task/team nodes and creation, assignment, deletion actions | Task/team visibility with command-based management | Replace slash-command input dialogs with structured assignment, handoff, acceptance and blocker forms. |
| Skills | Skill tree, local/Git sources, personal/repo activation, package updates and Copilot shared skills | Pinned GitHub reference-skill commands | Add a skill catalog tree and enable/disable actions. Executable skill farming and broad global installs should not return automatically. |
| Model and terminal | Agent-provider controls and native editor sessions | Model field, Feishu model commands, separate tmux consoles | Add an editor model/effort picker and a direct terminal action. |
| Trigger/progress | All-message/mention controls and full/summary detail | All/mention triggers and curated public-prose progress | Core controls are restored. Keep function calls and private reasoning out of Feishu progress. |
| Cleanup and localization | Workspace/task/intern/team removal, force deletion, language switch | Conversation archival; English interface | Add reviewed cleanup and translation. Force deletion and turning format checks off should stay unavailable by default. |
| Distribution | Previously installed full extension | Local preview VSIX with Linux Remote SSH backend | Real editor installation/upgrade validation, licensing/provenance, publisher setup and public release remain separate work. |

The highest-value next frontend work is the visual setup checklist, structured task/team forms, a skill catalog and model/terminal controls. GitHub-only hosting, one permanent branch per new bot, unchanged owner prompts, validated history and explicit PR authorization remain the current design.

The new initialization interface uses the same durable relay workflow as Feishu; it does not implement a second orchestration engine. See [project initialization](PROJECT_INIT.md) for its flow, review step and recovery rules.
