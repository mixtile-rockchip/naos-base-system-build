# SPDX-License-Identifier: MIT
"""Export bounded build metadata for components actually selected by a product."""
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import re


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def load_inventory_helper(d):
    path = Path(d.getVar('NAOS_LAYER_DIR')) / 'recipes-core/images/files/naos_sbom_inventory.py'
    spec = importlib.util.spec_from_file_location('naos_sbom_inventory', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def component_enabled(d):
    """Evaluate before sstate's anonymous function registers task hooks."""
    import bb

    pn = d.getVar('PN')
    explicit = ('linux-naos-rockchip', 'u-boot-naos-rockchip', 'naos-core',
                'naos-container', 'naos-initramfs', 'naos-initramfs-encrypted')
    skip = ('naos-release', 'naos-image', 'naos-sbom', 'naos-alpine-rootfs',
            'naos-bundle', 'naos-bootloader-bundle', 'naos-package', 'naos-package-rootfs')
    native = any(bb.data.inherits_class(c, d) for c in ('native', 'nativesdk', 'cross', 'crosssdk'))
    return (d.getVar('NAOS_SBOM') == '1' and pn not in skip and not native
            and (pn in explicit or bool(d.getVar('PACKAGES'))))


def _valid_license_expression(expression):
    """Check operator/parenthesis syntax without inventing missing operators."""
    tokens = re.findall(r'\(|\)|[^\s()]+', expression)
    index = 0

    def primary():
        nonlocal index
        if index >= len(tokens):
            return False
        token = tokens[index]
        index += 1
        if token == '(':
            if not compound() or index >= len(tokens) or tokens[index] != ')':
                return False
            index += 1
            return True
        if token in (')', 'AND', 'OR', 'WITH'):
            return False
        if index < len(tokens) and tokens[index] == 'WITH':
            index += 1
            if index >= len(tokens) or tokens[index] in ('(', ')', 'AND', 'OR', 'WITH'):
                return False
            index += 1
        return True

    def compound():
        nonlocal index
        if not primary():
            return False
        while index < len(tokens) and tokens[index] in ('AND', 'OR'):
            index += 1
            if not primary():
                return False
        return True

    return compound() and index == len(tokens)


def license_info(raw, d):
    """Keep raw declarations; normalize only licenses for which we have evidence."""
    import oe.spdx_common

    catalog = oe.spdx_common.load_spdx_license_data(d)
    known = set(catalog['licenses'])
    expression, texts = [], {}
    tokens = re.findall(r'\(|\)|\&|\||[^\s()&|]+', raw or '')
    for token in tokens:
        if token in ('CLOSED', 'NONE', 'NOASSERTION'):
            return {'license_expression':None, 'license_texts':{},
                    'license_list_version':catalog['licenseListVersion']}
        if token in ('(', ')', 'AND', 'OR', 'WITH'):
            expression.append(token)
        elif token in ('&', '|'):
            expression.append({'&':'AND', '|':'OR'}[token])
        else:
            mapped = d.getVarFlag('SPDXLICENSEMAP', token) or token
            if mapped in known or ' WITH ' in mapped:
                expression.append(mapped)
                continue
            if not re.fullmatch(r'[A-Za-z0-9.+_-]+', token):
                return {'license_expression':None, 'license_texts':{},
                        'license_list_version':catalog['licenseListVersion']}
            directories = [d.getVar('COMMON_LICENSE_DIR')] + (d.getVar('LICENSE_PATH') or '').split()
            text = None
            for directory in directories:
                candidate = Path(directory) / token
                if candidate.is_file():
                    text = candidate.read_text(errors='replace')
                    break
            if text is None:
                # This is an unknown declaration, not permission to invent terms.
                return {'license_expression':None, 'license_texts':{},
                        'license_list_version':catalog['licenseListVersion']}
            identifier = 'LicenseRef-' + re.sub(r'[^A-Za-z0-9.-]', '-', token)
            if not re.fullmatch(r'[A-Za-z0-9.-]+', token):
                identifier += '-' + hashlib.sha256(token.encode()).hexdigest()[:12]
            expression.append(identifier)
            texts[identifier] = text
    expression = ' '.join(expression)
    if not _valid_license_expression(expression):
        expression, texts = None, {}
    return {'license_expression':expression, 'license_texts':texts,
            'license_list_version':catalog['licenseListVersion']}


def sources(d):
    import bb.fetch2

    records = []
    for uri in (d.getVar('SRC_URI') or '').split():
        kind, host, path, user, password, params = bb.fetch2.decodeurl(uri)
        name = params.get('name', 'default')
        if kind in ('git', 'gitsm'):
            protocol = params.get('protocol', 'git')
            if protocol == 'file':
                location = 'file://' + Path(path).name
            else:
                location = protocol + '://' + host + path.split('?', 1)[0]
        elif kind == 'file':
            location = 'file://' + Path(path).name
        else:
            location = kind + '://' + host + path.split('?', 1)[0]
        record = {'uri':location}
        revision = d.getVar('SRCREV_' + name) or d.getVar('SRCREV')
        if kind in ('git', 'gitsm') and revision:
            record['revision'] = revision
        if kind not in ('file', 'git', 'gitsm'):
            expected = d.getVarFlag('SRC_URI', name + '.sha256sum')
            if not expected and name == 'default':
                expected = d.getVarFlag('SRC_URI', 'sha256sum')
            if expected:
                record['sha256'] = expected
        # Only a supplied local release archive needs an additional checksum.
        # Do not traverse arbitrary local directories or signing material.
        if kind == 'file' and path.endswith(('.tar.gz', '.tar.xz', '.tar.zst', '.zip')):
            fetcher = bb.fetch2.Fetch([uri], d)
            local = Path(fetcher.localpath(uri))
            if local.is_file():
                record['sha256'] = digest(local)
        records.append(record)
    return sorted(records, key=lambda r:(r['uri'], r.get('revision', '')))


def runtime_overlay(d, dirname):
    root = Path(d.getVar('DEPLOY_DIR_IMAGE')) / dirname
    if not root.is_dir():
        raise ValueError('Missing deployed runtime component: ' + dirname)
    paths, checksums = [], {}
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            paths.append(relative)
        elif path.is_file():
            paths.append(relative)
            checksums[relative] = digest(path)
    return root, paths, checksums


def initramfs_metadata(d, result):
    import oe.packagedata

    deploy = Path(d.getVar('DEPLOY_DIR_IMAGE'))
    stem = d.getVar('IMAGE_LINK_NAME')
    if not stem:
        raise ValueError('Initramfs lacks IMAGE_LINK_NAME')
    archive, manifest = deploy / (stem + '.cpio.gz'), deploy / (stem + '.manifest')
    if not archive.is_file() or not manifest.is_file():
        raise ValueError('Missing deployed initramfs archive or package manifest: ' + stem)
    packages = []
    for line in manifest.read_text().splitlines():
        if not line.strip():
            continue
        name, architecture, version = line.split()
        # Runtime pkgdata stores overrides such as FILES_INFO:<package>.
        # Use OE's public collapsing helper before looking up those fields.
        pkgdata = oe.packagedata.read_subpkgdata_dict(name, d)
        if not pkgdata:
            raise ValueError('Missing runtime pkgdata for initramfs package: ' + name)
        declared = pkgdata.get('LICENSE', '')
        if 'FILES_INFO' not in pkgdata:
            raise ValueError('Missing FILES_INFO for initramfs package: ' + name)
        files = json.loads(pkgdata['FILES_INFO'])
        package = {'name':name, 'architecture':architecture, 'version':version,
                   'source_recipe':pkgdata.get('PN', name), 'license':declared,
                   'files':sorted(p.lstrip('/') for p in files)}
        package.update(license_info(declared, d))
        packages.append(package)
    result['image_packages'] = sorted(packages, key=lambda p:p['name'])
    result['image_inventory'] = load_inventory_helper(d).collect_cpio(archive)
    result['artifacts'] = [{'path':archive.name, 'sha256':digest(archive),
                            'size':archive.stat().st_size}]


def cve_status(d):
    """Export recipe assessments, without scanning a vulnerability database."""
    import oe.cve_check

    scope = d.getVar('SPDX_INCLUDE_VEX') or 'current'
    if scope not in ('none', 'current', 'all'):
        raise ValueError('SPDX_INCLUDE_VEX must be none, current or all')
    if scope == 'none':
        return {}
    result = {}
    for cve, assessment in sorted(oe.cve_check.get_patched_cves(d).items()):
        detail = assessment['status']
        # Match the native SPDX policy for already-fixed upstream versions.
        if scope != 'all' and detail in ('fixed-version', 'cpe-stable-backport'):
            continue
        value = {'status': assessment['abbrev-status'], 'detail': detail,
                 'description': assessment.get('justification') or ''}
        if assessment.get('resource'):
            # Patch resources contain WORKDIR paths in OE's internal metadata.
            value['patches'] = sorted({Path(path).name for path in assessment['resource']})
        result[cve] = value
    return result


def base_component(d):
    pn = d.getVar('PN')
    declared = d.getVar('LICENSE') or ''
    result = {'schema_version':1, 'recipe':pn, 'name':d.getVar('BPN') or pn,
              'version':d.getVar('PV') or '', 'license':declared,
              'homepage':d.getVar('HOMEPAGE') or '', 'sources':sources(d),
              'payload_paths':[], 'cve_product':d.getVar('CVE_PRODUCT') or ''}
    result.update(license_info(declared, d))
    assessments = cve_status(d)
    if assessments:
        result['cve_status'] = assessments
    return result


def extend_component(result, d, output):
    extension = d.getVar('NAOS_SBOM_COMPONENT_EXTENSION') or ''
    if extension:
        module, function = extension.split(':', 1)
        getattr(importlib.import_module(module), function)(result, d, output)


def emit_bsp_component(d):
    """Run inside do_deploy so config/input evidence shares its sstate cache."""
    output = Path(d.getVar('DEPLOYDIR')) / 'sbom-inputs'
    output.mkdir(parents=True, exist_ok=True)
    result = base_component(d)
    extend_component(result, d, output)
    (output / ('bsp-' + d.getVar('PN') + '.json')).write_text(
        json.dumps(result, sort_keys=True, indent=2) + '\n')


def emit_component(d):
    output = Path(d.getVar('NAOS_SBOM_COMPONENT_OUTPUT'))
    output.mkdir(parents=True, exist_ok=True)
    pn = d.getVar('PN')
    if d.getVar('NAOS_SBOM_PREEXPORTED') == '1':
        # do_deploy restores this evidence even when WORKDIR was removed.
        source = Path(d.getVar('DEPLOY_DIR_IMAGE')) / 'sbom-inputs' / ('bsp-' + pn + '.json')
        result = json.loads(source.read_text())
        if result['recipe'] != pn or result['version'] != d.getVar('PV'):
            raise ValueError('BSP SBOM evidence differs from the selected recipe')
        (output / (pn + '.json')).write_text(json.dumps(result, sort_keys=True, indent=2) + '\n')
        return
    result = base_component(d)
    runtime_dirs = {'rauc':'naos-rauc', 'rauc-hawkbit-updater':'naos-hawkbit-updater',
                    'naos-core':'naos-core', 'naos-container':'naos-container'}
    if pn in runtime_dirs:
        root, result['payload_paths'], result['payload_hashes'] = runtime_overlay(d, runtime_dirs[pn])
        if pn == 'naos-core':
            info_path = root / 'usr/share/naos-core/build-info.json'
            info = json.loads(info_path.read_text())
            result['prebuilt'] = {k:info[k] for k in (
                'schema_version', 'binary_sha256', 'source_sha256', 'go_version',
                'goos', 'goarch', 'cgo_enabled', 'modules') if k in info}
            if result['payload_hashes'].get('usr/bin/naos-core') != info.get('binary_sha256'):
                raise ValueError('Deployed Core differs from its independent build metadata')
        elif pn == 'naos-container':
            result['dependency_metadata'] = 'not_provided'
        if pn in ('naos-core', 'naos-container'):
            release = root / ('usr/share/' + pn + '/sbom.spdx.json')
            if release.is_file():
                import bb.fetch2
                from .sbom_release import load_release_sbom

                archive = None
                for uri in (d.getVar('SRC_URI') or '').split():
                    if uri.split(';', 1)[0].endswith(('.tar.gz', '.tar.xz', '.tar.zst', '.tgz')):
                        # HTTP fetchers also resolve an already cached local
                        # archive. localonly=True excludes them entirely; no
                        # download is performed by localpath().
                        fetcher = bb.fetch2.Fetch([uri], d, cache=False)
                        candidate = Path(fetcher.localpath(uri))
                        if candidate.is_file():
                            archive = candidate
                            break
                result['release_sbom'] = load_release_sbom(release, root, pn, archive)
                result.pop('dependency_metadata', None)
    if pn in ('naos-initramfs', 'naos-initramfs-encrypted'):
        initramfs_metadata(d, result)
    # Keep final-file collection tied to the deployed build, not mutable WORKDIR
    # paths consumed later by the product renderer.
    path = output / (pn + '.json')
    path.write_text(json.dumps(result, sort_keys=True, indent=2) + '\n')


def dependencies(d):
    root = Path(d.getVar('NAOS_LAYER_DIR'))
    files = list((root / 'lib/naos').glob('*.py'))
    files.append(root / 'recipes-core/images/files/naos_sbom_inventory.py')
    extra = d.getVar('NAOS_SBOM_EXTENSION_DIR') or ''
    if extra:
        files.extend(Path(extra).glob('*.py'))
    return ' '.join(str(path) + ':True' for path in sorted(files))
