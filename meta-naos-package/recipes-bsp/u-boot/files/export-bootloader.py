#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Export compiled Rockchip inputs before signing; never access signing keys."""

import argparse
import configparser
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import struct


def relative(value):
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or str(path) == ".":
        raise ValueError("Unsafe bootloader input path")
    return str(path)


def inside(root, name):
    root = Path(root).resolve()
    path = root / relative(name)
    if not path.resolve().is_relative_to(root) or not path.is_file():
        raise ValueError("Missing or escaping bootloader input: " + name)
    return path


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            h.update(block)
    return {"sha256": h.hexdigest(), "size": path.stat().st_size}


def clean_dtb(path):
    """Reject injected public keys and burn flags using FDT structure tokens."""
    content = path.read_bytes()
    if len(content) < 40:
        raise ValueError("Truncated control DTB")
    magic, total, offset, strings, _, version, _, _, string_size, structure_size = struct.unpack_from(">10I", content)
    if (magic != 0xD00DFEED or version < 17 or total > len(content)
            or offset + structure_size > total or strings + string_size > total):
        raise ValueError("Invalid control DTB header")
    position, end, depth = offset, offset + structure_size, 0
    while position + 4 <= end:
        token = struct.unpack_from(">I", content, position)[0]
        position += 4
        if token == 1:
            stop = content.find(b"\0", position, end)
            if stop < 0:
                raise ValueError("Unterminated DTB node")
            name = content[position:stop].decode("ascii")
            if name.startswith("key-"):
                raise ValueError("Control DTB already contains an injected signing key")
            position, depth = (stop + 4) & ~3, depth + 1
        elif token == 2:
            depth -= 1
            if depth < 0:
                raise ValueError("Unbalanced control DTB")
        elif token == 3:
            if position + 8 > end:
                raise ValueError("Truncated DTB property")
            length, name_offset = struct.unpack_from(">II", content, position)
            position += 8
            start = strings + name_offset
            stop = content.find(b"\0", start, strings + string_size)
            if name_offset >= string_size or stop < 0 or position + length > end:
                raise ValueError("Invalid DTB property")
            name = content[start:stop].decode("ascii")
            if name == "burn-key-hash" or name.startswith("rsa,"):
                raise ValueError("Control DTB contains signing or OTP provisioning data")
            position = (position + length + 3) & ~3
        elif token == 4:
            pass
        elif token == 9:
            if depth:
                raise ValueError("Unclosed control DTB")
            return
        else:
            raise ValueError("Unknown control DTB token")
    raise ValueError("Unterminated control DTB")


def capture(source, target):
    target.mkdir(parents=True, exist_ok=True)
    for original, exported in (("u-boot.dtb", "u-boot.clean.dtb"),
                               ("spl/u-boot-spl.dtb", "spl/u-boot-spl.clean.dtb")):
        path = inside(source, original)
        clean_dtb(path)
        output = target / exported
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, output)
        output.chmod(0o644)


def configuration(path):
    result = {}
    for line in path.read_text().splitlines():
        if line.startswith("CONFIG_") and "=" in line:
            name, value = line.split("=", 1)
            result[name] = json.loads(value) if value.startswith('"') else value
    return result


def read_ini(path):
    result = configparser.ConfigParser(interpolation=None, strict=True)
    result.optionxform = str
    result.read_string(path.read_text())
    return result


def elf_segments(path):
    content = path.read_bytes()
    if len(content) < 64 or content[:6] != b"\x7fELF\x02\x01":
        raise ValueError("Expected a little-endian ELF64 BL31")
    fields = struct.unpack_from("<16sHHIQQQIHHHHHH", content)
    offset, stride, count = fields[5], fields[9], fields[10]
    if stride < 56 or offset + stride * count > len(content):
        raise ValueError("Invalid BL31 program headers")
    result = []
    for index in range(count):
        kind, _, start, _, address, size, _, _ = struct.unpack_from("<IIQQQQQQ", content, offset + index * stride)
        if kind != 1 or not size:
            continue
        if start + size > len(content):
            raise ValueError("Truncated BL31 load segment")
        result.append({"address": address, "size": size,
                       "sha256": hashlib.sha256(content[start:start + size]).hexdigest(),
                       "prepared_path": "bl31_0x%08x.bin" % address})
    if not result:
        raise ValueError("BL31 has no loadable segments")
    return result


def export(args):
    source, rkbin, output = args.source, args.rkbin, args.output
    if args.machine != "mixtile-core3588e-v2" or args.profile not in ("production", "development"):
        raise ValueError("Unsupported bootloader export board or profile")
    for revision in (args.uboot_revision, args.rkbin_revision):
        if not re.fullmatch(r"[a-f0-9]{40}", revision):
            raise ValueError("Export requires fixed Git source revisions")
    config_path = inside(source, ".config")
    if re.search(r'"(?:/home/|/Users/|/tmp/|/root/)', config_path.read_text()):
        raise ValueError("Build configuration contains a host-specific absolute path")
    values = configuration(config_path)
    if values.get("CONFIG_SYS_SOC") != "rk3588" or values.get("CONFIG_SPL_SEPARATE_BSS") != "y":
        raise ValueError("This export contract requires RK3588 with separate SPL BSS (no SPL pad)")
    if values.get("CONFIG_TPL") == "y":
        raise ValueError("TPL needs an extended export contract")
    if values.get("CONFIG_IMAGE_GZIP") != "y":
        raise ValueError("This export contract requires the selected gzip FIT format")
    loader_name = relative(values.get("CONFIG_LOADER_INI") or "RK3588MINIALL.ini")
    trust_name = relative(values.get("CONFIG_TRUST_INI") or "RK3588TRUST.ini")
    if "/" in loader_name or "/" in trust_name:
        raise ValueError("INI names must be relative to their selected rkbin directory")
    loader_path, trust_path = "RKBOOT/" + loader_name, "RKTRUST/" + trust_name
    loader_file, trust_file = inside(rkbin, loader_path), inside(rkbin, trust_path)
    loader, trust = read_ini(loader_file), read_ini(trust_file)
    if (loader.get("CHIP_NAME", "NAME") != "RK3588"
            or loader.getint("CODE471_OPTION", "NUM") != 1
            or loader.getint("CODE472_OPTION", "NUM") != 1
            or loader.getint("LOADER_OPTION", "NUM") != 2
            or {loader.get("LOADER_OPTION", "LOADER1"), loader.get("LOADER_OPTION", "LOADER2")} != {"FlashData", "FlashBoot"}
            or loader.get("CODE471_OPTION", "Path1") != loader.get("LOADER_OPTION", "FlashData")):
        raise ValueError("Unsupported selected loader firmware configuration")
    for section in trust.sections():
        if re.fullmatch(r"BL[0-9]+_OPTION", section):
            expected = 1 if section in ("BL31_OPTION", "BL32_OPTION") else 0
            if trust.getint(section, "SEC", fallback=0) != expected:
                raise ValueError("Unsupported selected trust firmware configuration")
    # Extra MCU/INIT/loadable sections would need additional exported payloads.
    if re.search(r"^(?:MCU\d*|INIT\d*|LOAD\d*)\s*=", trust_file.read_text(), re.MULTILINE):
        raise ValueError("Additional trust payloads require an extended export contract")
    firmware = [("ddr", loader.get("LOADER_OPTION", "FlashData"), loader_path, "LOADER_OPTION", "FlashData"),
                ("usbplug", loader.get("CODE472_OPTION", "Path1"), loader_path, "CODE472_OPTION", "Path1"),
                ("bl31", trust.get("BL31_OPTION", "PATH"), trust_path, "BL31_OPTION", "PATH"),
                ("bl32", trust.get("BL32_OPTION", "PATH"), trust_path, "BL32_OPTION", "PATH")]
    records = []
    output.mkdir(parents=True, exist_ok=True)

    def put(path, name, role, origin=None, content=None):
        name = relative(name)
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if content is None:
            shutil.copyfile(path, target)
        else:
            target.write_text(content, encoding="utf-8")
        target.chmod(0o644)
        record = {"role": role, "path": name, **digest(target)}
        if origin:
            record["source"] = origin
        records.append(record)
        return record

    for name, role in (("u-boot-nodtb.bin", "uboot_nodtb"), ("spl/u-boot-spl-nodtb.bin", "spl_nodtb")):
        put(inside(source, name), name, role,
            {"repository": "u-boot", "revision": args.uboot_revision, "path": name})
    for name, role in (("u-boot.clean.dtb", "uboot_control_dtb"), ("spl/u-boot-spl.clean.dtb", "spl_control_dtb")):
        path = inside(args.clean_dir, name)
        clean_dtb(path)
        if digest(path) != digest(inside(source, name.replace(".clean", ""))):
            raise ValueError("Control DTB changed after its clean capture")
        put(path, name, role,
            {"repository": "u-boot", "revision": args.uboot_revision, "path": name.replace(".clean", "")})
    firmware_records = []
    for role, name, ini, section, option in firmware:
        if not relative(name).startswith("bin/") or "_" + role + "_" not in PurePosixPath(name).name:
            raise ValueError("Selected firmware filename does not identify its role")
        record = put(inside(rkbin, name), "rkbin/" + name, role,
                     {"repository": "rkbin", "revision": args.rkbin_revision, "path": name})
        version = re.search(r"_v([0-9]+(?:\.[0-9]+)+)(?=[._-]|$)", name)
        firmware_records.append({**record, "version": version.group(1) if version else None,
                                 "selection": {"ini": ini, "section": section, "option": option}})
    template = inside(source, "u-boot.its").read_text()
    references = re.findall(r'/incbin/\(\s*"([^"\n]+)"\s*\)', template)
    if not references:
        raise ValueError("SDK did not generate the unsigned U-Boot ITS")
    for name in references:
        relative(name)
    if "hashed-nodes" in template or "burn-key-hash" in template:
        raise ValueError("Generated ITS contains completed signing metadata")
    put(None, "templates/u-boot.its.in", "uboot_fit_template", content=template)
    loader_template, substitutions = re.subn(r"(?m)^FlashBoot=[^\r\n]*", "FlashBoot=@SPL_BINARY@", loader_file.read_text())
    if substitutions != 1:
        raise ValueError("Expected exactly one source-built SPL input in loader template")
    put(None, "rkbin/" + loader_path + ".in", "loader_config", content=loader_template)
    put(trust_file, "rkbin/" + trust_path, "trust_config")
    put(config_path, "config/u-boot.config", "build_config")
    enabled = lambda name: values.get("CONFIG_" + name) == "y"
    capabilities = {"fit_signature": enabled("FIT_SIGNATURE"), "spl_fit_signature": enabled("SPL_FIT_SIGNATURE"),
                    "fit_hardware_crypto": enabled("FIT_HW_CRYPTO"), "spl_fit_hardware_crypto": enabled("SPL_FIT_HW_CRYPTO"),
                    "spl_secure_otp": enabled("SPL_ROCKCHIP_SECURE_OTP")}
    capabilities["otp_key_hash_programming"] = all(capabilities[name] for name in
        ("spl_fit_signature", "spl_fit_hardware_crypto", "spl_secure_otp"))
    configuration_text = inside(source, "u-boot.cfg").read_text()
    dram = re.search(r"^#define CFG_SYS_SDRAM_BASE (0[xX][0-9a-fA-F]+|[0-9]+)$", configuration_text, re.MULTILINE)
    if not dram:
        raise ValueError("Cannot establish compiled SDRAM base")
    number = lambda name: int(values["CONFIG_" + name], 0)
    script_text = inside(source, "scripts/fit-core.sh").read_text()
    external = re.search(r'^OFFS_DATA="(0x[0-9a-fA-F]+)"$', script_text, re.MULTILINE)
    if not external:
        raise ValueError("Cannot establish the SDK FIT external data offset")
    for compiled, selected in (("bl31.elf", firmware[2][1]), ("tee.bin", firmware[3][1])):
        if digest(inside(source, compiled)) != digest(inside(rkbin, selected)):
            raise ValueError("Generated FIT firmware differs from the selected rkbin input")
    segments = elf_segments(inside(rkbin, firmware[2][1]))
    reference_names = [str(PurePosixPath(name)) for name in references]
    allowed = {"u-boot-nodtb.bin.gz", "u-boot-nodtb.bin.digest", "u-boot.dtb", "tee.bin.gz", "tee.bin.digest"}
    if len(reference_names) != len(set(reference_names)) or not allowed.issubset(reference_names):
        raise ValueError("Unsupported U-Boot/BL32 data or digest references in generated ITS")
    gzip_segments = [segment["prepared_path"] + ".gz" for segment in segments
                     if segment["prepared_path"] + ".gz" in reference_names]
    for segment in segments:
        segment["compression"] = "gzip" if segment["prepared_path"] + ".gz" in gzip_segments else "none"
        expected = segment["prepared_path"] + (".gz" if segment["compression"] == "gzip" else "")
        if expected not in reference_names:
            raise ValueError("Generated ITS omits a selected BL31 load segment")
        allowed.add(expected)
        if segment["compression"] == "gzip":
            allowed.add(segment["prepared_path"].removesuffix(".bin") + ".bin.digest")
    if set(reference_names) != allowed:
        raise ValueError("Generated ITS contains additional or missing firmware inputs")
    metadata = {
        "schema_version": 1, "stage": "compiled-before-key-injection", "profile": args.profile, "machine": args.machine,
        "artifacts": sorted(records, key=lambda item: item["path"]),
        "source_revisions": {"uboot": args.uboot_revision, "rkbin": args.rkbin_revision},
        "firmware_inputs": firmware_records, "capabilities": capabilities,
        "addresses": {"uboot_load": number("TEXT_BASE"), "spl_text_base": number("SPL_TEXT_BASE"),
                      "spl_fit_load": number("SPL_LOAD_FIT_ADDRESS"), "sdram_base": int(dram.group(1), 0),
                      "bl32_load": int(trust.get("BL32_OPTION", "ADDR"), 0)},
        "packing": {"spl_separate_bss": True, "spl_padding_required": False, "tpl_required": False,
                    "spl_max_size": number("SPL_MAX_SIZE"), "spl_pad_to": number("SPL_PAD_TO"),
                    "uboot_fit_copy_size": number("SPL_FIT_IMAGE_KB") * 1024,
                    "uboot_fit_copies": number("SPL_FIT_IMAGE_MULTIPLE"),
                    "fit_external_data_offset": int(external.group(1), 0),
                    "idblock_copies": 2, "idblock_copy_stride": 512 * 1024,
                    "loader_spl_placeholder": "@SPL_BINARY@", "loader_spl_base": "rkbin",
                    "its_working_directory": ".", "bl31_segments": segments,
                    "uboot_compression": "gzip", "bl32_compression": "gzip",
                    "compressed_payload_digest": "sha256-of-uncompressed-payload"},
        "signature": {"enabled": capabilities["fit_signature"] and capabilities["spl_fit_signature"],
                      "algorithm": "sha256,rsa2048", "padding": "pss", "pss_salt_length": "maximum",
                      "key_name_hint": "dev", "required": "conf", "keys_injected": False},
        "otp": {"burn_key_hash_default": False, "flag_injected": False,
                "programming_stage": "SPL after signed loader boots; never on the host",
                "existing_key_constraint": "An already provisioned device requires its existing trusted signing key"},
        "host_requirements": ["Rockchip-compatible mkimage/fit_check_sign with RSA2048 PSS maximum salt and hardware-RSA DTB parameters",
                              "Rockchip rk3588 boot_merger and rk_sign_tool compatible with the selected rkbin revision",
                              "FDT editing, ELF64 PT_LOAD extraction, deterministic gzip and SHA256"],
    }
    expected = {item["path"] for item in records} | {"bootloader.json"}
    actual = {str(path.relative_to(output)) for path in output.rglob("*") if path.is_file()}
    if actual - expected or len(records) != 12:
        raise ValueError("Unexpected file in the 13-file bootloader export")
    (output / "bootloader.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("capture", "export"))
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--clean-dir", required=True, type=Path)
    parser.add_argument("--rkbin", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--machine")
    parser.add_argument("--profile")
    parser.add_argument("--uboot-revision")
    parser.add_argument("--rkbin-revision")
    args = parser.parse_args()
    if args.action == "capture":
        capture(args.source, args.clean_dir)
    else:
        if any(getattr(args, name) is None for name in ("rkbin", "output", "machine", "profile", "uboot_revision", "rkbin_revision")):
            parser.error("export requires rkbin, output, machine, profile and source revisions")
        export(args)


if __name__ == "__main__":
    main()
