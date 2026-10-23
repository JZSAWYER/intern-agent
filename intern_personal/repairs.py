"""Reviewed local repairs with stale-plan checks and private rollback receipts."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat

from .core import Invalid,github_url,valid_branch
from .live_state import private_json,write_private


def parent(state):
    path=state.runtime/'repair-plans';path.mkdir(mode=0o700,exist_ok=True)
    if path.is_symlink() or path.stat().st_uid!=os.getuid() or path.stat().st_mode&0o077:raise Invalid('Repair receipts must be private')
    return path


def fingerprint(path):
    path=Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_uid!=os.getuid():raise Invalid('Repair target must be an owned regular file')
    s=path.stat()
    return dict(sha256=hashlib.sha256(path.read_bytes()).hexdigest(),mode=stat.S_IMODE(s.st_mode),inode=s.st_ino)


def guard_policy(state,binding):
    from .git_hooks import git
    directory=Path(binding['directory']).resolve(strict=True);file=Path(binding['config_path'])
    expected=state.runtime/'git-hooks'/hashlib.sha256(str(directory).encode()).hexdigest()[:20]/'binding.json'
    if file!=expected:raise Invalid('Guard configuration location is not registered')
    config=private_json(file)
    branches=config.get('branches',[]);base=config.get('base_branch')
    if (config.get('directory')!=str(directory) or not valid_branch(base) or not branches
            or any(not valid_branch(b) or b==base for b in branches)
            or type(config.get('management')) is not bool or type(config.get('combined_records',False)) is not bool):raise Invalid('Unsafe guard policy requires manual review')
    origin=github_url(git(directory,'remote','get-url','origin').decode().strip())
    registered=[github_url(r[0]) for r in state.db.execute('SELECT repo_url FROM tracked_workspaces')]
    if origin not in registered:raise Invalid('Guard repository is not an attached GitHub workspace')
    if git(directory,'branch','--show-current').decode().strip() not in branches:raise Invalid('Guard branch changed; inspect it manually')
    return config


def build(state):
    actions=[];manual=[]
    for name in ['live.json','credentials.json']:
        path=state.root/name
        if path.exists():
            before=fingerprint(path)
            if before['mode']!=0o600:actions.append(dict(kind='permissions',path=str(path),before=before,mode=0o600))
    from .git_hooks import verified_path
    for binding in state.db.execute('SELECT * FROM git_hook_bindings ORDER BY directory'):
        try:verified_path(binding['config_path'],binding['directory'])
        except Exception:
            try:
                config=guard_policy(state,binding)
                before={name:fingerprint(Path(binding['config_path']).parent/name) for name in ['binding.json','pre-commit','pre-push']}
                actions.append(dict(kind='guard',path=binding['config_path'],directory=binding['directory'],before=before,
                    branches=config['branches'],base_branch=config['base_branch'],management=config['management'],combined_records=config.get('combined_records',False)))
            except Exception:manual.append('A Git guard cannot be safely reconstructed; inspect its registered policy and branch manually.')
    source=Path(__file__).parent
    release=hashlib.sha256(b''.join(p.name.encode()+p.read_bytes() for p in sorted(source.glob('*.py')))).hexdigest()
    return dict(schema='intern-agent.repair.v1',actions=actions,manual=manual,source_digest=release,
                policy='Local permission/guard repairs only. No packages, credentials, remotes, services or account changes.')


def preview(state):
    plan=build(state);key=hashlib.sha256(json.dumps(plan,sort_keys=True).encode()).hexdigest()
    write_private(parent(state)/(key+'.json'),plan)
    return dict(plan_id=key,**plan)


def identifier(key):
    import re
    if not isinstance(key,str) or not re.fullmatch('[0-9a-f]{64}',key):raise Invalid('Use the exact reviewed repair plan ID')


def restore_actions(state,receipt):
    from .git_hooks import git
    for backup in reversed(receipt['backups']):
        if backup['kind']=='permissions':Path(backup['path']).chmod(backup['mode'])
        else:
            for name,text in backup['files'].items():
                p=Path(backup['path'])/name;p.write_text(text);p.chmod(backup['modes'][name])
            git(backup['directory'],'config','--worktree','core.hooksPath',backup['path'])


def apply(state,key):
    identifier(key);file=parent(state)/(key+'.json');plan=private_json(file)
    if plan!=build(state):raise Invalid('Repair plan is stale; preview the current state again')
    if any(a['kind']=='guard' for a in plan['actions']) and state.db.execute("SELECT 1 FROM events WHERE state IN ('running','awaiting_terminal') LIMIT 1").fetchone():
        raise Invalid('Finish running turns before repairing Git guards')
    receipt_file=parent(state)/(key+'.receipt.json')
    if receipt_file.exists():raise Invalid('Repair plan was already applied; preview again')
    backups=[]
    for action in plan['actions']:
        if action['kind']=='permissions':backups.append(dict(kind='permissions',path=action['path'],mode=action['before']['mode']))
        else:
            path=Path(action['path']).parent
            backups.append(dict(kind='guard',path=str(path),directory=action['directory'],files={n:(path/n).read_text() for n in action['before']},modes={n:action['before'][n]['mode'] for n in action['before']}))
    receipt=dict(plan_id=key,backups=backups,after={},state='applying')
    write_private(receipt_file,receipt)
    try:
        from .deployment import prepare_runtime
        release=prepare_runtime(state,Path(__file__).parent.parent)
        spec=importlib.util.spec_from_file_location('intern_personal._reviewed_guard',release/'intern_personal/git_hooks.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        for action in plan['actions']:
            if action['kind']=='permissions':Path(action['path']).chmod(action['mode'])
            else:module.install(state,action['directory'],action['branches'],management=action['management'],base_branch=action['base_branch'],combined_records=action['combined_records'])
        for action in plan['actions']:
            receipt['after'][action['path']]=fingerprint(action['path'])
            if action['kind']=='guard':
                for name in ['pre-commit','pre-push']:receipt['after'][str(Path(action['path']).parent/name)]=fingerprint(Path(action['path']).parent/name)
        receipt['state']='applied';write_private(receipt_file,receipt)
    except BaseException:
        restore_actions(state,receipt);receipt['state']='rolled_back';write_private(receipt_file,receipt);raise
    return dict(applied=True,actions=len(backups),rollback_id=key)


def rollback(state,key):
    identifier(key);file=parent(state)/(key+'.receipt.json');receipt=private_json(file)
    if receipt['state']!='applied':raise Invalid('This repair is not awaiting rollback')
    if any(fingerprint(path)!=before for path,before in receipt['after'].items()):raise Invalid('Repair targets changed; inspect them before rollback')
    restore_actions(state,receipt);receipt['state']='rolled_back';write_private(file,receipt)
    return dict(rolled_back=True,actions=len(receipt['backups']))
