"""Plan or install a separate, wheel-hashed Linux runtime; never start a relay."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import venv

ROOT=Path(__file__).resolve().parents[1]
LOCK='requirements/public-linux-py312.lock'
PUBLIC_PROFILE='requirements/public-profile.json'
REFERENCE_LOCK='requirements/deployed-linux-py312.lock'
REFERENCE_PROFILE='requirements/deployed-profile.json'
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'tools'))
from check_release_runtime import verify


class InstallError(ValueError):pass


def plain_path(value):
    p=Path(value).expanduser()
    if not p.is_absolute() or any(c in str(p) for c in '<>\0\n'):
        raise InstallError('choose_absolute_install_paths')
    for parent in [p,*p.parents]:
        if parent.is_symlink():raise InstallError('linked_install_path')
    return p.resolve()


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
    if platform.system()!='Linux' or platform.machine() not in {'x86_64','AMD64'} or sys.version_info[:2]!=(3,12):
        raise InstallError('selected_profile_requires_linux_x86_64_python312')
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
            'creates_private_state':False,'starts_relay':False,'copies_existing_credentials':False}


def apply(root,prepared,wheelhouse,reproduce=False):
    root=Path(root).resolve()
    if prepared.get('dependency_profile')=='deployed-reference-only' and not reproduce:
        raise InstallError('reference_dependency_profile_requires_explicit_selection')
    current=plan(root,prepared['prefix'],prepared['state'],reproduce)
    if current!=prepared:raise InstallError('install_plan_changed')
    wheelhouse=plain_path(wheelhouse)
    if not wheelhouse.is_dir():raise InstallError('wheelhouse_missing')
    from intern_personal.distribution import package_files
    inputs=package_files(root)
    target=Path(prepared['prefix']);target.mkdir(mode=0o700,parents=True,exist_ok=False)
    receipt={'schema':'intern-agent.install-receipt.v1','snapshot_sha256':prepared['snapshot_sha256'],
             'lock_sha256':prepared['lock_sha256'],'state_path':prepared['state'],
             'profile_sha256':prepared['profile_sha256'],
             'status':'preparing','starts_relay':False,'dependency_profile':prepared['dependency_profile'],
             'dependency_lock':prepared['dependency_lock'],'dependency_imports_verified':False}
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
                'dependency_profile':prepared['dependency_profile'],'dependency_imports_verified':True}
    except BaseException:
        receipt['status']='failed';save()
        # Preserve only this install's partial prefix/receipt for review; no broad deletion/retry.
        raise


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prefix',required=True);p.add_argument('--state',required=True)
    p.add_argument('--apply',action='store_true');p.add_argument('--wheelhouse')
    p.add_argument('--reproduce-deployed-dependencies',action='store_true')
    a=p.parse_args(argv)
    try:
        prepared=plan(ROOT,a.prefix,a.state,a.reproduce_deployed_dependencies)
        if a.apply:
            if not a.wheelhouse:raise InstallError('select_local_wheelhouse')
            result=apply(ROOT,prepared,a.wheelhouse,a.reproduce_deployed_dependencies)
        else:result=prepared
        print(json.dumps(result,sort_keys=True));return 0
    except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError) as e:
        print(json.dumps({'ok':False,'error':str(e) if isinstance(e,InstallError) else 'install_preflight_or_apply_failed',
                          'starts_relay':False}));return 2


if __name__=='__main__':sys.exit(main())
