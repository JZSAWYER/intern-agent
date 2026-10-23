"""One branch per new bot, with compatible legacy metadata tracking; no PRs."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import time

from .core import Invalid, WORKSPACE, github_url, git_environment, valid_branch
from .distribution import KEY_PATTERN


class TrackingError(RuntimeError):
    pass


def secret_values(state):
    values=[]
    file=state.root/'credentials.json'
    if file.exists() or file.is_symlink():values.extend(state.credentials().values())
    from .github_auth import path,token
    file=path(state)
    if file.exists() or file.is_symlink():values.append(token(state))
    return values


class WorkspaceTracking:
    def __init__(self, state):
        self.state = state

    def git(self, directory, *args, network=False):
        env = git_environment()
        # Only explicitly registered, hash-checked guards can run. Arbitrary
        # repository hooks stay disabled. Local reads never invoke helpers.
        if network:
            env.update(HOME=str(Path.home()), GIT_ALLOW_PROTOCOL="https:ssh",
                       GIT_SSH_COMMAND="ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=15",
                       GIT_LFS_SKIP_SMUDGE="1")
            env.pop("GIT_NO_LAZY_FETCH", None)
            if os.environ.get("SSH_AUTH_SOCK"):
                env["SSH_AUTH_SOCK"] = os.environ["SSH_AUTH_SOCK"]
            env['PYTHONDONTWRITEBYTECODE']='1'
            configured=self.state.root/'live.json'
            proxy=self.state.config().get('codex_proxy') if configured.exists() or configured.is_symlink() else None
            if proxy:env['https_proxy']=proxy
        hook_path='/dev/null'
        row=self.state.db.execute('SELECT config_path FROM git_hook_bindings WHERE directory=?',
                                  (str(Path(directory).resolve()),)).fetchone()
        if row:
            from .git_hooks import verified_path
            hook_path=str(verified_path(row['config_path'],directory))
        command = ["/usr/bin/git", "-c", "core.hooksPath="+hook_path, "-c", "submodule.recurse=false",
                   "-c", "user.name=Intern Agent", "-c", "user.email=intern-agent@users.noreply.github.com",
                   "-C", str(directory), *args]
        if network:
            from .github_auth import git_options
            command[1:1]=git_options(self.state)
        try:
            with subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, encoding="utf-8", errors="replace", start_new_session=True) as process:
                try:
                    stdout, stderr = process.communicate(timeout=90)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:process.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL);process.communicate()
                    raise
                result_code=process.returncode
        except (OSError,subprocess.TimeoutExpired) as error:
            raise TrackingError("Git operation unavailable: " + type(error).__name__) from None
        if result_code:
            # Git stderr may contain authenticated URLs or local identity details.
            from .git_diagnostics import failure
            raise failure(args[0],result_code,stderr)
        return stdout.strip()

    @contextmanager
    def lock(self, workspace):
        name = hashlib.sha256(workspace.encode()).hexdigest()[:20]
        fd = os.open(self.state.runtime / ("tracking-"+name+".lock"), os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def code_lock(self,session):
        intern=self.intern(session)
        # A combined bot owns this checkout and branch. Its Git upload must not
        # block another bot in the same logical workspace. Legacy shared record
        # branches retain their workspace lock.
        key=('code:'+session['workspace']+':'+str(Path(intern['code_path']).resolve())
             if intern and intern.get('metadata_mode')=='bot_branch' else session['workspace'])
        return self.lock(key)

    def binding(self, workspace):
        row=self.state.db.execute("SELECT * FROM tracked_workspaces WHERE workspace=?",(workspace,)).fetchone()
        return dict(row) if row else None

    def intern(self, session):
        row=self.state.db.execute("SELECT * FROM tracked_interns WHERE session=?",(session["id"],)).fetchone()
        return dict(row) if row else None

    def record_binding(self,session):
        binding=self.require_workspace(session['workspace']);intern=self.intern(session)
        if intern and intern.get('metadata_mode')=='bot_branch':
            return dict(binding,metadata_path=intern['code_path'],metadata_branch=intern['code_branch'],
                        metadata_mode='bot_branch',record_session=session['id'],
                        workspace_metadata_branch=binding['metadata_branch'] if binding.get('metadata_mode')=='metadata_branch' else None)
        return binding

    def verify_origin(self,binding):
        urls=self.git(binding["source_path"],"config","--get-all","remote.origin.url").splitlines()
        pushes=self.git(binding["source_path"],"remote","get-url","--push","--all","origin").splitlines()
        if not urls or not pushes or any(github_url(url)!=binding["repo_url"] for url in urls+pushes):
            raise Invalid("Workspace origin must match its explicitly attached GitHub repository")

    def verify_branch(self,directory,branch):
        try:
            actual=self.git(directory,"symbolic-ref","--short","HEAD") if Path(directory).is_dir() else None
        except TrackingError:
            actual=None
        if actual!=branch:
            raise Invalid("Workspace branch changed; restore its registered worktree before continuing")

    def create_worktree(self,source,directory,branch,base):
        try:sparse=self.git(source,"config","--bool","--get","core.sparseCheckout")=="true"
        except TrackingError:sparse=False
        if not sparse:
            self.git(source,"worktree","add","-b",branch,str(directory),base,network=True)
            return
        # Preserve deliberate source checkout coverage in each new worktree.
        # Tracked excluded artifacts remain in Git's tree; they are not deleted.
        source_patterns=Path(self.git(source,"rev-parse","--git-path","info/sparse-checkout"))
        if not source_patterns.is_absolute():source_patterns=Path(source)/source_patterns
        patterns=source_patterns.read_text()
        self.git(source,"worktree","add","--no-checkout","-b",branch,str(directory),base)
        self.git(directory,"config","--worktree","core.sparseCheckout","true")
        self.git(directory,"config","--worktree","core.sparseCheckoutCone","false")
        target=Path(self.git(directory,"rev-parse","--git-path","info/sparse-checkout"))
        if not target.is_absolute():target=Path(directory)/target
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(patterns+"\n/.intern_workspace/\n")
        self.git(directory,"read-tree","-mu","HEAD")

    def require_workspace(self,workspace):
        binding=self.binding(workspace)
        if not self.state.workspace(workspace) or not binding or not binding["published"]:
            raise Invalid("Attach this GitHub workspace first. Unknown names never create empty workspaces.")
        self.verify_origin(binding)
        if binding.get('metadata_mode')!='bot_branch':self.verify_branch(binding["metadata_path"],binding["metadata_branch"])
        return binding

    def code_directory(self,session):
        self.require_workspace(session["workspace"])
        intern=self.intern(session)
        if not intern or not intern["published"]:
            raise Invalid("Intern has no published workspace/branch record")
        self.verify_branch(intern["code_path"],intern["code_branch"])
        return intern["code_path"]

    def clean(self,text):
        text=str(text)
        # Do not copy transport identity, credentials or signed URLs into Git.
        for value in secret_values(self.state):
            text=text.replace(value,"[redacted]")
        text=re.sub(r'\b(?:ou|oc|om|cli)_[A-Za-z0-9_]+\b','[redacted identity]',text)
        # Apply the same policy to plain text and quoted Markdown. Tutorial CODE
        # is a placeholder; actual codes are private. Consume the whole existing
        # marker so repeated projection/archival cannot grow "code]" suffixes.
        text=re.sub(r'(?im)^((?:[ \t]*>[ \t]*)*[ \t]*/(?:pair|approve|deny|answer)[ \t]+)(\[redacted code\]|\S+)',
                    lambda m:m[1]+(m[2] if m[2] in {'CODE','[redacted code]'} else '[redacted code]'),text)
        text=KEY_PATTERN.sub("[redacted]",text)
        text=re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----","[redacted]",text,flags=re.S)
        text=re.sub(r"https?://\S*(?:token|secret|signature|authorization)[=:]\S*","[redacted URL]",text,flags=re.I)
        text=re.sub(r"(?im)^.*\b(?:app_secret|api_key|access_token|refresh_token|password)\b[\"']?\s*[:=].*$","[redacted credential field]",text)
        return text

    def code_credentials(self,text):
        from .git_hooks import code_credentials
        return code_credentials(text,secret_values(self.state))

    def write(self,binding,relative,text):
        root=Path(binding["metadata_path"])
        destination=root/relative
        if not relative.startswith(".intern_workspace/") or not destination.resolve().is_relative_to(root.resolve()):
            raise Invalid("Metadata path escapes its management worktree")
        cursor=root
        for part in Path(relative).parts:
            cursor=cursor/part
            if cursor.is_symlink():raise Invalid("Linked metadata path is not writable")
        text=self.clean(text)
        if destination.is_file() and destination.read_text()==text:return
        destination.parent.mkdir(parents=True,exist_ok=True)
        fd,temp=tempfile.mkstemp(prefix=".record-",dir=destination.parent)
        try:
            with os.fdopen(fd,"w") as stream:
                stream.write(text);stream.flush();os.fsync(stream.fileno())
            os.replace(temp,destination)
        finally:
            Path(temp).unlink(missing_ok=True)

    def publish(self,binding,extra_branch=None):
        if binding.get('metadata_mode')=='bot_branch':
            from .bot_records import publish
            return publish(self,binding,extra_branch)
        self.verify_origin(binding)
        self.verify_branch(binding["metadata_path"],binding["metadata_branch"])
        root=Path(binding["metadata_path"])
        if extra_branch and self.state.db.execute('SELECT 1 FROM git_hook_bindings WHERE directory=?',(str(root.resolve()),)).fetchone():
            from .git_hooks import install
            install(self.state,root,[binding['metadata_branch'],extra_branch],management=True,base_branch=binding['base_branch'])
        from .git_guard_run import worktree
        worktree(self,binding)
        self.git(root,"add","--",".intern_workspace")
        staged=self.git(root,"diff","--cached","--name-only").splitlines()
        if any(not name.startswith(".intern_workspace/") for name in staged):
            raise Invalid("Management branch has staged changes outside workspace metadata")
        if staged:
            self.git(root,"commit","-m","Update Intern Agent workspace records")
        self.audit_history(root,binding,management=True)
        from .turn_uploads import committed
        committed(self.state,binding,self.git(root,'rev-parse','HEAD'))
        refs=["refs/heads/"+binding["metadata_branch"]+":refs/heads/"+binding["metadata_branch"]]
        if extra_branch:refs.append("refs/heads/"+extra_branch+":refs/heads/"+extra_branch)
        # Atomic, ordinary pushes only: no force, default-branch updates or PRs.
        self.git(root,"push","--atomic","origin",*refs,network=True)
        self.state.db.execute("DELETE FROM tracking_pending WHERE workspace=?",(binding["workspace"],))

    def attach(self,workspace,source,repo_url,metadata_branch=None,base_branch="main",mode='bot_branch'):
        if mode=='bot_branch':
            from .bot_records import attach
            return attach(self,workspace,source,repo_url,base_branch)
        if mode!='metadata_branch':raise Invalid('Unknown branch layout')
        if not WORKSPACE.fullmatch(workspace) or not valid_branch(metadata_branch) or not valid_branch(base_branch):
            raise Invalid("Invalid workspace or branch name")
        if metadata_branch==base_branch:raise Invalid("Management must use a separate new branch")
        repo_url=github_url(repo_url)
        source=Path(source).expanduser().resolve(strict=True)
        self.state.register_workspace(workspace,source)
        with self.lock(workspace):
            old=self.binding(workspace)
            if old:
                if (old["repo_url"],old["source_path"],old["metadata_branch"],old["base_branch"])!=(repo_url,str(source),metadata_branch,base_branch):
                    raise Invalid("Workspace is already attached with a different branch/repository")
                binding=old
            else:
                metadata=self.state.runtime/"metadata"/workspace
                binding={"workspace":workspace,"repo_url":repo_url,"source_path":str(source),"metadata_path":str(metadata),
                         "metadata_branch":metadata_branch,"base_branch":base_branch,"published":0}
                self.verify_origin(binding)
                if self.git(source,"status","--porcelain"):
                    raise Invalid("Attach a clean isolated source checkout; existing changes are preserved")
                remote=self.git(source,"ls-remote","--heads","origin","refs/heads/"+metadata_branch,network=True)
                if remote:raise Invalid("Management branch already exists; choose a new branch or explicitly adopt it")
                # Duplicate local branches/path fail rather than overwrite existing work.
                metadata.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
                self.create_worktree(source,metadata,metadata_branch,"refs/remotes/origin/"+base_branch)
                self.state.db.execute('''INSERT INTO tracked_workspaces
                    (workspace,repo_url,source_path,metadata_path,metadata_branch,base_branch) VALUES(?,?,?,?,?,?)''',
                    (workspace,repo_url,str(source),str(metadata),metadata_branch,base_branch))
            from .git_hooks import install
            install(self.state,binding['metadata_path'],[metadata_branch],management=True,base_branch=base_branch)
            self.write(binding,".intern_workspace/README.md","# Intern Agent workspace management\n\n"
                       "Workspace: "+workspace+"\nRepository: "+repo_url+"\nManagement branch: "+metadata_branch+"\n\n"
                       "Management changes stay on this branch. No pull request is created without explicit user instruction.\n"
                       "Intern code worktrees use separate branches. Machine credentials, chat identities and runtime state are private.\n")
            self.write(binding,".intern_workspace/workspace.json",json.dumps({"schema":"intern-agent.workspace.v1","workspace_id":workspace,
                       "repo_url":repo_url,"provider":"github","metadata_mode":"metadata_branch","metadata_branch":metadata_branch,
                       "default_branch":base_branch,"pull_requests":"explicit_user_request_only"},indent=2)+"\n")
            self.write(binding,".intern_workspace/project_rule.txt","Use the registered workspace and your own code branch. Follow repository AGENTS.md.\n"
                       "Respect the repository's assigned roles and user-owned instruction files.\n"
                       "Keep management records on the dedicated metadata branch. Never create PRs without explicit user instruction.\n")
            if not (Path(binding["metadata_path"])/".intern_workspace/ERROR_BOOK.md").exists():
                self.write(binding,".intern_workspace/ERROR_BOOK.md","# Workspace error book\n\nNo recorded project errors yet.\n")
            self.publish(binding)
            self.state.db.execute("UPDATE tracked_workspaces SET published=1 WHERE workspace=?",(workspace,))
            return self.binding(workspace)

    def prepare_session(self,session,mode=None,base_ref=None):
        existing=self.intern(session)
        restored=self.state.db.execute('SELECT * FROM restored_conversations WHERE session=?',(session['id'],)).fetchone()
        selected=existing['metadata_mode'] if existing else restored['metadata_mode'] if restored else mode or 'bot_branch'
        if selected=='bot_branch':
            from .bot_records import prepare
            return prepare(self,session,base_ref=base_ref)
        if base_ref is not None:raise Invalid('A published baseline is supported only for a new single-branch bot')
        binding=self.require_workspace(session["workspace"])
        with self.lock(session["workspace"]):
            intern=self.intern(session)
            if not intern:
                restored=self.state.db.execute('SELECT * FROM restored_conversations WHERE session=?',(session['id'],)).fetchone()
                name=(restored['intern_name'] if restored else None) or "intern_"+session["workspace"].lower().replace("-","_")[:85]+"_"+session["id"][-8:]
                branch=(restored['code_branch'] if restored else None) or "intern-agent/"+session["workspace"]+"/"+session["id"][-8:]
                directory=self.state.runtime/"worktrees"/session["workspace"]/name
                directory.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
                remote=self.git(binding["source_path"],"ls-remote","--heads","origin","refs/heads/"+branch,network=True)
                if remote and not restored:
                    raise Invalid("Intern branch already exists; inspect it before adoption")
                if restored and not remote:raise Invalid('Recovered code branch is missing on GitHub; inspect it before reconnecting')
                if restored:self.git(binding['source_path'],'fetch','origin','refs/heads/'+branch,network=True)
                self.create_worktree(binding["source_path"],directory,branch,remote.split()[0] if restored else "refs/remotes/origin/"+binding["base_branch"])
                self.state.db.execute("INSERT INTO tracked_interns(session,intern_name,code_path,code_branch,home_branch) VALUES(?,?,?,?,?)",
                                      (session["id"],name,str(directory),branch,branch))
                intern=self.intern(session)
                from .git_hooks import install
                install(self.state,directory,[branch],base_branch=binding['base_branch'])
            self.write_intern(binding,intern,session,"Creating")
            knowledge=".intern_workspace/interns/"+intern["intern_name"]+"/knowledge.md"
            if not (Path(binding['metadata_path'])/knowledge).exists():
                self.write(binding,knowledge,"# Intern knowledge\n\nThis conversation belongs to workspace "+session["workspace"]+".\n"
                           "Follow the role defined by repository AGENTS.md and preserve user-owned instruction files.\n")
            self.publish(binding,intern["code_branch"])
            self.state.db.execute("UPDATE tracked_interns SET published=1 WHERE session=?",(session["id"],))
            return self.intern(session)

    def prepare_existing(self,session,directory,branch,mode='bot_branch'):
        """Adopt an explicitly selected, already published code branch/pane."""
        binding=self.require_workspace(session['workspace'])
        if mode=='bot_branch':
            if self.intern(session):raise Invalid('Session already has an intern worktree')
            from .bot_records import prepare
            return prepare(self,session,directory,branch)
        directory=str(Path(directory).resolve())
        if directory!=binding['source_path'] or branch==binding['base_branch'] or not valid_branch(branch):
            raise Invalid('Existing terminal needs its registered source and a separate code branch')
        self.verify_branch(directory,branch)
        head=self.git(directory,'rev-parse','HEAD')
        remote=self.git(directory,'ls-remote','--heads','origin','refs/heads/'+branch,network=True)
        if not remote or remote.split()[0]!=head:raise Invalid('Publish existing code branch before attaching terminal')
        with self.lock(session['workspace']):
            if self.intern(session):raise Invalid('Session already has an intern worktree')
            name='intern_'+session['workspace'].replace('-','_')[:85]+'_'+session['id'][-8:]
            self.state.db.execute('INSERT INTO tracked_interns(session,intern_name,code_path,code_branch,published) VALUES(?,?,?,?,1)',
                                  (session['id'],name,directory,branch))
            self.state.db.execute('UPDATE tracked_interns SET home_branch=? WHERE session=?',(branch,session['id']))
            from .git_hooks import install
            install(self.state,directory,[branch],base_branch=binding['base_branch'])
            self.write_intern(binding,self.intern(session),session,'Attaching')
            self.write(binding,'.intern_workspace/interns/'+name+'/knowledge.md',
                       '# Intern knowledge\n\nAttached to the existing terminal conversation. Historical transcript remains private on this machine.\n')
            self.publish(binding)
            return self.intern(session)

    def write_intern(self,binding,intern,session,status,task=""):
        latest=self.state.db.execute('SELECT activity FROM turn_records WHERE conversation=? ORDER BY seq DESC LIMIT 1',(session['id'],)).fetchone()
        activity=latest['activity'] if latest else 'waiting'
        selected=self.state.ledger.selection(session['id'])
        task=selected['id'] if selected else ''
        status='Working' if selected else 'Idle'
        model=session.get("model") or self.state.config()["model"]
        effort=session.get("effort") or self.state.config().get("reasoning_effort","high")
        member=self.state.db.execute("SELECT m.role,m.team FROM team_members m JOIN teams t ON t.id=m.team WHERE m.session=? AND t.state='active'",(session['id'],)).fetchone()
        role,team=(member['role'],member['team']) if member else ('independent','')
        record_branch='same as current branch (code and records)' if binding.get('metadata_mode')=='bot_branch' else binding['metadata_branch']
        self.write(binding,".intern_workspace/interns/"+intern["intern_name"]+"/status.md",
                   "# "+intern["intern_name"]+"\n\n<!-- METADATA:STATUS="+status+",TASK="+task+",ROLE="+role+",TEAM_ID="+team+" -->\n\n"
                   "| Field | Value |\n| --- | --- |\n| Workspace | "+session["workspace"]+" |\n| Role | "+role+" |\n"
                   "| Current branch | "+intern["code_branch"]+" |\n| Current PR | none; explicit user request required |\n"
                   "| Management branch | "+record_branch+" |\n| Model | "+model+" |\n| Reasoning effort | "+effort+" |\n| Status | "+status+" |\n| Agent activity | "+activity+" |\n")

    def dirty(self,directory):
        names=set(self.git(directory,'diff','--name-only','-z','HEAD').split('\0'))
        names.update(self.git(directory,'ls-files','--others','--exclude-standard','-z').split('\0'))
        names.discard('')
        result={}
        for name in names:
            path=Path(directory)/name
            result[name]=hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() and not path.is_symlink() else 'absent-or-linked'
        return result

    def begin_turn(self,session,item):
        selected=self.state.ledger.selection(session['id'])
        if selected:
            from .task_branches import switch
            switch(self,session,selected)
        # A background history publisher holds this lock across commit + push.
        # Sampling its temporary unpublished HEAD must not block an untouched
        # request. Legacy task switching takes its own lock above this scope.
        with self.code_lock(session):
            directory=self.code_directory(session)
            record=self.state.ledger.ensure_turn(item)
            if record and record['baseline']:return
            head=self.git(directory,'rev-parse','HEAD')
            branch=self.intern(session)['code_branch']
            remote=self.git(directory,'ls-remote','--heads','origin','refs/heads/'+branch,network=True)
            if not remote or remote.split()[0]!=head:raise Invalid('Publish or isolate existing local commits before starting a tracked turn')
            baseline={'head':head,'dirty':self.dirty(directory),
                      'staged':self.git(directory,'diff','--cached','--name-only','-z').split('\0')}
            from .draft_baselines import capture
            capture(self,session,item,baseline)
            self.state.ledger.update(item,event_phase='code_snapshot',code_start=head,baseline=json.dumps(baseline,sort_keys=True))

    def publish_code(self,session,item=None,push=True):
        binding=self.record_binding(session)
        directory=self.code_directory(session)
        intern=self.intern(session)
        if item:
            from .peer_mail import binding as peer_binding
            if peer_binding(self.state,item['seq'])[1]=='request':
                # Read-only peer inspections do not own background job output
                # or pre-existing project drafts. Publish their records only.
                head=self.git(directory,'rev-parse','HEAD')
                record=self.state.ledger.ensure_turn(item)
                if record['code_start'] and head!=record['code_start']:
                    changes=self.git(directory,'diff','--name-only',record['code_start'],head)
                    if any(not n.startswith('.intern_workspace/') for n in changes.splitlines()):raise Invalid('The code branch changed during a read-only peer inspection')
                self.state.ledger.update(item,code_end=head)
                return head
        if item:
            from .turn_uploads import observation
            saved=self.state.db.execute('SELECT * FROM turn_uploads WHERE seq=?',(item['seq'],)).fetchone()
            if saved:return saved['code_head']
            event=self.state.db.execute('SELECT * FROM events WHERE seq=?',(item['seq'],)).fetchone()
            if event and observation(self.state,dict(event)):
                if binding.get('metadata_mode')=='bot_branch':
                    remote=self.git(directory,'ls-remote','--heads','origin','refs/heads/'+intern['code_branch'],network=True)
                    if not remote:raise Invalid('Historical record upload needs its published branch')
                    changes=self.git(directory,'log','--format=','--name-only','-z',remote.split()[0]+'..HEAD')
                    if any(name.strip('\n') and not name.strip('\n').startswith('.intern_workspace/') for name in changes.split('\0')):
                        raise Invalid('Historical record upload cannot publish unassigned code commits')
                record=self.state.ledger.ensure_turn(dict(event));return record['code_end']
        with self.code_lock(session):
            names=set(self.git(directory,"diff","--name-only","-z","HEAD").split("\0"))
            names.update(self.git(directory,"ls-files","--others","--exclude-standard","-z").split("\0"))
            names.discard("")
            if binding.get('metadata_mode')=='bot_branch':names={n for n in names if not n.startswith('.intern_workspace/')}
            record=self.state.ledger.ensure_turn(item) if item else None
            if record and record['baseline']:
                baseline=json.loads(record['baseline']);now=self.dirty(directory)
                changes={name for name,digest in baseline['dirty'].items() if now.get(name)!=digest
                    and not (binding.get('metadata_mode')=='bot_branch' and name.startswith('.intern_workspace/'))}
                from .draft_baselines import check,review
                check(self,session,item,baseline,changes)
                if any(baseline['staged']):raise Invalid('Pre-existing staged work requires explicit review before recording code')
                reviewed=review(self,session,item,baseline)
                if reviewed:names&=set(reviewed['files'])
                names-=set(baseline['dirty'])-changes
            elif item and names:
                raise Invalid('This native turn has no starting code baseline; review its changes before upload')
            for name in names:
                path=Path(directory)/name
                if (Path(name).is_absolute() or ".." in Path(name).parts or path.is_symlink()
                        or any(part in {".codex",".ssh",".env","runtime","__pycache__",".intern_workspace"} for part in Path(name).parts)
                        or any(word in path.name.lower() for word in ["credential","secret",".pem",".key"])):
                    raise Invalid("Private or linked code file cannot be uploaded")
                if path.is_file():
                    if path.stat().st_size>10*1024*1024:raise Invalid("Large code artifact requires explicit upload review")
                    text=path.read_bytes().decode("utf-8",errors="ignore")
                    if self.code_credentials(text):raise Invalid("Credential material in code; upload blocked")
            # New source files need not occur in the inherited sparse patterns.
            # Explicit inspected paths may be staged without materializing artifacts.
            if names:self.git(directory,"add","--sparse","--",*sorted(names))
            if self.git(directory,"diff","--cached","--name-only"):
                self.git(directory,"commit","-m","Save Intern Agent workspace work")
            self.audit_history(directory,binding)
            if push:self.git(directory,"push","origin","refs/heads/"+intern["code_branch"]+":refs/heads/"+intern["code_branch"],network=True)
            head=self.git(directory,'rev-parse','HEAD')
            if item:self.state.ledger.update(item,code_end=head)
            return head

    def audit_history(self,directory,binding,management=False):
        guard=self.state.db.execute('SELECT config_path FROM git_hook_bindings WHERE directory=?',(str(Path(directory).resolve()),)).fetchone()
        if guard:
            from .git_hooks import run
            from .live_state import private_json
            policy=private_json(guard['config_path'])
            if not policy.get('combined_records') and bool(policy['management'])!=bool(management):raise Invalid('Git audit scope changed')
            branch=self.git(directory,'symbolic-ref','--short','HEAD');head=self.git(directory,'rev-parse','HEAD')
            run(guard['config_path'],'pre-push',['refs/heads/'+branch+' '+head+' refs/heads/'+branch+' '+'0'*40])
            return
        if binding.get('metadata_mode')=='bot_branch':
            from .git_hooks import run
            row=self.state.db.execute('SELECT config_path FROM git_hook_bindings WHERE directory=?',(str(Path(directory).resolve()),)).fetchone()
            if not row:raise Invalid('Combined bot branch needs its registered Git guards')
            branch=binding['metadata_branch'];head=self.git(directory,'rev-parse','HEAD')
            run(row[0],'pre-push',['refs/heads/'+branch+' '+head+' refs/heads/'+branch+' '+'0'*40])
            return
        # Include added-then-deleted blobs, so local commits cannot bypass the
        # working-tree checks. Existing public base history is not republished.
        revision="refs/remotes/origin/"+binding["base_branch"]+"..HEAD"
        names=set(self.git(directory,"log","--format=","--name-only","-z",revision).split("\0"))
        for name in names:
            name=name.strip("\n")
            if not name:continue
            if management and not name.startswith(".intern_workspace/"):
                raise Invalid("Management history changes files outside workspace metadata")
            if not management and (any(part in {".codex",".ssh",".env","runtime","__pycache__",".intern_workspace"} for part in Path(name).parts)
                    or any(word in Path(name).name.lower() for word in ["credential","secret",".pem",".key"])):
                raise Invalid("Private or management path in unpublished code history")
        objects=self.git(directory,"rev-list","--objects",revision).splitlines()
        for entry in objects:
            identity,_,name=entry.partition(" ")
            if self.git(directory,"cat-file","-t",identity)!="blob":continue
            parts=Path(name).parts
            if management:
                if not name.startswith(".intern_workspace/"):
                    raise Invalid("Management history contains changes outside workspace metadata")
            elif (any(part in {".codex",".ssh",".env","runtime","__pycache__",".intern_workspace"} for part in parts)
                  or any(word in Path(name).name.lower() for word in ["credential","secret",".pem",".key"])):
                raise Invalid("Private or management files in unpublished code history")
            limit=(2 if management else 10)*1024*1024
            if int(self.git(directory,"cat-file","-s",identity))>limit:
                raise Invalid("Large unpublished blob requires upload review")
            text=self.git(directory,"cat-file","blob",identity)
            if (self.clean(text)!=text if management else self.code_credentials(text)):
                raise Invalid("Credential material in unpublished history; upload blocked")

    def record(self,session,item=None,phase="Ready"):
        intern=self.intern(session)
        if intern and intern.get('metadata_mode')=='bot_branch':
            from .bot_records import record
            return record(self,session,item,phase)
        binding=dict(self.require_workspace(session["workspace"]),publication_session=session['id'])
        self.code_directory(session)
        intern=self.intern(session)
        with self.lock(session["workspace"]):
            if item and phase=='Reply ready':
                from .turn_uploads import retry_commit
                if retry_commit(self,session,item,binding):return
            if ((item and self.state.db.execute('SELECT 1 FROM events WHERE seq=?',(item['seq'],)).fetchone())
                    or (not item and self.state.db.execute('SELECT 1 FROM record_events WHERE workspace=?',(session['workspace'],)).fetchone())):
                if item:self.state.ledger.observe(item,phase)
                if item and phase=='Reply ready':
                    from .turn_uploads import prepare
                    prepare(self,session,item,binding)
                from .record_projection import project
                revision=project(self,binding)
                latest=self.state.db.execute("SELECT e.* FROM events e WHERE session=? AND role='agent' ORDER BY seq DESC LIMIT 1",(session['id'],)).fetchone()
                selected=self.state.ledger.selection(session['id'])
                self.write_intern(binding,intern,session,latest['state'] if latest else session['state'],selected['id'] if selected else '')
                from .turn_uploads import observation
                history_only=bool(item and observation(self.state,dict(self.state.db.execute('SELECT * FROM events WHERE seq=?',(item['seq'],)).fetchone())))
                self.publish(binding,intern['code_branch'] if phase=='Reply ready' and not history_only else None)
                if item and phase=='Reply ready':
                    from .turn_uploads import uploaded
                    uploaded(self,session,item,binding)
                self.state.db.execute('UPDATE turn_records SET projected_revision=revision WHERE workspace=? AND revision<=?',(binding['workspace'],revision))
                return
            task="task_"+str(item["seq"]) if item else ""
            self.write_intern(binding,intern,session,phase,task)
            if item:
                relative=".intern_workspace/tasks/"+task+"/"
                self.write(binding,relative+"README.md","# "+task+"\n\nWorkspace: "+session["workspace"]+"\nIntern: "+intern["intern_name"]+
                           "\nCode branch: "+intern["code_branch"]+"\nStatus: "+phase+"\n\n## Request\n\n"+item["text"]+"\n")
                history=Path(binding["metadata_path"])/relative/"history_log.md"
                old=history.read_text() if history.exists() else "# Task history\n"
                marker="<!-- event:"+str(item["seq"])+":"+phase+" -->"
                if marker not in old:
                    self.write(binding,relative+"history_log.md",old+"\n"+marker+"\n## "+phase+"\n\n"+(item.get("result") or "")+"\n")
                if item.get("result"):
                    self.write(binding,relative+"task_knowledge.md","# Task knowledge\n\n"+item["result"]+"\n")
            self.publish(binding)

    def queue_record(self,session,item=None,phase="Ready"):
        if not session and item:
            controller=self.state.controller_session()
            if controller and item.get('session')==controller['id']:session=controller
        if session and self.intern(session) and self.intern(session).get('metadata_mode')=='bot_branch':
            from .bot_records import queue
            return queue(self,session,item,phase)
        # Local durable task state is already saved. Retry only publishing its
        # projection, never the agent or message send that produced it.
        if not session and item:
            workspace,_=self.state.ledger.workspace_for(item)
            if not workspace:return True
            binding=self.binding(workspace)
            if binding and binding.get('metadata_mode')=='bot_branch':
                target=self.state.session(self.state.config().get('records_bot_session'))
                if not target or target['workspace']!=workspace or not self.intern(target):raise Invalid('Select an explicit bot branch for controller records')
                from .bot_records import queue
                return queue(self,target,item,phase)
            try:
                self.state.ledger.observe(item,phase)
                binding=self.require_workspace(workspace)
                with self.lock(workspace):
                    from .record_projection import project
                    project(self,binding);self.publish(binding)
                return True
            except Exception as error:
                self.state.db.execute('INSERT OR REPLACE INTO tracking_pending(workspace,error_type,retry_at) VALUES(?,?,?)',(workspace,type(error).__name__,time.time()+60))
                return False
        if not session:return True
        try:
            self.record(session,item,phase)
            return True
        except Exception as error:
            self.state.db.execute("INSERT OR REPLACE INTO tracking_pending(workspace,error_type,retry_at) VALUES(?,?,?)",
                                  (session["workspace"],type(error).__name__,time.time()+60))
            return False

    def retry_pending(self):
        # A parent workspace has no bot branch of its own. Adopt the legacy
        # completion queue into the actual bot destinations before projecting.
        self.state.db.execute('BEGIN IMMEDIATE')
        try:
            self.state.db.execute('''INSERT OR IGNORE INTO bot_tracking_pending
                SELECT i.session,p.error_type,p.retry_at FROM tracking_pending p
                JOIN tracked_workspaces w ON w.workspace=p.workspace AND w.metadata_mode='bot_branch'
                JOIN sessions s ON s.workspace=p.workspace JOIN tracked_interns i ON i.session=s.id AND i.metadata_mode='bot_branch' ''')
            self.state.db.execute('''DELETE FROM tracking_pending WHERE workspace IN
                (SELECT w.workspace FROM tracked_workspaces w JOIN sessions s ON s.workspace=w.workspace
                 JOIN tracked_interns i ON i.session=s.id WHERE w.metadata_mode='bot_branch' AND i.metadata_mode='bot_branch')''')
            self.state.db.execute('COMMIT')
        except BaseException:self.state.db.execute('ROLLBACK');raise
        from .bot_records import retry
        retry(self)
        rows=self.state.db.execute("SELECT workspace FROM tracking_pending WHERE retry_at<=?",(time.time(),)).fetchall()
        for row in rows:
            if any(self.state.ledger.busy(s['id']) for s in self.state.sessions() if s['workspace']==row['workspace']):continue
            workspace=row["workspace"]
            try:
                binding=self.require_workspace(workspace)
                with self.lock(workspace):
                    from .record_projection import project
                    revision=project(self,binding)
                    self.publish(binding)
                    self.state.db.execute('UPDATE turn_records SET projected_revision=revision WHERE workspace=? AND revision<=?',(workspace,revision))
            except Exception as error:
                self.state.db.execute("INSERT OR REPLACE INTO tracking_pending(workspace,error_type,retry_at) VALUES(?,?,?)",
                                      (workspace,type(error).__name__,time.time()+60))

    def describe(self,session):
        binding=self.record_binding(session) if self.intern(session) else self.binding(session["workspace"])
        intern=self.intern(session)
        if not binding or not intern:return "Workspace tracking: unavailable (legacy connectivity-only chat)"
        return ("Workspace: "+session["workspace"]+"\nRepository: "+binding["repo_url"]+
                "\nCode branch: "+intern["code_branch"]+"\nWorking directory: "+intern["code_path"]+
                ("\nRecords: same bot branch" if intern.get('metadata_mode')=='bot_branch' else "\nManagement branch: "+binding["metadata_branch"])+
                "\nRecords: "+binding["repo_url"].removesuffix(".git")+"/tree/"+binding["metadata_branch"]+"/.intern_workspace")
