#!/usr/bin/env python3
"""Assemble the Q900 system ciphertext and factory-only import image."""

import argparse
import os
from pathlib import Path
import subprocess
import tempfile
from xml.etree import ElementTree

from system_encryption import (
    IMPORT_BYTES,
    IMPORT_OFFSET,
    encrypt_system,
    read_material,
    require,
    write_factory_import,
)

MISC_BYTES = 4 * 1024 * 1024


def build_system(args, material):
    environment = dict(os.environ, SOURCE_DATE_EPOCH=str(int(args.root.stat().st_mtime)))
    with tempfile.TemporaryDirectory(dir=args.work_directory, prefix="system-private.") as private:
        plaintext = Path(private) / "system.squashfs"
        subprocess.run(
            ["mksquashfs", str(args.root), str(plaintext), "-noappend", *args.squashfs_options],
            env=environment,
            check=True,
        )
        encrypt_system(plaintext, args.system_image, material["key"], alignment=4096)
    args.key_fingerprint.write_text(material["key_sha256"] + "\n")


def prepare_factory(args, material):
    require(args.key_fingerprint.read_text().strip() == material["key_sha256"],
            "Q900 factory import key differs from the encrypted system key")
    parser = ElementTree.XMLParser(target=ElementTree.TreeBuilder(insert_comments=True))
    tree = ElementTree.parse(args.rawprogram, parser=parser)
    filenames = {
        "system_a": ("naos-q900-system.squashfs", args.system_image.name),
        "system_b": ("naos-q900-system.squashfs", args.system_image.name),
        "misc": ("zeros_misc.bin", args.misc.name),
    }
    for label, (original, replacement) in filenames.items():
        entries = tree.findall(f"./program[@label='{label}']")
        require(len(entries) == 1 and entries[0].get("filename") == original and
                entries[0].get("physical_partition_number") == "0",
                f"Unexpected Q900 factory flash entry for {label}")
        entries[0].set("filename", replacement)

    layout = {"misc_regions": [
        {"name": "system_key_import", "offset": IMPORT_OFFSET, "size": IMPORT_BYTES},
    ]}
    fd = os.open(args.misc, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w+b") as misc:
        misc.truncate(MISC_BYTES)
        write_factory_import(misc, layout, {"misc": (0, MISC_BYTES)}, material)
    tree.write(args.rawprogram, encoding="utf-8", xml_declaration=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key-directory", required=True, type=Path)
    parser.add_argument("--key-fingerprint", required=True, type=Path)
    commands = parser.add_subparsers(required=True)
    image = commands.add_parser("image")
    image.add_argument("root", type=Path)
    image.add_argument("system_image", type=Path)
    image.add_argument("work_directory", type=Path)
    image.add_argument("squashfs_options", nargs=argparse.REMAINDER)
    image.set_defaults(run=build_system)
    factory = commands.add_parser("factory")
    factory.add_argument("rawprogram", type=Path)
    factory.add_argument("system_image", type=Path)
    factory.add_argument("misc", type=Path)
    factory.set_defaults(run=prepare_factory)
    args = parser.parse_args()
    require(args.key_directory.is_absolute(), "System key directory must be absolute")
    args.run(args, read_material(args.key_directory))


if __name__ == "__main__":
    main()
