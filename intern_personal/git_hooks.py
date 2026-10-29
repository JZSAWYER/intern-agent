"""Explicitly installed, hash-checked Git guards for attached debug workspaces."""
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

if __name__ == '__main__' and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = 'intern_personal'

from .core import Invalid
from .distribution import KEY_PATTERN
from .live_state import private_json, write_private


def code_credentials(text, credentials=()):
    if any(value and value in text for value in credentials) or KEY_PATTERN.search(text):
        return True
    if re.search(r"-----BEGIN [^-]*PRIVATE KEY-----", text):
        return True
    # Source references to credential fields are normal. Literal values must be
    # explicit examples/fixtures, or uploads fail. Actual live values always fail.
    safe = re.compile(r"^(?:example[-_]|fixture[-_]|synthetic[-_]|private-test-|local-test-|test[-_]|YOUR_|REPLACE_|<|\[redacted|fake$)", re.I)
    for match in re.finditer(r'https?://[^\s"\'<>]*(?:token|secret|signature|authorization)[=:]([^\s"\'<>]+)',text,re.I):
        if not safe.match(match[1]):return True
    sensitive = re.compile(r"^(?:app_secret|api_key|access_token|refresh_token|password|token)$", re.I)
    values = []
    try:
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values):
                    if isinstance(key, ast.Constant) and isinstance(key.value, str) and sensitive.fullmatch(key.value):
                        if isinstance(value, ast.Constant) and isinstance(value.value, str):values.append(value.value)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(isinstance(t, ast.Name) and sensitive.fullmatch(t.id) for t in targets):
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):values.append(node.value.value)
    except SyntaxError:
        for match in re.finditer(r"[\"']?(?:app_secret|api_key|access_token|refresh_token|password|token)[\"']?\s*[:=]\s*[\"']([^\"'\n]+)[\"']", text, re.I):
            values.append(match[1])
    return any(value and not safe.match(value) for value in values)


def git(directory, *args):
    result = subprocess.run(['/usr/bin/git', '-c', 'core.hooksPath=/dev/null', '-C', str(directory), *args],
                            capture_output=True, timeout=30)
    if result.returncode:raise Invalid('Git guard could not inspect repository')
    return result.stdout


def validate(name, data, management=False):
    parts = Path(name).parts
    if (Path(name).is_absolute() or '..' in parts or any(p in {'.git','.codex','.ssh','.env','runtime','reference','__pycache__'} for p in parts)
            or any(word in Path(name).name.lower() for word in ['credential','secret','.pem','.key'])
            or name in {'personal.json','bindings.json','live.json','health.json','STATUS.md'}):
        raise Invalid('Private path rejected by Git hook')
    if management != name.startswith('.intern_workspace/'):
        raise Invalid('Code and management files must stay on their own branches')
    if len(data)> (2 if management else 10)*1024*1024:raise Invalid('Large artifact rejected by Git hook')
    suffix=Path(name).suffix.lower()
    if not management and suffix=='.zip':
        from .attachments import validate_file
        validate_file(Path(name).name,data,credential_check=code_credentials)
    # Keep metadata and code parsers strict while recognizing bounded site and
    # research assets by their format signatures.
    signatures={'.pdf':(b'%PDF-',),'.png':(b'\x89PNG\r\n\x1a\n',),
        '.jpg':(b'\xff\xd8\xff',),'.jpeg':(b'\xff\xd8\xff',),'.gif':(b'GIF87a',b'GIF89a'),'.zip':(b'PK\x03\x04',b'PK\x05\x06'),'.woff2':(b'wOF2',)}
    binary=not management and suffix in signatures
    if binary and not data.startswith(signatures[suffix]):raise Invalid('Document or image signature does not match its file type')
    if binary and suffix=='.woff2':
        # Check the WOFF2 header envelope, without decoding or executing fonts:
        # https://www.w3.org/TR/WOFF2/#woff20Header
        if len(data)<48 or int.from_bytes(data[8:12],'big')!=len(data):
            raise Invalid('WOFF2 header length does not match the file')
        tables=int.from_bytes(data[12:14],'big');compressed=int.from_bytes(data[20:24],'big')
        if not tables or not compressed or 48+2*tables+compressed>len(data):
            raise Invalid('WOFF2 table directory or compressed data is truncated')
        for offset,length in [(28,32),(40,44)]:
            start=int.from_bytes(data[offset:offset+4],'big');size=int.from_bytes(data[length:length+4],'big')
            if bool(start)!=bool(size) or (start and (start<48 or start+size>len(data))):
                raise Invalid('WOFF2 optional data extends outside the file')
    try:text=data.decode('utf-8',errors='ignore' if binary else 'strict')
    except UnicodeDecodeError:raise Invalid('Unsupported binary artifact; review before upload') from None
    if code_credentials(text):raise Invalid('Credential literal rejected by Git hook')
    if suffix=='.py':ast.parse(text)
    if suffix=='.json':json.loads(text)
    if management:
        from .records import validate_document
        record=validate_document(name,text) if suffix=='.md' else None
        if suffix not in {'.md','.txt','.json'}:raise Invalid('Invalid management file format')
        if suffix=='.md' and not text.startswith('# '):raise Invalid('Markdown needs a title')
        if name.endswith('/status.md') and not record:
            if not re.search(r'<!-- METADATA:STATUS=[^,\n]+,TASK=[^,\n]*,ROLE=(?:independent|lead|worker),TEAM_ID=(?:team_[a-z0-9]+)? -->',text):
                raise Invalid('Legacy status marker is missing or invalid')
            for field in ['Workspace','Role','Current branch','Current PR','Management branch','Status']:
                if '| '+field+' |' not in text:raise Invalid('Legacy status table field is missing')
        if name.endswith('/workspace.json'):
            value=json.loads(text)
            if value.get('schema')!='intern-agent.workspace.v1' or value.get('pull_requests')!='explicit_user_request_only':
                raise Invalid('Workspace manifest format is invalid')


def install(state, directory, branches, management=False, base_branch='main',combined_records=False):
    directory=str(Path(directory).resolve())
    key=hashlib.sha256(directory.encode()).hexdigest()[:20]
    root=state.runtime/'git-hooks'/key
    root.mkdir(mode=0o700,parents=True,exist_ok=True)
    config=root/'binding.json'
    source=Path(__file__).resolve().parent.parent
    # No shell interpolation of repository content; only fixed, quoted paths.
    import shlex
    hashes={}
    for phase in ['pre-commit','pre-push']:
        path=root/phase
        text='#!/bin/sh\nexec '+shlex.quote(sys.executable)+' '+shlex.quote(str(source/'intern_personal/git_hooks.py'))+' '+shlex.quote(str(config))+' '+phase+'\n'
        path.write_text(text);path.chmod(0o700)
        hashes[phase]=hashlib.sha256(path.read_bytes()).hexdigest()
    hashes['module']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    hashes['records']=hashlib.sha256((Path(__file__).parent/'records.py').read_bytes()).hexdigest()
    for name in ['recovery','portable_settings','goals','user_questions','core','distribution','teams','archive_parts','record_storage','feature_state','skills','attachments','peer_mail','git_objects','git_guard_run']:
        hashes[name]=hashlib.sha256((Path(__file__).parent/(name+'.py')).read_bytes()).hexdigest()
    if combined_records and (management or len(branches)!=1):raise Invalid('Combined records require exactly one bot branch')
    write_private(config,{'directory':directory,'branches':branches,'management':management,'combined_records':bool(combined_records),'base_branch':base_branch,'hashes':hashes,'source':str(source)})
    state.db.execute('INSERT OR REPLACE INTO git_hook_bindings(directory,config_path) VALUES(?,?)',(directory,str(config)))
    # Worktrees share ordinary repository config. Each must inspect its own
    # index/branch rather than accidentally running a sibling's management hook.
    git(directory,'config','extensions.worktreeConfig','true')
    existing=subprocess.run(['/usr/bin/git','-C',directory,'config','--local','--get','core.hooksPath'],capture_output=True)
    if existing.returncode==0:git(directory,'config','--local','--unset-all','core.hooksPath')
    git(directory,'config','--worktree','core.hooksPath',str(root))
    return config


def verified_path(config_path, directory):
    config=private_json(config_path);root=Path(config_path).parent
    if config['directory']!=str(Path(directory).resolve()):raise Invalid('Git hook binding changed')
    if root.is_symlink() or root.stat().st_uid!=os.getuid() or root.stat().st_mode&0o077:raise Invalid('Git hook directory is not private')
    for name,digest in config['hashes'].items():
        path=Path(__file__) if name=='module' else (Path(__file__).parent/(name+'.py') if name in {'records','recovery','portable_settings','goals','user_questions','core','distribution','teams','archive_parts','record_storage','feature_state','skills','attachments','peer_mail','git_objects','git_guard_run'} else root/name)
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
            raise Invalid('Git hook definition changed; explicitly reinstall reviewed hooks')
    return root


def run(config_path, phase, lines=()):
    from .git_guard_run import run as inspect
    return inspect(config_path,phase,lines)


if __name__=='__main__':
    try:
        run(sys.argv[1],sys.argv[2],sys.stdin if sys.argv[2]=='pre-push' else ())
    except Exception as error:
        # Repository/transport exception text may contain credentials.
        print('Intern Agent Git guard blocked operation: '+type(error).__name__,file=sys.stderr)
        raise SystemExit(1)
