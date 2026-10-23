"""Inspect or explicitly install a checksummed release skill bundle, without executing it."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import stat
import sys
import uuid
import zipfile
from urllib.parse import unquote, urlsplit

MANIFEST = 'skills-manifest.json'
SCHEMA = 'intern-agent.skill-bundle.v1'
RECEIPTS = '.intern-agent-skill-receipts'
MAX_ARCHIVE = 8 * 1024 * 1024
MAX_FILE = 128 * 1024
MAX_TOTAL = 2 * 1024 * 1024
MAX_FILES = 256
TEXT_SUFFIXES = {'.md', '.txt', '.json', '.yaml', '.yml', '.toml'}
SHA256 = re.compile(r'[0-9a-f]{64}')
VERSION = re.compile(r'(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)')
SKILL = re.compile(r'[a-z][a-z0-9]*(?:-[a-z0-9]+)*')


class SkillInstallError(ValueError):
    """Fixed diagnostic labels never echo rejected input contents."""


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise SkillInstallError('duplicate_manifest_key')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique)


def _path(value, *, absolute=False):
    path = Path(value).expanduser()
    if not path.is_absolute():
        if absolute:
            raise SkillInstallError('choose_absolute_skill_destination')
        path = Path.cwd() / path
    if any(part in {'.', '..'} for part in path.parts) or any(c in str(path) for c in '\x00\r\n<>'):
        raise SkillInstallError('invalid_local_path')
    return path


def _directory(path, *, create=False):
    """Walk from an anchored directory descriptor; never follow an ancestor link."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(path.anchor, flags)
    try:
        for part in path.parts[1:]:
            try:
                child = os.open(part, flags, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    os.close(descriptor)
                    return None
                try:
                    os.mkdir(part, 0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
                child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _read_file(path, *, maximum, private=False):
    parent = _directory(path.parent)
    if parent is None:
        return None
    try:
        try:
            descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
        except FileNotFoundError:
            return None
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
                raise SkillInstallError('unsafe_or_oversized_local_file')
            if private and (info.st_uid != os.getuid() or info.st_mode & 0o077):
                raise SkillInstallError('installation_receipt_must_be_private')
            with os.fdopen(descriptor, 'rb') as stream:
                descriptor = None
                data = stream.read(maximum + 1)
            if len(data) > maximum:
                raise SkillInstallError('oversized_local_file')
            return data
        finally:
            if descriptor is not None:
                os.close(descriptor)
    finally:
        os.close(parent)


def _member(name):
    if not isinstance(name, str) or not name or '\\' in name or '\x00' in name:
        raise SkillInstallError('unsafe_bundle_member')
    relative = PurePosixPath(name)
    if relative.is_absolute() or '..' in relative.parts or relative.as_posix() != name or len(relative.parts) > 12:
        raise SkillInstallError('unsafe_bundle_member')
    if len(name) > 240 or any(not re.fullmatch(r'[A-Za-z0-9_.-]+', part) for part in relative.parts):
        raise SkillInstallError('unsafe_bundle_member')
    return relative


def _entry(entry):
    if not isinstance(entry, dict) or set(entry) != {'path', 'sha256'}:
        raise SkillInstallError('invalid_bundle_file_entry')
    relative = _member(entry['path'])
    if not isinstance(entry['sha256'], str) or not SHA256.fullmatch(entry['sha256']):
        raise SkillInstallError('invalid_bundle_file_hash')
    return relative


def inspect(bundle, expected_sha256=None):
    """Return validated inert bytes; inspection creates no files or directories."""
    bundle = _path(bundle)
    raw = _read_file(bundle, maximum=MAX_ARCHIVE)
    if raw is None:
        raise SkillInstallError('skill_bundle_missing')
    digest = hashlib.sha256(raw).hexdigest()
    if expected_sha256 is not None:
        if not isinstance(expected_sha256, str) or not SHA256.fullmatch(expected_sha256):
            raise SkillInstallError('invalid_external_bundle_sha256')
        if digest != expected_sha256:
            raise SkillInstallError('bundle_sha256_mismatch')
    from io import BytesIO
    with zipfile.ZipFile(BytesIO(raw)) as archive:
        members = archive.infolist()
        if not 2 <= len(members) <= MAX_FILES + 2:
            raise SkillInstallError('bundle_member_count_exceeded')
        names = set()
        folded_names = set()
        total = 0
        for member in members:
            _member(member.filename)
            mode = member.external_attr >> 16
            if (member.filename.casefold() in folded_names or member.is_dir() or member.flag_bits & 1
                    or member.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                    or stat.S_IFMT(mode) not in {0, stat.S_IFREG} or mode & 0o111):
                raise SkillInstallError('duplicate_linked_or_unsupported_bundle_member')
            if not 0 <= member.file_size <= MAX_FILE:
                raise SkillInstallError('bundle_resource_too_large')
            total += member.file_size
            if total > MAX_TOTAL:
                raise SkillInstallError('bundle_total_size_exceeded')
            names.add(member.filename)
            folded_names.add(member.filename.casefold())
        if MANIFEST not in names:
            raise SkillInstallError('skill_manifest_missing')
        manifest = _json(archive.read(MANIFEST).decode('utf-8'))
        required = {'schema', 'product', 'version', 'source_commit', 'files', 'license', 'release_approved'}
        if (not isinstance(manifest, dict) or set(manifest) not in (required, required | {'license_file'})
                or manifest['schema'] != SCHEMA or manifest['product'] != 'intern-agent'
                or not isinstance(manifest['version'], str) or len(manifest['version']) > 64 or not VERSION.fullmatch(manifest['version'])
                or not isinstance(manifest['source_commit'], str) or not re.fullmatch('[0-9a-f]{40}', manifest['source_commit'])
                or type(manifest['release_approved']) is not bool
                or manifest['license'] not in {'MIT', 'UNLICENSED'}
                or not isinstance(manifest['files'], list) or not 1 <= len(manifest['files']) <= MAX_FILES):
            raise SkillInstallError('invalid_skill_manifest')
        payload = {}
        skills = set()
        for entry in manifest['files']:
            relative = _entry(entry)
            if (len(relative.parts) < 3 or relative.parts[0] != 'skills'
                    or not SKILL.fullmatch(relative.parts[1]) or len(relative.parts[1]) > 64
                    or relative.suffix.lower() not in TEXT_SUFFIXES or any(part.startswith('.') for part in relative.parts)):
                raise SkillInstallError('bundle_resource_is_not_inert_skill_text')
            if entry['path'] in payload:
                raise SkillInstallError('duplicate_bundle_file_entry')
            payload[entry['path']] = None
            skills.add(relative.parts[1])
        if any('skills/' + name + '/SKILL.md' not in payload for name in skills):
            raise SkillInstallError('bundled_skill_instructions_missing')
        license_entry = manifest.get('license_file')
        expected = set(payload) | {MANIFEST}
        if license_entry is not None:
            if _entry(license_entry).as_posix() != 'LICENSE' or manifest['license'] != 'MIT':
                raise SkillInstallError('invalid_bundle_license_notice')
            expected.add('LICENSE')
        if manifest['release_approved'] and (manifest['license'] != 'MIT' or license_entry is None):
            raise SkillInstallError('approved_bundle_requires_mit_notice')
        if names != expected:
            raise SkillInstallError('bundle_inventory_mismatch')
        notice = None
        for entry in manifest['files'] + ([license_entry] if license_entry else []):
            data = archive.read(entry['path'])
            if hashlib.sha256(data).hexdigest() != entry['sha256']:
                raise SkillInstallError('bundle_resource_sha256_mismatch')
            if '\x00' in data.decode('utf-8'):
                raise SkillInstallError('bundle_resource_is_not_utf8_text')
            if entry['path'] in payload:
                payload[entry['path']] = data
            else:
                notice = data
        for name, data in payload.items():
            if PurePosixPath(name).suffix.lower() != '.md':
                continue
            for match in re.finditer(r'!?\[[^\]\n]*\]\((?:<([^>\n]+)>|([^\s)]+))', data.decode('utf-8')):
                target = urlsplit(match.group(1) or match.group(2))
                if target.scheme in {'https', 'http', 'mailto'} or not target.path:
                    continue
                resource = unquote(target.path)
                resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), resource))
                prefix = '/'.join(PurePosixPath(name).parts[:2]) + '/'
                if target.scheme or target.netloc or '\\' in resource or not resolved.startswith(prefix) or resolved not in payload:
                    raise SkillInstallError('skill_reference_missing_or_outside_bundle')
        return manifest, digest, payload, notice


def _receipt(manifest, digest):
    result = {'schema': 'intern-agent.skill-install-receipt.v1', 'product': 'intern-agent',
              'version': manifest['version'], 'source_commit': manifest['source_commit'],
              'bundle_sha256': digest, 'license': manifest['license'],
              'files': sorted(manifest['files'], key=lambda item: item['path'])}
    if 'license_file' in manifest:
        result['license_file'] = manifest['license_file']
    return (json.dumps(result, indent=2, sort_keys=True) + '\n').encode()


def _preflight(manifest, digest, payload, destination, notice):
    directory = _directory(destination)
    if directory is not None:
        try:
            info = os.fstat(directory)
            if info.st_uid != os.getuid() or info.st_mode & 0o022:
                raise SkillInstallError('skill_destination_must_be_owner_controlled')
        finally:
            os.close(directory)
    actions = []
    for name, data in sorted(payload.items()):
        relative = PurePosixPath(name).relative_to('skills')
        target = destination.joinpath(*relative.parts)
        existing = _read_file(target, maximum=MAX_FILE)
        if existing is not None and existing != data:
            raise SkillInstallError('existing_skill_resource_differs')
        actions.append({'path': relative.as_posix(), 'sha256': hashlib.sha256(data).hexdigest(),
                        'action': 'preserve_identical' if existing is not None else 'install'})
    receipt_dir = destination / RECEIPTS
    descriptor = _directory(receipt_dir)
    if descriptor is not None:
        try:
            info = os.fstat(descriptor)
            if info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise SkillInstallError('installation_receipts_directory_must_be_private')
        finally:
            os.close(descriptor)
    receipt_path = receipt_dir / (manifest['version'] + '.json')
    existing = _read_file(receipt_path, maximum=MAX_FILE, private=True)
    if existing is not None and existing != _receipt(manifest, digest):
        raise SkillInstallError('installed_bundle_version_conflict')
    if notice is not None:
        existing = _read_file(receipt_dir / (manifest['version'] + '.LICENSE'), maximum=MAX_FILE, private=True)
        if existing is not None and existing != notice:
            raise SkillInstallError('installed_bundle_notice_conflict')
    return actions


def plan(bundle, destination, expected_sha256=None):
    destination = _path(destination, absolute=True)
    if destination == Path(destination.anchor):
        raise SkillInstallError('choose_a_dedicated_skill_destination')
    manifest, digest, payload, notice = inspect(bundle, expected_sha256)
    actions = _preflight(manifest, digest, payload, destination, notice)
    return {'schema': 'intern-agent.skill-install-plan.v1', 'product': 'intern-agent',
            'version': manifest['version'], 'source_commit': manifest['source_commit'],
            'bundle_sha256': digest, 'destination': str(destination),
            'release_approved': manifest['release_approved'], 'license': manifest['license'],
            'apply_allowed': manifest['release_approved'] and manifest['license'] == 'MIT',
            'files': actions, 'writes_files': False, 'executes_skills': False, 'uses_network': False}


def _write_new(path, data, *, receipt=False):
    existing = _read_file(path, maximum=MAX_FILE, private=receipt)
    if existing is not None:
        if existing != data:
            raise SkillInstallError('skill_destination_changed_during_install')
        return
    parent = _directory(path.parent, create=True)
    try:
        if receipt:
            info = os.fstat(parent)
            if info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise SkillInstallError('installation_receipts_directory_must_be_private')
        temporary = '.intern-agent-write-' + uuid.uuid4().hex
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                # Hard-link publication is atomic and cannot replace a user's file.
                os.link(temporary, path.name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
                os.fsync(parent)
            except FileExistsError:
                if _read_file(path, maximum=MAX_FILE, private=receipt) != data:
                    raise SkillInstallError('skill_destination_changed_during_install') from None
        finally:
            os.unlink(temporary, dir_fd=parent)
    finally:
        os.close(parent)


def install(bundle, destination, expected_sha256):
    if expected_sha256 is None:
        raise SkillInstallError('apply_requires_external_bundle_sha256')
    destination = _path(destination, absolute=True)
    prepared = plan(bundle, destination, expected_sha256)
    if not prepared['apply_allowed']:
        raise SkillInstallError('preparation_bundle_is_plan_only')
    manifest, digest, payload, notice = inspect(bundle, expected_sha256)
    # Recheck the entire batch before the first directory or file is created.
    actions = _preflight(manifest, digest, payload, destination, notice)
    for name, data in sorted(payload.items()):
        relative = PurePosixPath(name).relative_to('skills')
        _write_new(destination.joinpath(*relative.parts), data)
    receipt = destination / RECEIPTS / (manifest['version'] + '.json')
    if notice is not None:
        _write_new(receipt.with_suffix('.LICENSE'), notice, receipt=True)
    _write_new(receipt, _receipt(manifest, digest), receipt=True)
    return {'schema': 'intern-agent.skill-install-result.v1', 'installed': True,
            'version': manifest['version'], 'bundle_sha256': digest,
            'files_installed': sum(item['action'] == 'install' for item in actions),
            'files_preserved': sum(item['action'] == 'preserve_identical' for item in actions),
            'receipt': str(receipt), 'executes_skills': False, 'uses_network': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', help='Downloaded release skills ZIP')
    parser.add_argument('--destination', required=True, help='Explicit absolute skills directory')
    parser.add_argument('--sha256', help='Expected SHA-256 from the selected release checksums')
    parser.add_argument('--apply', action='store_true', help='Install an approved MIT bundle after full preflight')
    args = parser.parse_args(argv)
    try:
        result = install(args.bundle, args.destination, args.sha256) if args.apply else plan(args.bundle, args.destination, args.sha256)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, KeyError, zipfile.BadZipFile, NotImplementedError) as error:
        label = str(error) if isinstance(error, SkillInstallError) else 'skill_bundle_or_destination_rejected'
        print(json.dumps({'ok': False, 'error': label, 'executes_skills': False, 'uses_network': False}, sort_keys=True))
        return 2


if __name__ == '__main__':
    sys.exit(main())
