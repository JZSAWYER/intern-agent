"""Combined code/history branches for new bots. Legacy branches stay intact."""
import json
from pathlib import Path
import time

from .core import Invalid,WORKSPACE,github_url,valid_branch
from .git_hooks import install
from .records import validate_tree


def attach(tracker,workspace,source,repo_url,base_branch):
    if not WORKSPACE.fullmatch(workspace) or not valid_branch(base_branch):raise Invalid('Invalid workspace or base branch')
    source=Path(source).expanduser().resolve(strict=True);repo_url=github_url(repo_url)
    old=tracker.binding(workspace)
    if old:
        if (old['repo_url'],old['source_path'],old['base_branch'])!=(repo_url,str(source),base_branch):raise Invalid('Workspace already attached to a different repository')
        return tracker.require_workspace(workspace)
    binding=dict(workspace=workspace,repo_url=repo_url,source_path=str(source),metadata_path=str(source),
                 metadata_branch='',base_branch=base_branch,metadata_mode='bot_branch',published=1)
    tracker.verify_origin(binding)
    if tracker.git(source,'status','--porcelain'):raise Invalid('Attach a clean isolated checkout; existing changes are preserved')
    tracker.git(source,'rev-parse','--verify','refs/remotes/origin/'+base_branch)
    tracker.state.register_workspace(workspace,source)
    tracker.state.db.execute('INSERT INTO tracked_workspaces('+','.join(binding)+') VALUES('+','.join('?' for _ in binding)+')',tuple(binding.values()))
    return tracker.binding(workspace)


def seed(tracker,binding,intern,session,parent):
    root=Path(binding['metadata_path'])/'.intern_workspace'
    workspace=session['workspace']
    # Shared legacy guidance is reference data. Copy files only for this new bot.
    for name in ['project_rule.txt','ERROR_BOOK.md']:
        source=Path(parent['metadata_path'])/'.intern_workspace'/name
        if not (root/name).exists() and source.is_file() and not source.is_symlink():
            text=source.read_text()
            if name=='project_rule.txt':text=text.replace('Keep management records on the dedicated metadata branch.', 'Keep code and management records on this bot\'s single registered branch.')
            tracker.write(binding,'.intern_workspace/'+name,text)
    if not (root/'project_rule.txt').exists():
        tracker.write(binding,'.intern_workspace/project_rule.txt',"Use this bot's registered workspace and permanent branch. Follow AGENTS.md and user-owned instructions.\nKeep code and Markdown history together. Named tasks do not create branches. Never create PRs without explicit user instruction.\n")
    if not (root/'ERROR_BOOK.md').exists():tracker.write(binding,'.intern_workspace/ERROR_BOOK.md','# Workspace error book\n\nNo recorded project errors yet.\n')
    if not (root/'workspace.json').exists():
        tracker.write(binding,'.intern_workspace/workspace.json',json.dumps(dict(schema='intern-agent.workspace.v1',workspace_id=workspace,
            repo_url=parent['repo_url'],provider='github',metadata_mode='bot_branch',metadata_branch=intern['code_branch'],
            default_branch=parent['base_branch'],pull_requests='explicit_user_request_only'),indent=2)+'\n')
    if not (root/'README.md').exists():tracker.write(binding,'.intern_workspace/README.md',
        '# Intern Agent bot records\n\nCode and validated Markdown records share this bot\'s one permanent branch.\nWorkspace records are a snapshot at this bot\'s latest upload; other bots keep their own branches. No automatic PR or merge.\n')
    knowledge='.intern_workspace/interns/'+intern['intern_name']+'/knowledge.md'
    if not (Path(binding['metadata_path'])/knowledge).exists():tracker.write(binding,knowledge,
        '# Intern knowledge\n\nThis conversation belongs to workspace '+workspace+'. Its code and history stay on its one permanent branch.\n')


def prepare(tracker,session,directory=None,branch=None,base_ref=None):
    state=tracker.state;parent=tracker.require_workspace(session['workspace'])
    with tracker.lock(session['workspace']):
        intern=tracker.intern(session)
        if not intern:
            restored=state.db.execute('SELECT * FROM restored_conversations WHERE session=?',(session['id'],)).fetchone()
            # Record IDs allow 101 characters: retain the unique suffix and
            # limit the generated label without changing the workspace/branch.
            name=(restored['intern_name'] if restored else None) or 'intern_'+session['workspace'].lower().replace('-','_')[:85]+'_'+session['id'][-8:]
            branch=branch or (restored['code_branch'] if restored else None) or 'intern-agent/'+session['workspace']+'/'+session['id'][-8:]
            if not valid_branch(branch) or branch==parent['base_branch']:raise Invalid('Bot needs its own non-default branch')
            remote=tracker.git(parent['source_path'],'ls-remote','--heads','origin','refs/heads/'+branch,network=True)
            if directory is not None:
                directory=str(Path(directory).resolve())
                if directory!=parent['source_path']:raise Invalid('Existing terminal must use its registered source')
                tracker.verify_branch(directory,branch)
                if not remote or remote.split()[0]!=tracker.git(directory,'rev-parse','HEAD'):raise Invalid('Publish existing terminal branch before attaching')
            else:
                if remote and not restored:raise Invalid('Bot branch already exists; inspect it before adoption')
                if restored and not remote:raise Invalid('Recovered bot branch is missing')
                directory=state.runtime/'worktrees'/session['workspace']/name;directory.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
                if restored:tracker.git(parent['source_path'],'fetch','origin','refs/heads/'+branch,network=True)
                base=remote.split()[0] if restored else 'refs/remotes/origin/'+parent['base_branch']
                if base_ref is not None:
                    prefix='refs/remotes/origin/'
                    if restored or not isinstance(base_ref,str) or not base_ref.startswith(prefix) or not valid_branch(base_ref[len(prefix):]):
                        raise Invalid('A new bot can fork only a published GitHub branch')
                    published=tracker.git(parent['source_path'],'ls-remote','--heads','origin','refs/heads/'+base_ref[len(prefix):],network=True)
                    local=tracker.git(parent['source_path'],'rev-parse','--verify',base_ref)
                    if not published or published.split()[0]!=local:raise Invalid('Publish the selected bot baseline before forking')
                    base=local
                tracker.create_worktree(parent['source_path'],directory,branch,base)
            state.db.execute('''INSERT INTO tracked_interns(session,intern_name,code_path,code_branch,home_branch,metadata_mode)
                VALUES(?,?,?,?,?,'bot_branch')''',(session['id'],name,str(directory),branch,branch))
            intern=tracker.intern(session)
            install(state,directory,[branch],base_branch=parent['base_branch'],combined_records=True)
        binding=tracker.record_binding(session)
        seed(tracker,binding,intern,session,parent)
        tracker.write_intern(binding,intern,session,'Creating')
        from .record_projection import project
        project(tracker,binding)
        publish(tracker,binding)
        state.db.execute('UPDATE tracked_interns SET published=1 WHERE session=?',(session['id'],))
        return tracker.intern(session)


def publish(tracker,binding,extra_branch=None):
    if extra_branch and extra_branch!=binding['metadata_branch']:raise Invalid('A bot upload may touch only its permanent branch')
    tracker.verify_origin(binding);root=Path(binding['metadata_path']);tracker.verify_branch(root,binding['metadata_branch'])
    from .git_guard_run import worktree
    worktree(tracker,binding)
    staged=tracker.git(root,'diff','--cached','--name-only').splitlines()
    if any(not n.startswith('.intern_workspace/') for n in staged):raise Invalid('Pre-existing staged code needs review before recording history')
    tracker.git(root,'add','--sparse','--','.intern_workspace')
    if tracker.git(root,'diff','--cached','--name-only'):tracker.git(root,'commit','-m','Update Intern Agent bot records')
    tracker.audit_history(root,binding)
    from .turn_uploads import committed
    committed(tracker.state,binding,tracker.git(root,'rev-parse','HEAD'))
    branch=binding['metadata_branch'];tracker.git(root,'push','origin','refs/heads/'+branch+':refs/heads/'+branch,network=True)
    tracker.state.db.execute('DELETE FROM bot_tracking_pending WHERE session=?',(binding['record_session'],))


def record(tracker,session,item=None,phase='Ready'):
    tracker.code_directory(session)
    with tracker.code_lock(session):
        binding=tracker.record_binding(session)
        binding=dict(binding,publication_session=session['id'])
        if item and phase=='Reply ready':
            from .turn_uploads import retry_commit
            if retry_commit(tracker,session,item,binding):return
        if item:tracker.state.ledger.observe(item,phase)
        if item and phase=='Reply ready':
            from .turn_uploads import prepare
            prepare(tracker,session,item,binding)
        from .record_projection import project
        revision=project(tracker,binding)
        selected=tracker.state.ledger.selection(session['id'])
        tracker.write_intern(binding,tracker.intern(session),session,phase,selected['id'] if selected else '')
        publish(tracker,binding)
        if item and phase=='Reply ready':
            from .turn_uploads import uploaded
            uploaded(tracker,session,item,binding)
        tracker.state.db.execute('UPDATE turn_records SET projected_revision=revision WHERE conversation=? AND revision<=?',(session['id'],revision))


def queue(tracker,session,item=None,phase='Ready'):
    from .turn_uploads import pending
    if tracker.state.ledger.busy(session['id']) or pending(tracker.state,session):
        tracker.state.db.execute('INSERT OR REPLACE INTO bot_tracking_pending VALUES(?,NULL,?)',(session['id'],time.time()+10))
        return False
    try:record(tracker,session,item,phase);return True
    except Exception as error:
        tracker.state.db.execute('INSERT OR REPLACE INTO bot_tracking_pending VALUES(?,?,?)',(session['id'],type(error).__name__,time.time()+60))
        return False


def retry(tracker):
    for row in tracker.state.db.execute('SELECT session FROM bot_tracking_pending WHERE retry_at<=?',(time.time(),)).fetchall():
        session=tracker.state.session(row[0])
        if session:queue(tracker,session)
