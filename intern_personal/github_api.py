"""Small GitHub-only API client; existing host authentication stays in memory."""
import json
import os
from pathlib import Path
import shlex
import subprocess
from urllib.error import HTTPError,URLError
from urllib.request import HTTPRedirectHandler,ProxyHandler,Request,build_opener

from .core import Invalid,REPO,valid_owner


class GitHubError(RuntimeError):
    def __init__(self,operation,status=None):
        self.operation,self.status=operation,status
        super().__init__('GitHub '+operation+' failed'+(' (HTTP '+str(status)+')' if status else '')+
            '. Check the host GitHub authentication, account permissions and network.')


def host_token():
    """Use environment or builtin Git helpers, never arbitrary helper scripts."""
    for key in ['GH_TOKEN','GITHUB_TOKEN']:
        token=os.environ.get(key)
        if token and '\n' not in token and '\r' not in token:return token
    try:
        result=subprocess.run(['/usr/bin/git','config','--get-all','credential.helper'],
            capture_output=True,text=True,timeout=5)
        helpers=result.stdout.splitlines() if result.returncode==0 else []
        for value in helpers:
            parts=shlex.split(value)
            if not parts or parts[0] not in {'store','cache'}:continue
            option='--file' if parts[0]=='store' else '--socket'
            args=[]
            if len(parts)==1:pass
            elif len(parts)==2 and parts[1].startswith(option+'='):
                args=[option,parts[1].split('=',1)[1]]
            elif len(parts)==3 and parts[1]==option:args=parts[1:]
            else:continue
            if args and (not Path(args[1]).is_absolute() or any(c in args[1] for c in ['\0','\n','\r'])):continue
            command=['/usr/bin/git','credential-'+parts[0],*args,'get']
            result=subprocess.run(command,input='protocol=https\nhost=github.com\n\n',
                env=os.environ|{'GIT_TERMINAL_PROMPT':'0'},capture_output=True,text=True,timeout=5)
            if result.returncode!=0:continue
            fields=dict(line.split('=',1) for line in result.stdout.splitlines() if '=' in line)
            token=fields.get('password')
            if token and '\n' not in token and '\r' not in token:return token
    except (OSError,ValueError,subprocess.TimeoutExpired):pass
    raise Invalid('GitHub API authentication is unavailable. Configure the host GH_TOKEN/GITHUB_TOKEN or a builtin Git store/cache credential helper securely. SSH login alone cannot create a GitHub repository. No credentials are stored by this feature.')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


class GitHubAPI:
    def __init__(self,state,token=None):
        from .github_auth import token as private_token
        self.token=private_token(state) if token is None else token
        proxies={'https':'http://127.0.0.1:17890'} if state.config().get('codex_proxy') else None
        self.opener=build_opener(ProxyHandler(proxies),NoRedirect())

    def request(self,method,path,body=None,*,missing=False):
        if not path.startswith('/') or any(c in path for c in ['\n','\r','\0','#','?']):
            raise Invalid('Invalid GitHub API path')
        request=Request('https://api.github.com'+path,
            data=json.dumps(body).encode() if body is not None else None,method=method,
            headers={'Authorization':'Bearer '+self.token,'Accept':'application/vnd.github+json',
                     'Content-Type':'application/json','X-GitHub-Api-Version':'2022-11-28',
                     'User-Agent':'Intern-Agent-Personal'})
        try:
            with self.opener.open(request,timeout=20) as response:
                if response.geturl()!=request.full_url:raise GitHubError('redirect')
                data=response.read(2*1024*1024+1)
                if len(data)>2*1024*1024:raise GitHubError('response size')
                return json.loads(data)
        except HTTPError as error:
            error.close()
            if missing and method=='GET' and error.code==404:return None
            raise GitHubError('request',error.code) from None
        except (URLError,OSError,TimeoutError,ValueError):raise GitHubError('request') from None

    @staticmethod
    def path(owner,repo):
        if not valid_owner(owner) or not REPO.fullmatch(repo) or repo in {'.','..'}:
            raise Invalid('Invalid GitHub owner or repository name')
        return '/repos/'+owner+'/'+repo

    def viewer(self):return self.request('GET','/user')

    def repository(self,owner,repo):return self.request('GET',self.path(owner,repo),missing=True)

    def create(self,owner,repo,marker):
        self.path(owner,repo)
        return self.request('POST','/user/repos',dict(name=repo,private=True,auto_init=False,
            description=marker,has_projects=False,has_wiki=False))

    def close(self):self.token=None
