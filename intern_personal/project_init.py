"""Reviewed, durable initialization of a new private GitHub project."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid

from .core import Invalid,REPO,WORKSPACE,github_url
from .github_api import GitHubAPI,GitHubError
from .live_state import private_json,write_private
from .workspace_tracking import TrackingError


PLAN=re.compile(r'init_[0-9a-f]{16}')


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS initialization_receipts (
        seq INTEGER PRIMARY KEY,received_at REAL,source TEXT NOT NULL,completed_at REAL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS project_initializations (
        id TEXT PRIMARY KEY,workspace TEXT NOT NULL UNIQUE,owner TEXT NOT NULL COLLATE NOCASE,repo TEXT NOT NULL COLLATE NOCASE,
        phase TEXT NOT NULL,data TEXT NOT NULL,created_at REAL NOT NULL,updated_at REAL NOT NULL,
        error_type TEXT,error_message TEXT,UNIQUE(owner,repo))''')


def jobs(state):
    return [summary(state,dict(row)) for row in state.db.execute('SELECT * FROM project_initializations ORDER BY created_at DESC')]


def summary(state,row):
    data=json.loads(row['data']);session=state.session(data.get('session'))
    intern=state.db.execute('SELECT code_path,code_branch FROM tracked_interns WHERE session=?',(session['id'],)).fetchone() if session else None
    from .feishu_live import chat_link
    return dict(plan_id=row['id'],workspace=row['workspace'],repo_url=github_url('https://github.com/'+row['owner']+'/'+row['repo']),
        visibility='private',phase=row['phase'],source_path=data['source_path'],base_branch='main',
        session=data.get('session'),chat_url=chat_link(session['chat_id']) if session and session['chat_id'] else None,
        working_directory=intern['code_path'] if intern else None,code_branch=intern['code_branch'] if intern else None,
        error_type=row['error_type'],error_message=row['error_message'],
        steps=['Create the new private GitHub repository','Push one empty initial commit to main',
               'Attach a clean checkout on the relay data volume','Create a bot with one permanent code/history branch',
               'Open its owner-only Feishu group and its own Codex/tmux console'],
        existing_repositories_untouched=True,pull_request=False)


class ProjectInit:
    def __init__(self,relay,github=None):
        self.relay,self.state,self.tracker=relay,relay.state,relay.tracker
        self.github=github

    def client(self):
        if self.github is None:self.github=GitHubAPI(self.state)
        return self.github

    def row(self,plan):
        if not isinstance(plan,str) or not PLAN.fullmatch(plan):raise Invalid('Use the plan ID returned by /init WORKSPACE')
        row=self.state.db.execute('SELECT * FROM project_initializations WHERE id=?',(plan,)).fetchone()
        if not row:raise Invalid('Unknown project initialization plan')
        return dict(row)

    def update(self,row,phase=None,**data):
        value=json.loads(row['data']);value.update(data)
        self.state.db.execute('UPDATE project_initializations SET phase=?,data=?,updated_at=?,error_type=NULL,error_message=NULL WHERE id=?',
            (phase or row['phase'],json.dumps(value),time.time(),row['id']))
        return self.row(row['id'])

    def owner(self,row=None):
        if not self.state.identity()['owner']:raise Invalid('Pair the owner Control group first')
        owner=self.state.config()['github_owner']
        if row and row['owner'].casefold()!=owner.casefold():raise Invalid('Configured GitHub owner changed; inspect this initialization plan')
        try:viewer=self.client().viewer()
        except GitHubError as error:raise Invalid(str(error)) from None
        if not isinstance(viewer,dict) or viewer.get('type')!='User' or (viewer.get('login') or '').casefold()!=owner.casefold():
            raise Invalid('GitHub authentication must belong to the configured personal owner. No repository was created.')
        return owner

    def paths(self,name):
        parent=self.state.runtime/'projects'
        if parent.exists() and (parent.is_symlink() or not parent.is_dir() or parent.stat().st_uid!=os.getuid() or parent.stat().st_mode&0o077):
            raise Invalid('Project storage must be a private real directory')
        root=parent/name
        if root.is_symlink():raise Invalid('Project directory cannot be a link')
        return root,root/'source'

    def preview(self,name,repo=None):
        if not isinstance(name,str) or not WORKSPACE.fullmatch(name) or name.casefold() in {'apply','status'}:raise Invalid('Use a simple workspace name; apply and status are reserved commands')
        repo=repo or name
        if not isinstance(repo,str) or not REPO.fullmatch(repo) or repo in {'.','..'} or repo.casefold().endswith('.git'):
            raise Invalid('Use a repository name without a URL, path or .git suffix')
        owner=self.owner();root,source=self.paths(name)
        existing=self.state.db.execute('SELECT * FROM project_initializations WHERE workspace=?',(name,)).fetchone()
        if existing:
            if existing['owner'].casefold()!=owner.casefold() or existing['repo'].casefold()!=repo.casefold():raise Invalid('This workspace has a different initialization plan')
            return summary(self.state,dict(existing))
        if self.state.workspace(name) or root.exists():raise Invalid('Workspace or local project path already exists; existing files are preserved')
        try:remote=self.client().repository(owner,repo)
        except GitHubError as error:raise Invalid(str(error)) from None
        if remote is not None:raise Invalid('GitHub repository already exists. Attach its clean checkout instead; /init only creates new repositories.')
        plan='init_'+uuid.uuid4().hex[:16];data=dict(source_path=str(source),marker='Initialized by Intern Agent ('+plan+')')
        now=time.time()
        try:self.state.db.execute('INSERT INTO project_initializations VALUES(?,?,?,?,?,?,?,?,NULL,NULL)',
            (plan,name,owner,repo,'preview',json.dumps(data),now,now))
        except Exception:
            # Concurrent previews must not return a second plan for one repo.
            found=self.state.db.execute('SELECT * FROM project_initializations WHERE workspace=?',(name,)).fetchone()
            if found and found['repo'].casefold()==repo.casefold():return summary(self.state,dict(found))
            raise Invalid('Repository already has another initialization plan') from None
        return summary(self.state,self.row(plan))

    def verify_repo(self,row,repo):
        data=json.loads(row['data'])
        if (not isinstance(repo,dict) or type(repo.get('id')) is not int or repo['id']<=0 or repo.get('private') is not True
                or (repo.get('owner',{}).get('login') or '').casefold()!=row['owner'].casefold()
                or (repo.get('name') or '').casefold()!=row['repo'].casefold() or repo.get('description')!=data['marker']
                or (data.get('repository_id') and repo['id']!=data['repository_id'])):
            raise Invalid('Repository identity or privacy differs from this plan; inspect it before continuing')
        return repo

    def checkout(self,row):
        data=json.loads(row['data']);root,source=self.paths(row['workspace'])
        if str(source)!=data['source_path']:raise Invalid('Planned local path changed')
        parent=root.parent;parent.mkdir(mode=0o700,exist_ok=True)
        if root.exists():
            if root.stat().st_uid!=os.getuid() or root.stat().st_mode&0o077 or not root.is_dir():raise Invalid('Project directory is not private')
            marker=private_json(root/'operation.json')
            if marker!={'plan_id':row['id'],'repository_id':data['repository_id']}:raise Invalid('Local directory belongs to another initialization')
        else:
            root.mkdir(mode=0o700);write_private(root/'operation.json',dict(plan_id=row['id'],repository_id=data['repository_id']))
        if source.is_symlink():raise Invalid('Source directory cannot be a link')
        source.mkdir(mode=0o700,exist_ok=True)
        if source.stat().st_uid!=os.getuid() or source.stat().st_mode&0o077:raise Invalid('Source directory is not private')
        gitdir=source/'.git'
        if not gitdir.exists():
            if list(source.iterdir()):raise Invalid('Source directory contains unowned files')
            self.tracker.git(source,'init','-b','main')
        if not gitdir.is_dir() or gitdir.is_symlink():raise Invalid('Source Git directory is invalid')
        if Path(self.tracker.git(source,'rev-parse','--show-toplevel')).resolve()!=source.resolve():raise Invalid('Source checkout points outside its owned directory')
        self.tracker.verify_branch(source,'main')
        if self.tracker.git(source,'status','--porcelain'):raise Invalid('Initial source contains changes; no existing files will be committed')
        remote='git@github.com:'+row['owner']+'/'+row['repo']+'.git'
        remotes=self.tracker.git(source,'remote').splitlines()
        if remotes:
            if remotes!=['origin'] or self.tracker.git(source,'config','--get-all','remote.origin.url')!=remote:
                raise Invalid('Initial source remote differs from this plan')
        else:self.tracker.git(source,'remote','add','origin',remote)
        try:head=self.tracker.git(source,'rev-parse','--verify','HEAD')
        except TrackingError:head=None
        subject='Initialize empty project ('+row['id']+')'
        if not head:
            self.tracker.git(source,'commit','--allow-empty','-m',subject)
            head=self.tracker.git(source,'rev-parse','HEAD')
        if (self.tracker.git(source,'rev-list','--count','HEAD')!='1' or self.tracker.git(source,'ls-tree','-r','--name-only','HEAD')
                or self.tracker.git(source,'log','-1','--format=%s')!=subject or (data.get('initial_commit') and head!=data['initial_commit'])):
            raise Invalid('Initial commit differs from the approved empty baseline')
        return source,head

    def bind_initial_turns(self,row,session,item):
        # First-project onboarding has no controller Git archive yet. Retain
        # those original command bodies on the new bot branch once it exists.
        data=json.loads(row['data'])
        sequences=list(data.get('preview_seqs',[]))
        if data.get('preview_seq'):sequences.append(data['preview_seq'])
        if item:sequences.append(item.get('seq'))
        identity=self.state.identity()
        for seq in sequences:
            if not seq or self.state.db.execute('SELECT 1 FROM turn_records WHERE seq=?',(seq,)).fetchone():continue
            event=self.state.db.execute('SELECT * FROM events WHERE seq=?',(seq,)).fetchone()
            if not event:continue
            event=dict(event)
            if (event['session'] or event['role']!='control' or event['sender']!=identity['owner']
                    or event['app_id']!=identity['app_id'] or event['chat_id']!=identity['management_chat']):continue
            self.state.db.execute('UPDATE events SET session=? WHERE seq=?',(session['id'],seq))
            event['session']=session['id']
            receipt=self.state.db.execute('SELECT * FROM initialization_receipts WHERE seq=?',(seq,)).fetchone()
            self.state.ledger.ensure_turn(event,receipt['received_at'] if receipt else None,receipt['source'] if receipt else 'legacy')
            if receipt and receipt['completed_at'] is not None:self.state.ledger.update(event,completed_at=receipt['completed_at'])
            self.state.ledger.observe(event,event['state'])
            if item and seq==item['seq']:item['session']=session['id']

    def apply(self,plan,item=None):
        self.row(plan)
        fd=os.open(self.state.runtime/('project-init-'+plan+'.lock'),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise Invalid('This initialization is already running; use /init status '+plan)
            row=self.row(plan)
            if row['phase']=='complete':return summary(self.state,row)
            self.owner(row)
            creating_request=False
            try:
                data=json.loads(row['data']);repo=self.client().repository(row['owner'],row['repo'])
                if row['phase']=='preview':
                    root,_=self.paths(row['workspace'])
                    if root.exists() or self.state.workspace(row['workspace']):raise Invalid('Local workspace appeared after preview; existing files are preserved')
                    if repo is not None:raise Invalid('Repository appeared after preview; existing repositories are preserved')
                    row=self.update(row,'creating_repository')
                    creating_request=True
                    repo=self.client().create(row['owner'],row['repo'],data['marker'])
                elif repo is None:
                    # A timed-out POST might still be running. Never issue it again.
                    raise Invalid('Repository creation is unconfirmed. Inspect GitHub and retry this same plan later; no second creation request was sent.')
                repo=self.verify_repo(row,repo)
                row=self.update(row,'repository_created',repository_id=repo['id'])
                source,head=self.checkout(row)
                row=self.update(row,'initial_commit_ready',initial_commit=head)
                refs=self.tracker.git(source,'ls-remote','--heads','origin',network=True).splitlines()
                expected=head+'\trefs/heads/main'
                if not json.loads(row['data']).get('baseline_verified'):
                    if refs and refs!=[expected]:raise Invalid('Remote has other commits or branches; no default branch will be overwritten')
                    if not refs:self.tracker.git(source,'push','origin','HEAD:refs/heads/main',network=True)
                if self.tracker.git(source,'ls-remote','--heads','origin','refs/heads/main',network=True)!=expected:
                    raise Invalid('Initial empty main push is not verified')
                self.tracker.git(source,'update-ref','refs/remotes/origin/main',head)
                row=self.update(row,'baseline_uploaded',baseline_verified=True)
                self.tracker.attach(row['workspace'],source,github_url('https://github.com/'+row['owner']+'/'+row['repo']),None,'main',mode='bot_branch')
                row=self.update(row,'workspace_attached')
                session=self.state.session(json.loads(row['data']).get('session'))
                if session and session['state']=='creating':raise Invalid('Bot creation is incomplete; inspect its existing session before continuing. No duplicate group or Codex thread was created.')
                if not session:
                    session,created=self.state.reserve_session(row['workspace'])
                    if not created and session['state']!='ready':raise Invalid('Existing project conversation needs inspection')
                    row=self.update(row,'creating_conversation',session=session['id'])
                    self.bind_initial_turns(row,session,item)
                    if created:
                        # Use the same bounded creation path as /new, but retain
                        # its session before every potentially ambiguous call.
                        self.tracker.prepare_session(session)
                        chat=self.relay.api.create_chat(session,self.state.identity()['owner'])
                        self.state.set_session(session['id'],chat_id=chat)
                        session=self.state.session(session['id'])
                        thread=self.relay.host.create_thread(session)
                        self.state.set_session(session['id'],thread_id=thread,state='ready')
                    session=self.state.session(session['id'])
                self.tracker.record(session,phase='Ready')
                row=self.update(row,'complete',session=session['id'])
                return summary(self.state,row)
            except Exception as error:
                # Steps remain durable, including an ambiguous POST or bot start.
                # Raw HTTP/Git output and credential values never enter the journal.
                if creating_request and isinstance(error,GitHubError) and error.status in {400,401,403,404,422}:
                    # A definite rejection is different from a timed-out POST.
                    # An explicit later apply may retry after account repair.
                    row=self.update(row,'preview')
                message=str(error) if isinstance(error,(Invalid,TrackingError,GitHubError)) else 'The initialization step needs review. Check authentication, network and /init status before retrying the same plan.'
                self.state.db.execute('UPDATE project_initializations SET error_type=?,error_message=?,updated_at=? WHERE id=?',
                    (type(error).__name__,message,time.time(),plan))
                return summary(self.state,self.row(plan))
        finally:os.close(fd)

    def close(self):
        if self.github is not None and hasattr(self.github,'close'):self.github.close()


def text(value):
    body='Project initialization: '+value['workspace']+'\nRepository: '+value['repo_url']+' (private)\nPhase: '+value['phase']+'\n'
    if value['phase']=='preview':
        if value['error_message']:body+='\nPrevious attempt: '+value['error_message']+'\n'
        body+='\n'+'\n'.join(str(i+1)+'. '+step for i,step in enumerate(value['steps']))+'\nStorage: '+value['source_path']
        body+='\n\nReview this plan, then /init apply '+value['plan_id']+'. This authorizes the new repository, its empty initial main, and the bot branch/chat/console. Existing repositories and branches are preserved. No PR or merge.'
    elif value['phase']=='complete':
        body+='\nWorkspace ready. Send ordinary text in its project chat. One permanent branch holds code and history.\n'+(value['chat_url'] or '')
    elif value['error_type']:body+='\n'+value['error_message']+'\nUse /init status '+value['plan_id']+'. Retry only this same plan after fixing the blocker.'
    else:body+='\nInitialization is in progress. /init status '+value['plan_id']
    return body


def command(relay,tokens,item=None):
    manager=ProjectInit(relay)
    try:
        if not tokens:raise Invalid('Use /init WORKSPACE [REPOSITORY], /init apply PLAN_ID or /init status [PLAN_ID]. New projects are private.')
        action=tokens[0].casefold()
        if action=='apply' and len(tokens)==2:return text(manager.apply(tokens[1],item=item))
        if action=='status' and len(tokens) in {1,2}:
            values=[summary(relay.state,manager.row(tokens[1]))] if len(tokens)==2 else jobs(relay.state)
            return '\n\n'.join(text(x) for x in values) or 'No project initialization plans yet.'
        if len(tokens) in {1,2} and action not in {'apply','status'}:
            value=manager.preview(*tokens)
            if item and value['phase']=='preview':
                row=manager.row(value['plan_id']);sequences=json.loads(row['data']).get('preview_seqs',[])
                if item['seq'] not in sequences:manager.update(row,preview_seqs=sequences+[item['seq']])
            return text(value)
        raise Invalid('Use /init WORKSPACE [REPOSITORY], /init apply PLAN_ID or /init status [PLAN_ID]')
    finally:manager.close()
