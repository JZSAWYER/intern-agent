# Intern Agent Control

The paired, owner-only Control group accepts both ordinary language and slash commands. Ordinary text is submitted unchanged to its controller Codex conversation. Commands are handled by the relay, so `/stop` remains immediate and `/list`, `/new`, `/team` and `/resolve` retain their management functions.

## Binding

Pair the group and attach a trusted GitHub workspace first. On the Linux host, run:

```sh
python3 -m intern_personal.feishu_live --state /YOUR/PRIVATE/STATE attach-controller WORKSPACE
```

This reuses the paired group and creates one controller Codex thread, its own worktree, and one permanent branch containing code and validated Markdown history. Its console is named `ia-intern-agent-control-*` on the default tmux socket. No extra group, repository, PR or default-branch update is created. Existing bots keep their own threads and bindings.

Repeating the command returns the ready controller. An incomplete creation is left for inspection; it never automatically launches another thread after an ambiguous failure. A missing binding gives setup feedback. Paused conversations require `/resume`. The route is checked against the current app, paired owner group and exact session before dispatch and delivery. Other workspace operators cannot use the Control group.

## Using both paths

Send `Explain the available bots` to talk to the controller. User text, including whitespace and multiline content, stays intact. Workspace and controller instructions arrive through reference hooks. Hooks and the relay save each turn; assistant text does not rewrite the owner's message.

`/model`, `/reasoning`, `/task start NAME`, `/task status`, `/memory`, `/progress`, `/console`, `/pause`, `/resume` and `/stop` default to the controller. For another bot, retain the existing management syntax, such as `/model SESSION MODEL`, `/task SESSION start NAME`, or `/stop SESSION`. `/list`, `/status` and `/consoles` provide the management overview. `/help` shows the tutorial. Unknown slash commands give command feedback and are not sent to Codex.

`/fast` toggles Control's Fast tier; `/fast on|off|status` sets or inspects it.
Use `/fast SESSION on|off|status` for another bot. See [Fast mode](FAST.md) for
account support, increased usage and verified native-console behavior.

Natural-language requests can ask the controller to queue supported relay commands. Its private reference context describes the local relay CLI. Owner intent still determines authorization: changing app publication, permissions, collaborators or creating repositories requires an explicit request. A question about an action does not silently authorize it.

The controller can work alongside another bot in the same registered workspace only when Git tracking proves different worktrees and branches. Its own turns remain ordered. The thinking reaction, public-prose progress updates, final delivery and Markdown/Git checks follow the same pipeline as other bots. Both command and conversation turns initiated in this group are recorded on the controller's permanent branch. Earlier archived management turns retain their original records.

The console observer recognizes Codex's provenance tags for generated environment and relay reference messages. Those references cannot acknowledge or replace the owner's prompt. Text that merely imitates a reference wrapper is still treated as owner text, and a changed dispatched message fails closed. A saved final reply stays durable through recording failures and is delivered after reconciliation without rerunning Codex.

## Recovery and distribution

The controller route is private runtime state and is not exported with Git history or VSIX files. After a fresh installation or historical recovery, verify the owner pairing and explicitly attach the controller. Unresolved execution and delivery are reconciled without replaying old prompts. Shared examples contain placeholders; credentials and personal account defaults are never bundled.
