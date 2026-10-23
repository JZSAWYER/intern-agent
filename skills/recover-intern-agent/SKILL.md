---
name: recover-intern-agent
description: Reconnect Intern Agent on an existing Linux SSH host or restore its uploaded project histories, task state and preferences from GitHub on a replacement host. Use for Intern Agent installation recovery and workspace migration, not generic Git repair or automatic research continuation.
---

Recover the owner's saved project continuity without rerunning old requests.
Use the installed VSIX backend or a maintained public source checkout. Run its
Python modules from that directory with the chosen Python environment and an
explicit private `--state` directory on a data volume.

## Reconnect first

Run `python3 -m intern_personal.host_profile` before creating another installation.
If it finds a profile on this Linux host, use its state directory and Python.
Inspect `feishu_live --state STATE status` and `dashboard`; reuse existing bot
IDs, chats, threads and worktrees. A second Windows/SSH editor is a client of
the same relay. It does not need another receiver, clone or pairing.

## Restore after host/state loss

1. Check installed prerequisites: Linux, Git, tmux, supported Python, Codex and
   the relay dependencies. Use the release's setup/diagnostics. Do not download
   and execute an old project's scripts as a substitute for the installed tools.
2. Use the VSIX setup for a new private state directory. GitHub PAT input is
   masked and goes through stdin into a `0600` private authentication file.
   Never request it in chat, embed it in a URL/argv, or commit it. Existing host
   SSH authentication can also clone repositories; API operations need API auth.
3. For each requested repository, run:

   ```text
   python3 -m intern_personal.feishu_live --state STATE recover-repository preview OWNER/REPOSITORY
   python3 -m intern_personal.feishu_live --state STATE recover-repository apply PLAN_ID
   python3 -m intern_personal.feishu_live --state STATE recover-repository status
   ```

   Replace uppercase values with the owner's actual local choices. Preview
   clones an independent checkout, discovers the newest published workspace
   archive and pins required branches. Apply imports validated records and
   preferences. Repeat an already applied plan safely; it does not duplicate
   history or replay commands. A changed remote requires a new preview.
4. Restore all desired repositories before starting new work when possible.
   Published turn/revision IDs remain stable. An identity collision from another
   installation requires a separate state profile; never renumber, delete or
   overwrite old history to force an import.
5. Configure missing Codex login and Feishu app credentials locally. A GitHub
   PAT cannot recreate them. Verify app publication, permissions and pairing.
   Before moving reception to a different host, establish that the old receiver
   will not also receive the same app's events. Do not stop unrelated jobs.
6. In the paired owner Control chat, use `/reconnect SESSION` for each desired
   recovered bot, or the VSIX Reconnect Restored Conversation action. It reuses
   that bot's branch and saved task/memory, creating a new chat/thread only after
   total routing loss. `/new WORKSPACE` picks one conversation; it is not a way
   to recover every bot in a multi-bot workspace.
7. Check the exact bot/branch, task selection, notes, model/Fast preferences,
   Git upload and a new message round-trip. Re-enable research schedules, peer
   actions or native terminal attachments only after checking their current
   evidence and host bindings. Archived jobs and requests are not a work queue.

Report the recovered Git commit and remaining account/setup requirements.
Git recovers the last successfully uploaded checkpoint. RAM, unpushed changes,
in-flight computations, expired credentials and lost private Codex transcripts
cannot be reconstructed from Markdown. Distinguish project continuity from
restoring an identical live process. Keep public release source/VSIX/skills
separate from private project records, state and development history.
