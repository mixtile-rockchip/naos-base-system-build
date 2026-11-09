"""Render an actual NAOS build inventory using OE's pinned SPDX 3 model.

The renderer consumes hashes supplied by build producers. It neither opens
firmware payloads nor tries to infer dependencies from opaque binaries.
"""

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from urllib.parse import quote, urlsplit

import oe.spdx30 as spdx


_MODULE = re.compile(r"\.ko(?:\.(?:xz|gz|zst))?$")
_CUSTOM_LICENSE = re.compile(r"\b(?:LicenseRef|AdditionRef)-[A-Za-z0-9.-]+\b")
_KNOWN_GO_LICENSES = {
    "github.com/godbus/dbus/v5": "BSD-2-Clause",
    "github.com/go-ini/ini": "Apache-2.0",
    "gopkg.in/ini.v1": "Apache-2.0",
}


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _digest(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _path(value, *, root=False):
    if not isinstance(value, str) or not value or "\0" in value or value.startswith("/") or ".." in value.split("/"):
        raise ValueError("Expected a safe root-relative inventory path")
    result = str(PurePosixPath(value))
    if result == "." and not root:
        raise ValueError("An individual payload path cannot be the root directory")
    return result


def _sha256(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", value):
        raise ValueError("Expected an actual SHA256 digest in build metadata")
    return value.lower()


def _uri(value):
    if not value:
        return None
    if not isinstance(value, str) or any(character.isspace() for character in value):
        raise ValueError("Source URI must be a string")
    parsed = urlsplit(value)
    if not parsed.scheme or parsed.username or parsed.password:
        raise ValueError("Source URI is absent, relative or contains credentials")
    return value


def _license_version(value):
    value = str(value)
    if re.fullmatch(r"[0-9]+\.[0-9]+", value):
        value += ".0"
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value):
        raise ValueError("License list version must be a numeric SPDX list version")
    return value


def _valid_license_expression(expression):
    tokens = re.findall(r"\(|\)|[A-Za-z0-9][A-Za-z0-9.+-]*", expression)
    if "".join(tokens) != re.sub(r"\s+", "", expression):
        return False
    position = 0

    def identifier():
        nonlocal position
        if position >= len(tokens) or tokens[position] in ("(", ")", "AND", "OR", "WITH"):
            raise ValueError
        position += 1

    def term():
        nonlocal position
        if position < len(tokens) and tokens[position] == "(":
            position += 1
            group()
            if position >= len(tokens) or tokens[position] != ")":
                raise ValueError
            position += 1
        else:
            identifier()
        if position < len(tokens) and tokens[position] == "WITH":
            position += 1
            identifier()

    def group():
        nonlocal position
        term()
        while position < len(tokens) and tokens[position] in ("AND", "OR"):
            position += 1
            term()
    try:
        group()
        return position == len(tokens)
    except ValueError:
        return False


def _canonical(value, key=None):
    if isinstance(value, dict):
        return {name: _canonical(item, name) for name, item in sorted(value.items())}
    if isinstance(value, list):
        result = [_canonical(item) for item in value]
        if key == "@graph":
            return sorted(result, key=lambda item: item.get("spdxId", item.get("@id", "")))
        if all(isinstance(item, str) for item in result):
            return sorted(result)
        return result
    return value


class _Graph:
    def __init__(self, namespace, epoch, license_list_version):
        self.namespace = namespace
        self.license_list_version = license_list_version
        self.objects = {}
        self.unknown_licenses = []
        self.creation = spdx.CreationInfo(
            _id=self.identifier("creation", epoch),
            created=datetime.fromtimestamp(int(epoch), timezone.utc), specVersion="3.0.1")
        self.organization = self.add(spdx.Organization(
            _id=self.identifier("organization", "NAOS"), name="NAOS", creationInfo=self.creation))
        self.tool = self.add(spdx.Tool(
            _id=self.identifier("tool", "NAOS SBOM"), name="NAOS SBOM", creationInfo=self.creation))
        self.creation.createdBy = [self.organization]
        self.creation.createdUsing = [self.tool]
        self.add(self.creation)

    def identifier(self, kind, identity):
        return self.namespace + kind + ":" + _digest(identity)

    def add(self, obj):
        previous = self.objects.get(obj._id)
        if previous is not None:
            return previous
        self.objects[obj._id] = obj
        return obj

    def hash(self, checksum):
        checksum = _sha256(checksum)
        return self.add(spdx.Hash(_id=self.identifier("sha256", checksum),
                                  algorithm=spdx.HashAlgorithm.sha256, hashValue=checksum))

    def package(self, scope, identity, *, name, version=None, comment=None, purpose=None,
                purl=None, homepage=None, source_info=None):
        values = dict(_id=self.identifier("package-" + scope, identity),
                      name=name, creationInfo=self.creation)
        if version is not None:
            values["software_packageVersion"] = str(version)
        if comment:
            values["comment"] = comment
        if purpose:
            if purpose not in spdx.software_SoftwarePurpose.NAMED_INDIVIDUALS:
                raise ValueError("Unsupported SPDX software purpose: " + purpose)
            values["software_primaryPurpose"] = getattr(spdx.software_SoftwarePurpose, purpose)
        if purl:
            values["software_packageUrl"] = purl
        if homepage:
            values["software_homePage"] = _uri(homepage)
        if source_info:
            values["software_sourceInfo"] = source_info
        return self.add(spdx.software_Package(**values))

    def file(self, scope, identity, *, name, sha256=None, size=None, kind="file", comment=None, purpose=None):
        values = dict(_id=self.identifier("file-" + scope, identity), name=name,
                      creationInfo=self.creation,
                      software_fileKind=getattr(spdx.software_FileKindType, "directory" if kind == "directory" else "file"))
        details = {}
        if size is not None:
            if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                raise ValueError("Invalid inventory file size")
            details["size"] = size
        if comment:
            details["details"] = comment
        if details:
            values["comment"] = _json(details)
        if sha256:
            values["verifiedUsing"] = [self.hash(sha256)]
        if purpose:
            values["software_primaryPurpose"] = getattr(spdx.software_SoftwarePurpose, purpose)
        return self.add(spdx.software_File(**values))

    def relation(self, source, kind, targets, *, comment=None):
        targets = sorted({target._id: target for target in targets}.values(), key=lambda item: item._id)
        if not targets:
            return None
        identity = [source._id, kind, [target._id for target in targets], comment]
        return self.add(spdx.Relationship(
            _id=self.identifier("relationship", identity), creationInfo=self.creation,
            from_=source, relationshipType=getattr(spdx.RelationshipType, kind),
            to=targets, **({"comment": comment} if comment else {})))

    def license(self, owner, expression, texts, raw=None):
        if not expression or str(expression).upper() in ("CLOSED", "NONE", "NOASSERTION", "UNKNOWN"):
            self.unknown_licenses.append({"element": owner._id, "declaration": raw})
            return None
        expression = str(expression).strip()
        if not _valid_license_expression(expression):
            self.unknown_licenses.append({"element": owner._id, "declaration": raw or expression,
                                          "malformed_license_expression": expression})
            return None
        refs = sorted(set(_CUSTOM_LICENSE.findall(expression)))
        missing = [name for name in refs if not isinstance(texts.get(name), str) or not texts[name]]
        if missing:
            self.unknown_licenses.append({"element": owner._id, "declaration": raw or expression,
                                          "missing_custom_license_text": missing})
            return None
        mappings = []
        for name in refs:
            text = self.add(spdx.simplelicensing_SimpleLicensingText(
                _id=self.identifier("license-text", [name, texts[name]]), creationInfo=self.creation,
                name=name, simplelicensing_licenseText=texts[name]))
            mappings.append(self.add(spdx.DictionaryEntry(
                _id=self.identifier("license-map", [name, text._id]), key=name, value=text._id)))
        lic = self.add(spdx.simplelicensing_LicenseExpression(
            _id=self.identifier("license-expression", [expression, {name: texts[name] for name in refs}, self.license_list_version]),
            creationInfo=self.creation, simplelicensing_licenseExpression=expression,
            simplelicensing_licenseListVersion=self.license_list_version,
            simplelicensing_customIdToUri=mappings))
        self.relation(owner, "hasDeclaredLicense", [lic])
        return lic


def _inventory_files(inventory):
    result = {}
    for entry in inventory.get("files", []):
        path = _path(entry["path"], root=entry.get("kind") == "directory")
        if path in result:
            raise ValueError("Duplicate delivered inventory path: " + path)
        if entry.get("kind") in ("regular", "hardlink"):
            _sha256(entry.get("sha256"))
        result[path] = entry
    return result


def _add_file_inventory(graph, scope, entries, evidence=None):
    result = {}
    for path, entry in sorted(entries.items()):
        details = {key: value for key, value in entry.items() if key not in ("path", "size", "sha256")}
        if evidence and path in evidence:
            details["apk_installed_database_records"] = evidence[path]
        display = ("/" if path == "." else "/" + path) if scope == "rootfs" else "initramfs:/" + path
        result[path] = graph.file(scope, [path, entry], name=display,
                                  sha256=entry.get("sha256"), size=entry.get("size"),
                                  kind="directory" if entry.get("kind") == "directory" else "file",
                                  comment=details)
    return result


def _component_key(component):
    return [component.get("recipe", component["name"]), component["name"], component.get("version")]


def _component_ownership(components, files):
    claims = defaultdict(list)
    for index, component in enumerate(components):
        paths = set(component.get("payload_paths", []))
        paths.update(module["path"] for module in component.get("modules", []))
        for value in sorted(paths):
            prefix = _path(value)
            for path in files:
                if path == prefix or path.startswith(prefix + "/"):
                    claims[path].append((len(PurePosixPath(prefix).parts), index))
    result = {}
    for path, candidates in claims.items():
        score = max(candidate[0] for candidate in candidates)
        owners = {index for candidate_score, index in candidates if candidate_score == score}
        if len(owners) != 1:
            raise ValueError("Ambiguous custom component ownership: " + path)
        result[path] = next(iter(owners))
    return result


def _apk_license(package):
    if "license_expression" in package:
        return package["license_expression"]
    raw = package.get("license") or ""
    if raw.upper() in ("CLOSED", "NONE", "NOASSERTION", "UNKNOWN"):
        return None
    return raw.replace(" & ", " AND ").replace(" | ", " OR ") or None


def _add_release(graph, owner, component, release, files, file_nodes):
    """Merge verified release dependencies, without importing archive-only files."""
    if release.get("schema_version") != 1 or release.get("spdx_version") != "SPDX-2.3":
        raise ValueError("Unsupported normalized release SBOM")
    packages = {package["spdx_id"]: package for package in release["packages"]}
    main = release["main_package"]
    if main not in packages or packages[main]["name"] != component["recipe"]:
        raise ValueError("Release SBOM main package differs from component")
    relationships = release["relationships"]
    reached, pending = {main}, [main]
    while pending:
        source = pending.pop()
        for relation in relationships:
            if relation["source"] != source or relation["type"] != "STATIC_LINK":
                continue
            target = relation["target"]
            if target not in packages:
                raise ValueError("Release static-link target is not a package")
            if target not in reached:
                reached.add(target)
                pending.append(target)
    nodes, notes = {main: owner}, list(release.get("limitations", []))
    for identifier in sorted(reached):
        package = packages[identifier]
        details = {key: package.get(key) for key in (
            "source_info", "comment", "external_refs", "checksums", "package_file_name", "download_location",
            "license_concluded", "license_info_from_files")}
        details.update(release_document=release["document_namespace"],
                       release_document_sha256=release["document_sha256"],
                       release_package_id=identifier)
        if identifier == main:
            node = owner
            if package.get("version") is not None:
                node.software_packageVersion = str(package["version"])
            node.software_sourceInfo = _json(details)
        else:
            node = graph.package("release-dependency", [_component_key(component), release["document_namespace"], identifier],
                name=package["name"], version=package.get("version"), purpose="library",
                purl=package.get("purl"),
                homepage=package.get("homepage") if package.get("homepage") not in ("NONE", "NOASSERTION") else None,
                source_info=_json(details), comment=package.get("comment"))
            nodes[identifier] = node
            if package.get("comment"):
                notes.append(component["name"] + " dependency " + package["name"] + ": " + package["comment"])
        if package.get("purl"):
            node.software_packageUrl = package["purl"]
        if package.get("checksums", {}).get("SHA256"):
            node.verifiedUsing = [graph.hash(package["checksums"]["SHA256"])]
        location = package.get("download_location")
        if location and location not in ("NONE", "NOASSERTION"):
            node.software_downloadLocation = _uri(location)
        declared = package.get("license_declared")
        graph.license(node, declared, release.get("license_texts", {}), declared)
    release_files, verified = {}, 0
    for record in release.get("files", []):
        path = record.get("installed_path")
        if path is None:
            continue
        path = _path(path)
        if not record.get("installed_verified") or path not in files or files[path].get("sha256") != _sha256(record["sha256"]):
            raise ValueError("Release file differs from final installed payload: " + path)
        release_files[record["spdx_id"]] = file_nodes[path]
        verified += 1
    for relation in relationships:
        source = nodes.get(relation["source"])
        if source is None:
            continue
        if relation["type"] == "STATIC_LINK":
            graph.relation(source, "hasStaticLink", [nodes[relation["target"]]], comment=relation.get("comment"))
        elif relation["type"] == "CONTAINS" and relation["target"] in release_files:
            graph.relation(source, "contains", [release_files[relation["target"]]])
    return {"dependencies": {node._id for identifier, node in nodes.items() if identifier != main},
            "limitations": notes, "verified_files": verified}


def render_product(inventory: dict, components: list[dict], artifacts: dict, output_path,
                   *, machine, version, build_id, epoch, license_list_version) -> dict:
    """Write deterministic SPDX 3.0.1 JSON-LD and return coverage counts."""
    for key, value in (("machine", machine), ("version", version), ("build_id", build_id)):
        if artifacts.get(key) is not None and artifacts[key] != value:
            raise ValueError("Image manifest " + key + " differs from the SBOM build")
    license_list_version = _license_version(license_list_version)
    components = sorted(components, key=lambda component: (_json(_component_key(component)), _digest(component)))
    keys = [_json(_component_key(component)) for component in components]
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate component identity in the product SBOM")
    seed = {"inventory": inventory, "components": components, "artifacts": artifacts,
            "machine": machine, "version": version, "build_id": build_id, "epoch": int(epoch),
            "license_list_version": license_list_version}
    graph = _Graph("urn:naos:sbom:" + _digest(seed) + ":", epoch, license_list_version)
    flags = {key: artifacts[key] for key in ("secure_boot", "system_encryption", "data_encryption",
              "layout_compatible", "userdata_filesystem", "userdata_initialization") if key in artifacts}
    product = graph.package("product", [machine, version, build_id], name="NAOS " + str(machine),
                            version=version, purpose="operatingSystem",
                            comment=_json({"machine": machine, "build_id": build_id, "image_policy": flags,
                                           "scope": "Actual build outputs; opaque binary internals are not inferred"}))
    rootfs = graph.package("rootfs", [machine, version], name="NAOS delivered Alpine root filesystem",
                           version=version, purpose="filesystemImage",
                           comment="File hashes describe the final merged plaintext rootfs before optional encryption.")
    graph.relation(product, "contains", [rootfs])
    artifact_nodes, artifact_records, final_artifact_names = {}, {}, set()

    def artifact(record, *, final=False):
        name = _path(record["path"])
        checksum = _sha256(record["sha256"])
        normalized = {"sha256": checksum, "size": record.get("size")}
        if name in artifact_records and artifact_records[name] != normalized:
            raise ValueError("Conflicting artifact hashes or sizes: " + name)
        if name not in artifact_nodes:
            artifact_nodes[name] = graph.file("artifact", [name, normalized], name=name,
                                              sha256=checksum, size=record.get("size"),
                                              comment={"role": "final-image-output" if final else "native-deployment-output"})
            artifact_records[name] = normalized
        if final:
            final_artifact_names.add(name)
        return artifact_nodes[name]

    for name, record in sorted(artifacts.get("files", {}).items()):
        artifact({"path": name, **record}, final=True)
    if not final_artifact_names:
        raise ValueError("Product SBOM has no final image artifact hashes")
    graph.relation(product, "hasDistributionArtifact", [artifact_nodes[name] for name in sorted(final_artifact_names)])
    if "system.img" in artifact_nodes:
        graph.relation(rootfs, "hasDistributionArtifact", [artifact_nodes["system.img"]])

    files = _inventory_files(inventory)
    owners = _component_ownership(components, files)
    apk_evidence = defaultdict(list)
    for package in inventory.get("packages", []):
        for record in package.get("files", []):
            path = _path(record["path"])
            if path in files and record.get("sha256") is not None and record["sha256"] != files[path].get("sha256"):
                raise ValueError("APK final-file hash differs from final root inventory: " + path)
            apk_evidence[path].append({"package": package["name"], "version": package["version"],
                                       "status": record.get("status"),
                                       "original_checksum": record.get("apk_original_checksum"),
                                       "custom_component_overrides_owner": path in owners})
    for records in apk_evidence.values():
        records.sort(key=_json)
    file_nodes = _add_file_inventory(graph, "rootfs", files, apk_evidence)
    graph.relation(rootfs, "contains", list(file_nodes.values()))
    package_nodes, apk_owners = [], set()
    for package in sorted(inventory.get("packages", []), key=lambda item: (item["name"], item["version"], item.get("architecture", ""))):
        identity = [package["name"], package["version"], package.get("architecture", "")]
        info = {key: package.get(key) for key in ("origin", "aports_commit", "apk_control_checksum", "depends", "license")}
        info["missing_declared_files"] = sorted(record["path"] for record in package.get("files", []) if record["path"] not in files)
        purl = "pkg:apk/alpine/" + quote(package["name"], safe="") + "@" + quote(package["version"], safe="")
        if package.get("architecture"):
            purl += "?arch=" + quote(package["architecture"], safe="")
        pkg = graph.package("apk", identity, name=package["name"], version=package["version"],
                            purl=purl, homepage=package.get("homepage"),
                            source_info=_json(info), comment="Installed Alpine APK record; raw license: " + (package.get("license") or "NOASSERTION"))
        graph.license(pkg, _apk_license(package), package.get("license_texts", {}), package.get("license"))
        package_nodes.append(pkg)
        owned = []
        for record in package.get("files", []):
            path = _path(record["path"])
            if path in file_nodes and path not in owners:
                owned.append(file_nodes[path])
                apk_owners.add(path)
        graph.relation(pkg, "contains", owned)
        archive = package.get("archive")
        if archive and archive.get("sha256"):
            archive_name = _path(archive["name"])
            archive_file = graph.file("apk-input", [identity, archive], name=archive_name,
                                       sha256=archive["sha256"], size=archive.get("size"), purpose="archive",
                                       comment={"role": "original APK input, not final root-file bytes", "uri": archive.get("uri")})
            graph.relation(pkg, "hasDistributionArtifact", [archive_file])
    graph.relation(rootfs, "contains", package_nodes)

    component_nodes, opaque, module_paths, initramfs_packages, initramfs_files = [], [], set(), 0, 0
    source_nodes = {}

    def add_sources(owner, sources):
        for source in sorted(sources, key=_json):
            uri = _uri(source["uri"])
            identity = [uri, source.get("revision"), source.get("sha256")]
            key = _json(identity)
            if key not in source_nodes:
                src = graph.package("source", identity, name=uri, version=source.get("revision"),
                                    purpose="source", source_info=_json(source))
                src.software_downloadLocation = uri
                if source.get("sha256"):
                    src.verifiedUsing = [graph.hash(source["sha256"])]
                source_nodes[key] = src
            graph.relation(source_nodes[key], "generates", [owner])

    static_go_dependencies, opaque_firmware, validated_payloads = set(), set(), set()
    release_dependencies, release_notes, release_components, release_verified_files = set(), [], 0, 0
    for index, component in enumerate(components):
        recipe = component.get("recipe", component["name"])
        if component.get("is_native") or recipe.endswith("-native"):
            raise ValueError("Host tools must not be supplied as runtime product components: " + recipe)
        payload_hashes = component.get("payload_hashes", {})
        if not isinstance(payload_hashes, dict):
            raise ValueError("Component payload hashes must be a path/SHA256 mapping: " + recipe)
        for value, checksum in sorted(payload_hashes.items()):
            path = _path(value)
            if path not in files or files[path].get("sha256") != _sha256(checksum):
                raise ValueError("Final component payload differs from deployed sidecar: " + recipe + ": " + path)
            if owners.get(path) != index:
                raise ValueError("Hashed component payload is not owned by its producer: " + recipe + ": " + path)
            validated_payloads.add(path)
        details = {"recipe": recipe, "raw_license": component.get("license")}
        if component.get("cve_product"):
            details["cve_product"] = component["cve_product"]
        if component.get("prebuilt"):
            details["prebuilt"] = component["prebuilt"]
        release = component.get("release_sbom")
        is_container = recipe == "naos-container"
        if release:
            details["release_sbom"] = {key: release.get(key) for key in (
                "document_namespace", "document_sha256", "archive_verified", "limitations")}
            details["archive_only_release_paths"] = sorted(record["path"] for record in release.get("files", [])
                                                          if record.get("installed_path") is None)
        elif is_container:
            details["dependency_coverage"] = "Opaque prebuilt programs: internal static dependencies were not supplied; no internal dependency graph is asserted."
            opaque.append(component["name"])
        purpose = ("operatingSystem" if "modules" in component else "firmware" if "firmware_inputs" in component else
                   "filesystemImage" if "image_inventory" in component else None)
        pkg = graph.package("component", _component_key(component), name=component["name"],
                            version=component.get("version"), homepage=component.get("homepage"),
                            purpose=purpose, comment=_json(details))
        component_nodes.append(pkg)
        texts = component.get("license_texts", {})
        if not release:
            graph.license(pkg, component.get("license_expression"), texts, component.get("license"))
        graph.relation(pkg, "contains", [file_nodes[path] for path, owner in owners.items() if owner == index])
        add_sources(pkg, component.get("sources", []))
        configuration = component.get("configuration")
        if configuration:
            cfg = graph.file("configuration", [_component_key(component), configuration], name=configuration["file"],
                              sha256=configuration["sha256"], size=configuration.get("size"), purpose="configuration")
            graph.relation(cfg, "configures", [pkg])
        for output in component.get("artifacts", []):
            graph.relation(pkg, "hasDistributionArtifact", [artifact(output)])
        if component.get("module_archive"):
            graph.relation(pkg, "hasDistributionArtifact", [artifact(component["module_archive"])])
        for module in component.get("modules", []):
            path = _path(module["path"])
            if path in module_paths or path not in files:
                raise ValueError("Missing or duplicate final kernel module: " + path)
            module_paths.add(path)
            if files[path].get("sha256") != _sha256(module["sha256"]) or files[path].get("size") != module["size"]:
                raise ValueError("Kernel module hash/size differs from deployed sidecar: " + path)
        if "modules" in component and "boot.img" in artifact_nodes:
            graph.relation(artifact_nodes["boot.img"], "contains", [pkg])
        for firmware in component.get("firmware_inputs", []):
            opaque_firmware.add(firmware["role"])
            info = {key: firmware.get(key) for key in ("source", "source_revision", "path", "role", "selection", "version_source", "in_artifacts")}
            info["dependency_coverage"] = "Vendor binary internals are not described by supplied metadata."
            fw = graph.package("firmware", [_component_key(component), firmware],
                               name="Rockchip " + firmware["role"] + " firmware", version=firmware.get("version"),
                               purpose="firmware", source_info=_json(info),
                               comment="USB flashing firmware only." if firmware["role"] == "usbplug" else "Selected Rockchip prebuilt firmware.")
            fwfile = graph.file("firmware-input", firmware, name="rkbin/" + _path(firmware["path"]),
                                sha256=firmware["sha256"], size=firmware.get("size"), purpose="firmware")
            graph.relation(fw, "contains", [fwfile])
            vendor_expr = firmware.get("license_expression")
            if vendor_expr is None:
                vendor_expr = next((key for key in ("LicenseRef-Rockchip-rkbin", "LicenseRef-ROCKCHIP") if key in texts), None)
            graph.license(fw, vendor_expr, texts, "rkbin supplier license")
            graph.relation(pkg, "contains", [fw])
            for name in firmware.get("in_artifacts", []):
                if name not in artifact_nodes:
                    raise ValueError("Firmware names an unrecorded build artifact: " + name)
                graph.relation(artifact_nodes[name], "contains", [fw])
        spl = component.get("spl")
        if spl:
            if spl.get("source_built") is not True:
                raise ValueError("SPL provenance must identify a source-built input")
            splpkg = graph.package("spl", [_component_key(component), spl], name="U-Boot source-built SPL",
                                   version=component.get("version"), purpose="firmware", source_info=_json(spl))
            splfile = graph.file("spl-input", spl, name="u-boot/" + _path(spl["path"]),
                                 sha256=spl["sha256"], size=spl.get("size"), purpose="firmware")
            graph.relation(splpkg, "contains", [splfile])
            graph.relation(pkg, "generates", [splpkg])
            for name in spl.get("in_artifacts", []):
                if name not in artifact_nodes:
                    raise ValueError("SPL names an unrecorded build artifact: " + name)
                graph.relation(artifact_nodes[name], "contains", [splpkg])
        prebuilt = component.get("prebuilt", {})
        if release:
            imported = _add_release(graph, pkg, component, release, files, file_nodes)
            release_dependencies.update(imported["dependencies"])
            release_notes.extend(imported["limitations"])
            release_verified_files += imported["verified_files"]
            release_components += 1
            if recipe == "naos-core":
                static_go_dependencies.update(imported["dependencies"])
        elif recipe == "naos-core" and prebuilt:
            binary = prebuilt.get("binary_path", "usr/bin/naos-core")
            if prebuilt.get("binary_sha256") and (binary not in files or files[binary].get("sha256") != prebuilt["binary_sha256"]):
                raise ValueError("Final Core binary differs from its independent build metadata")
            go_version = prebuilt.get("go_version")
            if go_version:
                runtime = graph.package("go-runtime", go_version, name="Go standard library and runtime",
                                        version=str(go_version).removeprefix("go"), purpose="library",
                                        comment="Statically linked Go runtime/standard library, not an installed compiler.")
                graph.license(runtime, "BSD-3-Clause", {}, "BSD-3-Clause")
                graph.relation(pkg, "hasStaticLink", [runtime])
                static_go_dependencies.add(runtime._id)
            modules = prebuilt.get("modules", {})
            if not isinstance(modules, dict):
                raise ValueError("Core build modules must be a module-path/version mapping")
            for name, module_version in sorted(modules.items()):
                dependency = graph.package("go-module", [name, module_version], name=name, version=module_version,
                                           purpose="library", purl="pkg:golang/" + quote(name, safe="/") + "@" + quote(str(module_version), safe=""))
                expression = prebuilt.get("module_licenses", {}).get(name, _KNOWN_GO_LICENSES.get(name))
                graph.license(dependency, expression, texts, expression)
                graph.relation(pkg, "hasStaticLink", [dependency])
                static_go_dependencies.add(dependency._id)
        if "image_inventory" in component or "image_packages" in component:
            if not isinstance(component.get("image_inventory"), dict) or not isinstance(component.get("image_packages"), list):
                raise ValueError("Initramfs requires actual file inventory and installed package metadata")
            image_files = _inventory_files(component["image_inventory"])
            image_nodes = _add_file_inventory(graph, "initramfs-" + _digest(_component_key(component)), image_files)
            initramfs_files += len(image_nodes)
            graph.relation(pkg, "contains", list(image_nodes.values()))
            if "boot.img" in artifact_nodes:
                graph.relation(artifact_nodes["boot.img"], "contains", [pkg])
            for installed in sorted(component["image_packages"], key=lambda item: (item["name"], item["version"])):
                identity = [_component_key(component), installed["name"], installed["version"], installed.get("architecture")]
                installed_pkg = graph.package("initramfs-package", identity, name=installed["name"], version=installed["version"],
                                               homepage=installed.get("homepage"),
                                               source_info=_json({key: installed.get(key) for key in ("source_recipe", "architecture", "license", "sources", "homepage", "cve_product")}),
                                               comment="Actual initramfs installed package; not an Alpine rootfs package.")
                graph.license(installed_pkg, installed.get("license_expression"), installed.get("license_texts", {}), installed.get("license"))
                add_sources(installed_pkg, installed.get("sources", []))
                graph.relation(pkg, "contains", [installed_pkg])
                graph.relation(installed_pkg, "contains", [image_nodes[_path(path)] for path in installed.get("files", []) if _path(path) in image_nodes])
                initramfs_packages += 1
    actual_modules = {path for path, record in files.items() if _MODULE.search(path) and record.get("kind") != "directory"}
    if actual_modules != module_paths:
        raise ValueError("Final rootfs kernel modules are not fully reconciled with deployed sidecars")
    graph.relation(product, "contains", component_nodes)
    unowned = sorted(path for path, entry in files.items()
                     if entry.get("kind") in ("regular", "hardlink", "symlink") and path not in owners and path not in apk_owners)
    limitations = []
    limitations.extend(sorted(set(release_notes)))
    if opaque:
        limitations.append("Container internal static dependencies were not supplied; no internal dependency graph is asserted.")
    if opaque_firmware:
        limitations.append("Rockchip firmware inner components were not supplied: " + ", ".join(sorted(opaque_firmware)) + ".")
    if static_go_dependencies:
        limitations.append("Core static-link edges cover its Go runtime/standard library and reported Go modules; unreported Core dependencies are not inferred.")
    if unowned:
        limitations.append(str(len(unowned)) + " delivered rootfs files have no asserted APK/custom-component owner.")
    if graph.unknown_licenses:
        limitations.append(str(len(graph.unknown_licenses)) + " supplier license declarations are unasserted or lack custom-license text; no terms were invented.")
    document_license = graph.add(spdx.simplelicensing_LicenseExpression(
        _id=graph.identifier("document-license", "CC0-1.0"), creationInfo=graph.creation,
        simplelicensing_licenseExpression="CC0-1.0", simplelicensing_licenseListVersion=license_list_version))
    bom = graph.add(spdx.software_Sbom(
        _id=graph.identifier("sbom", [machine, version, build_id]), name="NAOS product build SBOM",
        creationInfo=graph.creation, rootElement=[product], software_sbomType=[spdx.software_SbomType.build],
        comment=_json({"opaque_components": sorted(opaque), "unowned_rootfs_paths": unowned,
                       "unknown_license_declarations": graph.unknown_licenses, "limitations": limitations})))
    doc = graph.add(spdx.SpdxDocument(
        _id=graph.identifier("document", [machine, version, build_id]), name="NAOS " + str(version) + " " + str(machine),
        creationInfo=graph.creation, dataLicense=document_license, rootElement=[bom],
        profileConformance=[spdx.ProfileIdentifierType.core, spdx.ProfileIdentifierType.software, spdx.ProfileIdentifierType.simpleLicensing]))
    elements = [obj for obj in graph.objects.values() if isinstance(obj, spdx.Element) and obj is not doc]
    doc.element = sorted(elements, key=lambda obj: obj._id)
    bom.element = [obj for obj in doc.element if obj is not bom]
    model = spdx.SHACLObjectSet(graph.objects.values())
    if missing := model.link():
        raise ValueError("Unresolved SPDX references: " + ", ".join(sorted(missing)))
    encoded = _canonical(spdx.JSONLDSerializer().serialize_data(model, force_at_graph=True))
    decoded = spdx.SHACLObjectSet()
    spdx.JSONLDDeserializer().deserialize_data(encoded, decoded)
    if missing := decoded.link():
        raise ValueError("Round-trip SPDX references are not closed: " + ", ".join(sorted(missing)))
    payload = (json.dumps(encoded, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent, prefix=output.name + ".", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(payload)
            stream.flush()
            os.chmod(temporary, 0o644)
            os.replace(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)
    return {"format": "SPDX-3.0.1", "document": doc._id, "root_product": product._id,
            "machine": machine, "version": version, "build_id": build_id,
            "objects": len(decoded.objects), "sha256": hashlib.sha256(payload).hexdigest(), "size": len(payload),
            "rootfs_files": len(file_nodes), "apk_packages": len(package_nodes), "components": len(component_nodes),
            "kernel_modules": len(module_paths), "initramfs_packages": initramfs_packages,
            "initramfs_files": initramfs_files, "final_artifacts": len(final_artifact_names),
            "custom_owned_rootfs_files": len(owners), "unowned_rootfs_files": len(unowned),
            "validated_component_payload_files": len(validated_payloads),
            "opaque_components": sorted(opaque), "unknown_licenses": len(graph.unknown_licenses),
            "opaque_firmware_roles": sorted(opaque_firmware), "static_go_dependency_packages": len(static_go_dependencies),
            "release_sbom_components": release_components, "release_dependency_packages": len(release_dependencies),
            "release_verified_payload_files": release_verified_files,
            "limitations": limitations,
            "roundtrip_references_closed": True}
