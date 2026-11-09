"""Collect Rockchip build inputs after deployment, without executing SDK tools.

Hashes describe delivered file bytes. Compressed kernel modules retain their
on-disk encoding; firmware versions are taken only from selected filenames.
No signing keys, credentials or system-encryption keys are inputs here.
"""

import configparser
import gzip
import hashlib
import json
import lzma
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import struct
import tarfile
import tempfile


_CHUNK = 1024 * 1024
_TEXT_LIMIT = 4 * 1024 * 1024
_MODULE = re.compile(r"\.ko(?:\.(gz|xz|zst))?$")
_KEY_DIRECTORIES = frozenset(("key", "keys", "secrets", "credentials", ".ssh", ".gnupg"))
_KEY_SUFFIXES = (".key", ".pem", ".p12", ".pfx", ".pubkey")


def _deny_key_path(path):
    if any(part.lower() in _KEY_DIRECTORIES for part in Path(path).parts):
        raise ValueError("Refusing a key or credential path as an SBOM input")
    if Path(path).name.lower().endswith(_KEY_SUFFIXES):
        raise ValueError("Refusing a key file as an SBOM input")


def _safe_file(path):
    path = Path(path)
    _deny_key_path(path)
    _deny_key_path(path.resolve())
    if not path.is_file():
        raise ValueError("Missing SBOM input: " + path.name)
    return path


def _relative(value):
    if not isinstance(value, str) or not value or "\0" in value or value.startswith("/"):
        raise ValueError("Expected a relative SBOM input path")
    if ".." in value.split("/"):
        raise ValueError("SBOM input path escapes its root")
    path = PurePosixPath(value)
    if str(path) == ".":
        raise ValueError("Empty SBOM input path")
    return str(path)


def _inside(root, relative):
    _deny_key_path(root)
    root = Path(root).resolve()
    path = root / _relative(relative)
    _deny_key_path(path)
    if not path.resolve().is_relative_to(root):
        raise ValueError("SBOM input symlink escapes its root")
    return _safe_file(path)


def _digest(stream, limit=None):
    checksum = hashlib.sha256()
    size = 0
    while block := stream.read(_CHUNK):
        size += len(block)
        if limit is not None and size > limit:
            raise ValueError("SBOM input exceeds its expected size")
        checksum.update(block)
    return {"sha256": checksum.hexdigest(), "size": size}


def _record(path, label):
    with _safe_file(path).open("rb") as stream:
        return {"path": _relative(label), **_digest(stream)}


def _text(path):
    path = _safe_file(path)
    if path.stat().st_size > _TEXT_LIMIT:
        raise ValueError("SBOM configuration or manifest is unexpectedly large")
    return path.read_text(encoding="utf-8")


def _configuration(path, outdir, filename):
    path = _safe_file(path)
    if path.stat().st_size > _TEXT_LIMIT:
        raise ValueError("SBOM configuration is unexpectedly large")
    content = path.read_bytes()
    content.decode("utf-8")
    outdir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=outdir, prefix=filename + ".", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(content)
            stream.flush()
            os.chmod(temporary, 0o644)
            os.replace(temporary, outdir / filename)
        finally:
            temporary.unlink(missing_ok=True)
    return {"file": filename, "sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}


def _config_values(path):
    result = {}
    for line in _text(path).splitlines():
        if line.startswith("CONFIG_") and "=" in line:
            name, value = line.split("=", 1)
            result[name] = json.loads(value) if value.startswith('"') else value
    return result


def _deploy_record(deploy, relative):
    relative = _relative(relative)
    return _record(_inside(deploy, relative), relative)


def _deployment_directory(d):
    # The exporter runs as a do_deploy postfunc, before deploy's sstate copy.
    # When DEPLOYDIR is set, never fall back to possibly stale shared outputs.
    return Path(d.getVar("DEPLOYDIR") or d.getVar("DEPLOY_DIR_IMAGE"))


def _kernel(data, d, outdir):
    build = Path(d.getVar("B"))
    deploy = _deployment_directory(d)
    machine = d.getVar("MACHINE")
    release = _text(build / "include/config/kernel.release").strip()
    if not release or "/" in release or "\n" in release:
        raise ValueError("Invalid built kernel release")
    archive_name = d.getVar("NAOS_MODULES_FILE") or "modules-" + machine + ".tgz"
    archive = _inside(deploy, archive_name)
    modules, seen = [], set()
    with tarfile.open(archive, "r|*") as package:
        for member in package:
            if member.isdir() and member.name in (".", "./"):
                continue
            path = _relative(member.name)
            match = _MODULE.search(path)
            if not match:
                continue
            if not member.isfile() or path in seen:
                raise ValueError("Duplicate or non-regular kernel module: " + path)
            parts = PurePosixPath(path).parts
            prefix = 3 if parts[:2] == ("lib", "modules") else 4 if parts[:3] == ("usr", "lib", "modules") else 0
            if not prefix or len(parts) <= prefix or parts[prefix - 1] != release:
                raise ValueError("Deployed module does not match the built kernel release: " + path)
            seen.add(path)
            with package.extractfile(member) as stream:
                metadata = _digest(stream, member.size)
            if metadata["size"] != member.size:
                raise ValueError("Truncated module in deployment archive")
            modules.append({"path": path, "compression": match.group(1) or "none", **metadata})
    image = d.getVar("NAOS_KERNEL_FILE") or d.getVar("KERNEL_IMAGETYPE") or "Image"
    dtbs = (d.getVar("KERNEL_DEVICETREE") or machine + ".dtb").split()
    paths = [image]
    for dtb in dtbs:
        dtb = _relative(dtb)
        candidates = [dtb, PurePosixPath(dtb).name]
        selected = next((name for name in candidates if (deploy / name).is_file()), None)
        if selected is None:
            raise ValueError("Missing deployed kernel DTB: " + dtb)
        paths.append(selected)
    paths.append(d.getVar("NAOS_RESOURCE_FILE") or "resource.img")
    data["kernel_release"] = release
    data["configuration"] = _configuration(build / ".config", outdir, "kernel.config")
    data["modules"] = sorted(modules, key=lambda item: item["path"])
    data["module_archive"] = _deploy_record(deploy, archive_name)
    data["artifacts"] = [_deploy_record(deploy, path) for path in dict.fromkeys(paths)]


def _ini(path):
    result = configparser.ConfigParser(interpolation=None, strict=True)
    result.optionxform = str
    result.read_string(_text(path))
    return result


def _firmware_path(rkbin, value, role):
    relative = _relative(value)
    if not relative.startswith("bin/") or ("_" + role + "_") not in PurePosixPath(relative).name:
        raise ValueError("Selected firmware path does not identify " + role)
    return _inside(rkbin, relative), relative


def _firmware(rkbin, value, role, revision, artifacts, selection):
    path, relative = _firmware_path(rkbin, value, role)
    version = re.search(r"_v([0-9]+(?:\.[0-9]+)+)(?=[._-]|$)", path.name)
    return {"role": role, "source": "rkbin", "source_revision": revision,
            "version": version.group(1) if version else None,
            "version_source": "selected filename" if version else None,
            "in_artifacts": artifacts, "selection": selection, **_record(path, relative)}


def _decoded_record(path, limit):
    path = _safe_file(path)
    opener = gzip.open if path.suffix == ".gz" else lzma.open if path.suffix in (".xz", ".lzma") else open
    with opener(path, "rb") as stream:
        return _digest(stream, limit)


def _elf_segments(path):
    path = _safe_file(path)
    if path.stat().st_size > 16 * _CHUNK:
        raise ValueError("BL31 ELF is unexpectedly large")
    content = path.read_bytes()
    if len(content) < 64 or content[:6] != b"\x7fELF\x02\x01":
        raise ValueError("BL31 must be an ELF64 little-endian input")
    fields = struct.unpack_from("<16sHHIQQQIHHHHHH", content)
    phoff, entsize, count = fields[5], fields[9], fields[10]
    if entsize < 56 or phoff + entsize * count > len(content):
        raise ValueError("Invalid BL31 ELF program headers")
    segments = {}
    for index in range(count):
        kind, _, offset, _, address, size, _, _ = struct.unpack_from("<IIQQQQQQ", content, phoff + index * entsize)
        if kind != 1 or not size:
            continue
        if offset + size > len(content) or address in segments:
            raise ValueError("Invalid BL31 ELF load segment")
        segments[address] = {"size": size, "sha256": hashlib.sha256(content[offset:offset + size]).hexdigest()}
    if not segments:
        raise ValueError("BL31 has no loadable firmware segments")
    return segments


def _verify_trust_inputs(source, bl32):
    references = re.findall(r'\bdata\s*=\s*/incbin/\(\s*"([^"\n]+)"\s*\)\s*;', _text(source / "fit/u-boot.its"))
    if not references:
        raise ValueError("No generated U-Boot FIT input manifest is available")
    segments = _elf_segments(source / "bl31.elf")
    selected, tee = set(), []
    for relative in references:
        basename = PurePosixPath(relative).name
        atf = re.fullmatch(r"bl31_0x([0-9a-fA-F]+)\.bin(?:\.(?:gz|xz|lzma))?", basename)
        if atf:
            address = int(atf.group(1), 16)
            if address not in segments or address in selected:
                raise ValueError("Generated FIT selects an unexpected BL31 segment")
            if _decoded_record(_inside(source, relative), segments[address]["size"]) != segments[address]:
                raise ValueError("FIT BL31 payload differs from the selected original ELF")
            selected.add(address)
        elif re.fullmatch(r"tee\.bin(?:\.(?:gz|xz|lzma))?", basename):
            if bl32 is None:
                raise ValueError("FIT includes BL32 while selected trust INI disables it")
            if _decoded_record(_inside(source, relative), bl32["size"]) != {key: bl32[key] for key in ("size", "sha256")}:
                raise ValueError("FIT BL32 payload differs from the selected input")
            tee.append(_relative(relative))
        elif not re.fullmatch(r"(?:u-boot-nodtb\.bin(?:\.(?:gz|xz|lzma))?|u-boot\.dtb)", basename):
            raise ValueError("Unrecognized generated U-Boot FIT input: " + basename)
    if selected != set(segments) or (bl32 is not None and len(tee) != 1):
        raise ValueError("Generated FIT does not select the expected BL31/BL32 inputs")
    return {"bl31": [path for path in references if PurePosixPath(path).name.startswith("bl31_")], "bl32": tee}


def _uboot(data, d, outdir):
    source = Path(d.getVar("S"))
    rkbin = source.parent / "rkbin"
    deploy = _deployment_directory(d)
    values = _config_values(source / ".config")
    soc = values.get("CONFIG_SYS_SOC", "")
    if not re.fullmatch(r"rk[0-9a-z]+", soc):
        raise ValueError("Cannot establish the selected Rockchip SoC from U-Boot configuration")
    flags = shlex.split(d.getVar("NAOS_UBOOT_SIGN_ARGS") or "")
    if any(flag not in ("--no-sign", "--burn-key-hash") for flag in flags):
        raise ValueError("Custom U-Boot packing arguments require explicit SBOM selection support")
    compile_body = d.getVar("do_compile") or ""
    if "--spl-new" not in compile_body or "--idblock --spl" not in compile_body:
        raise ValueError("Cannot prove the source-built SPL packing policy")
    revision = d.getVar("SRCREV_rkbin") or ""
    if not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", revision):
        raise ValueError("A fixed rkbin source revision is required for firmware provenance")
    loader_name = values.get("CONFIG_LOADER_INI") or soc.upper() + "MINIALL.ini"
    trust_name = values.get("CONFIG_TRUST_INI") or soc.upper() + "TRUST.ini"
    if any(PurePosixPath(name).name != name for name in (loader_name, trust_name)):
        raise ValueError("Selected loader/trust INI must be inside its rkbin configuration directory")
    loader_path, trust_path = "RKBOOT/" + loader_name, "RKTRUST/" + trust_name
    loader, trust = _ini(_inside(rkbin, loader_path)), _ini(_inside(rkbin, trust_path))
    if loader.get("CHIP_NAME", "NAME") != soc.upper():
        raise ValueError("Loader configuration does not match the built SoC")
    if loader.getint("CODE471_OPTION", "NUM") != 1 or loader.getint("CODE472_OPTION", "NUM") != 1:
        raise ValueError("Multiple DDR/USB loader inputs require explicit SBOM selection support")
    if (loader.getint("LOADER_OPTION", "NUM") != 2
            or {loader.get("LOADER_OPTION", "LOADER1"), loader.get("LOADER_OPTION", "LOADER2")} != {"FlashData", "FlashBoot"}):
        raise ValueError("Additional loader firmware requires explicit SBOM selection support")
    for section in trust.sections():
        if re.fullmatch(r"BL[0-9]+_OPTION", section) and section not in ("BL31_OPTION", "BL32_OPTION"):
            if trust.getint(section, "SEC", fallback=0):
                raise ValueError("Additional trusted firmware requires explicit SBOM selection support")
    ddr = loader.get("LOADER_OPTION", "FlashData")
    if ddr != loader.get("CODE471_OPTION", "Path1"):
        raise ValueError("Download DDR and resident DDR inputs differ; selection requires explicit support")
    if trust.getint("BL31_OPTION", "SEC") != 1:
        raise ValueError("NAOS U-Boot FIT requires the selected BL31 firmware")
    boot_artifacts = ["uboot.img", "uboot-slot.img"]
    loader_artifacts = ["idblock.bin", "idblock-slot.img", "MiniLoaderAll.bin"]
    firmware = [
        _firmware(rkbin, ddr, "ddr", revision, loader_artifacts,
                  {"ini": loader_path, "section": "LOADER_OPTION", "option": "FlashData"}),
        _firmware(rkbin, loader.get("CODE472_OPTION", "Path1"), "usbplug", revision, ["MiniLoaderAll.bin"],
                  {"ini": loader_path, "section": "CODE472_OPTION", "option": "Path1"}),
        _firmware(rkbin, trust.get("BL31_OPTION", "PATH"), "bl31", revision, boot_artifacts,
                  {"ini": trust_path, "section": "BL31_OPTION", "option": "PATH"}),
    ]
    bl32 = None
    if trust.getint("BL32_OPTION", "SEC", fallback=0) == 1:
        bl32 = _firmware(rkbin, trust.get("BL32_OPTION", "PATH"), "bl32", revision, boot_artifacts,
                         {"ini": trust_path, "section": "BL32_OPTION", "option": "PATH"})
        firmware.append(bl32)
    for basename, expected in (("bl31.elf", firmware[2]), ("tee.bin", bl32)):
        if expected is not None:
            actual = _record(source / basename, basename)
            if any(actual[key] != expected[key] for key in ("size", "sha256")):
                raise ValueError("Built firmware copy differs from selected rkbin input: " + basename)
    fit_inputs = _verify_trust_inputs(source, bl32)
    names = ["uboot.img", "idblock.bin", "MiniLoaderAll.bin", "idblock-slot.img", "uboot-slot.img"]
    artifacts = [_deploy_record(deploy, name) for name in names]
    for artifact in artifacts:
        candidates = sorted(source.glob("*loader*.bin")) if artifact["path"] == "MiniLoaderAll.bin" else [source / artifact["path"]]
        if not any(_record(path, path.name)["sha256"] == artifact["sha256"] for path in candidates):
            raise ValueError("Deployment artifact differs from this U-Boot build: " + artifact["path"])
    uboot_revision = d.getVar("SRCREV_uboot") or ""
    if not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", uboot_revision):
        raise ValueError("A fixed U-Boot revision is required for source-built SPL provenance")
    data["configuration"] = _configuration(source / ".config", outdir, "uboot.config")
    data["artifacts"] = artifacts
    data["firmware_inputs"] = firmware
    data["spl"] = {"source": "u-boot", "source_built": True, "source_revision": uboot_revision,
                   "in_artifacts": loader_artifacts, **_record(source / "spl/u-boot-spl.bin", "spl/u-boot-spl.bin")}
    data["firmware_selection"] = {"loader_ini": _record(_inside(rkbin, loader_path), loader_path),
                                  "trust_ini": _record(_inside(rkbin, trust_path), trust_path),
                                  "generated_fit": _record(source / "fit/u-boot.its", "fit/u-boot.its"),
                                  "fit_inputs": fit_inputs}


def _uboot_package(data, d, outdir):
    """Use this task's early producer output, never older signed deployments."""
    deploy = _deployment_directory(d)
    prefix = "naos-package-inputs/bootloader/"
    root = deploy / prefix
    metadata = json.loads(_text(_inside(root, "bootloader.json")))
    if (metadata.get("schema_version") != 1 or metadata.get("stage") != "compiled-before-key-injection"
            or metadata.get("machine") != d.getVar("MACHINE")
            or metadata.get("profile") != d.getVar("NAOS_BUILD_MODE")):
        raise ValueError("Early bootloader metadata does not match this build")
    revisions = metadata["source_revisions"]
    if revisions != {"uboot": d.getVar("SRCREV_uboot"), "rkbin": d.getVar("SRCREV_rkbin")}:
        raise ValueError("Early bootloader source revisions do not match this build")
    roles = {}
    for record in metadata["artifacts"]:
        actual = _record(_inside(root, record["path"]), record["path"])
        if any(actual[key] != record[key] for key in ("sha256", "size")) or record["role"] in roles:
            raise ValueError("Invalid early bootloader artifact hash or role")
        roles[record["role"]] = record
    required = {"uboot_nodtb", "uboot_control_dtb", "spl_nodtb", "spl_control_dtb", "ddr", "usbplug",
                "bl31", "bl32", "uboot_fit_template", "loader_config", "trust_config", "build_config"}
    if set(roles) != required or metadata["signature"]["keys_injected"] or metadata["otp"]["flag_injected"]:
        raise ValueError("Incomplete or already signed early bootloader input set")
    data["configuration"] = _configuration(_inside(root, roles["build_config"]["path"]), outdir, "uboot.config")
    data["artifacts"] = [{**_record(_inside(root, record["path"]), prefix + record["path"]), "role": record["role"]}
                         for record in metadata["artifacts"]]
    firmware = []
    for record in metadata["firmware_inputs"]:
        role = record["role"]
        if role not in ("ddr", "usbplug", "bl31", "bl32") or record != roles[role] | {
                "version": record.get("version"), "selection": record.get("selection")}:
            raise ValueError("Unexpected early bootloader firmware selection")
        origin = record["source"]
        if origin["repository"] != "rkbin" or origin["revision"] != revisions["rkbin"]:
            raise ValueError("Early firmware source revision mismatch")
        firmware.append({"role": role, "source": "rkbin", "source_revision": origin["revision"],
                         "path": origin["path"], "version": record.get("version"),
                         "version_source": "selected filename" if record.get("version") else None,
                         "selection": record["selection"], "sha256": record["sha256"], "size": record["size"]})
    if {record["role"] for record in firmware} != {"ddr", "usbplug", "bl31", "bl32"} or len(firmware) != 4:
        raise ValueError("Incomplete early firmware metadata")
    data["firmware_inputs"] = firmware
    spl = roles["spl_nodtb"]
    data["spl"] = {"source": "u-boot", "source_built": True, "source_revision": revisions["uboot"],
                   "path": prefix + spl["path"], "sha256": spl["sha256"], "size": spl["size"],
                   "control_dtb": {key: roles["spl_control_dtb"][key] for key in ("path", "sha256", "size")}}
    data["package_input_stage"] = metadata["stage"]
    data["bootloader_capabilities"] = metadata["capabilities"]
    data["firmware_selection"] = {key: {field: roles[role][field] for field in ("path", "sha256", "size")}
                                  for key, role in (("loader_ini_template", "loader_config"),
                                                    ("trust_ini", "trust_config"), ("generated_fit_template", "uboot_fit_template"))}


def extend_component(data: dict, d, outdir: Path) -> None:
    """Extend a deployed component sidecar; unsupported recipes are unchanged."""
    pn = d.getVar("PN")
    if pn == "linux-naos-rockchip":
        _kernel(data, d, Path(outdir))
    elif pn == "u-boot-naos-rockchip":
        if d.getVar("NAOS_PACKAGE") == "1":
            _uboot_package(data, d, Path(outdir))
        else:
            _uboot(data, d, Path(outdir))
