"""Private PAT handoff and an HTTPS-only Git helper; tokens never enter argv."""
from pathlib import Path
import shlex
import sys

if __package__ in {None,''}:
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from intern_personal.core import Invalid
from intern_personal.live_state import private_json,write_private


def path(state):return state.root/'github-auth.json'


def token(state):
    file=path(state)
    if file.exists() or file.is_symlink():
        data=private_json(file)
        if set(data)!={'schema','pat'} or data['schema']!='intern-agent.github-auth.v1':raise Invalid('Invalid private GitHub authentication')
        return validate(data['pat'])
    from .github_api import host_token
    return host_token()


def validate(value):
    if not isinstance(value,str) or not 16<=len(value)<=512 or any(c.isspace() or c in '\x00<>' for c in value):
        raise Invalid('Supply a GitHub PAT through the masked input or stdin, never a command argument')
    return value


def configure(state,value):
    value=validate(value)
    from .github_api import GitHubAPI
    api=GitHubAPI(state,token=value)
    try:
        viewer=api.viewer()
        from .core import valid_owner
        if not isinstance(viewer,dict) or not valid_owner(viewer.get('login')):raise Invalid('GitHub account could not be verified')
    finally:api.close()
    write_private(path(state),dict(schema='intern-agent.github-auth.v1',pat=value))
    return dict(authenticated=True,login=viewer['login'],private_storage=True,repository_access_verified=False)


def git_options(state):
    # Do not replace working SSH authentication. Only an explicitly stored PAT
    # enables this helper; repository URLs and Git configuration stay unchanged.
    if not path(state).exists() and not path(state).is_symlink():return []
    token(state)  # Check ownership/mode before Git can request the credential.
    helper='!'+shlex.join([sys.executable,str(Path(__file__).resolve()),str(state.root)])
    return ['-c','credential.helper=','-c','credential.helper='+helper]


def helper(argv,stream_in,stream_out):
    if len(argv)!=2 or argv[1]!='get':return 0
    raw=stream_in.read(8193)
    if len(raw)>8192:return 1
    fields=dict(line.split('=',1) for line in raw.splitlines() if '=' in line)
    if fields.get('protocol')!='https' or fields.get('host')!='github.com':return 0
    root=Path(argv[0])
    if root.is_symlink() or not root.is_absolute() or root.stat().st_mode&0o077:raise Invalid('Private GitHub state is unavailable')
    from types import SimpleNamespace
    value=private_json(path(SimpleNamespace(root=root)))
    if value.get('schema')!='intern-agent.github-auth.v1' or set(value)!={'schema','pat'}:raise Invalid('Invalid private GitHub authentication')
    stream_out.write('username=x-access-token\npassword='+validate(value['pat'])+'\n\n')
    return 0


if __name__=='__main__':
    try:raise SystemExit(helper(sys.argv[1:],sys.stdin,sys.stdout))
    except (OSError,ValueError,Invalid):raise SystemExit(1)
