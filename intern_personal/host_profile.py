"""One small, owner-private pointer lets SSH editor clients reuse a host relay."""
import os
from pathlib import Path
import sys

from .core import Invalid
from .live_state import private_json,write_private


def location():
    base=Path(os.environ.get('XDG_STATE_HOME') or Path.home()/'.local/state')
    if not base.is_absolute():raise Invalid('XDG_STATE_HOME must be absolute')
    return base/'intern-agent'/'host.json'


def discover():
    file=location()
    if not file.exists() and not file.is_symlink():return dict(found=False)
    data=private_json(file)
    if (set(data)!={'schema','state_directory','python_executable'} or data['schema']!='intern-agent.host-profile.v1'
            or any(not isinstance(data[k],str) or not Path(data[k]).is_absolute() or any(c in data[k] for c in '\x00\r\n<>')
                   for k in ['state_directory','python_executable'])):raise Invalid('Invalid host relay profile')
    root=Path(data['state_directory'])
    if root.is_symlink() or not root.is_dir() or root.stat().st_uid!=os.getuid() or root.stat().st_mode&0o077:raise Invalid('Host relay state is unavailable or not private')
    private_json(root/'live.json')
    return dict(found=True,**data)


def register(state):
    state.config()
    file=location();parent=file.parent
    parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    if parent.is_symlink() or parent.stat().st_uid!=os.getuid() or parent.stat().st_mode&0o077:raise Invalid('Host profile directory must be private')
    existing=discover()
    if existing['found'] and Path(existing['state_directory']).resolve()!=state.root:
        raise Invalid('Another host relay is registered; choose its existing state or deliberately remove its pointer first')
    write_private(file,dict(schema='intern-agent.host-profile.v1',state_directory=str(state.root),python_executable=sys.executable))
    return dict(registered=True,existing_sessions_preserved=True)


if __name__=='__main__':
    import json
    try:print(json.dumps(discover()))
    except (Invalid,OSError,ValueError):
        print(json.dumps(dict(found=False,error='Private host profile needs inspection')))
        raise SystemExit(1)
