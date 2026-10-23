"""Name-to-Git recovery plans with pinned branches, collision checks and no replay."""
import json
from pathlib import Path
import re
import time
import uuid

from .core import Invalid,github_url,valid_branch
from .live_state import private_json,write_private
from .records import SHA

PLAN=re.compile(r'rp_[0-9a-f]{16}')


def repository(state,name):
    if isinstance(name,str) and '/' not in name and ':' not in name:
        name='https://github.com/'+state.config()['github_owner']+'/'+name
    elif isinstance(name,str) and name.count('/')==1 and ':' not in name:
        name='https://github.com/'+name
    return github_url(name)


def directory(state,ident):
    if not isinstance(ident,str) or not PLAN.fullmatch(ident):raise Invalid('Invalid recovery plan')
    parent=state.runtime/'recovery-plans';parent.mkdir(mode=0o700,exist_ok=True)
    if parent.is_symlink():raise Invalid('Recovery plan storage must be private')
    target=parent/ident
    if target.is_symlink():raise Invalid('Recovery plan must be a real directory')
    return target


def transport(state,url):
    from .github_auth import path
    file=path(state)
    return url if file.exists() or file.is_symlink() else 'git@github.com:'+url.removeprefix('https://github.com/')


def discover(tracker,source,repo_url):
    from .archive_parts import expand
    from .git_objects import Objects,MissingObject
    from .recovery import SCHEMA
    refs=tracker.git(source,'for-each-ref','--format=%(refname) %(objectname)','refs/remotes/origin/').splitlines()
    if len(refs)>500:raise Invalid('This repository has more than 500 branches; use the explicit single-branch restore command')
    candidates=[]
    with Objects(source) as objects:
        for ref in refs:
            name,commit=ref.split(' ',1);branch=name.removeprefix('refs/remotes/origin/')
            if branch=='HEAD':continue
            def read(path):
                raw=objects.blob(commit+':'+path)
                if len(raw)>2*1024*1024:raise Invalid('Oversized recovery record')
                return raw.decode('utf-8')
            try:raw=read('.intern_workspace/recovery.json')
            except MissingObject:continue
            try:
                data=expand(json.loads(raw),read)
                if (data['schema']!=SCHEMA or github_url(data['repo_url']).casefold()!=repo_url.casefold() or data['metadata_branch']!=branch
                        or not valid_branch(branch) or not isinstance(data['events'],list)):
                    raise Invalid('Recovery archive identity does not match the repository')
                revisions=[event['revision'] for event in data['events']]
                if any(type(r) is not int or r<1 for r in revisions):raise Invalid('Invalid recovery revision')
                candidates.append(dict(workspace=data['workspace'],repo_url=data['repo_url'],branch=branch,commit=commit,revision=max(revisions,default=0)))
            except (KeyError,TypeError,ValueError):raise Invalid('A recovery archive is malformed; inspect its branch before recovery') from None
    if not candidates:raise Invalid('No Intern Agent recovery archive was found. This repository may not contain uploaded agent records.')
    # Every branch projects its workspace ledger. Choose its newest published
    # revision, not a branch name or a workstation's filesystem timestamp.
    newest={}
    for row in sorted(candidates,key=lambda r:(r['revision'],r['branch'])):newest[row['workspace']]=row
    if len(newest)!=1:raise Invalid('Multiple workspace identities share this repository; select an explicit archive with the single-branch restore command')
    return next(iter(newest.values()))


def summary(plan):
    return {key:plan[key] for key in ['schema','plan_id','repo_url','workspace','branch','source_commit','tasks','turns','conversations','source_path','state']}


def preview(tracker,name):
    state=tracker.state;url=repository(state,name)
    existing=next((dict(row) for row in state.db.execute('SELECT * FROM tracked_workspaces')
                   if row['repo_url'].casefold()==url.casefold()),None)
    if existing:
        tracker.require_workspace(existing['workspace'])
        return dict(schema='intern-agent.repository-recovery.v1',state='already_registered',workspace=existing['workspace'],
            repo_url=existing['repo_url'],source_path=existing['source_path'],execution_replayed=False,applied=False)
    ident='rp_'+uuid.uuid4().hex[:16]
    target=directory(state,ident);target.mkdir(mode=0o700)
    source=target/'source'
    tracker.git(target,'clone','--no-recurse-submodules','--',transport(state,url),str(source),network=True)
    candidate=discover(tracker,source,url)
    # GitHub owner/repository spelling is case-insensitive; archive identity and
    # existing published records retain their original canonical spelling.
    url=candidate['repo_url']
    tracker.git(source,'remote','set-url','origin',transport(state,url))
    from .recovery import inspect
    data,commit=inspect(tracker,source,url,candidate['branch'])
    branches={data['base_branch'],candidate['branch']}
    branches.update(c['code_branch'] for c in data['conversations'])
    branches.update(t['code_branch'] for t in data['tasks'] if t.get('code_branch'))
    if data.get('workspace_metadata_branch'):branches.add(data['workspace_metadata_branch'])
    pins={}
    for branch in sorted(branches):
        head=tracker.git(source,'rev-parse','--verify','refs/remotes/origin/'+branch)
        if not SHA.fullmatch(head):raise Invalid('A required recovered branch is unavailable')
        pins[branch]=head
    plan=dict(schema='intern-agent.repository-recovery.v1',plan_id=ident,repo_url=url,workspace=data['workspace'],
        branch=candidate['branch'],source_commit=commit,source_path=str(source),pins=pins,tasks=len(data['tasks']),
        turns=len(data['turns']),conversations=len(data['conversations']),state='reviewed',created_at=time.time())
    write_private(target/'plan.json',plan)
    return dict(summary(plan),execution_replayed=False,account_credentials_restored=False,
        next_action='Apply this plan to import saved records. Then authenticate Codex/Feishu if needed, pair the owner and reconnect the restored conversations.')


def read(state,ident):
    target=directory(state,ident);plan=private_json(target/'plan.json')
    if (plan.get('schema')!='intern-agent.repository-recovery.v1' or plan.get('plan_id')!=ident
            or plan.get('source_path')!=str(target/'source') or (target/'source').is_symlink()
            or github_url(plan.get('repo_url'))!=plan['repo_url']):raise Invalid('Invalid private recovery plan')
    return plan


def apply(tracker,ident):
    state=tracker.state;plan=read(state,ident);source=Path(plan['source_path'])
    tracker.verify_origin(dict(source_path=str(source),repo_url=plan['repo_url']))
    # A completed plan is safe to reopen from a second SSH editor, including
    # after the restored conversations have been reconnected and advanced.
    receipt=state.db.execute('SELECT * FROM restore_receipts WHERE workspace=?',(plan['workspace'],)).fetchone()
    if receipt and (receipt['repo_url'],receipt['branch'],receipt['commit_id'])==(plan['repo_url'],plan['branch'],plan['source_commit']):
        return dict(summary(plan),state='restored',applied=True,already_applied=True,execution_replayed=False)
    remote={}
    for row in tracker.git(source,'ls-remote','--heads','origin',network=True).splitlines():
        head,ref=row.split(None,1);remote[ref.removeprefix('refs/heads/')]=head
    for branch,head in plan['pins'].items():
        if remote.get(branch)!=head or tracker.git(source,'rev-parse','--verify','refs/remotes/origin/'+branch)!=head:
            raise Invalid('Published progress changed after preview; create a fresh recovery plan before importing')
    from .recovery import restore
    result=restore(tracker,source,plan['repo_url'],plan['branch'],True,append=True)
    if result['source_commit']!=plan['source_commit']:raise Invalid('Recovery source changed')
    plan['state']='restored';write_private(directory(state,ident)/'plan.json',plan)
    return dict(summary(plan),applied=True,execution_replayed=False,routing_restored=False,
        next_action='Saved history and preferences are restored. Pair the owner and use Reconnect Restored Conversation; old prompts and research jobs have not been started.')


def status(state):
    return dict(restored=[dict(row) for row in state.db.execute('''SELECT s.id,s.workspace,s.state,r.intern_name,r.code_branch,p.role
        FROM sessions s JOIN restored_conversations r ON r.session=s.id LEFT JOIN restored_roles p ON p.session=s.id ORDER BY s.workspace,s.rowid''')],
        imports=[dict(workspace=row['workspace'],repo_url=row['repo_url'],branch=row['branch'],source_commit=row['commit_id'])
                 for row in state.db.execute('SELECT * FROM restore_receipts ORDER BY workspace')])
