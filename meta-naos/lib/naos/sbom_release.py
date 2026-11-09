# SPDX-License-Identifier: MIT
"""Validate a release SPDX 2.3 document against its installed payload.

This is a bounded offline importer, not a replacement SPDX schema validator.
Uninstalled release files remain document evidence and never become rootfs
files. The original archive may be absent after a valid sstate restoration.
"""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tarfile
from urllib.parse import urlsplit


_CHUNK = 1024 * 1024
_DOCUMENT_LIMIT = 16 * _CHUNK
_ID = re.compile(r'SPDXRef-[A-Za-z0-9.-]+$')
_LICENSE_REF = re.compile(r'\bLicenseRef-[A-Za-z0-9.-]+\b')
_REQUIRED = {'naos-core': {'usr/bin/naos-core'},
             'naos-container': {'bin/naos', 'bin/naosd', 'bin/naos-exec'}}


def _path(value):
    if not isinstance(value, str) or not value or '\0' in value or value.startswith('/') or '..' in value.split('/'):
        raise ValueError('Unsafe release SPDX file path')
    result = str(PurePosixPath(value))
    if result == '.':
        raise ValueError('Empty release SPDX file path')
    return result


def installed_path(recipe, value):
    """Return an explicit recipe installation mapping; never guess by name."""
    value = _path(value)
    if recipe == 'naos-core':
        if value == 'build-info.json':
            return 'usr/share/naos-core/build-info.json'
        if value in ('etc/init.d/naos-core', 'etc/init.d/naosd',
                     'etc/naos-core/device.ini', 'etc/naosd/config.yml'):
            return value
        if value == 'usr/bin/naos-core' or value.startswith('usr/share/licenses/naos-core/'):
            return value
    elif recipe == 'naos-container':
        if value in _REQUIRED[recipe]:
            return 'usr/' + value
        if value == 'README.md':
            return 'usr/share/doc/naos-container/README.md'
    else:
        raise ValueError('Unsupported release SPDX producer: ' + recipe)
    return None


def _sha256(stream):
    result = hashlib.sha256()
    while block := stream.read(_CHUNK):
        result.update(block)
    return result.hexdigest()


def _checksums(values, required=False):
    if not isinstance(values, list):
        raise ValueError('Release SPDX checksums must be a list')
    result = {}
    for value in values:
        if not isinstance(value, dict) or 'algorithm' not in value or 'checksumValue' not in value:
            raise ValueError('Invalid release SPDX checksum record')
        algorithm, checksum = value['algorithm'], value['checksumValue']
        if not isinstance(algorithm, str) or not isinstance(checksum, str) or algorithm in result:
            raise ValueError('Invalid or duplicate release SPDX checksum')
        size = {'SHA256': 64, 'SHA1': 40, 'MD5': 32}.get(algorithm)
        if size and not re.fullmatch(r'[0-9a-fA-F]{' + str(size) + '}', checksum):
            raise ValueError('Invalid release SPDX ' + algorithm + ' checksum')
        result[algorithm] = checksum.lower() if size else checksum
    if required and 'SHA256' not in result:
        raise ValueError('Release SPDX file lacks SHA256')
    return result


def _archive_files(archive_path, recipe, expected):
    seen, found = set(), set()
    with tarfile.open(archive_path, 'r|*') as archive:
        for member in archive:
            raw = member.name
            if member.isdir() and str(PurePosixPath(raw)) == '.':
                continue
            name = _path(raw)
            if recipe == 'naos-container':
                prefix = 'naos-alpine-arm64/'
                if name == prefix[:-1] and member.isdir():
                    continue
                if not name.startswith(prefix):
                    raise ValueError('Unexpected container release archive prefix')
                name = _path(name[len(prefix):])
            if name in seen:
                raise ValueError('Duplicate release archive path: ' + name)
            seen.add(name)
            if name not in expected:
                continue
            if not member.isfile():
                raise ValueError('Release SPDX file is not a regular archive member: ' + name)
            with archive.extractfile(member) as stream:
                if _sha256(stream) != expected[name]:
                    raise ValueError('Release SPDX hash differs from archive payload: ' + name)
            found.add(name)
    if found != set(expected):
        raise ValueError('Release SPDX names absent archive files: ' + ', '.join(sorted(set(expected) - found)))


def load_release_sbom(sbom_path, runtime_root, recipe, archive_path=None):
    """Return normalized, locally verified SPDX release evidence."""
    if recipe not in _REQUIRED:
        raise ValueError('Unsupported release SPDX producer: ' + recipe)
    sbom_path, runtime_root = Path(sbom_path), Path(runtime_root).resolve()
    if sbom_path.is_symlink() or not sbom_path.resolve().is_relative_to(runtime_root) or not sbom_path.is_file():
        raise ValueError('Missing or unsafe deployed release SPDX document')
    if sbom_path.stat().st_size > _DOCUMENT_LIMIT:
        raise ValueError('Release SPDX document exceeds size limit')
    raw = sbom_path.read_bytes()
    document = json.loads(raw)
    if not isinstance(document, dict):
        raise ValueError('Release SPDX document must be an object')
    if document.get('spdxVersion') != 'SPDX-2.3' or document.get('SPDXID') != 'SPDXRef-DOCUMENT':
        raise ValueError('Expected an SPDX 2.3 release document')
    if document.get('dataLicense') != 'CC0-1.0' or not isinstance(document.get('creationInfo'), dict):
        raise ValueError('Release SPDX document lacks standard creation/license fields')
    namespace = document.get('documentNamespace', '')
    parsed = urlsplit(namespace)
    if not namespace or not parsed.scheme or parsed.username or parsed.password or any(c.isspace() for c in namespace):
        raise ValueError('Invalid release SPDX document namespace')
    if document.get('externalDocumentRefs'):
        raise ValueError('External release SPDX document references are unsupported')
    packages, files, identities, paths = [], [], {'SPDXRef-DOCUMENT'}, set()
    for kind, values in (('package', document.get('packages')), ('file', document.get('files'))):
        if not isinstance(values, list) or not values:
            raise ValueError('Release SPDX lacks ' + kind + ' records')
        for value in values:
            if not isinstance(value, dict):
                raise ValueError('Release SPDX record must be an object')
            identifier = value.get('SPDXID')
            if not isinstance(identifier, str) or not _ID.fullmatch(identifier) or identifier in identities:
                raise ValueError('Invalid or duplicate release SPDX identifier')
            identities.add(identifier)
            if kind == 'package':
                if not isinstance(value.get('name'), str) or not value['name']:
                    raise ValueError('Release SPDX package lacks name')
                external = value.get('externalRefs', [])
                if not isinstance(external, list) or any(not isinstance(ref, dict) for ref in external):
                    raise ValueError('Invalid release SPDX external references')
                purls = [ref.get('referenceLocator') for ref in external if ref.get('referenceType') == 'purl']
                if len(purls) > 1 or any(not isinstance(purl, str) or not purl.startswith('pkg:') for purl in purls):
                    raise ValueError('Invalid release SPDX package URL')
                declared = value.get('licenseDeclared', 'NOASSERTION')
                if not isinstance(declared, str) or not declared:
                    raise ValueError('Invalid release SPDX package license')
                packages.append({'spdx_id': identifier, 'name': value['name'],
                    'version': value.get('versionInfo'), 'license_declared': declared,
                    'license_concluded': value.get('licenseConcluded'),
                    'license_info_from_files': value.get('licenseInfoFromFiles'),
                    'homepage': value.get('homepage'), 'download_location': value.get('downloadLocation'),
                    'source_info': value.get('sourceInfo'), 'comment': value.get('comment'),
                    'package_file_name': value.get('packageFileName'),
                    'external_refs': external, 'purl': purls[0] if purls else None,
                    'checksums': _checksums(value.get('checksums', []))})
            else:
                name = _path(value.get('fileName'))
                if name in paths:
                    raise ValueError('Duplicate release SPDX filename: ' + name)
                paths.add(name)
                checksums = _checksums(value.get('checksums', []), required=True)
                installed = installed_path(recipe, name)
                if installed is not None:
                    target = runtime_root / installed
                    if not target.resolve().is_relative_to(runtime_root) or target.is_symlink() or not target.is_file():
                        raise ValueError('Missing or unsafe installed release payload: ' + installed)
                    with target.open('rb') as stream:
                        if _sha256(stream) != checksums['SHA256']:
                            raise ValueError('Release SPDX hash differs from installed payload: ' + installed)
                files.append({'spdx_id': identifier, 'path': name, 'sha256': checksums['SHA256'],
                              'installed_path': installed, 'installed_verified': installed is not None,
                              'license_concluded': value.get('licenseConcluded'),
                              'license_info_in_files': value.get('licenseInfoInFiles')})
    package_ids = {package['spdx_id'] for package in packages}
    relationships, described, contained = [], [], set()
    if not isinstance(document.get('relationships'), list):
        raise ValueError('Release SPDX relationships must be a list')
    for value in document['relationships']:
        if not isinstance(value, dict):
            raise ValueError('Release SPDX relationship must be an object')
        source, target, kind = value.get('spdxElementId'), value.get('relatedSpdxElement'), value.get('relationshipType')
        if source not in identities or target not in identities or not isinstance(kind, str):
            raise ValueError('Unresolved release SPDX relationship')
        if source == 'SPDXRef-DOCUMENT' and kind == 'DESCRIBES':
            described.append(target)
        relationships.append({'source': source, 'target': target, 'type': kind, 'comment': value.get('comment')})
    if len(described) != 1 or described[0] not in package_ids:
        raise ValueError('Release SPDX must describe one main application package')
    main = next(package for package in packages if package['spdx_id'] == described[0])
    if main['name'] != recipe:
        raise ValueError('Release SPDX main application differs from recipe')
    if main.get('package_file_name') and 'SHA256' in main['checksums']:
        named = _path(main['package_file_name'])
        matching = next((file for file in files if file['path'] == named), None)
        if matching is None or matching['sha256'] != main['checksums']['SHA256']:
            raise ValueError('Release SPDX main package checksum differs from its named file')
    contained = {relation['target'] for relation in relationships
                 if relation['source'] == described[0] and relation['type'] == 'CONTAINS'}
    if not _REQUIRED[recipe].issubset({file['path'] for file in files if file['spdx_id'] in contained}):
        raise ValueError('Release SPDX main application does not contain its runtime binaries')
    license_texts = {}
    extracted = document.get('hasExtractedLicensingInfos', [])
    if not isinstance(extracted, list):
        raise ValueError('Release SPDX extracted licenses must be a list')
    for value in extracted:
        if not isinstance(value, dict):
            raise ValueError('Release SPDX extracted license must be an object')
        identifier, text = value.get('licenseId'), value.get('extractedText')
        if not isinstance(identifier, str) or not re.fullmatch(r'LicenseRef-[A-Za-z0-9.-]+', identifier) or identifier in license_texts or not isinstance(text, str) or not text:
            raise ValueError('Invalid release SPDX extracted license text')
        license_texts[identifier] = text
    for package in packages:
        if set(_LICENSE_REF.findall(package['license_declared'])) - set(license_texts):
            raise ValueError('Release SPDX custom license lacks extracted text')
    archive_verified = archive_path is not None
    if archive_verified:
        _archive_files(archive_path, recipe, {file['path']: file['sha256'] for file in files})
    notes = [value for value in (document.get('comment'), main.get('comment')) if isinstance(value, str) and value]
    if not archive_verified and any(file['installed_path'] is None for file in files):
        notes.append('Original release archive unavailable after deployment; uninstalled release files are document evidence only and were not independently verified.')
    return {'schema_version': 1, 'spdx_version': 'SPDX-2.3',
            'document_namespace': namespace, 'document_sha256': hashlib.sha256(raw).hexdigest(),
            'document_name': document.get('name'), 'main_package': described[0],
            'packages': sorted(packages, key=lambda item: item['spdx_id']),
            'files': sorted(files, key=lambda item: item['path']),
            'relationships': sorted(relationships, key=lambda item: (item['source'], item['type'], item['target'])),
            'license_texts': license_texts, 'archive_verified': archive_verified, 'limitations': notes}
