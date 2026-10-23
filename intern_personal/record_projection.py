"""Deterministic Markdown view of the durable ledger, never a stale worker view."""
import json
from pathlib import Path

from .core import Invalid
from .records import render, quote, validate_document, validate_evolution, validate_tree,parse,message_section
from .lifecycle import utc
from .record_quality import distinct_notes,turn_index


def committed_turns(tracker,root,turns,workspace):
    from .workspace_tracking import TrackingError
    from .git_objects import Objects,MissingObject
    from .archive_parts import expand
    with Objects(root) as objects:
        def read(name):
            try:return objects.blob('HEAD:'+name).decode()
            except MissingObject:raise TrackingError('Committed record is unavailable') from None
        archived={}
        try:archived={t['seq']:t for t in expand(json.loads(read('.intern_workspace/recovery.json')),read)['turns']}
        except TrackingError:pass
        for turn in turns:
            if turn['phase']!='complete':continue
            relative='.intern_workspace/conversations/'+turn['conversation']+'/turns/turn_'+str(turn['seq'])+'.md'
            try:previous=read(relative)
            except TrackingError:continue
            if parse(previous)['phase']!='complete':continue
            saved=archived.get(turn['seq'])
            if saved:
                if saved['phase']!='complete' or saved['conversation']!=turn['conversation'] or saved['workspace']!=workspace:
                    raise Invalid('Committed turn archive identity differs from Markdown')
                for section,key in [('Request','request'),('Response','response')]:
                    body=previous.split('## '+section+'\n',1)[1].split('\n## ',1)[0].strip()
                    if body!=quote(saved[key]).strip():raise Invalid('Committed turn archive text differs from Markdown')
                turn['text']=saved['request'];turn['result']=saved['response']
            else:
                turn['text']=message_section(previous,'Request')
                turn['result']=message_section(previous,'Response') if turn['result'] else None


def project(tracker, binding):
    state=tracker.state;db=state.db;workspace=binding['workspace']
    # Import known legacy events without inventing dates or named tasks.
    items=[dict(r) for r in db.execute('''SELECT e.* FROM events e LEFT JOIN sessions s ON s.id=e.session
        WHERE s.workspace=? OR EXISTS (SELECT 1 FROM turn_records r WHERE r.seq=e.seq AND r.workspace=?) ORDER BY e.seq''',(workspace,workspace))]
    for item in items:
        if not db.execute('SELECT 1 FROM turn_records WHERE seq=?',(item['seq'],)).fetchone():
            state.ledger.ensure_turn(item)
        state.ledger.observe(item,item['state'])
    # A short read transaction yields one consistent revision across all files.
    db.execute('BEGIN IMMEDIATE')
    try:
        turns=[dict(r) for r in db.execute('''SELECT r.*,e.text,e.result,e.state,e.role FROM turn_records r
            JOIN events e ON e.seq=r.seq WHERE r.workspace=? ORDER BY r.seq''',(workspace,))]
        tasks=state.ledger.tasks(workspace)
        sessions=[dict(r) for r in db.execute('''SELECT s.*,i.intern_name,i.code_branch,i.metadata_mode FROM sessions s
            JOIN tracked_interns i ON i.session=s.id WHERE s.workspace=?''',(workspace,))]
        from .portable_settings import snapshot as preference_snapshot,checkpoint as preference_checkpoint
        profile_ids={s['id'] for s in sessions}|{r['session'] for r in db.execute('''SELECT r.session FROM restored_conversations r
            JOIN sessions s ON s.id=r.session WHERE s.workspace=?''',(workspace,))}
        preferences=preference_snapshot(state,workspace,profile_ids)
        if preferences['conversations']:
            preference_checkpoint(state,workspace,preferences,binding.get('record_session') or preferences['conversations'][0]['conversation'])
        selected={r['session']:r['task_id'] for r in db.execute('SELECT * FROM conversation_tasks')}
        events=[dict(r) for r in db.execute('SELECT * FROM record_events WHERE workspace=? ORDER BY revision',(workspace,))]
        notes=[dict(r) for r in db.execute('SELECT * FROM workspace_memory WHERE workspace=? ORDER BY revision',(workspace,))]
        db.execute('COMMIT')
    except BaseException:db.execute('ROLLBACK');raise
    from .turn_uploads import overlay,scope as upload_scope
    turns=overlay(state,binding,turns)
    revision=max((r['revision'] for r in events),default=0)
    updated=utc(events[-1]['at']) if events else None
    root=Path(binding['metadata_path'])
    # Reuse published text exactly, including legacy quoted trailing newlines.
    if any(t['phase']=='complete' for t in turns):committed_turns(tracker,root,turns,workspace)
    def write(relative,title,metadata,sections):
        # Sanitize individual untrusted values before JSON encoding.
        def clean(value):
            if isinstance(value,str):return tracker.clean(value)
            if isinstance(value,list):return [clean(x) for x in value]
            if isinstance(value,dict):return {k:clean(v) for k,v in value.items()}
            return value
        metadata=dict(metadata,workspace=workspace,revision=metadata.get('revision',revision))
        text=render(title,clean(metadata),[(h,tracker.clean(t)) for h,t in sections])
        validate_document(relative,text)
        path=root/relative
        if path.exists():validate_evolution(path.read_text(),text)
        tracker.write(binding,relative,text)
    def history(relative,title,subset,**refs):
        entries=[{'event_id':'event_'+str(e['revision']),'revision':e['revision'],'phase':e['phase'],
                  'at':utc(e['at']),'turn_id':e['turn_id'],'task_id':e['task_id']} for e in subset]
        # Preserve a legacy file's existing prefix; additional history uses
        # bounded pages whose entry lists remain append-only.
        path=root/relative
        previous=parse(path.read_text()) if path.exists() else None
        first=max(500,len(previous['entries']) if previous else 0)
        groups=[entries[:first]]+[entries[i:i+500] for i in range(first,len(entries),500)]
        for index,group in enumerate(groups):
            name=relative if index==0 else str(Path(relative).parent/'history'/('page_'+str(index)+'.md'))
            lines=['- '+e['at']+' | '+e['phase']+' | '+(e['turn_id'] or e['task_id'] or 'conversation') for e in group]
            write(name,title,dict(kind='history',entries=group,**refs),[('Events','\n'.join(lines) or '(none)')])
    ids={s['id'] for s in sessions}|{t['conversation'] for t in turns}|{t['session'] for t in tasks}
    preference_rows={p['conversation']:p for p in preferences['conversations']}
    ids.update(preference_rows)
    modes={identity:('working' if selected.get(identity) else 'idle') for identity in ids}
    working=sum(mode=='working' for mode in modes.values())
    pending=bool(db.execute('SELECT 1 FROM tracking_pending WHERE workspace=?',(workspace,)).fetchone())
    pending=pending or bool(db.execute('SELECT 1 FROM bot_tracking_pending p JOIN sessions s ON s.id=p.session WHERE s.workspace=?',(workspace,)).fetchone())
    if binding.get('publication_session') and db.execute("SELECT 1 FROM turn_uploads WHERE session=? AND scope=? AND state='prepared'",
            (binding['publication_session'],upload_scope(binding))).fetchone():pending=False
    write('.intern_workspace/status.md','Workspace status',dict(kind='workspace',mode='working' if working else 'idle',
          working_conversations=working,updated_at=updated,sync='pending' if pending else 'synced'),
          [('Conversations','\n'.join('- '+i+' | '+modes[i] for i in sorted(ids)) or '(none)'),
           ('Policy',('Code and management share this bot\'s permanent branch.' if binding.get('metadata_mode')=='bot_branch' else 'Code and management use separate branches.')+' No pull request without explicit user instruction.')])
    write('.intern_workspace/memory.md','Workspace knowledge',dict(kind='knowledge'),
          [('Owner notes','\n\n'.join(n['kind']+': '+quote(n['body']) for n in distinct_notes(notes) if not n['task_id']) or '(none)')])
    for identity in sorted(ids):
        own=[t for t in turns if t['conversation']==identity]
        agent=[t for t in own if t['role']=='agent']
        latest=(agent or own)[-1] if (agent or own) else None
        activity=latest['activity'] if latest else 'waiting'
        if latest:
            activity={'queued':'waiting','running':'running','awaiting_terminal':'running','cancelled':'stopped',
                      'uncertain_execution':'unknown','record_blocked':'failed'}.get(latest['state'],activity)
            if latest['activity']=='awaiting_approval' and latest['state']=='running':activity='awaiting_approval'
            if activity=='running' and latest['stop_requested']:activity='stop_requested'
        prefix='.intern_workspace/conversations/'+identity+'/'
        if identity in preference_rows:
            write(prefix+'preferences.md','Conversation preferences',dict(kind='knowledge',conversation_id=identity),
                  [('Preferences',quote(json.dumps(preference_rows[identity],ensure_ascii=False,sort_keys=True,indent=2))),
                   ('Recovery','Saved preferences and roles are reference state. Account credentials and executable queues are excluded.')])
        write(prefix+'status.md','Conversation status',dict(kind='conversation',conversation_id=identity,
              mode=modes[identity],activity=activity,task_id=selected.get(identity),updated_at=updated),
              [('State','Mode: '+modes[identity]+'\n\nAgent: '+activity+'\n\nNamed task: '+(selected.get(identity) or 'none'))])
        history(prefix+'history_log.md','Conversation history',[e for e in events if e['conversation']==identity],conversation_id=identity)
        write(prefix+'knowledge.md','Conversation knowledge',dict(kind='knowledge',conversation_id=identity),
              [('Recorded results',turn_index(own,lambda t:'turns/turn_'+str(t['seq'])+'.md',tracker.clean)),
               ('All activity','See [history](history_log.md) and its continuation pages for commands, failures and state changes.'),
               ('Evidence','These links identify reported work, not verified knowledge. Read each source request, response and evidence before reuse. '
                'Completion and Git synchronization describe delivery, not independent verification of a claim. '
                'Commands and transport notices remain in their source records; internal reasoning is excluded.')])
        for turn in own:
            metadata={key:turn[key] for key in ('phase','activity','sync','source','code_start','code_end','revision')}
            metadata.update(kind='turn',conversation_id=identity,task_id=turn['task_id'],turn_id='turn_'+str(turn['seq']),
                            received_at=utc(turn['received_at']),completed_at=utc(turn['completed_at']),evidence=json.loads(turn['evidence']))
            write(prefix+'turns/turn_'+str(turn['seq'])+'.md','Turn '+str(turn['seq']),metadata,
                  [('Request',quote(tracker.clean(turn['text']))),('Response',quote(tracker.clean(turn['result']) if turn['result'] is not None else None)),
                   ('Evidence',quote('\n'.join(metadata['evidence'])) )])
    for task in tasks:
        prefix='.intern_workspace/tasks/'+task['id']+'/'
        metadata=dict(kind='task',task_id=task['id'],name=task['name'],state=task['state'],
                      conversation_id=task['session'],revision=task['revision'],updated_at=utc(task['updated_at']))
        if task.get('code_branch'):metadata['code_branch']=task['code_branch']
        if task.get('criteria'):metadata['criteria']=task['criteria']
        if task.get('acceptance'):metadata['acceptance']=json.loads(task['acceptance'])
        sections=[('Name',quote(task['name'])),('State',task['state'])]
        if task.get('code_branch'):sections.append(('Code branch',task['code_branch']))
        if task.get('criteria'):sections.append(('Acceptance criteria',quote(task['criteria'])))
        if task.get('acceptance'):sections.append(('Owner acceptance',quote(task['acceptance'])))
        write(prefix+'README.md','Named task',metadata,sections)
        history(prefix+'history_log.md','Task history',[e for e in events if e['task_id']==task['id']],task_id=task['id'])
        linked=[t for t in turns if t['task_id']==task['id']]
        write(prefix+'task_knowledge.md','Task knowledge',dict(kind='knowledge',task_id=task['id']),
              [('Owner notes','\n\n'.join(quote(n['body']) for n in distinct_notes(notes) if n['task_id']==task['id']) or '(none)'),
               ('Reported work',turn_index(linked,lambda t:'../../conversations/'+t['conversation']+'/turns/turn_'+str(t['seq'])+'.md',tracker.clean)),
               ('Turns','\n'.join('- [turn_'+str(t['seq'])+'](../../conversations/'+t['conversation']+'/turns/turn_'+str(t['seq'])+'.md)' for t in linked) or '(none)')])
    from .teams import project as project_teams
    project_teams(tracker,binding)
    from .peer_mail import project as project_peers
    project_peers(tracker,binding)
    from .feature_state import project as project_features
    project_features(tracker,binding)
    validate_tree(root/'.intern_workspace')
    from .recovery import archive
    archive(tracker,binding,turns,tasks,events,notes,sessions,selected,preferences=preferences)
    return revision
