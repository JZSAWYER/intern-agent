"""Install shipped public skills and inert resources without replacing user files."""
import os
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
import stat
import uuid
from urllib.parse import unquote, urlsplit

from .core import Invalid


MAX_FILE=128*1024
MAX_TOTAL=2*1024*1024
MAX_FILES=256
TEXT_SUFFIXES={'.md','.txt','.json','.yaml','.yml','.toml'}
NOTICE_NAMES={'LICENSE','NOTICE','COPYING','AUTHORS'}
SKILL=re.compile(r'[a-z][a-z0-9]*(?:-[a-z0-9]+)*')


def metadata(data,name):
    """Validate the small, literal frontmatter profile shipped by this product."""
    header=re.match(r'\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)',data.decode('utf-8'),re.S)
    if not header:raise Invalid('Bundled skill needs name and description frontmatter')
    fields={}
    for key in ['name','description']:
        values=re.findall(r'(?m)^'+key+r':[ \t]*(.*)\r?$',header[1])
        if len(values)!=1:raise Invalid('Bundled skill needs unique name and description')
        value=values[0].strip()
        if value.startswith('"'):
            try:value=json.loads(value)
            except ValueError:raise Invalid('Bundled skill metadata must be literal single-line strings') from None
        elif value.startswith("'"):
            if not value.endswith("'") or "'" in value[1:-1].replace("''",''):
                raise Invalid('Bundled skill metadata must be literal single-line strings')
            value=value[1:-1].replace("''", "'")
        elif (not value or value[0] in '&*!{}[]|>@%`' or ': ' in value or ' #' in value
                or value.lower() in {'true','false','null','~'}):
            raise Invalid('Bundled skill metadata must be literal single-line strings')
        if not isinstance(value,str) or not value.strip() or len(value)>1024 or any(ord(c)<32 for c in value):
            raise Invalid('Bundled skill metadata must be bounded single-line strings')
        fields[key]=value
    if fields['name']!=name:raise Invalid('Bundled skill name must match its directory')
    return fields


def _path(value,absolute=False):
    path=Path(value).expanduser()
    if absolute and not path.is_absolute():raise Invalid('Choose an absolute Codex skills directory')
    if not path.is_absolute():path=Path.cwd()/path
    if '..' in path.parts or any(c in str(path) for c in '\x00\r\n<>'):
        raise Invalid('Choose an ordinary absolute skills path')
    return path


def _directory(path,create=False):
    """Anchor every directory lookup; no root or ancestor symlink is followed."""
    flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW
    descriptor=os.open(path.anchor,flags)
    try:
        for part in path.parts[1:]:
            try:child=os.open(part,flags,dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    os.close(descriptor)
                    return None
                try:os.mkdir(part,0o700,dir_fd=descriptor)
                except FileExistsError:pass
                child=os.open(part,flags,dir_fd=descriptor)
            os.close(descriptor);descriptor=child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _read(parent,name):
    try:descriptor=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
    except FileNotFoundError:return None
    try:
        info=os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size>MAX_FILE:
            raise Invalid('A skill resource is not an ordinary bounded file')
        with os.fdopen(descriptor,'rb') as stream:
            descriptor=None;data=stream.read(MAX_FILE+1)
        if len(data)>MAX_FILE:raise Invalid('A skill resource is too large')
        return data
    finally:
        if descriptor is not None:os.close(descriptor)


def _payload(root):
    descriptor=_directory(root)
    if descriptor is None:raise Invalid('No bundled Codex skills are available')
    payload={};skills=[];total=0
    def walk(parent,prefix):
        nonlocal total
        for name in sorted(os.listdir(parent)):
            relative=prefix/name
            if (name.startswith('.') or not re.fullmatch(r'[A-Za-z0-9_.-]+',name)
                    or len(relative.parts)>11 or len(relative.as_posix())>240):
                raise Invalid('Invalid bundled skill resource path')
            info=os.stat(name,dir_fd=parent,follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
                try:walk(child,relative)
                finally:os.close(child)
            else:
                if (not stat.S_ISREG(info.st_mode)
                        or (relative.suffix.lower() not in TEXT_SUFFIXES and relative.name not in NOTICE_NAMES)):
                    raise Invalid('Bundled skills must contain only ordinary inert text resources')
                data=_read(parent,name)
                if data is None:raise Invalid('A bundled skill changed during installation')
                if '\x00' in data.decode('utf-8'):raise Invalid('A skill resource is not UTF-8 text')
                total+=len(data)
                if len(payload)>=MAX_FILES or total>MAX_TOTAL:raise Invalid('Bundled skill resource limits exceeded')
                payload[relative.as_posix()]=data
    try:
        for name in sorted(os.listdir(descriptor)):
            info=os.stat(name,dir_fd=descriptor,follow_symlinks=False)
            if stat.S_ISREG(info.st_mode):continue
            if not stat.S_ISDIR(info.st_mode) or len(name)>64 or not SKILL.fullmatch(name):
                raise Invalid('Invalid bundled skill')
            child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=descriptor)
            try:walk(child,PurePosixPath(name))
            finally:os.close(child)
            if name+'/SKILL.md' not in payload:raise Invalid('Bundled skill instructions are missing')
            metadata(payload[name+'/SKILL.md'],name)
            skills.append(name)
    finally:os.close(descriptor)
    if not skills:raise Invalid('No bundled Codex skills are available')
    for name,data in payload.items():
        if PurePosixPath(name).suffix.lower()!='.md':continue
        for match in re.finditer(r'!?\[[^\]\n]*\]\((?:<([^>\n]+)>|([^\s)]+))',data.decode('utf-8')):
            target=urlsplit(match.group(1) or match.group(2))
            if target.scheme in {'https','http','mailto'} or not target.path:continue
            resource=unquote(target.path)
            resolved=posixpath.normpath(posixpath.join(posixpath.dirname(name),resource))
            if (target.scheme or target.netloc or '\\' in resource
                    or not resolved.startswith(PurePosixPath(name).parts[0]+'/') or resolved not in payload):
                raise Invalid('A bundled skill reference is missing or outside its skill')
    return payload,skills


def _include_license(root,payload,skills):
    notice=_existing(root.parent/'LICENSE')
    if notice is None:return
    if '\x00' in notice.decode('utf-8'):raise Invalid('The release license is not UTF-8 text')
    for skill in skills:
        name=skill+'/LICENSE'
        if name in payload:
            if payload[name]!=notice:raise Invalid('A bundled skill license differs from the release notice')
            continue
        if len(payload)>=MAX_FILES or sum(map(len,payload.values()))+len(notice)>MAX_TOTAL:
            raise Invalid('Bundled skill resource limits exceeded')
        payload[name]=notice


def _existing(path):
    parent=_directory(path.parent)
    if parent is None:return None
    try:return _read(parent,path.name)
    finally:os.close(parent)


def _preflight(destination,payload):
    directory=_directory(destination)
    if directory is not None:
        try:
            info=os.fstat(directory)
            if info.st_uid!=os.getuid() or info.st_mode&0o022:
                raise Invalid('The skills destination must be owner controlled')
        finally:os.close(directory)
    for relative,data in payload.items():
        current=_existing(destination/relative)
        if current is not None and current!=data:
            raise Invalid('An installed skill resource differs from the release; review it before replacing your instructions')


def _write_new(path,data):
    current=_existing(path)
    if current is not None:
        if current!=data:raise Invalid('An installed skill resource differs from the release')
        return
    parent=_directory(path.parent,create=True)
    temporary='.intern-agent-write-'+uuid.uuid4().hex
    try:
        descriptor=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=parent)
        try:
            with os.fdopen(descriptor,'wb') as stream:
                stream.write(data);stream.flush();os.fsync(stream.fileno())
            try:
                os.link(temporary,path.name,src_dir_fd=parent,dst_dir_fd=parent,follow_symlinks=False)
                os.fsync(parent)
            except FileExistsError:
                if _existing(path)!=data:raise Invalid('An installed skill resource differs from the release') from None
        finally:os.unlink(temporary,dir_fd=parent)
    finally:os.close(parent)


def install(destination,source=None):
    destination=_path(destination,absolute=True)
    root=_path(source if source is not None else Path(__file__).resolve().parents[1]/'skills')
    if (destination==Path(destination.anchor) or destination.is_relative_to(root) or root.is_relative_to(destination)):
        raise Invalid('Keep bundled skills and installed user instructions in separate directories')
    try:
        payload,skills=_payload(root)
        _include_license(root,payload,skills)
        # Every resource and existing destination is checked before any write.
        _preflight(destination,payload)
        for relative,data in sorted(payload.items()):_write_new(destination/relative,data)
    except (OSError,UnicodeError):
        raise Invalid('A bundled skill or destination is linked, inaccessible or invalid; existing instructions are preserved') from None
    return dict(installed=skills,user_instructions_preserved=True)


def verify(destination,source=None):
    """Read every expected installed byte without installing or executing skills."""
    destination=_path(destination,absolute=True)
    root=_path(source if source is not None else Path(__file__).resolve().parents[1]/'skills')
    if destination==Path(destination.anchor) or destination.is_relative_to(root) or root.is_relative_to(destination):
        raise Invalid('Keep bundled skills and installed user instructions in separate directories')
    try:
        payload,skills=_payload(root)
        _include_license(root,payload,skills)
        _preflight(destination,payload)
        for relative,data in payload.items():
            if _existing(destination/relative)!=data:raise Invalid('An expected installed skill resource is missing or differs')
    except (OSError,UnicodeError):
        raise Invalid('Installed skill verification failed; existing instructions are preserved') from None
    return dict(skills=skills,files_verified=len(payload),writes_files=False,uses_network=False,
        skills_execution_verified=False,skill_discovery_verified=False)
