"""Verify selected deployed bytes in source or VSIX without inspecting private state."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]


class ParityError(ValueError):
    pass


def canonical_manifest_sha256(data, installed=False):
    """Hash manifest semantics, stripping only editor-owned installation metadata."""
    def unique_object(pairs):
        value={}
        for key,item in pairs:
            if key in value:raise ParityError('duplicate_frontend_manifest_key')
            value[key]=item
        return value
    def reject_constant(_):raise ParityError('invalid_frontend_manifest_json')
    try:
        value=json.loads(data,object_pairs_hook=unique_object,parse_constant=reject_constant)
        if not isinstance(value,dict):raise ParityError('invalid_frontend_manifest_json')
        if '__metadata' in value and not isinstance(value['__metadata'],dict):
            raise ParityError('invalid_frontend_installation_metadata')
        # Validate ignored metadata too: JSON parsing can otherwise produce
        # non-finite floats or escaped, unpaired Unicode surrogates.
        canonical=json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf-8')
        if installed:
            if '__metadata' not in value:raise ParityError('frontend_installation_metadata_missing')
            del value['__metadata']
            canonical=json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf-8')
    except ParityError:raise
    except (UnicodeError,ValueError,TypeError,RecursionError) as error:
        raise ParityError('invalid_frontend_manifest_json') from error
    return hashlib.sha256(canonical).hexdigest()


def fingerprint(payload, algorithm='sorted-path-v1'):
    if algorithm == 'sorted-path-v1':
        names = sorted(payload)
    elif algorithm == 'deployment-input-order-v2':
        names = sorted(n for n in payload if n.startswith('intern_personal/'))
        names += [n for n in ['skills/feishu-file-transfer/SKILL.md',
                             'docs/BOT_COMMUNICATION.md', 'docs/AUTORESEARCH.md'] if n in payload]
        if set(names) != set(payload):
            raise ParityError('invalid_deployment_fingerprint_inputs')
    elif algorithm == 'deployment-input-order-v3':
        names = sorted(n for n in payload if n.startswith('intern_personal/'))
        names += [n for n in ['skills/feishu-file-transfer/SKILL.md'] if n in payload]
        names += sorted(n for n in payload if PurePosixPath(n).parent.as_posix() == 'docs'
                        and PurePosixPath(n).suffix == '.md')
        if set(names) != set(payload):
            raise ParityError('invalid_deployment_fingerprint_inputs')
    else:
        raise ParityError('unknown_snapshot_algorithm')
    return hashlib.sha256(b''.join(n.encode()+b'\0'+payload[n]+b'\0' for n in names)).hexdigest()


def verify(root, deployed=None, vsix=None):
    root = Path(root).resolve()
    manifest = json.loads((root/'release-runtime.json').read_text())
    if manifest.get('schema') != 'intern-agent.runtime-baseline.v1':
        raise ParityError('unknown_runtime_baseline')
    entries = manifest.get('runtime_files')
    if not isinstance(entries, list) or not entries:
        raise ParityError('empty_runtime_baseline')
    expected = {}
    for entry in entries:
        name = entry['path'];path = PurePosixPath(name)
        if (path.is_absolute() or '..' in path.parts or str(path) != name or '\\' in name
                or not ((path.parent.as_posix() == 'intern_personal' and path.suffix == '.py')
                        or name == 'skills/feishu-file-transfer/SKILL.md'
                        or (path.parent.as_posix() == 'docs' and path.suffix == '.md')) or name in expected):
            raise ParityError('invalid_runtime_path')
        expected[name] = entry['sha256']
    def read_tree(base):
        base=Path(base).resolve();payload={}
        for name, digest in expected.items():
            p=base
            for part in PurePosixPath(name).parts:
                p=p/part
                if p.is_symlink():raise ParityError('linked_runtime_file')
            if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=digest:
                raise ParityError('runtime_bytes_differ')
            payload[name]=p.read_bytes()
        actual={'intern_personal/'+p.name for p in (base/'intern_personal').glob('*.py')}
        if actual!={n for n in expected if n.startswith('intern_personal/')}:
            raise ParityError('runtime_module_set_differs')
        # prepare_runtime v3 includes every top-level Markdown guide. Source
        # and deployed trees must both match this set; legacy baselines named
        # only selected guides and retain that historical interpretation.
        if manifest.get('snapshot_algorithm') == 'deployment-input-order-v3':
            actual_guides={'docs/'+p.name for p in (base/'docs').glob('*.md')}
            if actual_guides!={n for n in expected if n.startswith('docs/')}:
                raise ParityError('runtime_guide_set_differs')
        if fingerprint(payload, manifest.get('snapshot_algorithm', 'sorted-path-v1'))!=manifest['snapshot_sha256']:
            raise ParityError('runtime_digest_differs')
        return payload
    payload=read_tree(root)
    frontend_checked=0
    for entry in manifest['frontend']['files']:
        name=entry['path']
        if name not in {'extension/package.json','extension/extension.js'}:
            raise ParityError('invalid_frontend_path')
        file=root/name;installed_sibling=False
        if not file.is_file() and root.name=='backend':
            file=root.parent/Path(name).name;installed_sibling=True
        if not file.is_file():
            if root.name!='backend':raise ParityError('frontend_source_missing')
            continue
        if file.is_symlink():raise ParityError('frontend_source_drift')
        data=file.read_bytes()
        if hashlib.sha256(data).hexdigest()!=entry['sha256']:
            accepted=False
            if installed_sibling and name=='extension/package.json' and entry.get('canonical_sha256'):
                try:
                    accepted=canonical_manifest_sha256(data,installed=True)==entry['canonical_sha256']
                except ParityError:pass
            if not accepted:raise ParityError('frontend_source_drift')
        elif name=='extension/package.json' and entry.get('canonical_sha256'):
            if canonical_manifest_sha256(data)!=entry['canonical_sha256']:
                raise ParityError('frontend_source_drift')
        frontend_checked+=1
    if deployed is not None:read_tree(deployed)
    if vsix is not None:
        with zipfile.ZipFile(vsix) as z:
            names=z.namelist()
            if len(names)!=len(set(names)) or z.testzip() is not None:
                raise ParityError('invalid_vsix')
            actual={n.removeprefix('extension/backend/') for n in names
                    if n.startswith('extension/backend/intern_personal/') and n.endswith('.py')}
            if actual!={n for n in expected if n.startswith('intern_personal/')}:
                raise ParityError('vsix_module_set_differs')
            if manifest.get('snapshot_algorithm') == 'deployment-input-order-v3':
                actual_guides={n.removeprefix('extension/backend/') for n in names
                               if PurePosixPath(n).parent.as_posix() == 'extension/backend/docs'
                               and PurePosixPath(n).suffix == '.md'}
                if actual_guides!={n for n in expected if n.startswith('docs/')}:
                    raise ParityError('vsix_guide_set_differs')
            for name,data in payload.items():
                if z.read('extension/backend/'+name)!=data:raise ParityError('vsix_runtime_drift')
            for entry in manifest['frontend']['files']:
                if hashlib.sha256(z.read(entry['path'])).hexdigest()!=entry['sha256']:
                    raise ParityError('vsix_frontend_source_drift')
    return {'ok':True,'snapshot_sha256':manifest['snapshot_sha256'],'runtime_files':len(payload),
            'deployed_compared':deployed is not None,'vsix_compared':vsix is not None,
            'frontend_source_checked':frontend_checked==len(manifest['frontend']['files']),
            'frontend_live_parity_established':False,'private_state_read':False}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--deployed',type=Path);p.add_argument('--vsix',type=Path)
    args=p.parse_args(argv)
    try:result=verify(ROOT,args.deployed,args.vsix)
    except (ValueError,OSError,KeyError,TypeError,zipfile.BadZipFile) as e:
        result={'ok':False,'error':str(e) if isinstance(e,ParityError) else 'runtime_baseline_unavailable'}
    print(json.dumps(result,sort_keys=True));return 0 if result['ok'] else 2


if __name__=='__main__':sys.exit(main())
