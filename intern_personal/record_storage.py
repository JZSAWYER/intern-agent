"""Verified private backups before collecting untracked recovery fragments.

Call under the publisher's existing worktree lock. This module never rewrites
Git history, commits, pushes, expires a backup or touches conversation Markdown.
"""
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import tarfile
import tempfile
from contextlib import ExitStack

from .core import Invalid
from .live_state import write_private

BACKUP_SCHEMA = 'intern-agent.recovery-backup.v1'
PART = re.compile(r'[0-9a-f]{64}\.json')
BATCH_BYTES = 64 * 1024 * 1024
BATCH_FILES = 512


def read(path, maximum):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > maximum:
            raise Invalid('Recovery storage file needs inspection')
        data = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
        if len(data) != info.st_size or (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns) != (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns):
            raise Invalid('Recovery storage changed during inspection')
    return data, info


def references(root):
    from .archive_parts import expand, SCHEMA
    metadata = Path(root) / '.intern_workspace'
    if metadata.is_symlink() or (metadata / 'recovery-parts').is_symlink():
        raise Invalid('Linked recovery directory cannot be collected')
    raw, _ = read(metadata / 'recovery.json', 2 * 1024 * 1024)
    data = json.loads(raw)
    if not isinstance(data,dict) or data.get('schema') not in {SCHEMA,'intern-agent.recovery.v1'}:
        raise Invalid('Unknown recovery manifest; preserve fragments for review')
    # Verify every currently required part, its full archive digest and JSON.
    expand(data, lambda name: read(Path(root) / name, 128 * 1024 * 6 + 4)[0])
    keep = {p['path'] for p in data['parts']} if isinstance(data, dict) and data.get('schema') == SCHEMA else set()
    return raw, keep


def private_directory(path):
    path = Path(path)
    path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise Invalid('Recovery backup directory must be private and owned by you')
    return path


def plan(tracker, binding, *, include_tracked=False, limit=BATCH_BYTES):
    from .archive_parts import CHUNK
    root = Path(binding['metadata_path']).resolve()
    raw, keep = references(root)
    directory = root / '.intern_workspace/recovery-parts'
    index = tracker.git(root, 'ls-files', '--stage', '-z', '--', '.intern_workspace/recovery-parts')
    staged = {line.split('\t', 1)[1] for line in index.split('\0') if '\t' in line}
    entries = []; total = 0; skipped = 0
    with ExitStack() as stack:
        from .git_objects import Objects
        objects = stack.enter_context(Objects(root)) if include_tracked else None
        for path in sorted(directory.iterdir()) if directory.exists() else []:
            name = str(path.relative_to(root))
            if name in keep or not PART.fullmatch(path.name) or (not include_tracked and name in staged):
                continue
            try:
                data, info = read(path, CHUNK * 6 + 4)
                chunk = json.loads(data)
                if not isinstance(chunk, str) or hashlib.sha256(chunk.encode()).hexdigest() != path.stem:
                    raise Invalid('Recovery fragment digest changed')
                # A user's whitespace/encoding edit also remains for review.
                canonical = [(json.dumps(chunk, ensure_ascii=ascii) + '\n').encode() for ascii in (True, False)]
                if data not in canonical:raise Invalid('Recovery fragment is not generated canonical data')
                if name in staged and (objects.blob('HEAD:'+name) != data or objects.blob(':'+name) != data):
                    raise Invalid('Staged or modified recovery fragment needs review')
                if len(entries) >= BATCH_FILES or total + len(data) > limit:break
                entries.append(dict(path=name, bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                                    device=info.st_dev, inode=info.st_ino, mtime_ns=info.st_mtime_ns,
                                    tracked=name in staged))
                total += len(data)
            except (OSError, ValueError, UnicodeError):
                skipped += 1
    return dict(root=str(root), manifest=raw, manifest_sha256=hashlib.sha256(raw).hexdigest(),
                keep=keep, index=index, entries=entries, bytes=total, skipped=skipped)


def verify_backup(path, manifest):
    """Read back every archived byte; never extract a tar path into a checkout."""
    expected = {e['path']: e for e in manifest['files']}
    seen = set()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as raw:
        info = os.fstat(raw.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise Invalid('Recovery backup must be a private regular file')
        with tarfile.open(fileobj=raw, mode='r:gz') as archive:
            for member in archive:
                if not member.isfile() or member.name in seen:
                    raise Invalid('Invalid recovery backup entry')
                seen.add(member.name)
                if member.name == 'backup.json':
                    if member.size > 1024 * 1024 or json.load(archive.extractfile(member)) != manifest:
                        raise Invalid('Recovery backup inventory differs')
                elif member.name == 'current-recovery.json':
                    if member.size > 2 * 1024 * 1024 or hashlib.sha256(archive.extractfile(member).read()).hexdigest() != manifest['manifest_sha256']:
                        raise Invalid('Recovery backup manifest differs')
                else:
                    entry = expected.get(member.name)
                    if not entry or member.size != entry['bytes']:
                        raise Invalid('Unexpected recovery backup entry')
                    if hashlib.sha256(archive.extractfile(member).read()).hexdigest() != entry['sha256']:
                        raise Invalid('Recovery backup failed byte verification')
            if seen != set(expected) | {'backup.json', 'current-recovery.json'}:
                raise Invalid('Recovery backup is incomplete')
            archive.fileobj.read()  # Also check the gzip trailer/CRC.
    return True


def backup(tracker, snapshot):
    root = Path(snapshot['root'])
    state_runtime = Path(tracker.state.runtime)
    if state_runtime.is_symlink() or state_runtime.stat().st_mode & 0o077:
        raise Invalid('Recovery backup runtime must be private')
    parent = private_directory(state_runtime / 'recovery-backups')
    key = hashlib.sha256(str(root).encode()).hexdigest()[:20]
    directory = private_directory(parent / key)
    # No live credentials or transcripts: only generated fragments being removed.
    manifest = dict(schema=BACKUP_SCHEMA, checkout=str(root), manifest_sha256=snapshot['manifest_sha256'],
                    files=[{k:e[k] for k in ('path', 'bytes', 'sha256')} for e in snapshot['entries']])
    inventory = (json.dumps(manifest, sort_keys=True, separators=(',', ':')) + '\n').encode()
    path = directory / (hashlib.sha256(inventory).hexdigest() + '.tar.gz')
    if path.exists() or path.is_symlink():
        verify_backup(path, manifest)
        return path, manifest
    fd, temporary = tempfile.mkstemp(prefix='.backup-', dir=directory)
    try:
        with os.fdopen(fd, 'wb') as stream:
            with gzip.GzipFile(fileobj=stream, mode='wb', compresslevel=6, filename='', mtime=0) as compressed:
                with tarfile.open(fileobj=compressed, mode='w') as archive:
                    def add(name, data):
                        member = tarfile.TarInfo(name);member.size = len(data);member.mode = 0o600
                        archive.addfile(member, io.BytesIO(data))
                    add('backup.json', inventory)
                    add('current-recovery.json', snapshot['manifest'])
                    for entry in snapshot['entries']:
                        data, _ = read(root / entry['path'], 128 * 1024 * 6 + 4)
                        if hashlib.sha256(data).hexdigest() != entry['sha256']:
                            raise Invalid('Recovery fragment changed before backup')
                        add(entry['path'], data)
            stream.flush();os.fsync(stream.fileno())
        verify_backup(temporary, manifest)
        os.replace(temporary, path)
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)
        return path, manifest
    finally:
        Path(temporary).unlink(missing_ok=True)


def collect(tracker, binding, *, include_tracked=False, strict=False, limit=BATCH_BYTES):
    """One bounded batch. Tracked removal is reserved for explicit maintenance."""
    result = dict(removed=0, removed_bytes=0, skipped=0, backup=None, error_type=None)
    try:
        snapshot = plan(tracker, binding, include_tracked=include_tracked, limit=limit)
        result['skipped'] = snapshot['skipped']
        if not snapshot['entries']:return result
        path, _ = backup(tracker, snapshot)
        result['backup'] = str(path)
        root = Path(snapshot['root'])
        raw, keep = references(root)
        index = tracker.git(root, 'ls-files', '--stage', '-z', '--', '.intern_workspace/recovery-parts')
        if raw != snapshot['manifest'] or index != snapshot['index']:
            raise Invalid('Recovery manifest or staging changed; no cleanup attempted')
        # A publisher holds its existing worktree lock. Recheck bytes and inode
        # as well, so a late manual edit is retained rather than silently lost.
        for entry in snapshot['entries']:
            if entry['path'] in keep:raise Invalid('A required recovery part cannot be collected')
            data, info = read(root / entry['path'], 128 * 1024 * 6 + 4)
            if (info.st_dev,info.st_ino,info.st_mtime_ns) != (entry['device'],entry['inode'],entry['mtime_ns']) or hashlib.sha256(data).hexdigest() != entry['sha256']:
                raise Invalid('Recovery fragment changed after backup; preserved for review')
        metadata_fd = os.open(root / '.intern_workspace', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            parts_fd = os.open('recovery-parts', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=metadata_fd)
            try:
                for entry in snapshot['entries']:
                    name = Path(entry['path']).name
                    info = os.stat(name, dir_fd=parts_fd, follow_symlinks=False)
                    if not stat.S_ISREG(info.st_mode) or (info.st_dev,info.st_ino,info.st_mtime_ns,info.st_size) != (entry['device'],entry['inode'],entry['mtime_ns'],entry['bytes']):
                        raise Invalid('Recovery fragment changed before removal')
                    os.unlink(name, dir_fd=parts_fd)
                    result['removed'] += 1;result['removed_bytes'] += entry['bytes']
                os.fsync(parts_fd)
            finally:os.close(parts_fd)
        finally:os.close(metadata_fd)
        return result
    except (OSError, ValueError, tarfile.TarError, EOFError) as error:
        result['error_type'] = type(error).__name__
        if strict:raise
        return result
    finally:
        # Optional maintenance must not turn an otherwise valid reply into a
        # Git failure or a repeated Feishu notice. Expose a private diagnostic.
        if result['removed'] or result['error_type']:
            try:
                root = str(Path(binding['metadata_path']).resolve())
                key = hashlib.sha256(root.encode()).hexdigest()[:20]
                write_private(Path(tracker.state.runtime) / ('recovery-storage-' + key + '.json'), result)
            except (OSError, ValueError, AttributeError):pass
