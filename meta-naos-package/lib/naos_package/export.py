# SPDX-License-Identifier: MIT
"""Collect and atomically publish inputs for an external image generator."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
import uuid

MIB = 1024 * 1024
SECTOR = 512
PARTITION_TYPE = '0fc63daf-8483-4772-8e79-3d69d8477de4'
BOOTLOADER_FIXED = frozenset((
    'u-boot-nodtb.bin', 'u-boot.clean.dtb',
    'spl/u-boot-spl-nodtb.bin', 'spl/u-boot-spl.clean.dtb',
    'rkbin/RKBOOT/RK3588MINIALL_V5.ini.in',
    'rkbin/RKTRUST/RK3588TRUST.ini', 'templates/u-boot.its.in',
    'config/u-boot.config', 'bootloader.json',
))
FIRMWARE_PATTERNS = (
    r'rkbin/bin/rk35/rk3588_ddr_[A-Za-z0-9_.-]+\.bin',
    r'rkbin/bin/rk35/rk3588_usbplug_v[A-Za-z0-9_.-]+\.bin',
    r'rkbin/bin/rk35/rk3588_bl31_v[A-Za-z0-9_.-]+\.elf',
    r'rkbin/bin/rk35/rk3588_bl32_v[A-Za-z0-9_.-]+\.bin',
)
SBOM_FILES = ('components.json', 'inventory.json', 'kernel.config', 'uboot.config')


def _require(value, message):
    if not value:
        raise ValueError(message)


def _json(path):
    return json.loads(Path(path).read_text())


def _write(path, value):
    Path(path).write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')
    Path(path).chmod(0o644)


def _relative(name):
    _require(isinstance(name, str), 'Invalid export-relative path')
    path = PurePosixPath(name)
    _require(name and not path.is_absolute()
             and '..' not in path.parts and str(path) != '.' and '\\' not in name
             and '\0' not in name, 'Invalid export-relative path')
    return str(path)


def _label(value):
    _require(isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+-]*', value)
             and value not in ('.', '..'), 'Invalid machine or profile label')
    return value


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _record(path, role):
    _require(path.is_file() and path.stat().st_size > 0, 'Missing or empty generator input: ' + path.name)
    return {'role': role, 'size': path.stat().st_size, 'sha256': digest(path)}


def _copy(source, root, name, role, records):
    name = _relative(name)
    _require(source.is_file(), 'Missing generator input: ' + str(source))
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    target.chmod(0o644)
    records[name] = _record(target, role)


def effective_layout(original, system_encryption):
    """Keep the board storage contract independent of names and partition sizes."""
    layout = json.loads(json.dumps(original))
    _require(layout.get('format') == 1 and layout.get('sector_size') == SECTOR,
             'Unsupported partition layout')
    parts = layout['partitions']
    _require({p['name'] for p in parts} ==
             {'uboot', 'misc', 'boot_a', 'boot_b', 'system_a', 'system_b', 'userdata'}
             and len(parts) == 7, 'Unexpected NAOS partition set')
    end = 34 * SECTOR
    for part in parts:
        _require(type(part['start_mib']) is int and type(part['size_mib']) is int
                 and part['start_mib'] * MIB >= end and part['size_mib'] > 0,
                 'Overlapping or invalid partition: ' + part['name'])
        end = (part['start_mib'] + part['size_mib']) * MIB
    by_name = {p['name']: p for p in parts}
    for kind in ('boot', 'system'):
        _require(by_name[kind + '_a']['size_mib'] == by_name[kind + '_b']['size_mib'],
                 'A/B partition capacities must match: ' + kind)
    _require(parts[-1]['name'] == 'userdata' and parts[-1].get('initialize') == 'initramfs',
             'Userdata must be initialized by the initramfs')
    _require(layout['loader_copies'] == 2 and layout['loader_offset'] >= 34 * SECTOR
             and layout['loader_offset'] % SECTOR == 0
             and layout['loader_copy_size'] > 0 and layout['loader_copy_size'] % SECTOR == 0
             and layout['loader_offset'] + 2 * layout['loader_copy_size'] <= parts[0]['start_mib'] * MIB,
             'Invalid redundant IDBlock locations')
    regions = layout['misc_regions']
    if system_encryption:
        for name, offset in (('system_key_blob_a', 0x50000), ('system_key_blob_b', 0x60000)):
            existing = [v for v in regions if v['name'] == name]
            if existing:
                _require(existing == [{'name': name, 'offset': offset, 'size': 65536}],
                         'System-key blob layout differs from initramfs protocol')
            else:
                regions.append({'name': name, 'offset': offset, 'size': 65536})
        for name in ('system_a', 'system_b'):
            by_name[name]['payload'] = 'system.img'
    regions.sort(key=lambda r: r['offset'])
    end = 0
    _require(len({v['name'] for v in regions}) == len(regions), 'Duplicate misc region')
    for region in regions:
        _require(type(region['offset']) is int and type(region['size']) is int
                 and region['offset'] >= end and region['size'] > 0,
                 'Overlapping misc region')
        end = region['offset'] + region['size']
    _require(end <= by_name['misc']['size_mib'] * MIB, 'Misc region exceeds its partition')
    env = layout['environment']
    _require(env['partition'] == 'misc' and env['size'] == 32768
             and len(env['offsets']) == 2, 'Invalid initial environment layout')
    for suffix, offset in zip(('a', 'b'), env['offsets']):
        region = next(v for v in regions if v['name'] == 'uboot_env_' + suffix)
        _require(region['offset'] == offset and region['size'] >= env['size'],
                 'Environment differs from its reserved misc region')

    disk_guid = uuid.uuid5(uuid.NAMESPACE_URL, layout['compatible'])
    disk_size = (((parts[-1]['start_mib'] + parts[-1]['size_mib']) * MIB
                  + 34 * SECTOR + MIB - 1) // MIB) * MIB
    sectors = disk_size // SECTOR
    layout['gpt'] = {
        'disk_guid': str(disk_guid), 'partition_type_guid': PARTITION_TYPE,
        'entry_count': 128, 'entry_size': 128, 'protective_mbr': True,
        'primary_header_lba': 1, 'primary_entries_lba': 2,
        'backup_header_lba': sectors - 1, 'backup_entries_lba': sectors - 33,
        'first_usable_lba': 34, 'last_usable_lba': sectors - 34,
        'image_size_bytes': disk_size,
        'disk_guid_rule': 'UUIDv5(NAMESPACE_URL, compatible)',
        'partition_guid_rule': 'UUIDv5(disk_guid, partition_name)',
        'image_size_rule': 'align_up(last_partition_end_bytes + 34 * sector_size, 1 MiB)',
    }
    for part in parts:
        part['uuid'] = str(uuid.uuid5(disk_guid, part['name']))
        part['type_guid'] = PARTITION_TYPE
        part['attributes'] = 0
    return layout


def collect(inputs, deploy, layout_file, output, settings):
    machine, profile = _label(settings['machine']), _label(settings['profile'])
    _require(profile in ('development', 'production'), 'Unknown package profile')
    output.mkdir(parents=True, exist_ok=True)
    _require(not any(output.iterdir()), 'Collect output must be empty')
    metadata = _json(inputs / 'rootfs/metadata.json')
    for field, expected in (('machine', machine), ('profile', profile),
                            ('version', settings['version']), ('build_id', settings['build_id'])):
        _require(metadata.get(field) == expected, 'Stale rootfs package input: ' + field)
    for field in ('secure_boot', 'system_encryption', 'data_encryption', 'sbom'):
        _require(metadata.get(field) is settings[field], 'Rootfs package settings differ: ' + field)
    rootfs = inputs / 'rootfs/rootfs.tar.gz'
    _require(metadata.get('rootfs_sha256') == digest(rootfs)
             and metadata.get('rootfs_size') == rootfs.stat().st_size,
             'Rootfs package input differs from its metadata')
    records = {}
    for source, name, role in (
        (rootfs, 'rootfs.tar.gz', 'rootfs'),
        (deploy / 'Image', 'Image', 'kernel'),
        (deploy / settings['dtb'], settings['dtb'], 'kernel_dtb'),
        (inputs / 'initramfs/initramfs.cpio.gz', 'initramfs.cpio.gz', 'initramfs_template'),
        (deploy / 'resource.img', 'resource.img', 'kernel_resource'),
        (deploy / 'naos-uboot-env.bin', 'naos-uboot-env.bin', 'initial_environment'),
    ):
        _copy(source, output, name, role, records)
    _require(records['naos-uboot-env.bin']['size'] == 32768, 'Incorrect environment image size')
    template = _json(inputs / 'initramfs/template.json')
    _require(template.get('machine') == machine and template.get('profile') == profile
             and template.get('sha256') == records['initramfs.cpio.gz']['sha256']
             and template.get('size') == records['initramfs.cpio.gz']['size']
             and template.get('format') == 'newc' and template.get('compression') == 'gzip',
             'Initramfs template differs from its deployed metadata')
    _require(bool(template.get('needs_finalization')) == settings['system_encryption'],
             'Initramfs template differs from encryption settings')
    _require(template.get('system_key_fingerprint') ==
             ('usr/lib/naos/system-key.sha256' if settings['system_encryption'] else None),
             'Unexpected initramfs key-fingerprint target')
    bootloader = inputs / 'bootloader'
    actual = {p.relative_to(bootloader).as_posix() for p in bootloader.rglob('*') if p.is_file()}
    _require(BOOTLOADER_FIXED <= actual, 'Incomplete early bootloader inputs')
    firmware = actual - BOOTLOADER_FIXED
    _require(len(firmware) == len(FIRMWARE_PATTERNS)
             and all(sum(bool(re.fullmatch(pattern, p)) for p in firmware) == 1
                     for pattern in FIRMWARE_PATTERNS), 'Unexpected bootloader package files')
    for name in sorted(actual):
        source = bootloader / name
        _require(not source.is_symlink(), 'Bootloader package inputs must be ordinary files')
        _copy(source, output, 'bootloader/' + name, 'bootloader_input', records)
    bsp = _json(bootloader / 'bootloader.json')
    _require(bsp.get('machine') == machine and bsp.get('profile') == profile,
             'Early bootloader belongs to another machine or profile')
    if settings['sbom']:
        for name in SBOM_FILES:
            _copy(inputs / 'sbom' / name, output, 'sbom/' + name, 'sbom_input', records)
    layout = effective_layout(_json(layout_file), settings['system_encryption'])
    _require(metadata.get('layout_compatible') == layout['compatible'],
             'Rootfs and exported partition layout identities differ')
    _write(output / 'partition-layout.json', layout)
    records['partition-layout.json'] = _record(output / 'partition-layout.json', 'partition_layout')
    exported = {
        'schema_version': 1, 'stage': 'before-filesystem-compression-encryption-and-signing',
        'machine': machine, 'profile': profile, 'version': settings['version'],
        'build_id': settings['build_id'], 'architecture': settings['architecture'],
        'source_date_epoch': metadata['source_date_epoch'], 'kernel_release': metadata['kernel_release'],
        'input_files': dict(sorted(records.items())),
        'defaults': {key: settings[key] for key in
                     ('secure_boot', 'system_encryption', 'data_encryption', 'ota', 'sbom')},
        'otp': {'burn_public_key_hash': False, 'property': '/signature/key-dev/burn-key-hash',
                'target': 'SPL control DTB', 'execution': 'device SPL after successful U-Boot FIT verification'},
        'rootfs': {'archive': 'rootfs.tar.gz', 'format': 'tar+gzip',
                   'preserve': ['numeric_uid_gid', 'mode', 'symlinks', 'hardlinks', 'device_nodes'],
                   'squashfs': {'compression': 'zstd', 'root_uid': 0, 'root_gid': 0,
                                'root_mode': '0755', 'timestamps': 'source_date_epoch', 'all_root': False},
                   'public_certificate_paths': {'ota': 'etc/rauc/keyring.pem', 'ssh_ca': 'etc/ssh/ssh_ca_key.pub'}},
        'initramfs': {**template, 'archive': 'initramfs.cpio.gz', 'format': 'newc+gzip',
                      'fingerprint_algorithm': 'SHA256 of 64 lowercase ASCII hexadecimal key bytes without newline',
                      'fingerprint_mode': '0644', 'fingerprint_uid': 0, 'fingerprint_gid': 0},
        'system_encryption': {'cipher': 'aes-cbc-plain', 'key_bytes': 32, 'key_file_format': '64 lowercase hexadecimal ASCII bytes without newline',
                              'sector_bytes': 512, 'iv': 'LE32(sector_number) followed by 12 zero bytes',
                              'iv_offset_sectors': 0, 'data_offset_sectors': 0, 'alignment_bytes': 4096,
                              'import': {'misc_region': 'system_key_import', 'format': 'NAOS-SYSTEM-KEY version=2',
                                         'record_bytes': 65536, 'header_bytes': 256,
                                         'header_template': 'NAOS-SYSTEM-KEY\nversion=2\nkey={key_hex}\n',
                                         'header_padding_byte': 32, 'checksum_offset': 256,
                                         'checksum_bytes': 64, 'checksum': 'lowercase ASCII SHA256 of the padded header',
                                         'remaining_record_padding_byte': 0}},
        'storage': {'layout': 'partition-layout.json', 'environment': 'naos-uboot-env.bin',
                    'environment_format': 'redundant U-Boot, 32768 bytes', 'unused_bytes': 0,
                    'initial_userdata': 'unformatted zero-filled partition; grow and create F2FS in initramfs',
                    'recompute_gpt_after_layout_changes': True,
                    'validate_payload_capacity': True},
        'bootloader': 'bootloader/bootloader.json',
        'ota': {
            'enabled_by_default': settings['ota'], 'bundle_format': 'verity',
            'compatible': layout['compatible'], 'version': settings['version'], 'build': settings['build_id'],
            'signing_material': 'External OTA signing key and matching certificate supplied to the image generator',
            'rootfs_keyring': 'etc/rauc/keyring.pem', 'squashfs_compression': 'zstd',
            'system_bundle': {
                'images': [{'class': 'rootfs', 'filename': 'system.img' if settings['system_encryption'] else 'system.squashfs'},
                           {'class': 'boot', 'filename': 'boot.img'}],
                'install_to': 'inactive A/B slot',
                'system_encryption_key_policy': 'Use the key already provisioned on the devices; key rotation is a separate migration',
            },
            'bootloader_bundle': {
                'images': [{'class': 'uboot', 'filename': 'uboot-slot.img', 'size': 2 * MIB, 'copies_in_payload': 1},
                           {'class': 'idb', 'filename': 'idblock-slot.img', 'size': 512 * 1024, 'copies_in_payload': 1}],
                'installation_order': ['uboot', 'idb'], 'switch_system_slot': False,
                'slot_type': 'boot-raw-fallback', 'redundant_copies_written_by': 'RAUC on the device',
                'signing_key_policy': 'Remain compatible with the existing SPL and device OTP trust root',
            },
        },
        'sbom': {'included': settings['sbom'], 'stage': 'image-generator inputs',
                 'finalization': 'Rescan changed rootfs/initramfs and bind final image, FIT and OTA digests when rendering final SPDX'},
    }
    _write(output / 'export.json', exported)
    verify(output)
    return exported


def verify(root):
    document = _json(root / 'export.json')
    _require(document.get('schema_version') == 1, 'Unsupported generator package schema')
    expected = document['input_files']
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}
    _require(actual == set(expected) | {'export.json'}, 'Export contains missing or unexpected files')
    for name, record in expected.items():
        path = root / _relative(name)
        _require(not path.is_symlink() and path.is_file(), 'Export contains a non-ordinary input')
        _require(record['size'] == path.stat().st_size and record['sha256'] == digest(path),
                 'Export file differs from manifest: ' + name)
    _require(document['otp']['burn_public_key_hash'] is False, 'Export must default OTP burning to off')
    return document


def publish(source, destination, *, machine, profile, version, build_id):
    document = verify(source)
    for name, value in (('machine', machine), ('profile', profile), ('version', version), ('build_id', build_id)):
        _require(document.get(name) == value, 'Stale package deployment: ' + name)
    _label(machine)
    _label(profile)
    _require(destination.name == machine and destination.parent.name == profile,
             'Export destination must be the selected profile/machine directory')
    _require(not destination.is_symlink(), 'Export destination cannot be a symbolic link')
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.' + machine + '-', dir=destination.parent))
    backup = destination.with_name('.' + machine + '-old-' + uuid.uuid4().hex)
    try:
        shutil.copytree(source, temporary, dirs_exist_ok=True)
        verify(temporary)
        if destination.exists():
            _require(destination.is_dir(), 'Export destination must be a directory')
            destination.rename(backup)
        try:
            temporary.rename(destination)
        except BaseException:
            if backup.exists():
                backup.rename(destination)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
