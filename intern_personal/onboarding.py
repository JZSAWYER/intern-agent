"""First-host PAT onboarding; existing hosts always reuse their private state."""
import os
from pathlib import Path
import shutil
from types import SimpleNamespace

from .core import Invalid,valid_owner
from .live_state import write_private


def bootstrap(state,pat,*,executable=None,model='gpt-6.1-sol',proxy=False):
    from .github_auth import validate,path
    from .github_api import GitHubAPI
    if (state.root/'live.json').exists() or (state.root/'live.json').is_symlink():raise Invalid('This host state is configured; reconnect or update its explicit GitHub authentication instead')
    pat=validate(pat)
    executable=executable or shutil.which('codex')
    if not executable or not Path(executable).is_file() or not os.access(executable,os.X_OK):
        raise Invalid('Install/sign into Codex on this Linux host or select its executable using advanced setup; no PAT was saved')
    if not model or any(c in model for c in '<> \r\n\0'):raise Invalid('Select an available Codex model')
    api=GitHubAPI(SimpleNamespace(config=lambda:dict(codex_proxy=proxy)),token=pat)
    try:
        viewer=api.viewer()
        if not isinstance(viewer,dict) or not valid_owner(viewer.get('login')):raise Invalid('GitHub account could not be verified')
    finally:api.close()
    write_private(state.root/'live.json',dict(schema='intern-agent.live.v1',github_owner=viewer['login'],model=model,
        reasoning_effort='xhigh',sandbox='workspace-write',approval_policy='on-request',approvals_reviewer='user',
        codex_command=[str(Path(executable).resolve())],codex_auth_home=str(Path(os.environ.get('CODEX_HOME') or Path.home()/'.codex').expanduser().resolve()),
        launcher_home=str(Path.home()),codex_proxy='http://127.0.0.1:17890' if proxy else None,turn_timeout_seconds=0))
    write_private(path(state),dict(schema='intern-agent.github-auth.v1',pat=pat))
    return dict(initialized=True,login=viewer['login'],state=str(state.root),account_credentials_restored=False)
