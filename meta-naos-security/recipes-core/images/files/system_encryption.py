#!/usr/bin/env python3
"""Build the legacy NAOS sector-CBC system image and its factory import record.

Only ordinary build files are opened. No host device, loop, TEE or RPMB access.
Actual key material is read from private local inputs and never logged.
"""

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import tempfile

from Cryptodome.Cipher import AES

SECTOR = 512
IMPORT_OFFSET = 0x20000
IMPORT_BYTES = 0x10000
BLOB_REGIONS = (("system_key_blob_a", 0x50000), ("system_key_blob_b", 0x60000))


def require(value, message):
    if not value:
        raise ValueError(message)


def private_read(path, maximum, secret=False):
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode), "Encryption input must be an ordinary file")
        require(0 < info.st_size <= maximum, "Encryption input has an invalid length")
        if secret:
            require(info.st_mode & 0o077 == 0, "System key permissions must exclude group and other access")
        data = os.read(fd, maximum + 1)
        require(len(data) == info.st_size, "Encryption input changed or could not be read completely")
        return data
    finally:
        os.close(fd)


def read_material(directory):
    directory = Path(directory)
    key_hex = private_read(directory / "system.key", 64, secret=True)
    require(re.fullmatch(rb"[0-9a-f]{64}", key_hex), "System key must contain exactly 64 lowercase hex characters")
    return {"key_hex": key_hex, "key": bytes.fromhex(key_hex.decode("ascii")),
            "key_sha256": hashlib.sha256(key_hex).hexdigest()}


def load_build_config(path):
    data = json.loads(Path(path).read_text())
    require(set(data) == {"key_directory"}, "Invalid system encryption build configuration")
    directory = Path(data["key_directory"])
    require(directory.is_absolute(), "System encryption key directory must be absolute")
    return read_material(directory)


def sector_iv(number):
    require(isinstance(number, int) and number >= 0, "Invalid encryption sector number")
    return struct.pack("<I", number & 0xFFFFFFFF) + bytes(12)


def crypt_sectors(data, key, first_sector=0, decrypt=False):
    require(len(key) == 32 and len(data) % SECTOR == 0, "Invalid sector cipher input")
    result = bytearray()
    for offset in range(0, len(data), SECTOR):
        cipher = AES.new(key, AES.MODE_CBC, sector_iv(first_sector + offset // SECTOR))
        block = data[offset:offset + SECTOR]
        result.extend(cipher.decrypt(block) if decrypt else cipher.encrypt(block))
    return bytes(result)


def make_import_record(key_hex):
    if isinstance(key_hex, str):
        key_hex = key_hex.encode("ascii")
    require(re.fullmatch(rb"[0-9a-f]{64}", key_hex), "Invalid import key format")
    payload = b"NAOS-SYSTEM-KEY\nversion=2\nkey=" + key_hex + b"\n"
    require(len(payload) <= 256, "System import header exceeds its fixed region")
    payload = payload.ljust(256, b" ")
    record = payload + hashlib.sha256(payload).hexdigest().encode("ascii") + bytes(192)
    return record.ljust(IMPORT_BYTES, b"\0")


def encrypt_system(source, destination, key, alignment=4096):
    source, destination = Path(source), Path(destination)
    require(source.is_file() and not source.is_symlink(), "System source must be an ordinary file")
    require(source.resolve() != destination.resolve(), "Cipher input and output must differ")
    require(alignment > 0 and alignment % SECTOR == 0, "Invalid system cipher alignment")
    source_bytes = source.stat().st_size
    require(source_bytes >= 96, "System source is too small to contain SquashFS")
    with source.open("rb") as stream:
        require(stream.read(4) == b"hsqs", "System source is not SquashFS")
    padded_bytes = (source_bytes + alignment - 1) // alignment * alignment
    expected_hash = hashlib.sha256()
    fd, temporary = tempfile.mkstemp(prefix=".system-cipher-", dir=destination.parent)
    try:
        with source.open("rb") as plain, os.fdopen(fd, "wb") as encrypted:
            for sector in range(padded_bytes // SECTOR):
                data = plain.read(SECTOR)
                expected_hash.update(data)
                encrypted.write(crypt_sectors(data.ljust(SECTOR, b"\0"), key, sector))
            require(not plain.read(1), "System source grew during encryption")
            encrypted.flush()
            os.fsync(encrypted.fileno())
        # Read back and decrypt the written payload before publishing it.
        actual_hash = hashlib.sha256()
        ciphertext_hash = hashlib.sha256()
        with open(temporary, "rb") as encrypted:
            for sector in range(padded_bytes // SECTOR):
                data = encrypted.read(SECTOR)
                require(len(data) == SECTOR, "Truncated encrypted system payload")
                ciphertext_hash.update(data)
                plain = crypt_sectors(data, key, sector, decrypt=True)
                meaningful = max(0, min(SECTOR, source_bytes - sector * SECTOR))
                actual_hash.update(plain[:meaningful])
                require(not any(plain[meaningful:]), "Encrypted system padding did not round-trip")
            require(not encrypted.read(1), "Encrypted system payload has trailing bytes")
        require(actual_hash.digest() == expected_hash.digest(), "Encrypted system readback differs from SquashFS")
        os.chmod(temporary, 0o644)
        os.replace(temporary, destination)
        return {"plain_bytes": source_bytes, "encrypted_bytes": padded_bytes,
                "ciphertext_sha256": ciphertext_hash.hexdigest()}
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def encrypted_layout(layout):
    layout = copy.deepcopy(layout)
    for part in layout["partitions"]:
        if part["name"] in ("system_a", "system_b"):
            require(part.get("payload") == "system.squashfs", "Unexpected system partition payload")
            part["payload"] = "system.img"
    # Add system-key storage only to the encrypted image's private layout.
    # The base/development layout and userdata key records remain unchanged.
    regions = layout["misc_regions"]
    names = [region["name"] for region in regions]
    require(len(names) == len(set(names)), "Duplicate misc region names")
    for name, offset in BLOB_REGIONS:
        require(name not in names, "System-key blob region is already declared")
        regions.append({"name": name, "offset": offset, "size": IMPORT_BYTES})
    regions.sort(key=lambda item: item["offset"])
    misc = next(part for part in layout["partitions"] if part["name"] == "misc")
    end = 0
    for region in regions:
        require(region["offset"] >= end and region["size"] > 0,
                "Overlapping or invalid misc regions")
        end = region["offset"] + region["size"]
    require(end <= misc["size_mib"] * 1024 * 1024, "Misc regions exceed the partition")
    return layout


def private_workdir(output):
    directory = Path(output).parent / "system-private"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    require(directory.is_dir() and not directory.is_symlink(), "Invalid private system work directory")
    directory.chmod(0o700)
    # Unlike the public image directory this directory survives do_assemble's
    # cleandirs. Do not let sqfstar append to a previous build's filesystem.
    for name in ("system.tar.gz", "system.squashfs"):
        path = directory / name
        require(not path.is_symlink(), "Private system intermediate must not be a symlink")
        if path.exists():
            require(path.is_file(), "Private system intermediate must be an ordinary file")
            path.unlink()
    return directory


def finish_system(plain, output, material):
    metadata = encrypt_system(plain, Path(output) / "system.img", material["key"])
    public = {"format": 1, "cipher": "aes-cbc-plain", "key_bytes": 32, "sector_bytes": SECTOR,
              "iv_offset_sectors": 0, "data_offset_sectors": 0, "key_storage": "misc-huk-blob",
              "key_sha256": material["key_sha256"], **metadata}
    (Path(output) / "system-encryption.json").write_text(json.dumps(public, sort_keys=True, indent=2) + "\n")


def write_factory_import(disk, layout, offsets, material):
    region = next(item for item in layout["misc_regions"] if item["name"] == "system_key_import")
    require(region["offset"] == IMPORT_OFFSET and region["size"] == IMPORT_BYTES,
            "Unexpected system-key import region")
    misc_start, misc_size = offsets["misc"]
    require(IMPORT_OFFSET + IMPORT_BYTES <= misc_size, "System-key import exceeds misc")
    record = make_import_record(material["key_hex"])
    disk.seek(misc_start + IMPORT_OFFSET)
    require(disk.read(IMPORT_BYTES) == bytes(IMPORT_BYTES), "System-key import region is already occupied")
    disk.seek(misc_start + IMPORT_OFFSET)
    require(disk.write(record) == IMPORT_BYTES, "Could not write complete system-key import record")
    disk.flush()
    disk.seek(misc_start + IMPORT_OFFSET)
    require(disk.read(IMPORT_BYTES) == record, "System-key import readback differs")
