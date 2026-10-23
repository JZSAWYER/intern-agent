"""Validate explicit source inputs and create a local review ZIP; never publish."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from intern_personal.distribution import credential_literals

MANIFEST = 'source-distribution.json'
PRIVATE_PARTS = {'.git', '.intern_workspace', '.ssh', 'runtime', 'reports',
                 'reference', 'dist', '.release-review', '__pycache__', 'node_modules'}
PRIVATE_NAMES = {'personal.json', 'bindings.json', 'live.json', 'health.json',
                 'credentials.json', 'STATUS.md', 'pairing-code.txt', 'auth.json'}
SOURCE_DIRS = ('intern_personal', 'extension', 'docs', 'tests', 'tools', 'templates', 'skills', 'requirements', '.github')
TOKEN_SHAPE = re.compile(r'(?<![A-Za-z0-9_])(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,})')
PRIVATE_ID = re.compile(r'\b(?:ou_|oc_|cli_)[A-Za-z0-9]{12,}\b')
HOST_PATH = re.compile(r'/home/(?:batchcom|dataset-local)/')


class Rejected(ValueError):
    """Fixed public error labels; never include rejected input values."""


def safe_file(root, name):
    if not isinstance(name, str) or not name or '\\' in name:
        raise Rejected('invalid_source_path')
    relative = PurePosixPath(name)
    if relative.is_absolute() or '..' in relative.parts or str(relative) != name:
        raise Rejected('invalid_source_path')
    if (any(part in PRIVATE_PARTS for part in relative.parts)
            or relative.name in PRIVATE_NAMES
            or relative.name.startswith('.env')
            or any(word in relative.name.lower() for word in ('secret', 'credential'))
            or relative.suffix in {'.pem', '.key', '.sqlite', '.sqlite3', '.db', '.pyc', '.vsix', '.zip'}):
        raise Rejected('private_source_path')
    target = root
    for part in relative.parts:
        target = target / part
        if target.is_symlink():
            raise Rejected('linked_source_input')
    if not target.is_file() or not target.resolve().is_relative_to(root):
        raise Rejected('missing_or_external_source_input')
    return target


def review(root, release=False):
    root = Path(root).resolve()
    manifest_file = safe_file(root, MANIFEST)
    raw = manifest_file.read_bytes()
    manifest = json.loads(raw)
    if manifest.get('schema') != 'intern-agent.source-candidate.v1':
        raise Rejected('invalid_source_schema')
    entries = manifest.get('files')
    if not isinstance(entries, list) or not entries:
        raise Rejected('empty_source_inventory')
    payload = {MANIFEST: raw}
    literal_fixtures = manifest.get('synthetic_literal_fixtures', {})
    if not isinstance(literal_fixtures, dict):
        raise Rejected('invalid_fixture_inventory')
    for entry in entries:
        if not isinstance(entry, dict):
            raise Rejected('invalid_source_entry')
        name = entry.get('path')
        file = safe_file(root, name)
        if name in payload:
            raise Rejected('duplicate_source_entry')
        data = file.read_bytes()
        if hashlib.sha256(data).hexdigest() != entry.get('sha256'):
            raise Rejected('source_hash_mismatch')
        text = data.decode('utf-8')
        if name == 'extension/package.json' and '__metadata' in json.loads(text):
            raise Rejected('installed_editor_metadata_in_source')
        if TOKEN_SHAPE.search(text) or PRIVATE_ID.search(text) or HOST_PATH.search(text):
            raise Rejected('private_content_pattern')
        if credential_literals(file, text) and not literal_fixtures.get(name):
            raise Rejected('credential_literal_requires_review')
        if not entry.get('provenance') or not entry.get('rights'):
            raise Rejected('missing_provenance')
        payload[name] = data
    manifest_text = raw.decode('utf-8')
    if TOKEN_SHAPE.search(manifest_text) or PRIVATE_ID.search(manifest_text) or HOST_PATH.search(manifest_text):
        raise Rejected('private_manifest_pattern')
    for directory in SOURCE_DIRS:
        base = root / directory
        if base.is_symlink():
            raise Rejected('linked_source_input')
        for file in base.rglob('*'):
            if '__pycache__' in file.parts or file.suffix == '.pyc':
                continue
            if file.is_symlink():
                raise Rejected('linked_source_input')
            if file.is_file() and file.relative_to(root).as_posix() not in payload:
                raise Rejected('unlisted_source_file')
    if 'release-runtime.json' in payload:
        from tools.check_release_runtime import verify, ParityError
        try:verify(root)
        except ParityError:raise Rejected('runtime_parity_failed') from None
    if release:
        if manifest.get('ownership_approved') is not True:
            raise Rejected('ownership_review_pending')
        if manifest.get('license_approved') is not True or 'LICENSE' not in payload:
            raise Rejected('license_review_pending')
        if any(entry['rights'] != 'approved_for_distribution' for entry in entries):
            raise Rejected('component_rights_pending')
        if manifest.get('dependency_security_approved') is not True:
            raise Rejected('dependency_security_review_pending')
        package = json.loads(payload['extension/package.json'])
        if package.get('license') in {None, '', 'UNLICENSED'}:
            raise Rejected('package_license_pending')
    return payload


def archive(payload, destination):
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise Rejected('review_archive_already_exists')
    with zipfile.ZipFile(destination, 'x', zipfile.ZIP_DEFLATED) as output:
        for name, data in sorted(payload.items()):
            item = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            item.compress_type = zipfile.ZIP_DEFLATED
            item.external_attr = 0o100644 << 16
            output.writestr(item, data)
    with zipfile.ZipFile(destination) as output:
        if output.testzip() is not None or sorted(output.namelist()) != sorted(payload):
            raise Rejected('review_archive_integrity_failure')
        if any(output.read(name) != data for name, data in payload.items()):
            raise Rejected('review_archive_content_mismatch')
    return hashlib.sha256(destination.read_bytes()).hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release', action='store_true', help='Require reviewed ownership/license gates')
    parser.add_argument('--review-archive', type=Path, help='Create an explicit local source review artifact')
    args = parser.parse_args(argv)
    try:
        payload = review(ROOT, release=args.release)
        result = {'ok': True, 'files': len(payload), 'release_gate_checked': args.release,
                  'published': False, 'history_included': False}
        if args.review_archive:
            result['archive_sha256'] = archive(payload, args.review_archive)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (Rejected, OSError, ValueError, KeyError, TypeError, SyntaxError) as error:
        label = str(error) if isinstance(error, Rejected) else 'invalid_or_inaccessible_source_candidate'
        print(json.dumps({'ok': False, 'error': label, 'published': False}, sort_keys=True))
        return 2


if __name__ == '__main__':
    sys.exit(main())
