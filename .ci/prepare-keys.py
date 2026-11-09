#!/usr/bin/env python3
"""Copy GitLab File variables into the existing NAOS key-directory layout."""

import os
from pathlib import Path
import re
import sys


FILES = {
    "NAOS_CI_OTA_PRIVATE_KEY": "ota/signing.key.pem",
    "NAOS_CI_OTA_CERTIFICATE": "ota/signing.cert.pem",
    "NAOS_CI_SECURE_BOOT_PRIVATE_KEY": "secure-boot/dev.key",
    "NAOS_CI_SECURE_BOOT_CERTIFICATE": "secure-boot/dev.crt",
    "NAOS_CI_SECURE_BOOT_PUBLIC_KEY": "secure-boot/dev.pubkey",
    "NAOS_CI_SYSTEM_KEY": "system-encryption/system.key",
    "NAOS_CI_SSH_CA_PUBLIC_KEY": "ssh-ca/ssh_ca_key.pub",
}


def prepare(destination):
    if not os.environ.get("NAOS_CI_OTA_CERTIFICATE"):
        raise ValueError("NAOS_CI_OTA_CERTIFICATE is required for OTA verification")
    for variable, relative in FILES.items():
        source = os.environ.get(variable)
        if not source:
            continue
        try:
            data = Path(source).read_bytes()
        except OSError:
            raise ValueError(f"{variable} must reference a readable CI File variable") from None
        if not data:
            raise ValueError(f"{variable} must not be empty")
        if variable == "NAOS_CI_SYSTEM_KEY" and not re.fullmatch(rb"[0-9a-f]{64}", data):
            raise ValueError(f"{variable} must contain exactly 64 lowercase hex bytes without a newline")
        target = destination / relative
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with target.open("xb") as stream:
            os.chmod(target, 0o600)
            stream.write(data)


def main():
    if len(sys.argv) != 2:
        raise ValueError("Usage: prepare-keys.py PRIVATE_DIRECTORY")
    destination = Path(sys.argv[1])
    if not destination.is_dir() or destination.is_symlink():
        raise ValueError("The temporary private directory must already exist")
    destination.chmod(0o700)
    prepare(destination)


if __name__ == "__main__":
    try:
        main()
    except ValueError as error:
        print(f"CI key preparation failed: {error}", file=sys.stderr)
        sys.exit(1)
