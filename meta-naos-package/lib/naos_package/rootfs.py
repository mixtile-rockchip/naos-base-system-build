# SPDX-License-Identifier: MIT
"""Produce complete filesystem inputs before FIT/filesystem finalization."""
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import shutil
import tarfile


FINGERPRINT = 'usr/lib/naos/system-key.sha256'


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n', encoding='utf-8')


def _deployed(root, name):
    relative = PurePosixPath(name)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('Unsafe package input path')
    path = root / relative
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Missing or unsafe deployed package input: ' + name)
    return path


def kernel_release(archive):
    releases = set()
    with tarfile.open(archive, 'r|*') as package:
        for member in package:
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts:
                raise ValueError('Unsafe module archive path')
            parts = path.parts
            if len(parts) >= 3 and parts[:2] == ('lib', 'modules'):
                releases.add(parts[2])
    if len(releases) != 1:
        raise ValueError('The module archive must identify one kernel release')
    return releases.pop()


def produce(d, output):
    """Write rootfs/initramfs inputs without reading final private keys."""
    if d.getVar('NAOS_PACKAGE') != '1':
        raise ValueError('Early filesystem production requires NAOS_PACKAGE=1')
    profile = d.getVar('NAOS_BUILD_MODE')
    if profile not in ('development', 'production'):
        raise ValueError('Unsupported NAOS packaging profile')
    output = Path(output)
    deploy = Path(d.getVar('DEPLOY_DIR_IMAGE'))
    unpack = Path(d.getVar('UNPACKDIR'))
    base_name = d.getVar('NAOS_PACKAGE_ROOTFS_NAME')
    base = _deployed(deploy, base_name + '.tar.gz')
    modules = _deployed(deploy, d.getVar('NAOS_PACKAGE_MODULES_FILE'))
    layout = json.loads(Path(d.getVar('NAOS_PACKAGE_LAYOUT_FILE')).read_text())
    epoch = int(d.getVar('SOURCE_DATE_EPOCH') or '0')
    if epoch < 0:
        raise ValueError('Invalid package filesystem epoch')
    version = {'machine':d.getVar('MACHINE'), 'version':d.getVar('NAOS_VERSION'),
        'build_id':d.getVar('NAOS_BUILD_ID'), 'layout_compatible':layout['compatible'],
        'secure_boot':d.getVar('NAOS_SECURE_BOOT') == '1',
        'system_encryption':d.getVar('NAOS_SYSTEM_ENCRYPTION') == '1',
        'data_encryption':d.getVar('NAOS_DATA_ENCRYPTION') == '1'}
    overlays = [Path(d.getVar('NAOS_PACKAGE_OTA_OVERLAY'))] + [deploy / name for name in (
        'naos-rauc', 'naos-hawkbit-updater', 'naos-container', 'naos-core', 'naos-apk-repository')]
    if profile == 'development':
        overlays.append(deploy / 'naos-kernel-devsrc')
    if version['data_encryption']:
        overlays.append(unpack / 'userdata-rootfs')
    if version['system_encryption']:
        overlays.append(unpack / 'system-rootfs')
    assembler = _module('naos_package_assembler', unpack / 'assemble-naos-image.py')
    inventory = _module('naos_package_inventory', unpack / 'naos_sbom_inventory.py')
    rootfs = output / 'rootfs/rootfs.tar.gz'
    rootfs.parent.mkdir(parents=True, exist_ok=True)
    assembler.assemble_rootfs(base, modules, overlays, Path(d.getVar('NAOS_OTA_KEYRING')),
                             rootfs, version, epoch)
    source = _deployed(deploy, d.getVar('NAOS_PACKAGE_INITRAMFS_FILE'))
    template_path = output / 'initramfs/initramfs.cpio.gz'
    template_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, template_path)
    template_inventory = inventory.collect_cpio(template_path)
    if any(file['path'] == FINGERPRINT for file in template_inventory['files']):
        raise ValueError('Package initramfs contains a finalized system-key fingerprint; rebuild with NAOS_PACKAGE=1')
    if version['system_encryption']:
        template_inventory['snapshot'] = 'initramfs-template-before-system-key-fingerprint'
    template = {'schema_version':1, 'machine':version['machine'], 'profile':profile,
        'recipe':d.getVar('NAOS_PACKAGE_INITRAMFS_RECIPE'), 'format':'newc', 'compression':'gzip',
        'sha256':digest(template_path), 'size':template_path.stat().st_size,
        'needs_finalization':version['system_encryption'],
        'system_key_fingerprint':FINGERPRINT if version['system_encryption'] else None,
        'fingerprint_encoding':'sha256-hex-lf' if version['system_encryption'] else None,
        'fingerprint_key_format':'64-lowercase-ascii-hex-bytes-no-newline' if version['system_encryption'] else None}
    _write(output / 'initramfs/template.json', template)
    metadata = {'schema_version':1, 'machine':version['machine'], 'profile':profile,
        'version':version['version'], 'build_id':version['build_id'], 'source_date_epoch':epoch,
        'kernel_release':kernel_release(modules), 'rootfs_sha256':digest(rootfs),
        'rootfs_size':rootfs.stat().st_size, 'sbom':d.getVar('NAOS_SBOM') == '1',
        'secure_boot':version['secure_boot'], 'system_encryption':version['system_encryption'],
        'data_encryption':version['data_encryption'], 'layout_compatible':layout['compatible']}
    _write(output / 'rootfs/metadata.json', metadata)
    if d.getVar('NAOS_SBOM') == '1':
        from . import sbom
        root_inventory = inventory.collect_rootfs(rootfs, deploy / (base_name + '.manifest.json'))
        sbom.produce(d, output / 'sbom', root_inventory, template_inventory, template)
    return metadata
