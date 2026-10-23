"""Explicit owner-selected Codex execution settings, independent of task scope."""
import json
import os
from pathlib import Path
import re
import tempfile
import tomllib

from .core import Invalid


def settings(config):
    policy=config.get('approval_policy','on-request')
    reviewer=config.get('approvals_reviewer','user')
    sandbox=config.get('sandbox','workspace-write')
    if (not all(isinstance(x,str) for x in [policy,reviewer,sandbox]) or policy not in {'on-request','never'}
            or reviewer not in {'user','auto_review'} or sandbox not in {'read-only','workspace-write','danger-full-access'}):
        raise Invalid('Unsupported Codex execution policy')
    return dict(approval_policy=policy,approvals_reviewer=reviewer,sandbox_mode=sandbox)


def yolo(config):
    value=settings(config)
    return value['approval_policy']=='never' and value['sandbox_mode']=='danger-full-access'


def thread_fields(config):
    value=settings(config)
    return dict(approvalPolicy=value['approval_policy'],approvalsReviewer=value['approvals_reviewer'],sandbox=value['sandbox_mode'])


def update_fields(config):
    value=settings(config)
    policy={'type':{'read-only':'readOnly','workspace-write':'workspaceWrite','danger-full-access':'dangerFullAccess'}[value['sandbox_mode']]}
    return dict(approvalPolicy=value['approval_policy'],approvalsReviewer=value['approvals_reviewer'],sandboxPolicy=policy)


def cli_overrides(config):
    return [piece for key,value in settings(config).items() for piece in ['-c',key+'='+json.dumps(value)]]


def write_config(path, config, *, project=None):
    """Replace only top-level policy keys; preserve unrelated settings and trust."""
    path=Path(path)
    if path.is_symlink():raise Invalid('Linked Codex configuration cannot be rewritten')
    if path.exists():
        info=path.stat()
        if info.st_uid!=os.getuid() or info.st_mode&0o077:raise Invalid('Codex configuration must be private and owned by you')
        before=path.read_text()
    else:before=''
    try:parsed=tomllib.loads(before)
    except tomllib.TOMLDecodeError:raise Invalid('Codex configuration is not valid TOML') from None
    values=settings(config)
    lines=before.splitlines(keepends=True);body=[];top=True
    for line in lines:
        if line.lstrip().startswith('['):top=False
        if top and any(re.match(r'\s*'+key+r'\s*=',line) for key in values):continue
        body.append(line)
    text=''.join(key+' = '+json.dumps(value)+'\n' for key,value in values.items())+''.join(body)
    if project and project not in parsed.get('projects',{}):
        text+='\n[projects.'+json.dumps(str(project))+']\ntrust_level = "trusted"\n'
    try:after=tomllib.loads(text)
    except tomllib.TOMLDecodeError:raise Invalid('Codex policy update produced invalid TOML') from None
    if any(after.get(k)!=v for k,v in values.items()):raise Invalid('Codex policy update was not applied')
    if before==text:return False
    fd,temporary=tempfile.mkstemp(prefix='.policy-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as stream:
            stream.write(text);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:Path(temporary).unlink(missing_ok=True)
    return True
