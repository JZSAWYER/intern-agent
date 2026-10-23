"""Private immutable backend snapshots, independent of agent workspace branches."""
import hashlib
import os
from pathlib import Path
import shutil
import tempfile

from .core import Invalid


def service_preferences(state):
    """Protect selected tiers; observation timestamps are freshness, not settings."""
    return [dict(row) for row in state.db.execute('SELECT session,tier FROM session_service_tiers ORDER BY session')]


def prepare_runtime(state,source):
    source=Path(source).resolve();package=source/'intern_personal'
    from .distribution import linked_input
    files=sorted(package.glob('*.py'))
    if not (package/'feishu_live.py').is_file():raise Invalid('Runtime package is missing')
    payload={}
    for file in files:
        if linked_input(source,file) or not file.resolve().is_relative_to(package):raise Invalid('Linked runtime source rejected')
        payload['intern_personal/'+file.name]=file.read_bytes()
    for skill in sorted((source/'skills').glob('*/SKILL.md')):
        if linked_input(source,skill) or not skill.is_file() or not skill.resolve().is_relative_to(source):raise Invalid('Linked bundled skill rejected')
        payload[str(skill.relative_to(source))]=skill.read_bytes()
    # Operational references must travel with the code. Project workspaces do
    # not necessarily contain this relay's docs, and their copies can be older.
    # This is the same public Markdown directory allowlisted for distribution.
    for guide in sorted((source/'docs').glob('*.md')):
        if linked_input(source,guide) or not guide.is_file() or not guide.resolve().is_relative_to(source):
            raise Invalid('Linked runtime guide rejected')
        payload['docs/'+guide.name]=guide.read_bytes()
    digest=hashlib.sha256(b''.join(name.encode()+b'\0'+data+b'\0' for name,data in payload.items())).hexdigest()
    parent=state.runtime/'releases';parent.mkdir(mode=0o700,exist_ok=True)
    if parent.is_symlink() or parent.stat().st_uid!=os.getuid() or parent.stat().st_mode&0o077:
        raise Invalid('Runtime release directory must be private')
    target=parent/digest
    if target.exists():
        if target.is_symlink() or target.stat().st_mode&0o077:raise Invalid('Runtime snapshot is not private')
        for name,data in payload.items():
            file=target/name
            if file.is_symlink() or not file.is_file() or file.read_bytes()!=data or not file.resolve().is_relative_to(target):raise Invalid('Runtime snapshot changed')
        if {p.name for p in (target/'intern_personal').glob('*.py')}!={Path(name).name for name in payload if name.endswith('.py')}:raise Invalid('Unexpected runtime module')
        if {str(p.relative_to(target)) for p in (target/'skills').glob('*/SKILL.md')}!={name for name in payload if name.startswith('skills/')}:
            raise Invalid('Unexpected runtime skill')
        return target
    temporary=Path(tempfile.mkdtemp(prefix='.release-',dir=parent))
    try:
        (temporary/'intern_personal').mkdir(mode=0o700)
        for name,data in payload.items():
            file=temporary/name;file.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
            file.write_bytes(data);file.chmod(0o600)
        os.rename(temporary,target)
    finally:
        if temporary.exists():shutil.rmtree(temporary)
    return target
