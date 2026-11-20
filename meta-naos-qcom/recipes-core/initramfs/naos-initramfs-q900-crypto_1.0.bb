SUMMARY = "NAOS early userspace controls for Q900 disk encryption"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

COMPATIBLE_MACHINE = "${@'^radxa-airbox-q900$' if d.getVar('NAOS_DATA_ENCRYPTION') == '1' else '^$'}"
PACKAGE_ARCH = "${MACHINE_ARCH}"
INHIBIT_DEFAULT_DEPS = "1"

FILESEXTRAPATHS:prepend := "${NAOS_LAYER_DIR}/../meta-naos-security/recipes-core/initramfs/files:"
SRC_URI = "file://userdata-crypto.sh"
SRC_URI:append = "${@' file://system-crypto.sh' if d.getVar('NAOS_SYSTEM_ENCRYPTION') == '1' else ''}"
S = "${UNPACKDIR}"

RDEPENDS:${PN} = "naos-data-key libdevmapper q900-keybox qcs9075-hwkm-keybox"

do_configure[noexec] = "1"
do_compile[noexec] = "1"
do_install[file-checksums] += "${@d.getVar('NAOS_SYSTEM_KEY_DIR') + '/system.key:True' if d.getVar('NAOS_SYSTEM_ENCRYPTION') == '1' else ''}"

python do_install() {
    import hashlib
    import os
    from pathlib import Path
    import re
    import shutil
    import stat

    scripts = Path(d.getVar("UNPACKDIR"))
    dest = Path(d.getVar("D")) / d.getVar("libdir").lstrip("/") / "naos"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(scripts / "userdata-crypto.sh", dest / "userdata-crypto.sh")
    (dest / "userdata-encryption").write_text("1\n")
    if d.getVar("NAOS_SYSTEM_ENCRYPTION") != "1":
        return

    path = Path(d.getVar("NAOS_SYSTEM_KEY_DIR")) / "system.key"
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise ValueError
            key = source.read(65)
            if not re.fullmatch(rb"[0-9a-f]{64}", key):
                raise ValueError
    except (OSError, ValueError):
        bb.fatal("System encryption requires a private ordinary system.key containing 64 lowercase hexadecimal characters")

    shutil.copyfile(scripts / "system-crypto.sh", dest / "system-crypto.sh")
    (dest / "system-encryption").write_text("1\n")
    (dest / "system-key.sha256").write_text(hashlib.sha256(key).hexdigest() + "\n")
}

FILES:${PN} = "${libdir}/naos"
