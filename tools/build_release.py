"""Build explicit local release assets; publishing is a separate guarded CI step."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.check_source_candidate import review, safe_file

REPOSITORY = 'JZSAWYER/intern-agent'
PRODUCT = 'release-product.json'
SEMVER = re.compile(r'(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z')
COMMIT = re.compile(r'[0-9a-f]{40}\Z')
SKILL_NAME = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*\Z')
REQUIRED_RELEASE_SKILLS = {'recover-intern-agent', 'feishu-file-transfer'}


class ReleaseError(ValueError):
    """Public errors are fixed labels rather than rejected private values."""


def encoded(value):
    return (json.dumps(value, indent=2, sort_keys=True) + '\n').encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def release_policy(config, expected_channel=None):
    channel = config.get('release_channel')
    if not isinstance(channel, str) or channel not in {'private', 'public'}:
        raise ReleaseError('explicit_release_channel_required')
    hold = config.get('public_visibility_hold')
    if type(hold) is not bool:
        raise ReleaseError('explicit_visibility_hold_required')
    if channel == 'public' and hold:
        raise ReleaseError('public_visibility_hold_blocks_release')
    if expected_channel is not None and channel != expected_channel:
        raise ReleaseError('release_channel_differs')
    return channel


def check_repository_visibility(config, repository):
    channel = release_policy(config)
    if not isinstance(repository, dict) or repository.get('full_name') != config['repository']:
        raise ReleaseError('unexpected_release_repository')
    if (type(repository.get('private')) is not bool
            or repository['private'] != (channel == 'private')):
        raise ReleaseError('repository_visibility_differs')
    return channel


def check_event_visibility(config):
    event_file = os.environ.get('GITHUB_EVENT_PATH')
    if not event_file:
        raise ReleaseError('release_event_required')
    event = json.loads(Path(event_file).read_bytes())
    if not isinstance(event, dict):
        raise ReleaseError('invalid_release_event')
    check_repository_visibility(config, event.get('repository'))


def product_config(root):
    config = json.loads(safe_file(root, PRODUCT).read_bytes())
    if config.get('schema') != 'intern-agent.release-product.v1':
        raise ReleaseError('invalid_product_schema')
    if config.get('repository') != REPOSITORY:
        raise ReleaseError('unexpected_public_destination')
    release_policy(config)
    if (not isinstance(config.get('version'), str) or not SEMVER.fullmatch(config['version'])
            or config['version'] == '0.0.0'):
        raise ReleaseError('fresh_stable_version_required')
    extension = config.get('extension', {})
    if (extension.get('publisher'), extension.get('name'), extension.get('namespace')) != (
            'intern-agent', 'intern-agent', 'internAgentPublic'):
        raise ReleaseError('separate_public_extension_identity_required')
    if extension.get('license') != 'MIT':
        raise ReleaseError('unexpected_proposed_license')
    skills = config.get('skills')
    if (not isinstance(skills, list) or not skills or len(set(skills)) != len(skills)
            or any(not isinstance(name, str) or not SKILL_NAME.fullmatch(name) for name in skills)):
        raise ReleaseError('explicit_skill_inventory_required')
    return config


def check_handoff(config, manifest, prepare=False):
    if prepare:
        return False
    handoff = config.get('final_handoff', {})
    if handoff.get('approved') is not True:
        raise ReleaseError('final_source_handoff_pending')
    if (handoff.get('repository') != config['repository']
            or handoff.get('version') != config['version']):
        raise ReleaseError('handoff_identity_or_version_differs')
    if handoff.get('channel') != release_policy(config):
        raise ReleaseError('handoff_release_channel_differs')
    pin = handoff.get('source_commit')
    if not isinstance(pin, str) or not COMMIT.fullmatch(pin) or pin != manifest.get('source_commit'):
        raise ReleaseError('verified_source_commit_differs')
    gates = config.get('gates', {})
    for gate in ('ownership_approved', 'license_approved', 'dependency_security_approved', 'clean_install_approved'):
        if gates.get(gate) is not True:
            raise ReleaseError(gate + '_pending')
    return True


def check_checkout(root, expected_commit, ci=False):
    if not isinstance(expected_commit, str) or not COMMIT.fullmatch(expected_commit):
        raise ReleaseError('expected_export_commit_required')
    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.DEVNULL).decode().strip()
    if Path(git('rev-parse', '--show-toplevel')).resolve() != root:
        raise ReleaseError('clean_product_repository_required')
    if git('rev-parse', 'HEAD') != expected_commit:
        raise ReleaseError('export_commit_differs')
    if git('status', '--porcelain', '--untracked-files=all'):
        raise ReleaseError('clean_export_checkout_required')
    origin = git('remote', 'get-url', 'origin')
    if origin not in ('https://github.com/' + REPOSITORY + '.git',
                      'https://github.com/' + REPOSITORY, 'git@github.com:' + REPOSITORY + '.git'):
        raise ReleaseError('unexpected_export_origin')
    if ci and (os.environ.get('GITHUB_REPOSITORY') != REPOSITORY
               or os.environ.get('GITHUB_EVENT_NAME') != 'push'
               or os.environ.get('GITHUB_REF') != 'refs/heads/main'
               or os.environ.get('GITHUB_SHA') != expected_commit):
        raise ReleaseError('unexpected_release_workflow_context')


def check_public_inventory(root, payload):
    tracked = subprocess.check_output(['git', '-C', str(root), 'ls-files', '-z'],
                                      stderr=subprocess.DEVNULL).decode().split('\0')
    names = [name for name in tracked if name]
    if len(names) != len(set(names)) or set(names) != set(payload):
        raise ReleaseError('tracked_public_inventory_differs')


def check_dependency_profile(root, config, payload, prepare):
    profile_name, lock_name = config.get('dependency_profile'), config.get('dependency_lock')
    if profile_name not in payload or lock_name not in payload:
        raise ReleaseError('public_dependency_profile_not_in_inventory')
    profile = json.loads(payload[profile_name])
    if (profile.get('schema') != 'intern-agent.dependency-profile.v1'
            or profile.get('lock_file') != lock_name):
        raise ReleaseError('invalid_public_dependency_profile')
    if profile.get('lock_sha256') != digest(payload[lock_name]):
        raise ReleaseError('public_dependency_lock_differs')
    if not prepare and profile.get('public_install_recommended') is not True:
        raise ReleaseError('public_dependency_profile_not_accepted')
    return {'profile': profile_name, 'profile_sha256': digest(payload[profile_name]),
            'lock': lock_name, 'lock_sha256': digest(payload[lock_name])}


def skill_payload(config, payload, source_commit, prepare):
    if not prepare and not REQUIRED_RELEASE_SKILLS.issubset(config['skills']):
        raise ReleaseError('recovery_and_file_transfer_skills_required')
    selected = {}
    for skill in sorted(config['skills']):
        prefix = 'skills/' + skill + '/'
        if prefix + 'SKILL.md' not in payload:
            raise ReleaseError('skill_entrypoint_not_in_inventory')
        selected.update((name, data) for name, data in payload.items() if name.startswith(prefix))
    if any(Path(name).suffix.lower() not in {'.md', '.txt', '.json', '.yaml', '.yml', '.toml'} for name in selected):
        raise ReleaseError('skill_bundle_requires_inert_text_resources')
    manifest = {'schema': 'intern-agent.skill-bundle.v1', 'product': 'intern-agent',
                'version': config['version'], 'source_commit': source_commit,
                'license': 'UNLICENSED' if prepare else 'MIT',
                'release_approved': not prepare,
                'files': [{'path': name, 'sha256': digest(data)} for name, data in sorted(selected.items())]}
    # The manifest records source-relative paths; the safe installer selects skill directories.
    if not prepare:
        manifest['license_file'] = {'path': 'LICENSE', 'sha256': digest(payload['LICENSE'])}
        selected['LICENSE'] = payload['LICENSE']
    selected['skills-manifest.json'] = encoded(manifest)
    return selected


def write_archive(path, payload):
    with zipfile.ZipFile(path, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(payload.items()):
            item = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            item.create_system = 3
            item.compress_type = zipfile.ZIP_DEFLATED
            item.external_attr = 0o100644 << 16
            archive.writestr(item, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    with zipfile.ZipFile(path) as archive:
        if (archive.testzip() is not None or len(archive.namelist()) != len(payload)
                or set(archive.namelist()) != set(payload)
                or any(archive.read(name) != data for name, data in payload.items())):
            raise ReleaseError('release_archive_integrity_failure')


def validate_vsix(path, config, prepare):
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None or len(archive.namelist()) != len(set(archive.namelist())):
            raise ReleaseError('vsix_integrity_failure')
        package = json.loads(archive.read('extension/package.json'))
        expected_license = 'UNLICENSED' if prepare else 'MIT'
        extension = config['extension']
        if (package.get('publisher'), package.get('name'), package.get('version'), package.get('license')) != (
                extension['publisher'], extension['name'], config['version'], expected_license):
            raise ReleaseError('vsix_product_identity_differs')
        commands = package.get('contributes', {}).get('commands', [])
        if not commands or any(not item.get('command', '').startswith(extension['namespace'] + '.') for item in commands):
            raise ReleaseError('vsix_command_namespace_differs')
    return extension['publisher'] + '.' + extension['name']


def prepare_inputs(root, prepare=False, expected_export_commit=None, ci=False, expected_channel=None):
    root = Path(root).resolve()
    config = product_config(root)
    release_policy(config, expected_channel)
    if ci:
        check_event_visibility(config)
    # Handoff errors are useful before a source manifest has been re-signed for release.
    manifest = json.loads(safe_file(root, 'source-distribution.json').read_bytes())
    ready = check_handoff(config, manifest, prepare)
    if not prepare:
        check_checkout(root, expected_export_commit, ci)
    payload = review(root, release=not prepare)
    if not prepare:
        check_public_inventory(root, payload)
    if payload.get(PRODUCT) != safe_file(root, PRODUCT).read_bytes():
        raise ReleaseError('product_config_not_in_inventory')
    if 'release-runtime.json' not in payload:
        raise ReleaseError('runtime_baseline_not_in_inventory')
    runtime = json.loads(payload['release-runtime.json'])
    if runtime.get('source_commit') != manifest.get('source_commit'):
        raise ReleaseError('runtime_verified_source_commit_differs')
    dependencies = check_dependency_profile(root, config, payload, prepare)
    skills = skill_payload(config, payload, manifest['source_commit'], prepare)
    if 'tools/install_skill_bundle.py' not in payload:
        raise ReleaseError('standalone_skill_installer_not_in_inventory')
    return config, manifest, payload, skills, dependencies, ready


def build(root, output_dir, *, prepare=False, expected_export_commit=None, ci=False, expected_channel=None):
    root = Path(root).resolve()
    config, manifest, payload, skills, dependencies, ready = prepare_inputs(
        root, prepare, expected_export_commit, ci, expected_channel)
    channel = release_policy(config, expected_channel)
    output = Path(output_dir).resolve()
    if output.is_relative_to(root):
        raise ReleaseError('release_output_must_be_outside_source')
    # Never replace previous versions or follow a linked parent into another directory.
    for parent in [Path(output_dir), *Path(output_dir).parents]:
        if parent.is_symlink():
            raise ReleaseError('linked_release_output')
    if output.exists():
        raise ReleaseError('release_output_already_exists')
    output.mkdir(parents=True, mode=0o700)
    stem = 'intern-agent-' + config['version']
    source = output / (stem + '-source.zip')
    skill_zip = output / (stem + '-skills.zip')
    vsix = output / (stem + '.vsix')
    skill_installer = output / (stem + '-install-skills.py')
    write_archive(source, payload)
    write_archive(skill_zip, skills)
    skill_installer.write_bytes(payload['tools/install_skill_bundle.py'])
    from tools.build_vsix import build as build_vsix
    vsix_result = build_vsix(root, vsix, stable=True, product=config, prepare=prepare)
    identity = validate_vsix(vsix, config, prepare)
    # The builder reads paths; reject concurrent modifications after its packaging pass.
    if review(root, release=not prepare) != payload:
        raise ReleaseError('source_changed_during_release_build')
    artifacts = [{'file': path.name, 'sha256': digest(path.read_bytes()), 'bytes': path.stat().st_size}
                 for path in (source, skill_zip, vsix, skill_installer)]
    provenance = {'schema': 'intern-agent.release-provenance.v1', 'product': 'intern-agent',
                  'repository': config['repository'], 'version': config['version'],
                  'release_channel': channel, 'public_visibility_hold': config['public_visibility_hold'],
                  'source_commit': manifest['source_commit'],
                  'maintained_source_commit': manifest['source_commit'],
                  'runtime_snapshot_sha256': json.loads(payload['release-runtime.json'])['snapshot_sha256'],
                  'export_commit': expected_export_commit if not prepare else None,
                  'product_source_commit': expected_export_commit if not prepare else None,
                  'public_source_commit': expected_export_commit if not prepare and channel == 'public' else None,
                  'source_manifest_sha256': digest(payload['source-distribution.json']),
                  'product_config_sha256': digest(payload[PRODUCT]),
                  'source_files': len(payload), 'skills_files': len(skills),
                  'extension_identity': identity, 'dependencies': dependencies,
                  'license': 'UNLICENSED' if prepare else 'MIT',
                  'release_approved': ready, 'preparation_only': prepare,
                  'private_git_history_included': False, 'published': False,
                  'vsix_build': {key: value for key, value in vsix_result.items() if key in {
                      'files', 'sha256', 'published', 'stable', 'preparation_only', 'release_approved',
                      'identity', 'version', 'license', 'frontend_sha256', 'runtime_sha256', 'overlay_receipt'}},
                  'artifacts': artifacts}
    receipt = output / 'release-provenance.json'
    receipt.write_bytes(encoded(provenance))
    checksums = {item['file']: item['sha256'] for item in artifacts}
    checksums[receipt.name] = digest(receipt.read_bytes())
    (output / 'SHA256SUMS').write_text(''.join(value + '  ' + name + '\n'
                                             for name, value in sorted(checksums.items())), encoding='ascii')
    return provenance


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--prepare', action='store_true', help='Unpublished, unlicensed local preparation only')
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--expected-export-commit')
    parser.add_argument('--expected-channel', choices=['private', 'public'],
                        help='Require the reviewed release channel; cannot override the visibility hold')
    parser.add_argument('--ci', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.check_only:
            config, _, _, _, _, ready = prepare_inputs(
                ROOT, args.prepare, args.expected_export_commit, args.ci, args.expected_channel)
            result = {'ok': True, 'version': config['version'], 'release_approved': ready,
                      'release_channel': config['release_channel'],
                      'public_visibility_hold': config['public_visibility_hold'], 'published': False}
        else:
            if args.output_dir is None:
                raise ReleaseError('release_output_directory_required')
            result = build(ROOT, args.output_dir, prepare=args.prepare,
                           expected_export_commit=args.expected_export_commit, ci=args.ci,
                           expected_channel=args.expected_channel)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ValueError, OSError, KeyError, TypeError, SyntaxError, subprocess.SubprocessError, zipfile.BadZipFile) as error:
        label = str(error) if isinstance(error, ReleaseError) else 'release_inputs_rejected'
        print(json.dumps({'ok': False, 'error': label, 'published': False}, sort_keys=True))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
