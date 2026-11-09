#!/usr/bin/env python3
"""Assemble a GPT factory image from already built NAOS artifacts.

The optional security layer supplies the signed FIT builder. Only ordinary
files are written; this tool never flashes devices or provisions OTP/RPMB.
"""

import argparse
import contextlib
import copy
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import uuid
import zlib

MIB = 1024 * 1024
SECTOR = 512
LINUX_FS_GUID = uuid.UUID("0fc63daf-8483-4772-8e79-3d69d8477de4")


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_layout(path):
    layout = json.loads(Path(path).read_text())
    if layout.get("format") != 1 or layout.get("sector_size") != SECTOR:
        raise ValueError("Unsupported partition layout format or sector size")
    seen, end = set(), 34 * SECTOR
    for part in layout["partitions"]:
        if part["name"] in seen or len(part["name"].encode("utf-16-le")) > 72:
            raise ValueError("Duplicate or invalid GPT partition name")
        seen.add(part["name"])
        start, size = part["start_mib"] * MIB, part["size_mib"] * MIB
        if start < end or size <= 0:
            raise ValueError("Overlapping, unsorted or empty partitions")
        end = start + size
    if len(layout["partitions"]) > 128:
        raise ValueError("Too many GPT partitions")
    loader_start = layout["loader_offset"]
    loader_size = layout["loader_copy_size"]
    loader_copies = layout["loader_copies"]
    if (loader_start < 34 * SECTOR or loader_start % SECTOR or
            loader_size <= 0 or loader_size % SECTOR or loader_copies != 2 or
            loader_start + loader_size * loader_copies > layout["partitions"][0]["start_mib"] * MIB):
        raise ValueError("Invalid redundant IDBlock region")
    for prefix in ("boot", "system"):
        a, b = (next(part for part in layout["partitions"] if part["name"] == f"{prefix}_{slot}") for slot in ("a", "b"))
        if a["size_mib"] != b["size_mib"]:
            raise ValueError("A/B partition sizes must match")
    if layout["partitions"][-1]["name"] != "userdata":
        raise ValueError("userdata must be the final partition")
    misc = next(part for part in layout["partitions"] if part["name"] == "misc")
    region_names, previous_end = set(), 0
    regions = layout.get("misc_regions", [])
    for region in regions:
        if region["name"] in region_names or region["offset"] < previous_end or region["size"] <= 0:
            raise ValueError("Invalid or overlapping misc regions")
        region_names.add(region["name"])
        previous_end = region["offset"] + region["size"]
        if previous_end > misc["size_mib"] * MIB:
            raise ValueError("misc region exceeds the partition")
    environment = layout["environment"]
    if environment["partition"] != "misc" or len(environment["offsets"]) != 2:
        raise ValueError("The redundant U-Boot environment must be in misc")
    for name, offset in zip(("uboot_env_a", "uboot_env_b"), environment["offsets"]):
        region = next(region for region in regions if region["name"] == name)
        if offset != region["offset"] or environment["size"] > region["size"]:
            raise ValueError("U-Boot environment does not fit its reserved misc region")
    return layout


def gpt_header(current, backup, first, last, disk_guid, table_lba, table_crc):
    header = struct.pack("<8sIIIIQQQQ16sQIII", b"EFI PART", 0x10000, 92, 0, 0,
                         current, backup, first, last, disk_guid.bytes_le,
                         table_lba, 128, 128, table_crc)
    header = header[:16] + struct.pack("<I", zlib.crc32(header)) + header[20:]
    return header.ljust(SECTOR, b"\0")


def write_gpt(stream, layout, identity):
    """Write primary/backup GPT plus protective MBR, returning byte offsets."""
    parts = layout["partitions"]
    end = (parts[-1]["start_mib"] + parts[-1]["size_mib"]) * MIB
    disk_size = ((end + 34 * SECTOR + MIB - 1) // MIB) * MIB
    sectors = disk_size // SECTOR
    guid = uuid.uuid5(uuid.NAMESPACE_URL, identity)
    table = bytearray(128 * 128)
    offsets = {}
    for index, part in enumerate(parts):
        start = part["start_mib"] * MIB
        size = part["size_mib"] * MIB
        part_guid = uuid.uuid5(guid, part["name"])
        name = part["name"].encode("utf-16-le").ljust(72, b"\0")
        entry = struct.pack("<16s16sQQQ72s", LINUX_FS_GUID.bytes_le,
                            part_guid.bytes_le, start // SECTOR,
                            (start + size) // SECTOR - 1, 0, name)
        table[index * 128:(index + 1) * 128] = entry
        offsets[part["name"]] = (start, size)
    stream.truncate(disk_size)
    mbr = bytearray(SECTOR)
    mbr[446:462] = struct.pack("<B3sB3sII", 0, b"\x00\x02\x00", 0xee,
                               b"\xff\xff\xff", 1, min(sectors - 1, 0xffffffff))
    mbr[510:] = b"\x55\xaa"
    stream.seek(0)
    stream.write(mbr)
    table_crc = zlib.crc32(table)
    for header_lba, backup_lba, table_lba in ((1, sectors - 1, 2), (sectors - 1, 1, sectors - 33)):
        stream.seek(table_lba * SECTOR)
        stream.write(table)
        stream.seek(header_lba * SECTOR)
        stream.write(gpt_header(header_lba, backup_lba, 34, sectors - 34,
                                guid, table_lba, table_crc))
    return offsets


def copy_payload(stream, source, offset, maximum):
    size = source.stat().st_size
    if size > maximum:
        raise ValueError(f"{source.name}: {size} bytes exceeds reserved {maximum} bytes")
    stream.seek(offset)
    with source.open("rb") as data:
        shutil.copyfileobj(data, stream, 1024 * 1024)


def safe_tar_name(name):
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Unsafe archive path: {name}")
    return str(path)


def assemble_rootfs(base, modules, overlays, keyring, output, version, epoch):
    """Merge tar metadata without extracting files or losing UID/GID/devices."""
    with contextlib.ExitStack() as stack:
        archives = [stack.enter_context(tarfile.open(source, "r:*")) for source in (base, modules)]
        members = {}
        for archive in archives:
            for member in archive:
                name = safe_tar_name(member.name)
                if name != ".":
                    members[name] = (archive, member)
        # sqfstar otherwise invents the root inode from the build process's
        # UID/GID and umask. It is part of the delivered system metadata too.
        root = tarfile.TarInfo(".")
        root.type, root.mode = tarfile.DIRTYPE, 0o755
        root.uid = root.gid = 0
        additions = {".": (root, None)}
        for overlay in overlays:
            if not overlay.is_dir():
                raise ValueError(f"Missing image overlay: {overlay}")
            for source in sorted(overlay.rglob("*")):
                name = source.relative_to(overlay).as_posix()
                info = tarfile.TarInfo(name)
                info.uid = info.gid = 0
                info.mode = source.lstat().st_mode & 0o7777
                if source.is_symlink():
                    info.type, info.linkname = tarfile.SYMTYPE, os.readlink(source)
                    data = None
                elif source.is_dir():
                    info.type, data = tarfile.DIRTYPE, None
                else:
                    data = source.read_bytes()
                    info.size = len(data)
                additions[name] = (info, data)
        for program in ("rauc", "rauc-hawkbit-updater", "naos-core", "naos", "naosd", "naos-exec"):
            if "usr/bin/" + program not in additions:
                raise ValueError(f"The {program} runtime must be deployed before image assembly")
        for name, data, mode in (
            ("usr/lib/naos/version.json", (json.dumps(version, sort_keys=True, indent=2) + "\n").encode(), 0o644),
            ("etc/rauc/keyring.pem", keyring.read_bytes(), 0o644),
        ):
            info = tarfile.TarInfo(name)
            info.uid = info.gid = 0
            info.mode, info.size = mode, len(data)
            additions[name] = (info, data)
        for service in ("rauc", "naos-boot-confirm"):
            name = "etc/runlevels/default/" + service
            info = tarfile.TarInfo(name)
            info.type, info.mode, info.linkname = tarfile.SYMTYPE, 0o777, "/etc/init.d/" + service
            additions[name] = (info, None)
        # Parent directory entries are needed by sqfstar even for new overlays.
        for name in list(additions):
            for parent in PurePosixPath(name).parents:
                parent_name = str(parent)
                if parent_name == "." or parent_name in members or parent_name in additions:
                    continue
                info = tarfile.TarInfo(parent_name)
                info.type, info.mode = tarfile.DIRTYPE, 0o755
                additions[parent_name] = (info, None)
        with output.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=epoch) as compressed, tarfile.open(fileobj=compressed, mode="w|") as target:
            all_names = set(members) | set(additions)
            emitted, visiting = set(), set()

            def emit(name):
                if name in emitted:
                    return
                if name in visiting:
                    raise ValueError(f"Cyclic tar hardlink or directory reference: {name}")
                visiting.add(name)
                parent = str(PurePosixPath(name).parent)
                if parent != name and parent in all_names:
                    emit(parent)
                if name in additions:
                    info, data = additions[name]
                    info.mtime = epoch
                    target.addfile(info, io.BytesIO(data) if data is not None else None)
                else:
                    archive, original = members[name]
                    info = copy.copy(original)
                    info.name, info.mtime = name, epoch
                    info.pax_headers = {key: value for key, value in original.pax_headers.items() if key not in ("mtime", "atime", "ctime", "path", "linkpath")}
                    if info.islnk():
                        info.linkname = safe_tar_name(info.linkname)
                        if info.linkname not in all_names:
                            raise ValueError(f"Missing tar hardlink target: {name} -> {info.linkname}")
                        # sqfstar does not reliably fail for forward hardlinks;
                        # emit the target first instead of silently losing it.
                        emit(info.linkname)
                    target.addfile(info, archive.extractfile(original) if original.isfile() else None)
                visiting.remove(name)
                emitted.add(name)

            for name in sorted(all_names):
                emit(name)


def make_fit(args, layout, output):
    if getattr(args, "fit_builder", None):
        command = [sys.executable, str(args.fit_builder),
                   "--input-dir", str(args.input_dir), "--output-dir", str(output),
                   "--layout", str(args.layout), "--kernel", args.kernel,
                   "--dtb", args.dtb, "--initramfs", args.initramfs,
                   "--mkimage", str(args.mkimage), "--epoch", str(args.epoch),
                   "--fit-key-dir", str(args.fit_key_dir),
                   "--fit-check-sign", str(args.fit_check_sign),
                   "--fit-key-dtb", str(args.fit_key_dtb)]
        if getattr(args, "resource", None):
            command += ["--resource", args.resource]
        subprocess.run(command, check=True)
        if not (output / "boot.img").is_file() or not (output / "boot.img").stat().st_size:
            raise ValueError("The secure FIT builder did not produce boot.img")
        return
    fit = layout["fit"]
    nodes = []
    payloads = [
        ("kernel", args.kernel, "kernel", f'os = "linux"; load = <{fit["kernel_load"]}>; entry = <{fit["kernel_load"]}>;'),
        ("fdt", args.dtb, "flat_dt", f'load = <{fit["fdt_load"]}>;'),
        ("ramdisk", args.initramfs, "ramdisk", f'os = "linux"; load = <{fit["ramdisk_load"]}>;'),
    ]
    if getattr(args, "resource", None):
        payloads.append(("resource", args.resource, "multi", ""))
    for name, filename, kind, extra in payloads:
        source = (args.input_dir / filename).resolve(strict=True)
        if '"' in str(source) or "\n" in str(source):
            raise ValueError("FIT input path contains unsupported characters")
        # The cpio is already gzip-compressed. Linux decompresses it, so FIT's
        # ramdisk compression field is 'none' (no second U-Boot decompression).
        nodes.append(f'{name} {{ data = /incbin/("{source}"); type = "{kind}"; arch = "arm64"; compression = "none"; {extra} hash {{ algo = "sha256"; }}; }};')
    resource = ' multi = "resource";' if getattr(args, "resource", None) else ""
    source = '/dts-v1/; / { description = "NAOS development FIT"; #address-cells = <1>; images { ' + " ".join(nodes) + ' }; configurations { default = "conf"; conf { kernel = "kernel"; fdt = "fdt"; ramdisk = "ramdisk";' + resource + ' }; }; };\n'
    its = output / "boot.its"
    its.write_text(source)
    subprocess.run([args.mkimage, "-f", str(its), str(output / "boot.img")], check=True,
                   env={**os.environ, "SOURCE_DATE_EPOCH": str(args.epoch)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("input-dir", "output-dir", "layout", "keyring"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--overlay", type=Path, action="append", required=True)
    for name in ("machine", "version", "build-id", "rootfs", "kernel", "dtb", "initramfs", "modules"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--epoch", type=int, default=1748563200)
    parser.add_argument("--processors", type=int, default=4)
    parser.add_argument("--mkimage", default="mkimage")
    parser.add_argument("--sqfstar", default="sqfstar")
    parser.add_argument("--resource")
    parser.add_argument("--sbom-inventory", action="store_true")
    parser.add_argument("--secure-boot", action="store_true")
    for name in ("fit-builder", "fit-key-dir", "fit-check-sign", "fit-key-dtb"):
        parser.add_argument("--" + name, type=Path)
    args = parser.parse_args()
    if args.secure_boot:
        for name in ("fit_builder", "fit_key_dir", "fit_check_sign", "fit_key_dtb"):
            if getattr(args, name) is None:
                parser.error("secure boot requires --" + name.replace("_", "-"))
    elif any(getattr(args, name) is not None for name in ("fit_builder", "fit_key_dir", "fit_check_sign", "fit_key_dtb")):
        parser.error("FIT signing inputs require --secure-boot")
    layout = read_layout(args.layout)
    output = args.output_dir.resolve()
    if output == args.input_dir.resolve():
        parser.error("input and output directories must differ")
    output.mkdir(parents=True, exist_ok=True)
    version = {"machine": args.machine, "version": args.version, "build_id": args.build_id,
               "layout_compatible": layout["compatible"], "secure_boot": args.secure_boot,
               "system_encryption": False, "data_encryption": False}
    system_tar = output / "system.tar.gz"
    assemble_rootfs(args.input_dir / args.rootfs, args.input_dir / args.modules,
                    args.overlay, args.keyring, system_tar, version, args.epoch)
    if args.sbom_inventory:
        from naos_sbom_inventory import write_inventory
        inventory_manifest = (args.input_dir / args.rootfs.removesuffix(".tar.gz")).with_suffix(".manifest.json")
        write_inventory(system_tar, output / "naos-rootfs.inventory.json", inventory_manifest)
    with gzip.open(system_tar, "rb") as archive, tempfile.TemporaryFile() as diagnostics:
        process = subprocess.Popen([args.sqfstar, "-comp", "zstd", "-processors", str(args.processors),
                        "-mkfs-time", str(args.epoch), "-all-time", str(args.epoch),
                        "-root-uid", "0", "-root-gid", "0", "-root-mode", "0755",
                        "-no-progress", "-exit-on-error", str(output / "system.squashfs")],
                       stdin=subprocess.PIPE, stderr=diagnostics,
                       env={key: value for key, value in os.environ.items() if key != "SOURCE_DATE_EPOCH"})
        try:
            shutil.copyfileobj(archive, process.stdin)
        finally:
            process.stdin.close()
            status = process.wait()
        diagnostics.seek(0)
        errors = diagnostics.read().decode(errors="replace")
        if status or any(message in errors for message in ("Could not resolve hardlink", "FATAL ERROR")):
            raise RuntimeError(f"sqfstar failed with exit status {status}: {errors}")
        if errors:
            print(errors, end="")
    make_fit(args, layout, output)
    for name in ("idblock.bin", "uboot.img", "naos-uboot-env.bin", "MiniLoaderAll.bin"):
        shutil.copyfile(args.input_dir / name, output / name)
    raw_image = output / f"naos-{args.machine}.img"
    with raw_image.open("w+b") as disk:
        offsets = write_gpt(disk, layout, layout["compatible"])
        first_partition = min(start for start, size in offsets.values())
        loader_offset = layout["loader_offset"]
        if loader_offset < 34 * SECTOR:
            raise ValueError("Loader would overlap the primary GPT")
        for index in range(layout["loader_copies"]):
            copy_payload(disk, output / "idblock.bin",
                         loader_offset + index * layout["loader_copy_size"],
                         layout["loader_copy_size"])
        for part in layout["partitions"]:
            if "payload" in part:
                start, size = offsets[part["name"]]
                copy_payload(disk, output / part["payload"], start, size)
        environment = layout["environment"]
        start, capacity = offsets[environment["partition"]]
        env_image = output / environment["image"]
        if env_image.stat().st_size != environment["size"]:
            raise ValueError("Unexpected redundant U-Boot environment image size")
        last_end = 0
        for relative in environment["offsets"]:
            if relative < last_end or relative + environment["size"] > capacity:
                raise ValueError("Environment copies overlap or exceed the misc partition")
            copy_payload(disk, env_image, start + relative, environment["size"])
            last_end = relative + environment["size"]
    shutil.copyfile(args.layout, output / "partition-layout.json")
    package_manifest = args.input_dir / args.rootfs.removesuffix(".tar.gz")
    package_manifest = package_manifest.with_suffix(".manifest.json")
    if package_manifest.is_file():
        shutil.copyfile(package_manifest, output / "alpine-packages.json")
    manifest = {**version, "userdata_filesystem": "f2fs", "userdata_initialization": "initramfs", "files": {}}
    for path in sorted(output.iterdir()):
        if path.is_file() and path.name not in ("manifest.json", "SHA256SUMS"):
            manifest["files"][path.name] = {"sha256": sha256(path), "size": path.stat().st_size}
    (output / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    (output / "SHA256SUMS").write_text("".join(f"{sha256(path)}  {path.name}\n" for path in sorted(output.iterdir()) if path.is_file() and path.name != "SHA256SUMS"))


if __name__ == "__main__":
    main()
