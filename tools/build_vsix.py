"""Build deterministic preview or separately identified product VSIX files locally."""
from pathlib import Path
import argparse
import copy
import hashlib
import json
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from intern_personal.core import Invalid
from intern_personal.distribution import KEY_PATTERN, credential_literals, linked_input, package_files


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _product_frontend(project, product, prepare):
    if product is None:
        file = project / 'release-product.json'
        if file.is_symlink():
            raise Invalid('Linked product configuration')
        if not file.is_file():
            raise Invalid('Stable packaging requires licensed product configuration and final release handoff')
        product = json.loads(file.read_text())
    if product.get('schema') != 'intern-agent.release-product.v1':
        raise Invalid('Unknown product configuration')
    extension = product['extension']
    version = product['version']
    namespace = extension['namespace']
    if (not re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)', version)
            or version == '0.0.0'
            or not re.fullmatch(r'[A-Za-z][A-Za-z0-9]*', namespace)
            or namespace != 'internAgentPublic'
            or any(not re.fullmatch(r'[a-z0-9][a-z0-9-]{1,49}', extension[k])
                   for k in ('publisher', 'name'))
            or product['repository'] != 'JZSAWYER/intern-agent'
            or (extension['publisher'], extension['name'], extension['displayName']) != (
                'intern-agent', 'intern-agent', 'Intern Agent')
            or extension.get('license') != 'MIT'):
        raise Invalid('Invalid stable product identity')
    if not prepare:
        handoff = product.get('final_handoff', {})
        gates = product.get('gates', {})
        if (handoff.get('approved') is not True
                or handoff.get('repository') != product['repository']
                or handoff.get('version') != version
                or any(gates.get(k) is not True for k in (
                    'ownership_approved', 'license_approved',
                    'dependency_security_approved', 'clean_install_approved'))):
            raise Invalid('Final release handoff pending')
        from tools.check_source_candidate import review
        approved = review(project, release=True)
        inventory = json.loads(approved['source-distribution.json'])
        if inventory['source_commit'] != handoff.get('source_commit'):
            raise Invalid('Final release source pin differs')
    original_manifest = (project / 'extension/package.json').read_bytes()
    original_script = (project / 'extension/extension.js').read_bytes()
    manifest = json.loads(original_manifest)
    commands = manifest['contributes']['commands']
    prefixes = {item['command'].split('.', 1)[0] for item in commands}
    if prefixes not in ({'internAgent'}, {namespace}):
        raise Invalid('Mixed or unknown frontend namespaces')
    original_namespace = next(iter(prefixes))
    if not prepare and (manifest.get('name'), manifest.get('publisher'),
                        manifest.get('displayName'), manifest.get('version'),
                        manifest.get('license'), original_namespace) != (
                            extension['name'], extension['publisher'], extension['displayName'],
                            version, 'MIT', namespace):
        raise Invalid('Approved source frontend identity must match product')
    if original_namespace == namespace and re.search(
            r"\binternAgent(?=[.'\"]|Conversation|Initialization)", original_script.decode()):
        raise Invalid('Mixed source frontend namespaces')
    manifest = json.loads(json.dumps(manifest).replace(original_namespace, namespace))
    script = original_script.decode().replace(original_namespace, namespace)
    script = script.replace("createOutputChannel('Intern Agent')",
                            "createOutputChannel('Intern Agent (Public)')")
    manifest.update(name=extension['name'], publisher=extension['publisher'],
                    displayName=extension['displayName'], version=version,
                    license='UNLICENSED' if prepare else extension['license'])
    repository = 'https://github.com/' + product['repository']
    manifest.update(repository={'type': 'git', 'url': repository + '.git'},
                    homepage=repository, bugs={'url': repository + '/issues'})
    configuration = manifest['contributes'].get('configuration', {})
    if isinstance(configuration, dict):
        configuration['title'] = 'Intern Agent (Public)'
    for views in manifest['contributes'].get('views', {}).values():
        for view in views:
            view['name'] = 'Intern Agent (Public)'
    manifest_bytes = (json.dumps(manifest, indent=2) + '\n').encode()
    generated = {'extension/package.json': manifest_bytes,
                 'extension/extension.js': script.encode()}
    overlays = [{'path': name, 'source_sha256': _digest(original),
                 'packaged_sha256': _digest(generated[name]),
                 'reason': 'Separate product identity, namespace and release version'}
                for name, original in [('extension/package.json', original_manifest),
                                       ('extension/extension.js', original_script)]]
    return generated, manifest, overlays


def _write_member(archive, name, data):
    item = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
    item.compress_type = zipfile.ZIP_DEFLATED
    item.create_system = 3
    item.external_attr = 0o100644 << 16
    archive.writestr(item, data)


def build(project, output, private_owner=None, *, stable=False, product=None, prepare=False, release=False):
    project, output = Path(project).resolve(), Path(output).absolute()
    stable = stable or release
    if prepare and not stable:
        raise Invalid('Preparation mode requires stable product selection')
    baseline = project / 'release-runtime.json'
    if baseline.exists():
        from tools.check_release_runtime import verify
        verify(project)
    inputs = {'extension/backend/' + relative: project / relative
              for relative in package_files(project, private_owner)}
    for name in ['package.json', 'extension.js', 'README.md']:
        inputs['extension/' + name] = project / 'extension' / name
    if (project / 'LICENSE').is_file():
        inputs['extension/LICENSE'] = project / 'LICENSE'
    payload = {}
    for name, file in inputs.items():
        if linked_input(project, file) or not file.resolve().is_relative_to(project):
            raise Invalid('Linked/outside extension input')
        text = file.read_text()
        if (KEY_PATTERN.search(text) or credential_literals(file, text)
                or (private_owner and private_owner.casefold() in text.casefold())):
            raise Invalid('Private material in extension input')
        payload[name] = file.read_bytes()
    manifest = json.loads(payload['extension/package.json'])
    if '__metadata' in manifest:
        raise Invalid('Installed editor metadata is not a source input')
    overlays = []
    if stable:
        generated, manifest, overlays = _product_frontend(project, product, prepare)
        payload.update(generated)
        if baseline.exists():
            recorded = copy.deepcopy(json.loads(baseline.read_bytes()))
            for entry in recorded['frontend']['files']:
                entry['sha256'] = _digest(payload[entry['path']])
                if entry['path'] == 'extension/package.json':
                    from tools.check_release_runtime import canonical_manifest_sha256
                    entry['canonical_sha256'] = canonical_manifest_sha256(payload[entry['path']])
            recorded['frontend']['evidence'] = 'generated_product_source; live_editor_not_verified'
            name = 'extension/backend/release-runtime.json'
            payload[name] = (json.dumps(recorded, indent=2) + '\n').encode()
            overlays.append({'path': 'release-runtime.json',
                             'source_sha256': _digest(baseline.read_bytes()),
                             'packaged_sha256': _digest(payload[name]),
                             'reason': 'Pin generated product frontend bytes'})
        if not prepare and 'extension/backend/LICENSE' not in payload:
            raise Invalid('Approved LICENSE missing from VSIX allowlist')
        if 'extension/backend/LICENSE' in payload:
            payload['extension/LICENSE'] = payload['extension/backend/LICENSE']
    namespace = 'http://schemas.microsoft.com/developer/vsx-schema/2011'
    ET.register_namespace('', namespace)
    q = lambda name: '{' + namespace + '}' + name
    document = ET.Element(q('PackageManifest'), Version='2.0.0')
    metadata = ET.SubElement(document, q('Metadata'))
    ET.SubElement(metadata, q('Identity'), Language='en-US', Id=manifest['name'],
                  Version=manifest['version'], Publisher=manifest['publisher'])
    ET.SubElement(metadata, q('DisplayName')).text = manifest['displayName']
    ET.SubElement(metadata, q('Description')).text = manifest['description']
    ET.SubElement(metadata, q('Categories')).text = ','.join(manifest['categories'])
    properties = ET.SubElement(metadata, q('Properties'))
    for name, value in [('Engine', manifest['engines']['vscode']), ('ExtensionKind', 'workspace'),
                        ('ExecutesCode', 'true'), ('PreRelease', 'false' if stable else 'true')]:
        ET.SubElement(properties, q('Property'), Id='Microsoft.VisualStudio.Code.' + name, Value=value)
    installation = ET.SubElement(document, q('Installation'))
    ET.SubElement(installation, q('InstallationTarget'), Id='Microsoft.VisualStudio.Code')
    ET.SubElement(document, q('Dependencies'))
    assets = ET.SubElement(document, q('Assets'))
    for kind, name in [('Microsoft.VisualStudio.Code.Manifest', 'extension/package.json'),
                       ('Microsoft.VisualStudio.Services.Content.Details', 'extension/README.md')]:
        ET.SubElement(assets, q('Asset'), Type=kind, Path=name, Addressable='true')
    if 'extension/LICENSE' in payload:
        ET.SubElement(assets, q('Asset'), Type='Microsoft.VisualStudio.Services.Content.License',
                      Path='extension/LICENSE', Addressable='true')
    types_namespace = 'http://schemas.openxmlformats.org/package/2006/content-types'
    types = ET.Element('{' + types_namespace + '}Types')
    content_types = {'.json': 'application/json', '.js': 'application/javascript',
                     '.py': 'text/plain', '.md': 'text/markdown', '.xml': 'text/xml',
                     '.vsixmanifest': 'text/xml', '.lock': 'text/plain'}
    payload['extension.vsixmanifest'] = ET.tostring(document, encoding='utf-8', xml_declaration=True)
    for name in sorted(payload):
        ET.SubElement(types, '{' + types_namespace + '}Override', PartName='/' + name,
                      ContentType=content_types.get(Path(name).suffix, 'text/plain'))
    payload['[Content_Types].xml'] = ET.tostring(types, encoding='utf-8', xml_declaration=True)
    if any(p.is_symlink() for p in [output, *output.parents]):
        raise Invalid('Linked extension output path')
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() or output.is_symlink():
        raise Invalid('Artifact already exists; choose another output path')
    for name, data in payload.items():
        text = data.decode()
        if KEY_PATTERN.search(text) or (private_owner and private_owner.casefold() in text.casefold()):
            raise Invalid('Private material in final extension archive')
    with zipfile.ZipFile(output, 'x', zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(payload.items()):
            _write_member(archive, name, data)
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None or set(archive.namelist()) != set(payload):
            raise Invalid('Extension archive failed validation')
        if any(archive.read(name) != data for name, data in payload.items()):
            raise Invalid('Extension archive content differs')
    if baseline.exists():
        if stable:
            with tempfile.TemporaryDirectory(prefix='ia-vsix-', dir='/tmp') as temporary:
                unpacked = Path(temporary)
                for name, data in payload.items():
                    file = unpacked / name
                    file.parent.mkdir(parents=True, exist_ok=True)
                    file.write_bytes(data)
                verify(unpacked / 'extension/backend', vsix=output)
        else:
            verify(project, vsix=output)
    frontend = {name: _digest(payload[name]) for name in (
        'extension/package.json', 'extension/extension.js')}
    return {'artifact': str(output), 'files': len(payload), 'sha256': _digest(output.read_bytes()),
            'published': False, 'stable': stable, 'preparation_only': prepare,
            'release_approved': bool(stable and not prepare),
            'identity': manifest['publisher'] + '.' + manifest['name'],
            'version': manifest['version'], 'license': manifest.get('license'),
            'frontend_sha256': frontend, 'overlay_receipt': overlays,
            'runtime_sha256': json.loads(baseline.read_bytes())['snapshot_sha256'] if baseline.exists() else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--stable', '--release', dest='stable', action='store_true',
                        help='Require the separately licensed stable product and approved final handoff')
    parser.add_argument('--prepare', action='store_true', help='Local unlicensed product-identity preparation only')
    args = parser.parse_args()
    try:
        print(json.dumps(build(ROOT, args.output, stable=args.stable, prepare=args.prepare), indent=2))
    except (Invalid, ValueError, OSError, KeyError, TypeError):
        print(json.dumps({'error': 'extension_inputs_or_release_gate_rejected', 'published': False}))
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
