"""Plan or install a separate, wheel-hashed Linux runtime; never start a relay."""
from __future__ import annotations
import argparse
import hashlib
from http.client import HTTPException
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import sys
import time
import uuid
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

try:
    import venv
except ImportError:
    venv=None

ROOT=Path(__file__).resolve().parents[1]
LOCK='requirements/public-linux-py312.lock'
PUBLIC_PROFILE='requirements/public-profile.json'
REFERENCE_LOCK='requirements/deployed-linux-py312.lock'
REFERENCE_PROFILE='requirements/deployed-profile.json'
MAX_WHEEL_BYTES=64*1024*1024
MAX_WHEEL_TOTAL=256*1024*1024
DOWNLOAD_SECONDS=120
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'tools'))
from check_release_runtime import verify


class InstallError(ValueError):pass


def python_prerequisites():
    if venv is None:
        raise InstallError('python_venv_unavailable_install_python312_venv')
    try:
        import ensurepip
        ensurepip.version()
    except (ImportError,OSError,ValueError):
        raise InstallError('python_ensurepip_unavailable_install_python312_venv') from None


def plain_path(value):
    p=Path(value).expanduser()
    if not p.is_absolute() or '..' in p.parts or any(c in str(p) for c in '<>\0\r\n'):
        raise InstallError('choose_absolute_install_paths')
    for parent in [p,*p.parents]:
        if parent.is_symlink():raise InstallError('linked_install_path')
    return p.resolve()


def wheel_url(entry):
    value=entry.get('download_url')
    if not isinstance(value,str):raise InstallError('reviewed_wheel_download_url_unavailable')
    try:parsed=urlsplit(value)
    except ValueError:raise InstallError('unapproved_wheel_download_url') from None
    # The profile supplies a specific wheel, never a package index, redirect,
    # credential-bearing URL or another host selected by the environment.
    if (parsed.scheme!='https' or parsed.netloc!='files.pythonhosted.org' or parsed.query or parsed.fragment
            or value!='https://files.pythonhosted.org'+parsed.path
            or not re.fullmatch(r'/packages/[0-9a-f]{2}/[0-9a-f]{2}/[0-9a-f]{60}/'+re.escape(entry['wheel']),parsed.path)):
        raise InstallError('unapproved_wheel_download_url')
    return value


def wheel_entries(lock,metadata,*,download=False):
    """Require one reviewed wheel for every exact requirement in the lock."""
    def normalized(name):return re.sub(r'[-_.]+','-',name).lower()
    locked={}
    for line in lock.read_text().splitlines():
        line=line.strip()
        if not line or line.startswith('#'):continue
        match=re.fullmatch(r'([A-Za-z0-9][A-Za-z0-9_.-]*)==([A-Za-z0-9][A-Za-z0-9_.+!-]*) --hash=sha256:([0-9a-f]{64})',line)
        if not match:raise InstallError('dependency_lock_must_pin_exact_hashed_wheels')
        name,version,digest=match.groups();name=normalized(name)
        if name in locked:raise InstallError('dependency_lock_duplicate_requirement')
        locked[name]=(version,digest)
    wheels=metadata.get('wheels')
    if not isinstance(wheels,list) or not 1<=len(wheels)<=64:
        raise InstallError('dependency_wheel_inventory_invalid')
    selected={};filenames=set();total=0
    for entry in wheels:
        if not isinstance(entry,dict):raise InstallError('dependency_wheel_inventory_invalid')
        name,version,filename,digest=(entry.get(key) for key in ('name','version','wheel','sha256'))
        if (not all(isinstance(v,str) for v in (name,version,filename,digest))
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*',name)
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+!-]*',version)
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+-]*\.whl',filename)
                or not re.fullmatch(r'[0-9a-f]{64}',digest)):
            raise InstallError('dependency_wheel_inventory_invalid')
        key=normalized(name)
        if key in selected or filename.casefold() in filenames or locked.get(key)!=(version,digest):
            raise InstallError('dependency_wheel_lock_closure_mismatch')
        # Wheel distribution/version components must identify this lock entry.
        parts=filename[:-4].split('-')
        if len(parts) not in {5,6} or normalized(parts[0])!=key or parts[1]!=version:
            raise InstallError('dependency_wheel_filename_mismatch')
        size=entry.get('size')
        if size is not None and (type(size) is not int or not 1<=size<=MAX_WHEEL_BYTES):
            raise InstallError('dependency_wheel_size_invalid')
        total+=size if size is not None else 0
        if total>MAX_WHEEL_TOTAL:
            raise InstallError('dependency_wheel_total_size_exceeded')
        if download:
            wheel_url(entry)
            if size is None:raise InstallError('reviewed_wheel_download_size_unavailable')
        selected[key]=entry;filenames.add(filename.casefold())
    if set(selected)!=set(locked):raise InstallError('dependency_wheel_lock_closure_mismatch')
    return list(selected.values())


def _wheel_directory(path,*,create=False):
    """Keep cache writes anchored to ordinary, owner-controlled directories."""
    descriptor=os.open(path.anchor,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            try:child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    os.close(descriptor);return None
                try:os.mkdir(part,0o700,dir_fd=descriptor)
                except FileExistsError:pass
                child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=descriptor)
            os.close(descriptor);descriptor=child
        info=os.fstat(descriptor)
        if info.st_uid!=os.getuid() or info.st_mode&0o022:
            raise InstallError('wheelhouse_must_be_owner_controlled')
        return descriptor
    except BaseException:
        os.close(descriptor);raise


def _cached_wheel(directory,entry):
    try:descriptor=os.open(entry['wheel'],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
    except FileNotFoundError:return False
    try:
        info=os.fstat(descriptor);expected=entry.get('size')
        if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o022
                or not 1<=info.st_size<=MAX_WHEEL_BYTES):
            raise InstallError('cached_wheel_must_be_owned_regular_file')
        if expected is not None and info.st_size!=expected:
            raise InstallError('cached_wheel_size_mismatch_preserve_existing_file')
        digest=hashlib.sha256();total=0
        while True:
            data=os.read(descriptor,min(64*1024,MAX_WHEEL_BYTES+1-total))
            if not data:break
            total+=len(data);digest.update(data)
            if total>MAX_WHEEL_BYTES:raise InstallError('cached_wheel_size_limit_exceeded')
        if digest.hexdigest()!=entry['sha256']:
            raise InstallError('cached_wheel_sha256_mismatch_preserve_existing_file')
        return True
    finally:os.close(descriptor)


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise InstallError('wheel_download_redirect_rejected')


def _download_wheel(directory,entry,opener):
    temporary='.intern-agent-wheel-'+uuid.uuid4().hex
    descriptor=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory)
    try:
        with os.fdopen(descriptor,'wb') as stream:
            request=Request(wheel_url(entry),headers={'Accept-Encoding':'identity','User-Agent':'Intern-Agent-Installer/1'})
            started=time.monotonic();digest=hashlib.sha256();total=0
            try:
                with opener.open(request,timeout=30) as response:
                    if response.status!=200 or response.geturl()!=entry['download_url']:
                        raise InstallError('wheel_download_response_rejected')
                    length=response.headers.get('Content-Length')
                    if length is not None and length!=str(entry['size']):
                        raise InstallError('wheel_download_size_mismatch')
                    if response.headers.get('Content-Encoding','identity')!='identity':
                        raise InstallError('wheel_download_encoding_rejected')
                    while True:
                        data=response.read(min(64*1024,entry['size']+1-total))
                        if time.monotonic()-started>DOWNLOAD_SECONDS:
                            raise InstallError('wheel_download_time_limit_exceeded')
                        if not data:break
                        total+=len(data)
                        if total>entry['size']:raise InstallError('wheel_download_size_mismatch')
                        digest.update(data);stream.write(data)
            except (URLError,OSError,HTTPException):raise InstallError('wheel_download_failed_check_network_and_retry') from None
            if total!=entry['size']:raise InstallError('wheel_download_size_mismatch')
            if digest.hexdigest()!=entry['sha256']:raise InstallError('wheel_download_sha256_mismatch')
            stream.flush();os.fsync(stream.fileno())
        try:os.link(temporary,entry['wheel'],src_dir_fd=directory,dst_dir_fd=directory,follow_symlinks=False)
        except FileExistsError:
            if not _cached_wheel(directory,entry):raise InstallError('wheel_cache_changed_during_download')
        os.fsync(directory)
    finally:os.unlink(temporary,dir_fd=directory)


def prepare_wheels(root,wheelhouse,metadata,lock_name,*,download=False):
    entries=wheel_entries(root/lock_name,metadata,download=download)
    directory=_wheel_directory(wheelhouse,create=download)
    if directory is None:raise InstallError('wheelhouse_missing_use_explicit_download_or_supply_cache')
    try:
        # Check every existing entry before making any network request or write.
        missing=[entry for entry in entries if not _cached_wheel(directory,entry)]
        if missing and not download:raise InstallError('locked_wheel_missing_use_explicit_download_or_supply_cache')
        if missing:
            # No inherited proxy/auth handlers; the only destinations are reviewed
            # HTTPS wheel URLs. TLS certificate verification uses stdlib defaults.
            opener=build_opener(ProxyHandler({}),_NoRedirects())
            for entry in missing:_download_wheel(directory,entry,opener)
        return len(missing)
    except OSError:
        raise InstallError('wheelhouse_linked_or_inaccessible') from None
    finally:os.close(directory)


def dependency_profile(root,reproduce=False):
    lock_name=REFERENCE_LOCK if reproduce else LOCK
    profile_name=REFERENCE_PROFILE if reproduce else PUBLIC_PROFILE
    profile_id='deployed-reference-only' if reproduce else 'public-linux-py312'
    lock=root/lock_name;profile=root/profile_name
    if lock.is_symlink() or not lock.is_file() or not lock.resolve().is_relative_to(root):
        raise InstallError('dependency_lock_missing')
    if profile.is_symlink() or not profile.is_file() or not profile.resolve().is_relative_to(root):
        raise InstallError('dependency_profile_missing')
    metadata=json.loads(profile.read_text())
    if not isinstance(metadata,dict):raise InstallError('dependency_profile_invalid')
    if (metadata.get('profile',profile_id)!=profile_id or
            metadata.get('lock_file',lock_name)!=lock_name or
            metadata.get('platform','linux-x86_64')!='linux-x86_64' or
            metadata.get('python','3.12')!='3.12'):
        raise InstallError('dependency_profile_invalid')
    if hashlib.sha256(lock.read_bytes()).hexdigest()!=metadata['lock_sha256']:
        raise InstallError('dependency_lock_changed')
    if not reproduce and metadata.get('public_install_recommended') is not True:
        raise InstallError('public_dependency_profile_not_accepted')
    wheel_entries(lock,metadata)
    return lock_name,profile_id,metadata


def plan(root,prefix,state,reproduce=False):
    root=Path(root).resolve();prefix=plain_path(prefix);state=plain_path(state)
    if prefix==state or prefix.is_relative_to(state) or state.is_relative_to(prefix):
        raise InstallError('keep_install_and_state_separate')
    if prefix.exists():raise InstallError('install_target_already_exists')
    if len(os.fsencode(state/('runtime/h-'+'0'*16+'.sock')))>=104:
        raise InstallError('choose_shorter_private_state_path')
    if state.exists() and (not state.is_dir() or state.stat().st_uid!=os.getuid() or state.stat().st_mode&0o077):
        raise InstallError('state_directory_must_be_private_and_owned')
    if (platform.system()!='Linux' or platform.machine() not in {'x86_64','AMD64'}
            or platform.python_implementation()!='CPython' or sys.version_info[:2]!=(3,12)):
        raise InstallError('selected_profile_requires_linux_x86_64_python312')
    python_prerequisites()
    baseline=verify(root)
    lock_name,profile_id,metadata=dependency_profile(root,reproduce)
    return {'schema':'intern-agent.install-plan.v1','prefix':str(prefix),'state':str(state),
            'snapshot_sha256':baseline['snapshot_sha256'],'lock_sha256':metadata['lock_sha256'],
            'profile_sha256':hashlib.sha256((root/(REFERENCE_PROFILE if reproduce else PUBLIC_PROFILE)).read_bytes()).hexdigest(),
            'system_tools':{name:bool(shutil.which(name)) for name in ['git','tmux','codex']},
            'dependency_profile':profile_id,'dependency_lock':lock_name,
            'public_install_recommended':not reproduce,
            'steps':['Verify pinned runtime and dependency lock','Create separate virtual environment',
                     'Install hash-verified wheels without contacting an index','Copy allowlisted backend into install prefix',
                     'Import SDK, transport and installed application without network calls',
                     'Save receipt; leave account setup and live startup to the user'],
            'network_access':False,'creates_private_state':False,'starts_relay':False,'copies_existing_credentials':False}


def apply(root,prepared,wheelhouse,reproduce=False,*,download=False):
    root=Path(root).resolve()
    if prepared.get('dependency_profile')=='deployed-reference-only' and not reproduce:
        raise InstallError('reference_dependency_profile_requires_explicit_selection')
    current=plan(root,prepared['prefix'],prepared['state'],reproduce)
    if current!=prepared:raise InstallError('install_plan_changed')
    wheelhouse=plain_path(wheelhouse)
    for private_path in (Path(prepared['prefix']),Path(prepared['state'])):
        if wheelhouse==private_path or wheelhouse.is_relative_to(private_path) or private_path.is_relative_to(wheelhouse):
            raise InstallError('keep_wheelhouse_install_and_state_separate')
    lock_name,_,metadata=dependency_profile(root,reproduce)
    downloaded=prepare_wheels(root,wheelhouse,metadata,lock_name,download=download)
    from intern_personal.distribution import package_files
    inputs=package_files(root)
    target=Path(prepared['prefix']);target.mkdir(mode=0o700,parents=True,exist_ok=False)
    receipt={'schema':'intern-agent.install-receipt.v1','snapshot_sha256':prepared['snapshot_sha256'],
             'lock_sha256':prepared['lock_sha256'],'state_path':prepared['state'],
             'profile_sha256':prepared['profile_sha256'],
             'status':'preparing','starts_relay':False,'dependency_profile':prepared['dependency_profile'],
             'dependency_lock':prepared['dependency_lock'],'dependency_imports_verified':False,
             'downloads_requested':download,'wheels_downloaded':downloaded}
    def save():
        f=target/'install-receipt.json';f.write_text(json.dumps(receipt,indent=2)+'\n');f.chmod(0o600)
    save()
    try:
        venv.EnvBuilder(with_pip=True,system_site_packages=False).create(target/'venv')
        executable=target/'venv/bin/python'
        env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','PYTHONNOUSERSITE':'1','PYTHONDONTWRITEBYTECODE':'1'}
        # No inherited PIP index/auth/proxy configuration; only an explicit local wheelhouse.
        args=[str(executable),'-m','pip','--isolated','install','--no-index','--find-links',str(wheelhouse),
              '--only-binary=:all:','--require-hashes','--no-deps','--disable-pip-version-check','-r',str(root/prepared['dependency_lock'])]
        result=subprocess.run(args,env=env,capture_output=True,timeout=240)
        if result.returncode:raise InstallError('locked_dependency_install_failed')
        result=subprocess.run([str(executable),'-m','pip','check'],env=env,capture_output=True,timeout=30)
        if result.returncode:raise InstallError('dependency_closure_invalid')
        source=target/'backend';source.mkdir(mode=0o700)
        for name in inputs:
            original=root/name
            if original.is_symlink() or not original.resolve().is_relative_to(root):raise InstallError('linked_install_input')
            p=source/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(original.read_bytes())
        # Verify copied source, not just the input tree.
        runtime=verify(root)
        recorded=json.loads((root/'release-runtime.json').read_text())
        for entry in recorded['runtime_files']:
            if hashlib.sha256((source/entry['path']).read_bytes()).hexdigest()!=entry['sha256']:
                raise InstallError('copied_runtime_drift')
        copied=verify(source)
        if copied['snapshot_sha256']!=prepared['snapshot_sha256']:
            raise InstallError('copied_runtime_drift')
        _,_,copied_metadata=dependency_profile(source,reproduce)
        copied_profile=source/(REFERENCE_PROFILE if reproduce else PUBLIC_PROFILE)
        if (copied_metadata['lock_sha256']!=prepared['lock_sha256'] or
                hashlib.sha256(copied_profile.read_bytes()).hexdigest()!=prepared['profile_sha256']):
            raise InstallError('copied_dependency_profile_drift')
        imports=('import lark_oapi, requests; '
                 'from Crypto.Cipher import AES; '
                 'from websockets.sync.client import unix_connect; '
                 'import intern_personal.feishu_live, intern_personal.codex_bridge; '
                 'AES.new(bytes(16), AES.MODE_ECB).encrypt(bytes(16))')
        result=subprocess.run([str(executable),'-c',imports],cwd=source,env=env,capture_output=True,timeout=90)
        if result.returncode:raise InstallError('installed_runtime_import_failed')
        receipt['dependency_imports_verified']=True
        receipt['status']='installed';receipt['backend_inputs']=len(inputs);save()
        return {'installed':True,'snapshot_sha256':runtime['snapshot_sha256'],'starts_relay':False,
                'state_created':False,'python':str(executable),'backend':str(source),
                'dependency_profile':prepared['dependency_profile'],'dependency_imports_verified':True,
                'downloads_requested':download,'wheels_downloaded':downloaded}
    except BaseException:
        receipt['status']='failed';save()
        # Preserve only this install's partial prefix/receipt for review; no broad deletion/retry.
        raise


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prefix',required=True);p.add_argument('--state',required=True)
    p.add_argument('--apply',action='store_true');p.add_argument('--wheelhouse')
    p.add_argument('--download',action='store_true',help='With --apply and --wheelhouse, fetch missing exact reviewed HTTPS wheels without system pip')
    p.add_argument('--reproduce-deployed-dependencies',action='store_true')
    a=p.parse_args(argv)
    try:
        if a.download and (not a.apply or not a.wheelhouse):
            raise InstallError('download_requires_apply_and_explicit_wheelhouse')
        prepared=plan(ROOT,a.prefix,a.state,a.reproduce_deployed_dependencies)
        if a.apply:
            if not a.wheelhouse:raise InstallError('select_local_wheelhouse')
            result=apply(ROOT,prepared,a.wheelhouse,a.reproduce_deployed_dependencies,download=a.download)
        else:result=prepared
        print(json.dumps(result,sort_keys=True));return 0
    except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError) as e:
        print(json.dumps({'ok':False,'error':str(e) if isinstance(e,InstallError) else 'install_preflight_or_apply_failed',
                          'starts_relay':False}));return 2


if __name__=='__main__':sys.exit(main())
