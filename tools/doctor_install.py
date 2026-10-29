"""Read-only installation checks before account setup; no relay or private state."""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))

from check_release_runtime import verify as verify_runtime
from intern_personal import skill_bundle


def dependency_profile(root):
    """Check the accepted profile and every locked version/hash without fetching."""
    root = Path(root)
    profile_path = root / 'requirements/public-profile.json'
    lock_path = root / 'requirements/public-linux-py312.lock'
    for file in [profile_path, lock_path]:
        if file.is_symlink() or file.parent.is_symlink() or not file.is_file():
            raise ValueError('dependency_profile_missing_or_linked')
    profile = json.loads(profile_path.read_bytes())
    lock = lock_path.read_bytes()
    if (not isinstance(profile, dict) or profile.get('schema') != 'intern-agent.dependency-profile.v1'
            or profile.get('profile') != 'public-linux-py312'
            or profile.get('platform') != 'linux-x86_64' or profile.get('python') != '3.12'
            or profile.get('implementation') != 'CPython'
            or profile.get('lock_file') != 'requirements/public-linux-py312.lock'
            or profile.get('public_install_recommended') is not True
            or profile.get('lock_sha256') != hashlib.sha256(lock).hexdigest()):
        raise ValueError('dependency_profile_inconsistent')
    wheels = profile.get('wheels')
    if not isinstance(wheels, list) or not wheels:
        raise ValueError('dependency_profile_has_no_wheels')
    expected = {}
    for item in wheels:
        name, version, digest = item['name'], item['version'], item['sha256']
        if (not isinstance(name, str) or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', name)
                or name in expected or not isinstance(version, str)
                or not re.fullmatch(r'[A-Za-z0-9_.+!-]+', version)
                or not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)):
            raise ValueError('dependency_profile_invalid_wheel')
        expected[name] = (version, digest)
    actual = {}
    for line in lock.decode('utf-8').splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        match = re.fullmatch(r'([a-z0-9]+(?:-[a-z0-9]+)*)==([^\s]+) --hash=sha256:([0-9a-f]{64})', line)
        if not match or match[1] in actual:
            raise ValueError('dependency_lock_invalid_entry')
        actual[match[1]] = (match[2], match[3])
    if actual != expected:
        raise ValueError('dependency_lock_profile_mismatch')
    return {name: version for name, (version, _) in expected.items()}


def _run(args, root):
    environment = {'PATH': os.defpath, 'LANG': 'C.UTF-8', 'PYTHONNOUSERSITE': '1',
                   'PYTHONDONTWRITEBYTECODE': '1', 'PIP_DISABLE_PIP_VERSION_CHECK': '1'}
    try:
        result = subprocess.run(args, cwd=root, env=environment, capture_output=True, timeout=45)
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def diagnose(root=ROOT, *, require_runtime=False, skills_directory=None):
    root = Path(root)
    checks = []

    def add(identity, status, summary, action='', **details):
        checks.append(dict(id=identity, status=status, summary=summary,
                           next_action=action if status != 'pass' else '', **details))

    def local_check(identity, passed, summary, action):
        add(identity, 'pass' if passed else 'fail', summary, action)

    local_check('platform', sys.platform == 'linux' and platform.machine() in {'x86_64', 'AMD64'},
                'The accepted installation host is Linux x86_64.',
                'Use a Linux x86_64 workspace host, including VS Code Remote SSH.')
    local_check('python', platform.python_implementation() == 'CPython' and sys.version_info[:2] == (3, 12),
                'The accepted installation interpreter is CPython 3.12.',
                'Run this doctor with CPython 3.12 on the selected host.')
    for module in ['venv', 'ensurepip']:
        local_check(module, importlib.util.find_spec(module) is not None,
                    'Python ' + module + ' support is available.',
                    'Install the host Python venv/ensurepip support, then rerun the doctor.')
    for tool in ['git', 'tmux', 'codex']:
        local_check(tool, shutil.which(tool) is not None, tool + ' is available on PATH.',
                    'Install ' + tool + ' on the selected host and make it available on PATH. No tools were installed.')
    try:
        baseline = verify_runtime(root)
        add('release_integrity', 'pass', 'Selected runtime and available frontend match their recorded baseline.',
            snapshot_sha256=baseline['snapshot_sha256'])
    except (OSError, ValueError, KeyError, TypeError):
        add('release_integrity', 'fail', 'The selected release baseline could not be verified.',
            'Use a complete verified release or review intentional source changes and refresh its audited inventory.')
    versions = None
    try:
        versions = dependency_profile(root)
        add('dependency_profile', 'pass', 'Public dependency profile matches every locked version and wheel hash.',
            distributions=len(versions))
    except (OSError, ValueError, KeyError, TypeError):
        add('dependency_profile', 'fail', 'The public dependency profile or lock is missing or inconsistent.',
            'Restore the matching profile and lock from the selected verified release.')
    exact = versions is not None
    missing, different = [], []
    for name, version in (versions or {}).items():
        try:
            if importlib.metadata.version(name) != version:
                different.append(name)
        except importlib.metadata.PackageNotFoundError:
            missing.append(name)
    exact = exact and not missing and not different
    pending = 'fail' if require_runtime else 'warn'
    add('runtime_dependencies', 'pass' if exact else pending,
        'Selected Python distributions match the locked profile.' if exact else 'Selected Python is missing locked dependencies or has different versions.',
        'Install the isolated runtime with tools/install_runtime.py, select its venv Python, and rerun with --require-runtime.',
        missing_distributions=missing, different_distributions=different)
    # Only import SDKs after their complete locked environment has been identified.
    # No client construction, login, token read or network request is performed.
    imports_ok = False
    closure_ok = False
    if exact:
        code = ('import sys; sys.path.insert(0, sys.argv[1]); '
                'import lark_oapi, requests; from Crypto.Cipher import AES; '
                'from websockets.sync.client import unix_connect; '
                'import intern_personal.feishu_live, intern_personal.codex_bridge; '
                'AES.new(bytes(16), AES.MODE_ECB).encrypt(bytes(16))')
        imports_ok = _run([sys.executable, '-I', '-B', '-c', code, str(root)], root)
        closure_ok = _run([sys.executable, '-I', '-B', '-m', 'pip', '--isolated',
                           '--disable-pip-version-check', 'check'], root)
    add('runtime_imports', 'pass' if imports_ok else 'fail' if exact or require_runtime else 'warn',
        'SDK, transport, Crypto and application imports verified.' if imports_ok else 'Runtime imports have not passed in this Python environment.',
        'Use the installed venv Python; review a failed installation receipt before retrying.')
    add('dependency_closure', 'pass' if closure_ok else 'fail' if exact or require_runtime else 'warn',
        'Installed pip dependency closure verified.' if closure_ok else 'Installed dependency closure has not passed.',
        'Use the isolated installed runtime and run its Python with -m pip check; preserve existing installations.')
    try:
        if skill_bundle._existing(root / 'LICENSE') is None:
            raise ValueError('release_notice_missing')
        payload, skills = skill_bundle._payload(root / 'skills')
        skill_bundle._include_license(root / 'skills', payload, skills)
        add('bundled_skills', 'pass', 'Bundled skill names, descriptions, references and release notices are valid.',
            skills=skills, files_verified=len(payload))
    except (OSError, ValueError, AttributeError, NotImplementedError):
        add('bundled_skills', 'fail', 'The bundled skill instructions or references are incomplete or invalid.',
            'Restore the complete reviewed skills tree and its referenced resources.')
    if skills_directory is not None:
        try:
            result = skill_bundle.verify(skills_directory, root / 'skills')
            add('installed_skills', 'pass', 'Installed skill instruction, reference and license bytes match the selected release.',
                skills=result['skills'], files_verified=result['files_verified'])
        except (OSError, ValueError, AttributeError, NotImplementedError):
            add('installed_skills', 'fail', 'An installed skill is missing, changed, linked or invalid.',
                'Review the explicit skills destination and preserve modified instructions; install the matching complete bundle separately.')
    else:
        add('installed_skills', 'warn', 'No installed skills destination was selected for byte verification.',
            'Skills are optional. To verify an installation, rerun with --skills-directory and the intended session\'s explicit skills path.')
    add('skill_discovery', 'warn', 'Codex session discovery and agent execution cannot be established by copying skill files.',
        'Confirm the skills appear in the intended Codex session. Explicitly invoke $install-on-linux and provide the selected product/backend root.')
    ok = not any(item['status'] == 'fail' for item in checks)
    runtime_ready = ok and exact and imports_ok and closure_ok
    return dict(schema='intern-agent.install-doctor.v1', ok=ok, runtime_ready=runtime_ready,
                ready_for_setup=runtime_ready, checks=checks, writes_files=False, uses_network=False,
                starts_relay=False, reads_private_state=False, account_configuration_verified=False,
                skills_execution_verified=False, skill_discovery_verified=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--require-runtime', action='store_true', help='Fail unless this Python has the complete accepted runtime installed')
    parser.add_argument('--skills-directory', help='Explicit absolute installed skills directory to verify without writing')
    args = parser.parse_args(argv)
    report = diagnose(require_runtime=args.require_runtime, skills_directory=args.skills_directory)
    print(json.dumps(report, sort_keys=True))
    return 0 if report['ok'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
