"""Read-only diagnostics/dashboard and explicit conversation lifecycle controls."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import time

from .core import Invalid
from .live_state import private_json


def dashboard(state):
    from .auto_recovery import status as recovery_status
    from .research import listing as research_listing
    controller=state.controller_session()
    rows=[]
    for session in state.sessions():
        intern=state.db.execute('SELECT * FROM tracked_interns WHERE session=?',(session['id'],)).fetchone()
        task=state.ledger.selection(session['id'])
        latest=state.db.execute('SELECT r.activity,r.sync,e.state,e.receipt,o.state AS delivery FROM turn_records r JOIN events e ON e.seq=r.seq LEFT JOIN reply_outbox o ON o.seq=e.seq WHERE e.session=? AND e.role=\'agent\' ORDER BY e.seq DESC LIMIT 1',(session['id'],)).fetchone()
        from .feishu_live import chat_link
        from .session_hosts import console
        rows.append(dict(id=session['id'],workspace=session['workspace'],state=session['state'],name='Intern Agent Control' if controller and controller['id']==session['id'] else None,mode='working' if task else 'idle',
            activity=latest['activity'] if latest else 'waiting',
            task=dict(id=task['id'],name=task['name'],state=task['state']) if task else None,
            branch=intern['code_branch'] if intern else None,branch_layout=intern['metadata_mode'] if intern else None,sync=latest['sync'] if latest else 'synced',
            console=console(state,session),model=session.get('model'),chat_url=chat_link(session['chat_id']) if session['chat_id'] else None,
            recovery=recovery_status(state,session['id']),research_watches=research_listing(state,session),reply_delivery=('delivered' if latest['receipt'] else latest['delivery'] or 'not ready') if latest else 'not ready',
            file_transfers=dict(state.db.execute('SELECT state,COUNT(*) FROM file_transfers WHERE session=? GROUP BY state',(session['id'],)).fetchall()),
            peer_messages=dict(state.db.execute('SELECT state,COUNT(*) FROM peer_mail WHERE sender=? OR recipient=? GROUP BY state',(session['id'],session['id'])).fetchall()),
            attached_terminal=bool(state.db.execute('SELECT 1 FROM terminal_attachments WHERE session=?',(session['id'],)).fetchone())))
    from .teams import snapshot
    from .project_init import jobs
    from .release_sync import status as release_status
    return dict(schema='intern-agent.dashboard.v1',release_jobs=release_status(state),initializations=jobs(state),workspaces=[dict(id=r['workspace'],repo=r['repo_url'],management_branch=r['metadata_branch'] or None,branch_layout=r['metadata_mode']) for r in state.db.execute('SELECT * FROM tracked_workspaces')],
        conversations=rows,teams=snapshot(state)['teams'],tasks=[dict(r) for r in state.db.execute('SELECT id,workspace,name,state,code_branch FROM named_tasks')],
        uploads_pending=state.db.execute('SELECT COUNT(*) FROM tracking_pending').fetchone()[0]+state.db.execute('SELECT COUNT(*) FROM bot_tracking_pending').fetchone()[0],queue=dict(state.db.execute('SELECT state,COUNT(*) FROM events GROUP BY state').fetchall()))


def diagnose(state):
    checks=[]
    def add(name,passed,next_action,blocking=True):
        checks.append(dict(id=name,passed=bool(passed),blocking=bool(blocking and not passed),next_action='' if passed else next_action))
    add('platform',sys.platform=='linux','Use a Linux host, including VS Code Remote SSH.')
    add('python',sys.version_info>=(3,10),'Choose Python 3.10 or newer.')
    for name in ['git','tmux']:add(name,bool(shutil.which(name)),'Install '+name+' on the chosen host.')
    for name in ['lark_oapi','websockets']:add(name,importlib.util.find_spec(name) is not None,'Install the documented '+name+' dependency in the selected Python environment.')
    try:
        config=state.config();add('configuration',True,'')
        executable=config['codex_command'][0]
        add('codex_executable',Path(executable).is_file() and os.access(executable,os.X_OK),'Choose an executable Codex binary on this host.')
        add('codex_login',(Path(config['codex_auth_home'])/'auth.json').is_file(),'Sign into Codex locally on this host.')
    except (OSError,ValueError,Invalid):add('configuration',False,'Run Set Up Personal Relay; configuration must remain private.')
    try:state.credentials();add('credentials',True,'')
    except (OSError,ValueError,Invalid):add('credentials',False,'Store the new app credentials using Set Feishu Credentials.')
    add('owner_pairing',bool(state.identity()['owner']),'Pair the management group with your one-time command.')
    controller=state.controller_session()
    add('controller_conversation',bool(controller and controller['state']=='ready' and controller['thread_id']),
        'Attach the paired Control group to a tracked GitHub workspace with attach-controller WORKSPACE. Slash commands work before binding.',False)
    try:
        h=private_json(state.root/'health.json')
        connected=h['receiver_connected'] and 0<=time.time()-h['updated_at']<20
        add('receiver',connected,'Start the relay; check network and app credentials.')
        add('terminal_observation',connected and not h.get('terminal_error_type'),
            'Inspect the attached terminal binding and transcript; do not launch a replacement for the existing conversation.')
        add('console_observation',connected and not h.get('console_error_type'),
            'Use /consoles to locate the affected Codex view and inspect its saved transcript before retrying.')
        context=h.get('context_refresh')
        add('feature_context',connected and isinstance(context,dict) and context.get('failed')==0,
            'Inspect the registered workspace and private hook context; a startup reference refresh failed or has not been observed. No prompt needs to be replayed.',False)
        add('automatic_recovery_monitor',connected and not h.get('recovery_error_type'),
            'Inspect /recovery status and /recovery inbox; the monitor never replays an uncertain request.',False)
        from .peer_mail import enabled as peer_enabled
        peer_health=h.get('peer_health')
        add('peer_delivery',not peer_enabled(state) or connected and h.get('peer_worker_alive')
            and isinstance(peer_health,dict) and peer_health.get('errors')==0
            and 0<=time.time()-peer_health.get('updated_at',0)<20,
            'Inspect /mail status for the waiting reason and peer worker health. Stored is not Codex receipt; preserve uncertain execution and never replay a prompt.',False)
        from .release_sync import policy as release_policy
        releases=release_policy(state)
        add('release_sync',not releases or not releases['enabled'] or connected and h.get('release_worker_alive') and not h.get('release_error_type'),
            'Inspect the configured release operator and dashboard release jobs. A queued handoff does not prove that a VSIX was published.',False)
        add('thinking_reaction',not h.get('typing_error_type') and bool(state.db.execute('SELECT 1 FROM message_reactions LIMIT 1').fetchone()),'Verify im:message.reactions:write_only and an observed reaction; app publication may be needed.',False)
    except (OSError,ValueError,Invalid,KeyError):add('receiver',False,'Start the relay and check status.')
    add('tracked_workspace',bool(state.db.execute('SELECT 1 FROM tracked_workspaces WHERE published=1').fetchone()),'Attach a clean GitHub workspace; each bot publishes its own branch.')
    delivered=state.db.execute("SELECT 1 FROM turn_records r JOIN events e ON e.seq=r.seq WHERE r.source='feishu' AND e.role='agent' AND e.state='complete'").fetchone()
    add('message_round_trip',bool(delivered),'Send a text message in a ready workspace chat and verify its reply.',False)
    add('pending_uploads',not state.db.execute('SELECT 1 FROM tracking_pending UNION ALL SELECT 1 FROM bot_tracking_pending').fetchone(),'Inspect status and repair the blocked Git operation; saved prompts are not replayed.',False)
    add('delivery_queue',not state.db.execute("""SELECT 1 FROM events e WHERE state IN ('record_blocked','uncertain_execution','uncertain_delivery','sync_pending')
        AND NOT (e.role='control' AND e.state='uncertain_execution' AND EXISTS(SELECT 1 FROM fast_controls f WHERE f.seq=e.seq AND f.session=e.session AND f.phase='verified')) LIMIT 1""").fetchone(),
        'An unresolved saved turn can block later messages. Inspect /status, repair the history problem, then /resolve TASK retry for record_blocked. Never replay executed prompts.')
    add('editable_progress',not state.db.execute("SELECT 1 FROM progress_panels WHERE error_type IS NOT NULL AND updated_at>?",(time.time()-86400,)).fetchone(),
        'Progress editing failed. Check message-update permission or choose /progress messages; final replies and /stop remain independent.',False)
    add('file_delivery',not state.db.execute("SELECT 1 FROM file_transfers WHERE state IN ('failed','uncertain') LIMIT 1").fetchone(),
        'Inspect /files and /files permissions in the affected chat. Repair and publish missing app permissions, or fix transport, then retry the saved transfer. Check for an already delivered file if uncertain; never rerun Codex to resend it.',False)
    try:
        from .git_hooks import verified_path
        bindings=state.db.execute('SELECT * FROM git_hook_bindings').fetchall()
        for b in bindings:verified_path(b['config_path'],b['directory'])
        add('git_guards',bool(bindings),'Install reviewed guards for each registered worktree.')
    except (OSError,ValueError,Invalid):add('git_guards',False,'A registered Git guard changed or is unavailable. Reinstall it only from the reviewed release.')
    return dict(schema='intern-agent.diagnostics.v1',ok=not any(c['blocking'] for c in checks),checks=checks,
        account_configuration_verified=False,automatic_repairs=False)


def session_command(relay,session,action):
    if not session:raise Invalid('Choose a workspace conversation')
    state=relay.state
    if state.ledger.busy(session['id']):raise Invalid('Stop or finish the active turn before changing the conversation')
    if state.db.execute("""SELECT 1 FROM events e WHERE session=? AND state IN ('queued','uncertain_execution','uncertain_delivery','record_blocked','sync_pending')
        AND NOT (e.role='control' AND e.state='uncertain_execution' AND EXISTS(SELECT 1 FROM fast_controls f WHERE f.seq=e.seq AND f.session=e.session AND f.phase='verified'))""",(session['id'],)).fetchone():
        raise Invalid('Resolve queued or uncertain work first')
    if relay.terminal.binding(session):raise Invalid('Use the original terminal for compaction or conversation reset; its exact attachment is preserved')
    observer=getattr(relay.host,'observer',None)
    if observer and observer.binding(session):
        snapshot=observer.poll(session)
        if snapshot.get('active') or snapshot.get('pending'):raise Invalid('Finish or stop the active Codex console turn first')
    if action=='compact':
        relay.host.ensure_started(session)
        connection=relay.host.connect(session=session)
        try:
            params=relay.host.thread_params(session);params.update(threadId=session['thread_id'],excludeTurns=True)
            connection.request('thread/resume',params)
            connection.request('thread/compact/start',{'threadId':session['thread_id']})
            deadline=time.monotonic()+120
            observed=False
            while time.monotonic()<deadline:
                try:notice=connection.notification(min(15,deadline-time.monotonic()))
                except TimeoutError:continue
                p=notice.get('params',{})
                if p.get('threadId')!=session['thread_id']:continue
                if notice.get('method')=='item/completed' and p.get('item',{}).get('type')=='contextCompaction':observed=True
                if notice.get('method')=='turn/completed':
                    if p.get('turn',{}).get('status')!='completed':raise RuntimeError('Codex compaction did not complete')
                    observed=True;break
            if not observed:raise RuntimeError('Compaction completion was not observed; inspect the session before retrying')
        finally:connection.close()
        return 'Codex compaction completed. Git history and named tasks are preserved.'
    if action in {'clear','restart'}:
        # Initialize first; failed initialization must leave the saved thread intact.
        previous=session['thread_id']
        try:relay.host.create_thread(session)
        except BaseException:state.set_session(session['id'],thread_id=previous);raise
        state.ledger.revision(session['workspace'],'conversation',session['id'],'Ready',conversation=session['id'])
        relay.tracker.queue_record(state.session(session['id']),phase='Ready')
        return 'New Codex context started in this conversation. Git history, workspace and selected task remain saved.'
    if action=='archive':
        if state.ledger.selection(session['id']):raise Invalid('Pause or finish the selected task before archiving')
        if state.db.execute("SELECT 1 FROM team_members m JOIN teams t ON t.id=m.team WHERE m.session=? AND t.state='active'",(session['id'],)).fetchone():raise Invalid('Archive the team before archiving its members')
        state.set_session(session['id'],state='archived')
        relay.tracker.queue_record(state.session(session['id']),phase='Paused')
        return 'Conversation archived. Its group, code branches and history are preserved.'
    if action=='unarchive':
        if session['state']!='archived':raise Invalid('Choose an archived conversation')
        relay.tracker.code_directory(session)
        state.set_session(session['id'],state='ready');relay.tracker.queue_record(state.session(session['id']),phase='Ready')
        return 'Conversation restored. Existing group, thread and Git branches are reused.'
    raise Invalid('Use compact, clear, restart, archive or unarchive')


def usage(relay,session):
    if not session:raise Invalid('Choose a workspace conversation')
    target=None if relay.terminal.binding(session) else session
    relay.host.ensure_started(target)
    connection=relay.host.connect(session=target)
    try:
        data=connection.request('account/rateLimits/read',{})
        limits=data.get('rateLimits') or {}
        windows={k:{f:v for f,v in (limits.get(k) or {}).items() if f in {'usedPercent','windowDurationMins','resetsAt'}} for k in ['primary','secondary']}
        recorded=relay.state.db.execute('SELECT usage FROM conversation_usage WHERE session=?',(session['id'],)).fetchone()
        return json.dumps(dict(rate_limits=windows,tokens=json.loads(recorded[0]) if recorded else None,
            monetary_cost=None,note='Observed account limits and token counts; price and remaining entitlement are not inferred.'),ensure_ascii=False)
    finally:connection.close()


def goal(relay,session,tokens):
    if not session:raise Invalid('Choose a workspace conversation')
    if relay.terminal.binding(session):raise Invalid('Use /goal in the original terminal; its native goal is preserved')
    if relay.state.ledger.busy(session['id']):raise Invalid('Finish or stop the current turn before editing its goal')
    action=tokens[0] if tokens else 'show'
    if action not in {'show','set','clear'}:raise Invalid('Use /goal, /goal set TOKEN_BUDGET OBJECTIVE, or /goal clear. Automatic goal execution is not enabled.')
    if action=='set':
        try:budget=int(tokens[1]);objective=' '.join(tokens[2:])
        except (ValueError,IndexError):raise Invalid('Supply a positive token budget and objective') from None
        if not 1<=budget<=1000000 or not 1<=len(objective)<=4000:raise Invalid('Goal requires 1–1,000,000 tokens and an objective of at most 4,000 characters')
    relay.host.ensure_started(session)
    connection=relay.host.connect(session=session)
    try:
        p=relay.host.thread_params(session);p.update(threadId=session['thread_id'],excludeTurns=True);connection.request('thread/resume',p)
        if action=='set':data=connection.request('thread/goal/set',dict(threadId=session['thread_id'],objective=objective,tokenBudget=budget,status='paused'))
        elif action=='clear':data=connection.request('thread/goal/clear',dict(threadId=session['thread_id']))
        else:data=connection.request('thread/goal/get',dict(threadId=session['thread_id']))
        visible={k:v for k,v in (data.get('goal') or {}).items() if k in {'objective','status','tokenBudget','tokensUsed','timeUsedSeconds'}}
        if action!='show':
            from .memory import remember
            remember(relay.tracker,session,'note','Goal '+action+': '+json.dumps(visible,ensure_ascii=False))
        return json.dumps(dict(goal=visible or None,automatic_continuations=False,note='Goals stay paused. Send explicit tracked messages to work toward the objective.'),ensure_ascii=False)
    finally:connection.close()
