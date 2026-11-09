# SPDX-License-Identifier: MIT
"""Bind the final firmware inventory to deployed component build evidence."""
import json
from pathlib import Path, PurePosixPath
import re
import shutil

from .sbom_metadata import digest, license_info


def _label(value):
    if not value or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+-]*', value):
        raise ValueError('Invalid SBOM artifact label')
    return value


def _json(path):
    return json.loads(Path(path).read_text())


def _deployed_file(root, name):
    if not isinstance(name, str) or not name or '\0' in name:
        raise ValueError('Invalid deployed SBOM input path')
    relative = PurePosixPath(name)
    if relative.is_absolute() or '..' in relative.parts or str(relative) == '.':
        raise ValueError('Deployed SBOM input escapes its directory')
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Deployed SBOM input symlink escapes its directory')
    if not path.is_file():
        raise ValueError('Missing deployed SBOM input: ' + name)
    return path


def _check_artifact(root, record):
    path = _deployed_file(root, record['path'])
    if path.stat().st_size != record['size'] or digest(path) != record['sha256']:
        raise ValueError('Component artifact differs from its deployed build evidence: ' + record['path'])


def _check_manifest(manifest, d):
    expected = {
        'machine': d.getVar('MACHINE'), 'version': d.getVar('NAOS_VERSION'),
        'build_id': d.getVar('NAOS_BUILD_ID'),
        'secure_boot': d.getVar('NAOS_SECURE_BOOT') == '1',
        'system_encryption': d.getVar('NAOS_SYSTEM_ENCRYPTION') == '1',
        'data_encryption': d.getVar('NAOS_DATA_ENCRYPTION') == '1',
    }
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise ValueError('Firmware manifest differs from the selected build/profile')
    files = manifest.get('files', {})
    for name, record in files.items():
        _label(name)
        if (not re.fullmatch(r'[0-9a-f]{64}', record.get('sha256', ''))
                or type(record.get('size')) is not int or record['size'] < 0):
            raise ValueError('Invalid firmware artifact digest or size: ' + name)


def _check_inventory(path, manifest):
    record = manifest['files'].get(path.name)
    if not record or digest(path) != record['sha256'] or path.stat().st_size != record['size']:
        raise ValueError('Rootfs inventory is not from the selected firmware assembly')


def _copy_configuration(component, inputs, output, prefix):
    configuration = component.get('configuration')
    if not configuration:
        return
    source = _deployed_file(inputs, _label(configuration['file']))
    if digest(source) != configuration['sha256'] or source.stat().st_size != configuration['size']:
        raise ValueError('BSP configuration differs from its recorded build evidence')
    target = output / (prefix + '.' + source.name)
    if target.exists() and digest(target) != configuration['sha256']:
        raise ValueError('BSP configurations have conflicting delivered filenames')
    shutil.copyfile(source, target)
    # Render the delivered evidence filename, rather than an intermediate path.
    configuration['file'] = target.name


def generate(d):
    from .sbom_render import render_product

    deploy = Path(d.getVar('DEPLOY_DIR_IMAGE'))
    inputs = deploy / 'sbom-inputs'
    output = Path(d.getVar('NAOS_SBOM_OUTPUT'))
    output.mkdir(parents=True, exist_ok=True)
    manifest = _json(_deployed_file(deploy, 'manifest.json'))
    _check_manifest(manifest, d)
    inventory_path = _deployed_file(deploy, 'naos-rootfs.inventory.json')
    _check_inventory(inventory_path, manifest)
    inventory = _json(inventory_path)
    if d.getVar('NAOS_OTA') == '1':
        # The product document also identifies optional distribution artifacts.
        for bundle in ('naos-bundle', 'naos-bootloader-bundle'):
            filename = bundle + '-' + _label(d.getVar('MACHINE')) + '-' + _label(d.getVar('NAOS_VERSION')) + '.raucb'
            archive = _deployed_file(deploy, filename)
            manifest['files'][filename] = {'sha256': digest(archive), 'size': archive.stat().st_size}
    for package in inventory['packages']:
        package.update(license_info(package.get('license', ''), d))

    names = (d.getVar('NAOS_SBOM_COMPONENT_RECIPES') or '').split()
    if not names or len(names) != len(set(names)):
        raise ValueError('Expected a unique, nonempty product component recipe list')
    components = []
    for name in names:
        component = _json(_deployed_file(inputs, _label(name) + '.json'))
        if component.get('schema_version') != 1 or component.get('recipe') != name:
            raise ValueError('Invalid deployed component evidence: ' + name)
        for artifact in component.get('artifacts', []):
            expected = manifest['files'].get(artifact['path'])
            if expected and any(expected[key] != artifact[key] for key in ('sha256', 'size')):
                raise ValueError('Component artifact differs from the firmware: ' + artifact['path'])
            _check_artifact(deploy, artifact)
        if component.get('module_archive'):
            _check_artifact(deploy, component['module_archive'])
        for package in component.get('image_packages', []):
            # The package manifest decides what ships. Target build dependencies
            # absent from that manifest never become product components.
            source_path = _deployed_file(inputs, _label(package['source_recipe']) + '.json')
            source = _json(source_path)
            if source.get('recipe') != package['source_recipe']:
                raise ValueError('Initramfs package has mismatched recipe evidence')
            package['sources'] = source['sources']
            package['cve_product'] = source.get('cve_product', '')
            package['homepage'] = source.get('homepage', '')
        components.append(component)

    # The assembly task already hashes the final firmware, which can be several
    # GiB. Trust its task-bound manifest and check presence/size here; smaller
    # component outputs above are independently hashed to reject stale sidecars.
    for name, record in manifest['files'].items():
        if _deployed_file(deploy, name).stat().st_size != record['size']:
            raise ValueError('Firmware artifact size differs from its manifest: ' + name)

    prefix = 'naos-' + _label(d.getVar('MACHINE')) + '-' + _label(d.getVar('NAOS_BUILD_ID'))
    for component in components:
        _copy_configuration(component, inputs, output, prefix)
    path = output / (prefix + '.spdx.json')
    catalog = license_info('MIT', d)
    summary = render_product(
        inventory, components, manifest, path,
        machine=d.getVar('MACHINE'), version=d.getVar('NAOS_VERSION'),
        build_id=d.getVar('NAOS_BUILD_ID'),
        epoch=int(d.getVar('SOURCE_DATE_EPOCH') or '0'),
        license_list_version=catalog['license_list_version'])
    summary.update({
        'schema_version': 1, 'machine': d.getVar('MACHINE'),
        'version': d.getVar('NAOS_VERSION'), 'build_id': d.getVar('NAOS_BUILD_ID'),
        'spdx_file': path.name, 'spdx_sha256': digest(path),
        'inventory_sha256': manifest['files'][inventory_path.name]['sha256'],
        'native_spdx': 'See deploy/spdx for Yocto build-time provenance',
    })
    (output / (prefix + '.sbom.json')).write_text(json.dumps(summary, sort_keys=True, indent=2) + '\n')
    return summary
