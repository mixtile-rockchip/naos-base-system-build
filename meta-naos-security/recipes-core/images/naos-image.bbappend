FILESEXTRAPATHS:prepend := "${THISDIR}/files:"

SRC_URI:append = "${@' file://naos-sign-boot-fit.py' if d.getVar('NAOS_SECURE_BOOT') == '1' else ''}"
SRC_URI:append = "${@' file://0001-userdata-encrypted-image-overlay.patch file://userdata-rootfs' if d.getVar('NAOS_DATA_ENCRYPTION') == '1' else ''}"
SRC_URI:append = "${@' file://0002-system-encrypted-image.patch file://system_encryption.py file://system-rootfs' if d.getVar('NAOS_SYSTEM_ENCRYPTION') == '1' else ''}"
DEPENDS:append = "${@' python3-pycryptodomex-native' if d.getVar('NAOS_SYSTEM_ENCRYPTION') == '1' else ''}"

# These are host verification tools and the public control DTB from the exact
# bootloader build. Private keys are never copied into this deploy directory.
NAOS_FIT_BUILDER = "${@'${UNPACKDIR}/naos-sign-boot-fit.py' if d.getVar('NAOS_SECURE_BOOT') == '1' else ''}"
NAOS_FIT_MKIMAGE = "${@'${DEPLOY_DIR_IMAGE}/naos-secure-boot-tools/mkimage' if d.getVar('NAOS_SECURE_BOOT') == '1' else '${STAGING_BINDIR_NATIVE}/mkimage'}"
NAOS_FIT_CHECK_SIGN = "${DEPLOY_DIR_IMAGE}/naos-secure-boot-tools/fit_check_sign"
NAOS_FIT_KEY_DTB = "${DEPLOY_DIR_IMAGE}/naos-secure-boot-tools/u-boot.dtb"

python do_assemble:prepend() {
    if d.getVar("NAOS_SYSTEM_ENCRYPTION") == "1":
        import json
        from pathlib import Path
        path = Path(d.getVar("UNPACKDIR")) / "system-encryption-build.json"
        path.write_text(json.dumps({"key_directory": d.getVar("NAOS_SYSTEM_KEY_DIR")}) + "\n")
        path.chmod(0o600)
}

python __anonymous() {
    if d.getVar("NAOS_SECURE_BOOT") == "1":
        # A change to the same key path must invalidate the resulting FIT.
        keydir = d.getVar("NAOS_SECURE_BOOT_KEY_DIR") or ""
        if keydir:
            for name in ("dev.key", "dev.crt"):
                d.appendVarFlag("do_assemble", "file-checksums", " " + keydir + "/" + name + ":True")
    if d.getVar("NAOS_DATA_ENCRYPTION") == "1":
        d.setVar("NAOS_INITRAMFS_FILE", "naos-initramfs-encrypted-${MACHINE}.cpio.gz")
        dependencies = (d.getVarFlag("do_assemble", "depends") or "").split()
        dependencies = ["naos-initramfs-encrypted:do_image_complete" if item == "naos-initramfs:do_image_complete" else item for item in dependencies]
        d.setVarFlag("do_assemble", "depends", " ".join(dependencies))
        # Patch only this build's fetched assembler, before it consumes inputs.
        bb.build.addtask("do_assemble", "do_deploy", "do_patch", d)
    if d.getVar("NAOS_SYSTEM_ENCRYPTION") == "1":
        directory = d.getVar("NAOS_SYSTEM_KEY_DIR") or ""
        if not directory:
            bb.fatal("System encryption requires NAOS_SYSTEM_KEY_DIR")
        d.appendVarFlag("do_assemble", "file-checksums", " " + directory + "/system.key:True")
}
