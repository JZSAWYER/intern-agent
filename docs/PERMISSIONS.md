# Codex execution permissions

## Select YOLO for a personal relay

**Purpose:** run owner-requested shell commands and file changes without
repeated `/approve` messages. Codex full access combines `approval_policy =
"never"` with `sandbox_mode = "danger-full-access"`.
See [the official OpenAI approval documentation](https://learn.chatgpt.com/docs/agent-approvals-security).

**Prerequisites:** the owner explicitly selects this execution mode. Managed
client restrictions remain enforced separately from local configuration.

**Use:** for an existing private relay profile, set these fields in its protected
`live.json` and activate the reviewed backend:

```json
{
  "sandbox": "danger-full-access",
  "approval_policy": "never",
  "approvals_reviewer": "user"
}
```

These fields supplement the complete existing profile; do not replace the rest
of the file. For a new profile, `feishu_live init --yolo` selects this mode. The
shared installer continues to offer the standard mode unless the owner opts in.

For ordinary Codex CLI sessions, the equivalent top-level `config.toml` settings
are:

```toml
approval_policy = "never"
sandbox_mode = "danger-full-access"
```

**Expected result:** the relay applies the selected policy when starting,
resuming and dispatching a managed conversation. Isolated Codex homes and new
backend launches use the same policy. Existing threads can adopt it through
`thread/settings/update`, with an exact-thread settings acknowledgement. Remote
tmux views inherit the backend policy; they receive no unsupported CLI overrides.
If an execution approval still arrives during an explicitly selected YOLO turn,
the relay accepts a command/file request for that conversation without sending
an approval code. A stopped turn is not approved.

**Verification:** policy tests cover fresh and previously constructed hosts,
unchanged owner text and model, isolated-home configuration, safe TOML updates,
command/file decisions, stopped turns, foreign-thread rejection and genuine
clarification questions. Existing account credentials and hooks are preserved.

**Limits:** thread settings apply to subsequent turns. An already captured step
or pending approval retains its earlier settings; avoid restarting an active
conversation solely to change permissions. This active Debug Bot's host-managed
permissions cannot be removed by editing the relay or user config. The owner
must change that client's permissions control when the turn is finished, if
the client permits full access. Other managed policy restrictions still apply.

YOLO changes execution approvals, not task ownership or project workflow. GitHub
only, workspace/branch binding, code and history validation, private credential
handling and no PR without explicit owner instruction remain in force. Actual
questions about missing information still use `/answer`; the relay does not
invent answers. Connector-specific controls remain separate from shell policy.
