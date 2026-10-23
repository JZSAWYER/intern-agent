"""Inspect the exact index or new immutable history, without per-blob Git forks."""
import hashlib
import json
import os
from pathlib import Path

from .core import Invalid
from .git_objects import Objects,Proofs
from .live_state import private_json


def index_files(git,directory):
    result={}
    for entry in filter(None,git(directory,'ls-files','--stage','-z').split(b'\0')):
        header,name=entry.split(b'\t',1);mode,sha,stage=header.split()
        if stage!=b'0':raise Invalid('Unmerged Git index needs review')
        result[name.decode()]=(mode,sha.decode())
    return result


def tree(git,directory,tip):
    result={}
    for entry in filter(None,git(directory,'ls-tree','-r','-z',tip,'--','.intern_workspace').split(b'\0')):
        header,name=entry.split(b'\t',1);mode,kind,sha=header.split()
        if kind!=b'blob' and not (mode==b'160000' and kind==b'commit'):raise Invalid('Linked metadata rejected')
        result[name.decode()]=(mode,sha.decode())
    return result


def metadata(directory,files,objects,base_files):
    from .records import validate_tree
    files={name:value for name,value in files.items() if name.startswith('.intern_workspace/')}
    for name,value in list(files.items()):
        if value[0] not in {b'120000',b'160000'}:continue
        # Repositories may inherit a skill-provider link from their default
        # branch. Never follow it or treat it as a record. New/changed links,
        # including temporary additions later deleted, are still rejected.
        if name.startswith('.intern_workspace/.skill_sources/') and base_files.get(name)==value:
            del files[name]
        else:raise Invalid('Linked metadata rejected')
    documents=[(name,objects.blob(value[1]).decode()) for name,value in files.items() if name.endswith('.md')]
    validate_tree(directory,documents)
    if '.intern_workspace/recovery.json' in files:
        from .recovery import validate
        def read(name):
            if name not in files:raise Invalid('Referenced recovery part is missing')
            return objects.blob(files[name][1])
        validate(json.loads(read('.intern_workspace/recovery.json')),documents,read)


def worktree(tracker,binding):
    """Validate outgoing records without rewriting inherited repository data."""
    from .records import validate_tree
    root=Path(binding['metadata_path'])
    base=tracker.git(root,'rev-parse','refs/remotes/origin/'+binding['base_branch'])
    from .git_hooks import git
    inherited=tree(git,root,base);head=tree(git,root,'HEAD');documents=[]
    names=set(filter(None,tracker.git(root,'ls-files','--cached','--others','--exclude-standard','-z','--','.intern_workspace').split('\0')))
    with Objects(root) as objects:
        for name in sorted(names):
            path=root/name;original=inherited.get(name)
            unchanged=bool(original and original==head.get(name))
            if original and original[0] in {b'120000',b'160000'}:
                if not unchanged or not name.startswith('.intern_workspace/.skill_sources/'):raise Invalid('Linked records cannot be published')
                if original[0]==b'160000':continue  # Do not enter a dependency checkout.
                if path.is_symlink() and os.readlink(path).encode()==objects.blob(original[1]):continue
            if not path.exists() and not path.is_symlink():continue
            if path.is_symlink() or any(p.is_symlink() for p in path.parents if p!=root and p.is_relative_to(root)):
                raise Invalid('Linked records cannot be published')
            if not path.is_file():raise Invalid('Unexpected record type or size')
            size=path.stat().st_size
            if unchanged and bool(path.stat().st_mode&0o111)==(original[0]==b'100755'):
                digest=(hashlib.sha1 if len(original[1])==40 else hashlib.sha256)(b'blob '+str(size).encode()+b'\0')
                with path.open('rb') as stream:
                    for chunk in iter(lambda:stream.read(65536),b''):digest.update(chunk)
                if digest.hexdigest()==original[1]:
                    if path.suffix=='.md':documents.append((name,path.read_text()))
                    continue
            if path.suffix not in {'.md','.txt','.json'} or size>2*1024*1024:
                raise Invalid('Unexpected record type or size')
            data=path.read_bytes();text=data.decode()
            # An unchanged default-branch document is already part of the
            # project. It is not new outgoing relay data. Any edit loses this
            # exception and must pass the current privacy policy.
            if any(word in path.name.lower() for word in ['credential','secret','token','.env']):raise Invalid('Private record path rejected')
            if tracker.clean(text)!=text:raise Invalid('Credential material in records')
            if path.suffix=='.md':documents.append((name,text))
    validate_tree(root/'.intern_workspace',documents)
    recovery=root/'.intern_workspace/recovery.json'
    if recovery.exists():
        from .recovery import validate
        def read(name):
            path=root/name
            if (path.is_symlink() or any(p.is_symlink() for p in path.parents if p!=root and p.is_relative_to(root))
                    or not path.resolve().is_relative_to(root.resolve())):raise Invalid('Linked recovery part rejected')
            return path.read_text()
        validate(json.loads(read('.intern_workspace/recovery.json')),documents,read)


def run(config_path,phase,lines=()):
    from . import git_hooks as hooks
    git=hooks.git;config=private_json(config_path);directory=config['directory']
    hooks.verified_path(config_path,directory)
    branch=git(directory,'symbolic-ref','--short','HEAD').decode().strip()
    if branch not in config['branches'] or branch in {'main','master'}:raise Invalid('Unregistered Git branch rejected')
    combined=config.get('combined_records',False)
    base=git(directory,'rev-parse','--verify','refs/remotes/origin/'+config.get('base_branch','main')).decode().strip()
    with Objects(directory) as objects:
        base_files=tree(git,directory,base) if config['management'] or combined else {}
        if phase=='pre-commit':
            files=index_files(git,directory)
            previous=tree(git,directory,'HEAD') if config['management'] or combined else {}
            names=git(directory,'diff','--cached','--name-only','-z').decode().split('\0')
            for name in filter(None,names):
                is_metadata=name.startswith('.intern_workspace/')
                before=objects.blob(previous[name][1]) if name in previous and previous[name][0] not in {b'120000',b'160000'} else None
                if name not in files:
                    if before and name.endswith('.md') and b'intern-agent.record.v2' in before:
                        raise Invalid('Versioned records cannot be deleted; append a correction instead')
                    continue
                mode,sha=files[name]
                if mode in {b'120000',b'160000'}:raise Invalid('Linked or submodule file rejected')
                after=objects.blob(sha);hooks.validate(name,after,is_metadata if combined else config['management'])
                if (config['management'] or combined and is_metadata) and name.endswith('.md') and before is not None:
                    from .records import validate_evolution
                    validate_evolution(before.decode(),after.decode())
            if config['management'] or combined:metadata(directory,files,objects,base_files)
        elif phase=='pre-push':
            proofs=Proofs(config_path,config,base)
            for line in lines:
                local,tip,remote,_=line.split()
                if local!=remote or remote not in {'refs/heads/'+b for b in config['branches']} or not tip.strip('0'):
                    raise Invalid('Unregistered push or deletion rejected')
                management=config['management'] and remote=='refs/heads/'+branch
                start=proofs.start(directory,remote,tip,base)
                if start==tip:continue
                revision=start+'..'+tip
                for name in filter(None,git(directory,'log','--format=','--name-only','-z',revision).decode().split('\0')):
                    name=name.strip('\n')
                    if name and not combined and management!=name.startswith('.intern_workspace/'):
                        raise Invalid('Unpublished history crosses branch boundary')
                for entry in git(directory,'rev-list','--objects',revision).decode().splitlines():
                    sha,_,name=entry.partition(' ');kind,data=objects.get(sha)
                    if kind==b'blob':hooks.validate(name,data,name.startswith('.intern_workspace/') if combined else management)
                if management or combined:
                    from .records import validate_evolution
                    metadata(directory,tree(git,directory,tip),objects,base_files)
                    for commit in git(directory,'rev-list','--reverse',revision).decode().splitlines():
                        parent=git(directory,'rev-parse',commit+'^').decode().strip()
                        changed=git(directory,'diff','--raw','--no-abbrev','--no-renames','-z',parent,commit,'--','.intern_workspace').split(b'\0')
                        for index in range(0,len(changed)-1,2):
                            header,name=changed[index],changed[index+1].decode()
                            old_mode,new_mode,old_sha,new_sha,status=header.split()
                            if new_mode in {b'120000',b'160000'}:raise Invalid('Linked metadata rejected')
                            if not name.endswith('.md') or not old_sha.strip(b'0'):continue
                            before=objects.blob(old_sha.decode())
                            if b'intern-agent.record.v2' in before:
                                if not new_sha.strip(b'0'):raise Invalid('Versioned record deletion rejected')
                                validate_evolution(before.decode(),objects.blob(new_sha.decode()).decode())
                proofs.save(remote,tip)
        else:raise Invalid('Unknown hook phase')
