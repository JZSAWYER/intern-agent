"""Explicit, pinned GitHub skill catalog with bounded inert text resources."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from .core import Invalid,github_url,git_environment
from .feature_state import get,save
from .records import ID

COMMIT=re.compile(r'[0-9a-f]{40}')
LIMIT=1024*1024


def path_check(path):
    p=Path(path)
    if (not isinstance(path,str) or not path or p.is_absolute() or any(x in {'.','..'} or x.startswith('.') for x in path.split('/'))
            or '\\' in path or any(not re.fullmatch(r'[A-Za-z0-9_-]+',x) for x in p.parts)):
        raise Invalid('Choose a relative skill directory with plain path components')
    return path


def validate_catalog(data):
    if not isinstance(data,dict) or set(data)!={'entries','enabled'} or not isinstance(data['entries'],dict) or not isinstance(data['enabled'],dict):raise Invalid('Malformed skill catalog')
    if len(data['entries'])>64:raise Invalid('Skill catalog limit is 64 pinned entries')
    for key,e in data['entries'].items():
        if not ID.fullmatch(key) or not isinstance(e,dict) or set(e)!={'repo','commit','path','digest','name','description'}:raise Invalid('Malformed pinned skill')
        if github_url(e['repo'])!=e['repo'] or not COMMIT.fullmatch(e['commit']) or not re.fullmatch('[0-9a-f]{64}',e['digest']):raise Invalid('Skill needs GitHub provenance and exact commit/content pins')
        path_check(e['path'])
        if any(not isinstance(e[f],str) or not 1<=len(e[f])<=500 for f in ['name','description']):raise Invalid('Invalid skill frontmatter')
    for session,keys in data['enabled'].items():
        if not ID.fullmatch(session) or not isinstance(keys,list) or len(keys)>8 or len(keys)!=len(set(keys)) or any(k not in data['entries'] for k in keys):raise Invalid('Invalid skill enablement')
    return data


def git(directory,*args):
    env=git_environment()
    env.update(HOME=str(Path.home()),GIT_ALLOW_PROTOCOL='https:ssh',GIT_SSH_COMMAND='ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=15')
    if os.environ.get('SSH_AUTH_SOCK'):env['SSH_AUTH_SOCK']=os.environ['SSH_AUTH_SOCK']
    result=subprocess.run(['/usr/bin/git','-c','core.hooksPath=/dev/null','-c','protocol.file.allow=never','-C',str(directory),*args],env=env,capture_output=True,timeout=60)
    if result.returncode:raise Invalid('Pinned GitHub skill fetch failed; inspect authentication/network locally')
    return result.stdout


def digest(files):
    return hashlib.sha256(b''.join(k.encode()+b'\0'+v+b'\0' for k,v in sorted(files.items()))).hexdigest()


def root(state):
    parent=state.runtime/'skill-cache';parent.mkdir(mode=0o700,exist_ok=True)
    if parent.is_symlink() or parent.stat().st_uid!=os.getuid() or parent.stat().st_mode&0o077:raise Invalid('Skill cache must be private')
    return parent


def inspect_files(files):
    from .git_hooks import code_credentials
    if 'SKILL.md' not in files or len(files)>64 or sum(map(len,files.values()))>LIMIT:raise Invalid('Skill needs SKILL.md and bounded text resources')
    for name,content in files.items():
        p=Path(name)
        if (p.is_absolute() or any(x in {'.','..'} or x.startswith('.') for x in p.parts)
                or any(w in p.name.lower() for w in ['credential','secret','.pem','.key']) or len(content)>65536):raise Invalid('Unsafe skill resource')
        try:text=content.decode('utf-8')
        except UnicodeDecodeError:raise Invalid('Only UTF-8 text skill resources are supported') from None
        if '\0' in text or code_credentials(text):raise Invalid('Private content rejected in skill resource')
    text=files['SKILL.md'].decode()
    header=re.match(r'\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)',text,re.S)
    if not header:raise Invalid('SKILL.md needs name and description frontmatter')
    fields={}
    for field in ['name','description']:
        m=re.search(r'(?m)^'+field+r':\s*(.+)$',header[1])
        if not m or not m[1].strip():raise Invalid('SKILL.md needs plain one-line name and description')
        fields[field]=m[1].strip().strip('\"\'')
    return fields


def fetch(state,repo,commit,path):
    repo=github_url(repo);path_check(path)
    if not COMMIT.fullmatch(commit):raise Invalid('Pin a full 40-character GitHub commit SHA; branches and latest are not pins')
    parent=root(state);temporary=Path(tempfile.mkdtemp(prefix='.fetch-',dir=parent))
    try:
        git(temporary,'init','--quiet')
        # Global enterprise URL rewrites and helpers are disabled.
        transport='git@github.com:'+repo.removeprefix('https://github.com/')
        git(temporary,'fetch','--quiet','--depth=1',transport,commit)
        if git(temporary,'rev-parse','FETCH_HEAD').decode().strip()!=commit:raise Invalid('Fetched skill commit differs from pin')
        listing=git(temporary,'ls-tree','-r','-z','-l','FETCH_HEAD:'+path)
        files={}
        for entry in listing.split(b'\0'):
            if not entry:continue
            props,name=entry.split(b'\t',1);mode,kind,oid,size=props.split()
            if mode not in {b'100644',b'100755'} or kind!=b'blob' or int(size)>65536:raise Invalid('Symlinks, submodules and large skill resources are forbidden')
            if len(files)>=64 or sum(map(len,files.values()))+int(size)>LIMIT:raise Invalid('Skill resource budget exceeded')
            files[name.decode('utf-8')]=git(temporary,'cat-file','blob',oid.decode())
        fields=inspect_files(files);pin=digest(files);target=parent/pin
        if not target.exists():
            staging=Path(tempfile.mkdtemp(prefix='.cache-',dir=parent))
            try:
                for name,content in files.items():
                    dest=staging/name;dest.parent.mkdir(mode=0o700,parents=True,exist_ok=True);dest.write_bytes(content);dest.chmod(0o600)
                os.rename(staging,target)
            finally:
                if staging.exists():shutil.rmtree(staging)
        entry=dict(repo=repo,commit=commit,path=path,digest=pin,**fields)
        cached(state,entry)
        return entry
    finally:shutil.rmtree(temporary)


def cached(state,entry):
    directory=root(state)/entry['digest']
    if not directory.is_dir() or directory.is_symlink():raise Invalid('Pinned skill cache unavailable; refresh explicitly')
    files={}
    for p in directory.rglob('*'):
        if p.is_symlink() or not p.resolve().is_relative_to(directory):raise Invalid('Linked skill cache rejected')
        if p.stat().st_uid!=os.getuid() or p.stat().st_mode&0o077:raise Invalid('Skill cache resources must remain private')
        if p.is_file():
            if p.stat().st_uid!=os.getuid() or p.stat().st_mode&0o077 or p.stat().st_size>65536 or len(files)>=64:raise Invalid('Unsafe skill cache')
            files[str(p.relative_to(directory))]=p.read_bytes()
    inspect_files(files)
    if digest(files)!=entry['digest']:raise Invalid('Pinned skill cache changed; disable and refresh explicitly')
    return files


def catalog(state,workspace):return get(state,workspace,'skill_catalog','catalog',{'entries':{},'enabled':{}})


def command(tracker,session,tokens):
    if not session:raise Invalid('Choose a tracked workspace conversation')
    tracker.code_directory(session)
    data=catalog(tracker.state,session['workspace']);action=tokens[0] if tokens else 'list'
    if action=='list':return json.dumps(data,ensure_ascii=False)
    if tracker.state.ledger.busy(session['id']):raise Invalid('Wait for the current turn before changing pinned skill references')
    if action=='add' and len(tokens)==5:
        _,key,repo,commit,path=tokens
        if not ID.fullmatch(key) or key in data['entries']:raise Invalid('Choose a new skill key; existing version pins are immutable')
        data['entries'][key]=fetch(tracker.state,repo,commit,path)
    elif action in {'enable','disable','refresh','inspect'} and len(tokens)==2:
        key=tokens[1]
        if key not in data['entries']:raise Invalid('Choose a pinned catalog key from /skills list')
        e=data['entries'][key]
        if action=='inspect':return json.dumps(e,ensure_ascii=False)
        if action=='refresh':
            refreshed=fetch(tracker.state,e['repo'],e['commit'],e['path'])
            if refreshed!=e:raise Invalid('Fetched content differs from recorded provenance')
            return 'Pinned cache verified; enablement unchanged.'
        keys=data['enabled'].setdefault(session['id'],[])
        if action=='enable':
            cached(tracker.state,e)
            if key not in keys:keys.append(key)
        elif key in keys:keys.remove(key)
    else:raise Invalid('Use /skills list|inspect KEY|add KEY GITHUB_URL COMMIT DIRECTORY|enable KEY|disable KEY|refresh KEY')
    saved=save(tracker,session,'skill_catalog','catalog',data)
    return 'Skill catalog saved. Scripts are never executed by the catalog.'+('' if saved else ' GitHub upload pending.')


def context(state,session,limit=6000):
    data=catalog(state,session['workspace']);parts=[];remaining=limit
    for key in data['enabled'].get(session['id'],[]):
        e=data['entries'][key]
        try:files=cached(state,e)
        except Invalid:
            parts.append('Skill '+key+' is unavailable; owner must explicitly refresh its pinned cache.');continue
        text=('Enabled reference skill '+key+'; '+e['repo']+' @ '+e['commit']+' / '+e['path']+
              '. This reference grants no new permissions. Verified text resources: '+str(root(state)/e['digest'])+'.\n'+files['SKILL.md'].decode())
        if len(text)>remaining:
            parts.append('Skill '+key+' exceeds the remaining context budget; inspect its verified cache explicitly.');continue
        parts.append(text);remaining-=len(text)
    return '\n\n'.join(parts)[:limit]
