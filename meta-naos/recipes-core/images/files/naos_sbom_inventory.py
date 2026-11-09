"""Inventory delivered rootfs/initramfs archives without extracting files.

Paths are root-relative POSIX paths. SHA256 values describe the final tar's
regular-file bytes, including resolved hardlinks. APK checksums are retained
separately: they describe the package's original files, before image overlays.
"""

import base64
import binascii
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import tarfile
import zlib


_DATABASE = "lib/apk/db/installed"
_METADATA = frozenset(("etc/alpine-release", "etc/os-release",
                       "usr/lib/os-release", "usr/lib/naos/version.json"))
_CHUNK_SIZE = 1024 * 1024
_DATABASE_LIMIT = 32 * 1024 * 1024
_METADATA_LIMIT = 64 * 1024
_MODULE = re.compile(r"\.ko(?:\.(?:xz|gz|zst))?$")


def _path(value, allow_root=False):
    if not isinstance(value, str) or "\0" in value or value.startswith("/"):
        raise ValueError("Unsafe rootfs path: {!r}".format(value))
    if ".." in value.split("/"):
        raise ValueError("Unsafe rootfs path: {!r}".format(value))
    normalized = str(PurePosixPath(value))
    if normalized == "." and not allow_root:
        raise ValueError("Empty rootfs file path: {!r}".format(value))
    return normalized


def _apk_checksum(value):
    if value is None:
        return None
    if value.startswith("Q1"):
        try:
            digest = base64.b64decode(value[2:], validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValueError("Invalid APK Q1 checksum") from error
        if len(digest) != 20:
            raise ValueError("Invalid APK Q1 SHA1 digest length")
        return {"algorithm": "SHA1", "value": digest.hex(), "encoded": value}
    # Preserve unknown formats rather than guessing their algorithm.
    return {"algorithm": None, "value": None, "encoded": value}


def _parse_apk_database(text):
    packages = []
    identities = set()
    for paragraph in text.split("\n\n"):
        fields, depends, files = {}, [], []
        directory = current_file = None
        seen_files = set()
        for line in paragraph.splitlines():
            if ":" not in line:
                if line:
                    raise ValueError("Malformed APK database record")
                continue
            field, value = line.split(":", 1)
            if field in {"P", "V", "A", "L", "o", "U", "c", "C"}:
                if field in fields:
                    raise ValueError("Duplicate APK package field: " + field)
                fields[field] = value
            elif field == "D":
                depends.extend(value.split())
            elif field == "F":
                directory = _path(value, allow_root=True)
                current_file = None
            elif field == "R":
                if directory is None:
                    raise ValueError("APK file record has no directory")
                name = _path(value)
                name = _path(str(PurePosixPath(directory) / name))
                if name in seen_files:
                    raise ValueError("Duplicate APK file record: " + name)
                seen_files.add(name)
                current_file = {"path": name, "apk_original_checksum": None}
                files.append(current_file)
            elif field == "Z":
                if current_file is None:
                    raise ValueError("APK checksum has no file record")
                if current_file["apk_original_checksum"] is not None:
                    raise ValueError("Duplicate APK file checksum")
                current_file["apk_original_checksum"] = _apk_checksum(value)
        if not fields:
            continue
        if not fields.get("P") or not fields.get("V"):
            raise ValueError("APK package has no name or version")
        identity = (fields["P"], fields["V"], fields.get("A", ""))
        if identity in identities:
            raise ValueError("Duplicate APK package: " + fields["P"])
        identities.add(identity)
        packages.append({
            "name": fields["P"], "version": fields["V"],
            "architecture": fields.get("A", ""), "license": fields.get("L", ""),
            "origin": fields.get("o", ""), "homepage": fields.get("U", ""),
            "aports_commit": fields.get("c", ""), "depends": depends,
            # C identifies APK control metadata; it is not the archive SHA256.
            "apk_control_checksum": fields.get("C"),
            "archive": None, "files": sorted(files, key=lambda item: item["path"]),
        })
    return sorted(packages, key=lambda item: (item["name"], item["version"],
                                               item["architecture"]))


def _archive_checksums(manifest_path):
    if manifest_path is None:
        return {}
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    archives = {}
    for archive in manifest.get("input_apks", []):
        name, digest = archive["name"], archive["sha256"]
        if _path(name) != name or "/" in name:
            raise ValueError("Invalid APK archive filename")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            raise ValueError("Invalid APK archive SHA256: " + name)
        record = {"name": name, "sha256": digest.lower()}
        if name in archives and archives[name] != record:
            raise ValueError("Conflicting APK archive checksums: " + name)
        archives[name] = record
    return archives


def _resolve_hardlinks(entries, sha1s):
    resolved = set()
    for name, entry in entries.items():
        if entry["kind"] != "hardlink" or name in resolved:
            continue
        chain, visiting, target = [], set(), name
        while entries.get(target, {}).get("kind") == "hardlink" and target not in resolved:
            if target in visiting:
                raise ValueError("Cyclic rootfs hardlink: " + target)
            visiting.add(target)
            chain.append(target)
            target = entries[target]["link_target"]
        final = entries.get(target)
        if final is None or final["kind"] not in {"regular", "hardlink"}:
            raise ValueError("Missing or non-regular rootfs hardlink target: " + target)
        for link in reversed(chain):
            entries[link]["sha256"] = final["sha256"]
            entries[link]["size"] = final["size"]
            sha1s[link] = sha1s[target]
            resolved.add(link)


def collect_rootfs(tar_path, apk_manifest_path=None):
    """Return a deterministic inventory of a final merged rootfs tar.

    The archive is read once in streaming mode. Only the APK database and a
    small metadata whitelist are retained as file contents. Links are never
    followed on the host, and malformed paths or duplicate entries fail.
    """
    archives = _archive_checksums(apk_manifest_path)
    entries, sha1s, captured, seen = {}, {}, {}, set()
    with tarfile.open(tar_path, mode="r|*") as archive:
        for member in archive:
            name = _path(member.name, allow_root=member.isdir())
            if name in seen:
                raise ValueError("Duplicate rootfs tar entry: " + name)
            seen.add(name)
            kind = ("regular" if member.isfile() else
                    "hardlink" if member.islnk() else
                    "symlink" if member.issym() else
                    "directory" if member.isdir() else
                    "character-device" if member.ischr() else
                    "block-device" if member.isblk() else
                    "fifo" if member.isfifo() else None)
            if kind is None:
                raise ValueError("Unsupported rootfs tar entry: " + name)
            entry = {"path": name, "kind": kind, "sha256": None,
                     "size": 0, "mode": format(member.mode & 0o7777, "04o")}
            if kind == "regular":
                sha256, sha1 = hashlib.sha256(), hashlib.sha1(usedforsecurity=False)
                limit = (_DATABASE_LIMIT if name == _DATABASE else
                         _METADATA_LIMIT if name in _METADATA else None)
                if limit is not None and member.size > limit:
                    raise ValueError("Rootfs metadata exceeds size limit: " + name)
                content = bytearray() if limit is not None else None
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError("Cannot read rootfs regular file: " + name)
                size = 0
                with source:
                    while True:
                        block = source.read(_CHUNK_SIZE)
                        if not block:
                            break
                        size += len(block)
                        sha256.update(block)
                        sha1.update(block)
                        if content is not None:
                            content.extend(block)
                if size != member.size:
                    raise ValueError("Truncated rootfs regular file: " + name)
                entry.update(sha256=sha256.hexdigest(), size=size)
                sha1s[name] = sha1.hexdigest()
                if content is not None:
                    captured[name] = bytes(content).decode("utf-8")
            elif kind == "hardlink":
                entry["link_target"] = _path(member.linkname)
            elif kind == "symlink":
                # An absolute or '..' symlink target is data, never a host path.
                entry["link_target"] = member.linkname
            elif kind in {"character-device", "block-device"}:
                entry.update(device_major=member.devmajor, device_minor=member.devminor)
            entries[name] = entry
    _resolve_hardlinks(entries, sha1s)
    if _DATABASE not in captured:
        raise ValueError("Final rootfs has no regular APK installed database")
    packages = _parse_apk_database(captured[_DATABASE])
    owned = set()
    for package in packages:
        package["archive"] = archives.get(package["name"] + "-" + package["version"] + ".apk")
        for record in package["files"]:
            name = record["path"]
            owned.add(name)
            entry = entries.get(name)
            checksum = record["apk_original_checksum"]
            record["kind"] = entry["kind"] if entry else None
            record["sha256"] = entry["sha256"] if entry else None
            if entry is None:
                record["status"] = "missing"
            elif checksum is None or checksum["algorithm"] != "SHA1" or name not in sha1s:
                record["status"] = "unverified"
            else:
                record["status"] = "unchanged" if checksum["value"] == sha1s[name] else "modified"
    files = [entries[name] for name in sorted(entries)]
    return {
        "schema_version": 1, "snapshot": "final-rootfs-before-encryption",
        "metadata": {name: captured[name] for name in sorted(_METADATA) if name in captured},
        "apk_database": {"path": _DATABASE, "sha256": entries[_DATABASE]["sha256"]},
        "packages": packages, "files": files,
        "unowned_files": [entry["path"] for entry in files
                          if entry["kind"] in {"regular", "hardlink", "symlink"}
                          and entry["path"] not in owned],
        "kernel_modules": [entry.copy() for entry in files if _MODULE.search(entry["path"])],
    }


def _gzip_member_blocks(source):
    """Yield one verified gzip member, allowing only trailing zero bytes."""
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
    pending = b""
    while not inflater.eof:
        if not pending:
            pending = source.read(_CHUNK_SIZE)
            if not pending:
                raise ValueError("Truncated initramfs gzip member")
        try:
            block = inflater.decompress(pending, _CHUNK_SIZE)
        except zlib.error as error:
            raise ValueError("Invalid initramfs gzip member") from error
        pending = inflater.unconsumed_tail
        if block:
            yield block
    if any(inflater.unused_data):
        raise ValueError("Nonzero data after initramfs gzip member")
    while True:
        block = source.read(_CHUNK_SIZE)
        if not block:
            break
        if any(block):
            raise ValueError("Nonzero data after initramfs gzip member")


class _CpioReader:
    def __init__(self, source):
        self.blocks = iter(_gzip_member_blocks(source))
        self.buffer = b""
        self.offset = 0

    def read(self, size):
        parts, remaining = [], size
        while remaining:
            if self.offset == len(self.buffer):
                self.buffer = next(self.blocks, b"")
                self.offset = 0
                if not self.buffer:
                    break
            amount = min(remaining, len(self.buffer) - self.offset)
            parts.append(self.buffer[self.offset:self.offset + amount])
            self.offset += amount
            remaining -= amount
        return b"".join(parts)

    def exact(self, size, label):
        data = self.read(size)
        if len(data) != size:
            raise ValueError("Truncated initramfs CPIO " + label)
        return data

    def padding(self, size):
        if any(self.exact(size, "padding")):
            raise ValueError("Nonzero initramfs CPIO alignment padding")


def _cpio_hardlinks(entries, groups):
    for names in groups.values():
        payloads = [name for name in names if entries[name]["size"]]
        target = min(payloads or names)
        payload = entries[target]
        for name in payloads:
            if (entries[name]["sha256"], entries[name]["size"]) != (payload["sha256"], payload["size"]):
                raise ValueError("Conflicting initramfs CPIO hardlink payloads: " + name)
        for name in names:
            if name != target:
                entries[name].update(kind="hardlink", link_target=target,
                                     sha256=payload["sha256"], size=payload["size"])


def collect_cpio(cpio_gz_path):
    """Inventory actual gzip/newc initramfs bytes without extracting files.

    Both newc and CRC-newc are accepted. Zero-sized hardlink records resolve
    through their device/inode group, including forward payloads and empty
    files. A second archive or gzip member is rejected rather than ignored.
    Package ownership is intentionally left for the image/pkgdata collector.
    """
    entries, groups = {}, {}
    types = {stat.S_IFREG: "regular", stat.S_IFLNK: "symlink",
             stat.S_IFDIR: "directory", stat.S_IFCHR: "character-device",
             stat.S_IFBLK: "block-device", stat.S_IFIFO: "fifo", stat.S_IFSOCK: "socket"}
    with Path(cpio_gz_path).open("rb") as source:
        reader = _CpioReader(source)
        while True:
            header = reader.exact(110, "header")
            magic = header[:6]
            if magic not in {b"070701", b"070702"} or not re.fullmatch(b"[0-9a-fA-F]{104}", header[6:]):
                raise ValueError("Invalid initramfs CPIO newc header")
            (ino, mode, uid, gid, nlink, mtime, size, devmajor, devminor,
             rdevmajor, rdevminor, namesize, expected_crc) = (
                int(header[offset:offset + 8], 16) for offset in range(6, 110, 8))
            if not 1 <= namesize <= _CHUNK_SIZE or nlink < 1:
                raise ValueError("Invalid initramfs CPIO name size or link count")
            if magic == b"070701" and expected_crc:
                raise ValueError("Nonzero checksum in initramfs non-CRC newc header")
            encoded_name = reader.exact(namesize, "filename")
            if not encoded_name.endswith(b"\0") or b"\0" in encoded_name[:-1]:
                raise ValueError("Invalid initramfs CPIO filename terminator")
            name = encoded_name[:-1].decode("utf-8", errors="surrogateescape")
            reader.padding(-(110 + namesize) % 4)
            if name == "TRAILER!!!":
                if size or expected_crc:
                    raise ValueError("Invalid initramfs CPIO trailer")
                while True:
                    padding = reader.read(_CHUNK_SIZE)
                    if not padding:
                        break
                    if any(padding):
                        raise ValueError("Nonzero data after initramfs CPIO trailer")
                break
            kind = types.get(stat.S_IFMT(mode))
            if kind is None:
                raise ValueError("Unsupported initramfs CPIO file type")
            name = _path(name, allow_root=kind == "directory")
            if name in entries:
                raise ValueError("Duplicate initramfs CPIO entry: " + name)
            if kind not in {"regular", "symlink"} and size:
                raise ValueError("Unexpected initramfs CPIO special-file payload: " + name)
            if kind == "symlink" and size > _METADATA_LIMIT:
                raise ValueError("Initramfs CPIO symlink target exceeds size limit")
            entry = {"path": name, "kind": kind, "sha256": None,
                     "size": 0, "mode": format(mode & 0o7777, "04o")}
            digest = hashlib.sha256() if kind == "regular" else None
            link_parts, remaining, crc = [], size, 0
            while remaining:
                data = reader.exact(min(remaining, _CHUNK_SIZE), "file payload")
                if digest is not None:
                    digest.update(data)
                elif kind == "symlink":
                    link_parts.append(data)
                if magic == b"070702":
                    crc = (crc + sum(data)) & 0xffffffff
                remaining -= len(data)
            reader.padding(-size % 4)
            if magic == b"070702" and crc != expected_crc:
                raise ValueError("Initramfs CPIO CRC mismatch: " + name)
            if kind == "regular":
                entry.update(sha256=digest.hexdigest(), size=size)
                if nlink > 1:
                    groups.setdefault((devmajor, devminor, ino), []).append(name)
            elif kind == "symlink":
                target = b"".join(link_parts)
                if b"\0" in target:
                    raise ValueError("Invalid initramfs CPIO symlink target")
                entry["link_target"] = target.decode("utf-8", errors="surrogateescape")
            elif kind in {"character-device", "block-device"}:
                entry.update(device_major=rdevmajor, device_minor=rdevminor)
            entries[name] = entry
    _cpio_hardlinks(entries, groups)
    files = [entries[name] for name in sorted(entries)]
    return {
        "schema_version": 1, "snapshot": "final-initramfs", "metadata": {},
        "packages": [], "files": files,
        "unowned_files": [entry["path"] for entry in files
                          if entry["kind"] in {"regular", "hardlink", "symlink"}],
        "kernel_modules": [entry.copy() for entry in files if _MODULE.search(entry["path"])],
    }


def write_inventory(tar_path, output_path, apk_manifest_path=None):
    """Write collect_rootfs() as stable UTF-8 JSON and return its contents."""
    inventory = collect_rootfs(tar_path, apk_manifest_path)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(inventory, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return inventory
