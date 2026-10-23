"""Portable Git history recovery, with no credentials, routing or task replay."""
import json
from pathlib import Path
import re
import uuid

from .core import Invalid,github_url,valid_branch,WORKSPACE
from .records import validate_tree,parse,quote,PHASES,TASK_STATES,ID,SHA
from .lifecycle import utc
from .workspace_tracking import TrackingError

SCHEMA='intern-agent.recovery.v1'
OPTIONAL={'team_state','metadata_mode','workspace_metadata_branch','feature_state','preferences'}
TURN_FIELDS=('seq','workspace','conversation','task_id','source','received_at','completed_at','phase','activity','sync',
             'code_start','code_end','evidence','stop_requested','revision')
TASK_FIELDS=('id','workspace','name','state','session','created_at','updated_at','revision','code_branch','criteria','acceptance')
EVENT_FIELDS=('revision','workspace','kind','record_id','phase','at','conversation','task_id','turn_id')
NOTE_FIELDS=('workspace','task_id','kind','body','at','revision')


def archive(tracker,binding,turns,tasks,events,notes,sessions,selected,*,preferences=None):
    def clean(value):
        if isinstance(value,str):return tracker.clean(value)
        if isinstance(value,list):return [clean(x) for x in value]
        if isinstance(value,dict):return {k:clean(v) for k,v in value.items()}
        return value
    conversations={r['session']:{'id':r['session'],'intern_name':r['intern_name'],'code_branch':r['code_branch']}
                   for r in tracker.state.db.execute('''SELECT r.* FROM restored_conversations r
                       JOIN sessions s ON s.id=r.session WHERE s.workspace=?''',(binding['workspace'],))}
    conversations.update({s['id']:{'id':s['id'],'intern_name':s['intern_name'],
                                   'code_branch':tracker.intern(s).get('home_branch') or s['code_branch']} for s in sessions})
    for s in sessions:
        if s.get('metadata_mode')=='bot_branch':conversations[s['id']]['metadata_mode']='bot_branch'
    for row in tracker.state.db.execute('SELECT r.* FROM restored_conversations r JOIN sessions s ON s.id=r.session WHERE s.workspace=?',(binding['workspace'],)):
        if row['metadata_mode']=='bot_branch':conversations[row['session']]['metadata_mode']='bot_branch'
    data={'schema':SCHEMA,'workspace':binding['workspace'],'repo_url':binding['repo_url'],
          'metadata_branch':binding['metadata_branch'],'base_branch':binding['base_branch'],
          'tasks':[{k:t.get(k) for k in TASK_FIELDS} for t in tasks],
          'turns':[dict({k:t[k] for k in TURN_FIELDS},request=t['text'],response=t['result'],role=t['role']) for t in turns],
          'events':[{k:e[k] for k in EVENT_FIELDS} for e in events],
          'notes':[{k:n[k] for k in NOTE_FIELDS} for n in notes],
          'conversations':list(conversations.values()),
          'selections':{s:t for s,t in selected.items() if any(task['id']==t for task in tasks)}}
    if binding.get('metadata_mode')=='bot_branch':
        data.update(metadata_mode='bot_branch',workspace_metadata_branch=binding.get('workspace_metadata_branch'))
    from .teams import snapshot
    teams=snapshot(tracker.state,binding['workspace'])
    if teams['teams']:data['team_state']=teams
    from .feature_state import snapshot as feature_snapshot
    features=feature_snapshot(tracker.state,binding['workspace'])
    if features:data['feature_state']=features
    if preferences is None:
        from .portable_settings import snapshot
        preferences=snapshot(tracker.state,binding['workspace'],{c['id'] for c in data['conversations']})
    data['preferences']=preferences
    from .archive_parts import write
    write(tracker,binding,clean(data))


def inspect(tracker,source,repo_url,branch):
    source=Path(source).resolve(strict=True);repo_url=github_url(repo_url)
    if not valid_branch(branch):raise Invalid('Invalid recovery branch')
    binding={'source_path':str(source),'repo_url':repo_url}
    tracker.verify_origin(binding)
    if tracker.git(source,'status','--porcelain'):raise Invalid('Recover from a clean independent clone')
    commit=tracker.git(source,'rev-parse','--verify','refs/remotes/origin/'+branch)
    if not SHA.fullmatch(commit):raise Invalid('Fetch the metadata branch into your clone before recovery')
    from .git_objects import Objects
    with Objects(source) as objects:
        def read(name):
            raw=objects.blob(commit+':'+name)
            if len(raw)>2*1024*1024:raise Invalid('Oversized recovery record')
            return raw.decode('utf-8')
        from .archive_parts import expand
        data=expand(json.loads(read('.intern_workspace/recovery.json')),read)
        if (not isinstance(data,dict) or set(data)-OPTIONAL!= {'schema','workspace','repo_url','metadata_branch','base_branch','tasks','turns','events','notes','conversations','selections'}
                or data['schema']!=SCHEMA or data['repo_url']!=repo_url or data['metadata_branch']!=branch
                or not WORKSPACE.fullmatch(data['workspace']) or not valid_branch(data['base_branch']) or data['base_branch']==branch):
            raise Invalid('Recovery archive identity does not match its Git repository')
        names=tracker.git(source,'ls-tree','-r','--name-only',commit,'--','.intern_workspace').splitlines()
        documents=[(name,read(name)+'\n') for name in names if name.endswith('.md')]
        validate(data,documents)
    return data,commit


def validate(data,documents,reader=None):
    from .archive_parts import expand
    data=expand(data,reader)
    if (not isinstance(data,dict) or set(data)-OPTIONAL!={'schema','workspace','repo_url','metadata_branch','base_branch','tasks','turns','events','notes','conversations','selections'}
            or data['schema']!=SCHEMA or not isinstance(data['workspace'],str) or not WORKSPACE.fullmatch(data['workspace'])
            or not valid_branch(data['metadata_branch']) or not valid_branch(data['base_branch'])
            or data['metadata_branch']==data['base_branch'] or github_url(data['repo_url'])!=data['repo_url']
            or any(not isinstance(data[k],list) for k in ['tasks','turns','events','notes','conversations'])
            or not isinstance(data['selections'],dict)):
        raise Invalid('Malformed recovery archive')
    if data.get('metadata_mode','metadata_branch') not in {'metadata_branch','bot_branch'}:raise Invalid('Unknown recovery branch layout')
    if data.get('metadata_mode')=='bot_branch':
        parent=data.get('workspace_metadata_branch')
        if 'workspace_metadata_branch' not in data or (parent is not None and (not valid_branch(parent) or parent==data['base_branch'] or parent==data['metadata_branch'])):
            raise Invalid('Unsafe legacy workspace branch reference')
    elif 'workspace_metadata_branch' in data:raise Invalid('Unexpected legacy workspace branch reference')
    records=validate_tree(Path("."),documents)
    if any(r['workspace']!=data['workspace'] for r in records):raise Invalid('Recovery records cross workspace boundaries')
    turns={r['turn_id']:r for r in records if r['kind']=='turn'}
    tasks={r['task_id']:r for r in records if r['kind']=='task'}
    bodies={record['turn_id']:text for _,text in documents
            if '<!-- intern-agent.record.v2\n' in text
            for record in [parse(text)] if record['kind']=='turn'}
    expected_events={e['revision']:e for r in records if r['kind']=='history' for e in r['entries']}
    seen=set();revisions=set()
    for t in data['turns']:
        if set(t)!=set(TURN_FIELDS)|{'request','response','role'} or type(t['seq']) is not int or t['seq']<=0 or t['seq'] in seen:
            raise Invalid('Malformed or duplicate archived turn')
        seen.add(t['seq']);record=turns.get('turn_'+str(t['seq']))
        if (not record or t['workspace']!=data['workspace'] or t['conversation']!=record['conversation_id']
                or t['task_id']!=record['task_id'] or t['phase']!=record['phase'] or t['revision']!=record['revision']
                or t['activity']!=record['activity'] or t['sync']!=record['sync'] or t['code_end']!=record['code_end']
                or t['code_start']!=record['code_start'] or t['source']!=record['source']
                or json.loads(t['evidence'])!=record['evidence'] or not isinstance(t['request'],str)
                or (t['response'] is not None and not isinstance(t['response'],str)) or t['role'] not in {'agent','control','urgent'}):
            raise Invalid('Recovery turn disagrees with validated Markdown')
        for column in ['received_at','completed_at']:
            if t[column] is not None and (not isinstance(t[column],(int,float)) or t[column]<0):raise Invalid('Invalid recovery timestamp')
            if utc(t[column])!=record[column]:raise Invalid('Recovery timestamp disagrees with Markdown')
        body=bodies['turn_'+str(t['seq'])]
        for section,value in [('Request',t['request']),('Response',t['response'])]:
            rendered=body.split('## '+section+'\n',1)[1].split('\n## ',1)[0].strip()
            if rendered!=quote(value).strip():raise Invalid('Recovery message disagrees with Markdown')
    if len(seen)!=len(turns):raise Invalid('Recovery archive omits recorded turns')
    for t in data['tasks']:
        if set(t)!=set(TASK_FIELDS) or t['id'] not in tasks or t['state'] not in TASK_STATES or t['workspace']!=data['workspace']:
            raise Invalid('Malformed recovery task')
        r=tasks[t['id']]
        if any(t[k]!=r[key] for k,key in [('name','name'),('state','state'),('session','conversation_id'),('revision','revision')]):raise Invalid('Recovery task disagrees with Markdown')
        if (t['code_branch']!=r.get('code_branch') or t['criteria']!=(r.get('criteria') or '')
                or (json.loads(t['acceptance']) if t['acceptance'] else None)!=r.get('acceptance')):raise Invalid('Recovery task delivery disagrees with Markdown')
        if any(not isinstance(t[k],(int,float)) or t[k]<0 for k in ['created_at','updated_at']) or utc(t['updated_at'])!=r['updated_at']:
            raise Invalid('Invalid recovered task timestamp')
        if t['code_branch'] and (not valid_branch(t['code_branch']) or t['code_branch']==data['base_branch']):raise Invalid('Unsafe recovered task branch')
    if len(data['tasks'])!=len(tasks) or len({t['id'] for t in data['tasks']})!=len(tasks):raise Invalid('Duplicate or missing recovered task')
    for e in data['events']:
        if set(e)!=set(EVENT_FIELDS) or e['workspace']!=data['workspace'] or type(e['revision']) is not int or e['revision'] in revisions or e['phase'] not in PHASES:
            raise Invalid('Malformed recovery history')
        revisions.add(e['revision'])
        expected=expected_events.get(e['revision'])
        if not expected or any(e[k]!=expected[k] for k in ['phase','task_id','turn_id']) or utc(e['at'])!=expected['at']:
            raise Invalid('Recovery history disagrees with Markdown')
    if revisions!=set(expected_events):raise Invalid('Recovery archive omits history')
    for n in data['notes']:
        if set(n)!=set(NOTE_FIELDS) or n['workspace']!=data['workspace'] or n['kind'] not in {'note','error','rule','task'} or not isinstance(n['body'],str) or (n['task_id'] and n['task_id'] not in tasks):raise Invalid('Malformed recovery knowledge')
    convs=set()
    for c in data['conversations']:
        if (set(c)-{'metadata_mode'}!={'id','intern_name','code_branch'} or c.get('metadata_mode','metadata_branch') not in {'metadata_branch','bot_branch'} or not ID.fullmatch(c['id']) or not ID.fullmatch(c['intern_name'])
                or not valid_branch(c['code_branch']) or c['code_branch']==data['base_branch'] or c['id'] in convs):raise Invalid('Malformed recovered conversation')
        convs.add(c['id'])
    bot_branches=[c['code_branch'] for c in data['conversations'] if c.get('metadata_mode')=='bot_branch']
    if len(set(bot_branches))!=len(bot_branches):raise Invalid('Recovered bots cannot share a permanent branch')
    if data.get('metadata_mode')=='bot_branch' and data['metadata_branch'] not in bot_branches:raise Invalid('Combined archive is not on its bot branch')
    branches={c['id']:c['code_branch'] for c in data['conversations'] if c.get('metadata_mode')=='bot_branch'}
    if any(t['code_branch'] and t['session'] in branches and t['code_branch']!=branches[t['session']] for t in data['tasks']):raise Invalid('Recovered named task crosses its bot branch')
    for session,task in data['selections'].items():
        if session not in convs or task not in tasks or tasks[task]['state'] in {'completed','cancelled'}:raise Invalid('Malformed recovered selection')
    from .feature_state import validate as validate_features
    features=data.get('feature_state',[])
    validate_features(features,data['workspace'],convs,set(tasks))
    if sorted(features,key=lambda r:(r['kind'],r['key']))!=sorted([r['feature'] for r in records if r['kind']=='feature'],key=lambda r:(r['kind'],r['key'])):
        raise Invalid('Recovery feature documents disagree with Markdown')
    if 'preferences' in data:
        from .portable_settings import validate as validate_preferences
        validate_preferences(data['preferences'],convs)
        files=dict(documents)
        for profile in data['preferences']['conversations']:
            name='.intern_workspace/conversations/'+profile['conversation']+'/preferences.md'
            text=files.get(name,'')
            expected=quote(json.dumps(profile,ensure_ascii=False,sort_keys=True,indent=2)).strip()
            if '## Preferences\n' not in text or text.split('## Preferences\n',1)[1].split('\n## ',1)[0].strip()!=expected:
                raise Invalid('Recovery preferences disagree with their Markdown checkpoint')
    if 'team_state' in data:
        from .teams import validate as validate_teams
        validate_teams(data['team_state'],data['workspace'],convs,set(tasks))
        team_records=[r for r in records if r['kind']=='team']
        mail=[r['mail'] for r in records if r['kind']=='handoff']
        actual=dict(teams=[r['team'] for r in team_records],members=[m for r in team_records for m in r['members']],assignments=[a for r in team_records for a in r['assignments']],mail=mail)
        for key in actual:
            normalize=lambda rows:sorted(json.dumps(row,sort_keys=True) for row in rows)
            if normalize(actual[key])!=normalize(data['team_state'][key]):raise Invalid('Team archive disagrees with Markdown')
    return data


def restore(tracker,source,repo_url,branch,apply=False,*,append=False):
    data,commit=inspect(tracker,source,repo_url,branch);state=tracker.state;workspace=data['workspace']
    summary={'workspace':workspace,'tasks':len(data['tasks']),'turns':len(data['turns']),
             'conversations':len(data['conversations']),'source_commit':commit,'applied':False,
             'routing_restored':False,'execution_replayed':False}
    summary['teams']=len(data.get('team_state',{}).get('teams',[]))
    if not apply:return summary
    existing=state.db.execute('SELECT * FROM restore_receipts WHERE workspace=?',(workspace,)).fetchone()
    if append and existing and (existing['repo_url'],existing['branch'],existing['commit_id'])==(github_url(repo_url),branch,commit):
        return dict(summary,applied=True,already_applied=True)
    # Explicit repository recovery can append disjoint archives. Never renumber
    # published history or overwrite existing work to make an import fit.
    if not append and any(state.db.execute('SELECT 1 FROM '+table+' LIMIT 1').fetchone() for table in
           ['events','named_tasks','record_events','tracked_workspaces','sessions','workspace_memory','teams']):
        raise Invalid('Recovery requires a fresh private state directory; existing live state is never overwritten')
    ensure_disjoint(state,data)
    combined=data.get('metadata_mode')=='bot_branch'
    parent_branch=data.get('workspace_metadata_branch') if combined else branch
    metadata=state.runtime/'metadata'/workspace if parent_branch else Path(source).resolve()
    source=str(Path(source).resolve());binding={'workspace':workspace,'repo_url':github_url(repo_url),'source_path':source,
        'metadata_path':str(metadata),'metadata_branch':parent_branch or '', 'base_branch':data['base_branch'],
        'metadata_mode':'metadata_branch' if parent_branch else 'bot_branch','published':1}
    if parent_branch:
        metadata.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        parent_commit=commit if parent_branch==branch else tracker.git(source,'rev-parse','--verify','refs/remotes/origin/'+parent_branch)
        try:
            tracker.git(source,'rev-parse','--verify','refs/heads/'+parent_branch)
            tracker.git(source,'worktree','add',str(metadata),parent_branch)
        except TrackingError:
            if metadata.exists():raise Invalid('Recovery checkout already exists; inspect it instead of overwriting')
            tracker.create_worktree(source,metadata,parent_branch,parent_commit)
    state.db.execute('BEGIN IMMEDIATE')
    try:
        ensure_disjoint(state,data)
        state.db.execute('INSERT INTO workspaces(id,cwd) VALUES(?,?)',(workspace,source))
        state.db.execute('INSERT INTO tracked_workspaces('+','.join(binding)+') VALUES('+','.join('?' for _ in binding)+')',tuple(binding.values()))
        ids={t['conversation'] for t in data['turns']}|{t['session'] for t in data['tasks']}|{c['id'] for c in data['conversations']}
        for identity in ids:state.db.execute("INSERT INTO sessions(id,workspace,state,creation_key) VALUES(?,?,'restored',?)",(identity,workspace,uuid.uuid4().hex))
        if 'preferences' in data:
            from .portable_settings import apply as apply_preferences
            apply_preferences(state,data['preferences'])
        for t in data['tasks']:state.db.execute('INSERT INTO named_tasks('+','.join(TASK_FIELDS)+') VALUES('+','.join('?' for _ in TASK_FIELDS)+')',[t[k] for k in TASK_FIELDS])
        for t in data['turns']:
            # Saved records remain readable. No historical prompt enters a queue.
            state.db.execute("INSERT INTO events(seq,app_id,message_id,chat_id,sender,role,workspace,session,text,state,result) VALUES(?,'archive',?,'','',?,?,?,?,'archived',?)",
                (t['seq'],'archive_'+str(t['seq']),t['role'],workspace,t['conversation'],t['request'],t['response']))
            state.db.execute('INSERT INTO turn_records('+','.join(TURN_FIELDS)+') VALUES('+','.join('?' for _ in TURN_FIELDS)+')',[t[k] for k in TURN_FIELDS])
        for e in data['events']:state.db.execute('INSERT INTO record_events('+','.join(EVENT_FIELDS)+') VALUES('+','.join('?' for _ in EVENT_FIELDS)+')',[e[k] for k in EVENT_FIELDS])
        for n in data['notes']:state.db.execute('INSERT INTO workspace_memory('+','.join(NOTE_FIELDS)+') VALUES('+','.join('?' for _ in NOTE_FIELDS)+')',[n[k] for k in NOTE_FIELDS])
        for r in data.get('feature_state',[]):
            state.db.execute('INSERT INTO feature_documents VALUES(?,?,?,?,?)',(workspace,r['kind'],r['key'],json.dumps(r['data'],sort_keys=True),r['revision']))
        for c in data['conversations']:state.db.execute('INSERT INTO restored_conversations(session,intern_name,code_branch,metadata_mode) VALUES(?,?,?,?)',(c['id'],c['intern_name'],c['code_branch'],c.get('metadata_mode','metadata_branch')))
        for s,t in data['selections'].items():state.db.execute('INSERT INTO conversation_tasks(session,task_id) VALUES(?,?)',(s,t))
        for table,rows in [('teams',data.get('team_state',{}).get('teams',[])),('team_members',data.get('team_state',{}).get('members',[])),
                           ('team_assignments',data.get('team_state',{}).get('assignments',[])),('team_mail',data.get('team_state',{}).get('mail',[]))]:
            for row in rows:state.db.execute('INSERT INTO '+table+'('+','.join(row)+') VALUES('+','.join('?' for _ in row)+')',tuple(row.values()))
        state.db.execute('INSERT INTO restore_receipts VALUES(?,?,?,?)',(workspace,github_url(repo_url),branch,commit))
        state.db.execute('COMMIT')
    except BaseException:state.db.execute('ROLLBACK');raise
    from .git_hooks import install
    if parent_branch:install(state,metadata,[parent_branch],management=True,base_branch=data['base_branch'])
    summary['applied']=True
    return summary


def ensure_disjoint(state,data):
    if state.workspace(data['workspace']) or state.db.execute('SELECT 1 FROM tracked_workspaces WHERE workspace=?',(data['workspace'],)).fetchone():
        raise Invalid('This workspace is already registered; reconnect to it instead of importing over it')
    identities={c['id'] for c in data['conversations']}|{t['conversation'] for t in data['turns']}|{t['session'] for t in data['tasks']}
    groups=[('sessions','id',identities),('events','seq',[t['seq'] for t in data['turns']]),
            ('record_events','revision',[e['revision'] for e in data['events']]),('named_tasks','id',[t['id'] for t in data['tasks']]),
            ('teams','id',[t['id'] for t in data.get('team_state',{}).get('teams',[])])]
    for table,key,values in groups:
        for value in values:
            if state.db.execute('SELECT 1 FROM '+table+' WHERE '+key+'=?',(value,)).fetchone():
                raise Invalid('Recovery history identities conflict with this installation; use a separate state profile. No existing history was changed.')
