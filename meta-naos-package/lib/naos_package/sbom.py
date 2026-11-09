# SPDX-License-Identifier: MIT
"""Freeze source/runtime evidence for the later offline image generator."""
import copy
import json
from pathlib import Path, PurePosixPath
import shutil

from naos.sbom_metadata import digest, license_info


_FINAL_ARTIFACTS = frozenset(('uboot.img', 'idblock.bin', 'MiniLoaderAll.bin',
    'idblock-slot.img', 'uboot-slot.img', 'boot.img', 'system.img', 'system.squashfs'))


def _input(root, name):
    path = root / name
    if Path(name).name != name or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Missing or unsafe package SBOM input: ' + name)
    return path


def _component(inputs, name):
    result = json.loads(_input(inputs, name + '.json').read_text())
    if result.get('schema_version') != 1 or result.get('recipe') != name:
        raise ValueError('Package component evidence has a mismatched recipe: ' + name)
    return result


def _bootloader_path(value):
    path = PurePosixPath(value)
    if not value or path.is_absolute() or '..' in path.parts or '\\' in value or '\0' in value:
        raise ValueError('Unsafe early bootloader evidence path')
    value = str(path)
    if value.startswith('naos-package-inputs/bootloader/'):
        return value.removeprefix('naos-package-inputs/')
    if value.startswith('bootloader/'):
        return value
    return 'bootloader/' + value


def _relocate(component):
    """Separate exported file references from unexported build provenance."""
    archive = component.pop('module_archive', None)
    if archive is not None:
        component.setdefault('build_evidence', {})['module_archive'] = {
            **archive, 'scope':'source-build-only', 'exported':False,
            'note':'Modules are delivered within rootfs.tar.gz; this staging archive is not exported.'}
    if component['recipe'] != 'u-boot-naos-rockchip':
        return
    roles = {}
    for artifact in component.get('artifacts', []):
        artifact['path'] = _bootloader_path(artifact['path'])
        if artifact.get('role'):
            roles[artifact['role']] = artifact['path']
    spl = component.get('spl')
    if spl:
        spl['path'] = _bootloader_path(spl['path'])
        if spl.get('control_dtb'):
            spl['control_dtb']['path'] = _bootloader_path(spl['control_dtb']['path'])
    for selection in component.get('firmware_selection', {}).values():
        selection['path'] = _bootloader_path(selection['path'])
    for firmware in component.get('firmware_inputs', []):
        # Keep path/source_revision as repository provenance. The extra field
        # names the byte-identical file carried by this generator-input export.
        if firmware['role'] in roles:
            firmware['exported_path'] = roles[firmware['role']]


def produce(d, output, root_inventory, template_inventory, template):
    """Use deployed sidecars, never factory outputs or transient WORKDIR."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    inputs = Path(d.getVar('DEPLOY_DIR_IMAGE')) / 'sbom-inputs'
    names = d.getVar('NAOS_PACKAGE_COMPONENT_RECIPES').split()
    if len(names) != len(set(names)) or not names:
        raise ValueError('Expected a unique package component list')
    components = [_component(inputs, name) for name in names]
    selected = d.getVar('NAOS_PACKAGE_INITRAMFS_RECIPE')
    files = {record['path']:record for record in root_inventory['files']}
    for component in components:
        claims = [record['path'] for record in component.get('artifacts', [])]
        claims.extend(path for record in component.get('firmware_inputs', []) for path in record.get('in_artifacts', []))
        claims.extend(component.get('spl', {}).get('in_artifacts', []))
        if any(Path(path).name in _FINAL_ARTIFACTS for path in claims):
            raise ValueError('Package SBOM contains finalized boot/system artifact evidence')
        for path, checksum in component.get('payload_hashes', {}).items():
            if path not in files or files[path].get('sha256') != checksum:
                raise ValueError('Package runtime sidecar differs from actual rootfs: ' + path)
        for record in component.get('modules', []):
            actual = files.get(record['path'])
            if not actual or actual.get('sha256') != record['sha256'] or actual.get('size') != record['size']:
                raise ValueError('Package kernel sidecar differs from actual rootfs modules')
        configuration = component.get('configuration')
        if configuration:
            source = _input(inputs, configuration['file'])
            if digest(source) != configuration['sha256'] or source.stat().st_size != configuration['size']:
                raise ValueError('Package BSP configuration differs from deployed evidence')
            if source.name not in ('kernel.config', 'uboot.config'):
                raise ValueError('Unexpected package BSP configuration filename')
            shutil.copyfile(source, output / source.name)
            configuration['source_file'] = configuration['file']
            configuration['file'] = 'sbom/' + source.name
        if component['recipe'] == selected:
            matching = [artifact for artifact in component.get('artifacts', [])
                        if artifact['sha256'] == template['sha256'] and artifact['size'] == template['size']]
            if len(matching) != 1:
                raise ValueError('Initramfs component differs from the selected package template')
            if matching[0]['path'] != 'initramfs.cpio.gz':
                matching[0]['source_path'] = matching[0]['path']
            matching[0]['path'] = 'initramfs.cpio.gz'
            component['image_inventory'] = copy.deepcopy(template_inventory)
            component['template'] = copy.deepcopy(template)
            for package in component['image_packages']:
                source = _component(inputs, package['source_recipe'])
                package.update(sources=source['sources'], cve_product=source.get('cve_product', ''),
                               homepage=source.get('homepage', ''), source_version=source.get('version'),
                               source_component=source)
        _relocate(component)
    inventory = copy.deepcopy(root_inventory)
    for package in inventory.get('packages', []):
        package.update(license_info(package.get('license', ''), d))
    catalog = license_info('MIT', d)
    result = {'schema_version':1, 'license_list_version':catalog['license_list_version'],
              'machine':d.getVar('MACHINE'), 'profile':d.getVar('NAOS_BUILD_MODE'),
              'path_bases':{'artifacts.path':'export-root', 'configuration.file':'export-root',
                            'spl.path':'export-root', 'spl.control_dtb.path':'export-root',
                            'firmware_selection.path':'export-root', 'firmware_inputs.exported_path':'export-root',
                            'firmware_inputs.path':'source-repository', 'payload_paths':'rootfs',
                            'payload_hashes':'rootfs', 'modules.path':'rootfs',
                            'build_evidence':'source-build-only'},
              'components':components}
    (output / 'components.json').write_text(json.dumps(result, sort_keys=True, indent=2) + '\n')
    collected = {'schema_version':1, 'rootfs':inventory, 'initramfs':copy.deepcopy(template_inventory)}
    (output / 'inventory.json').write_text(json.dumps(collected, sort_keys=True, indent=2) + '\n')
    return result
