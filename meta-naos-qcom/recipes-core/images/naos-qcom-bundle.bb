SUMMARY = "Signed RAUC bundle for the Q900 slot UKIs and SquashFS system"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

inherit bundle
require recipes-core/images/naos-bundle-signing.inc

COMPATIBLE_MACHINE = "^radxa-airbox-q900$"

SRC_URI = "file://q900-rauc-hook"
RAUC_BUNDLE_HOOKS[file] = "q900-rauc-hook"
RAUC_BUNDLE_SLOTS = "rootfs boot"

RAUC_SLOT_rootfs = "naos-qcom-image"
RAUC_SLOT_rootfs[depends] = "naos-qcom-image:do_image_complete"
RAUC_SLOT_rootfs[type] = "file"
RAUC_SLOT_rootfs[file] = "naos-q900-system.squashfs"
RAUC_SLOT_rootfs[rename] = "system.squashfs"
RAUC_SLOT_rootfs[imagetype] = "raw"

RAUC_SLOT_boot = "naos-q900-boot"
RAUC_SLOT_boot[type] = "file"
RAUC_SLOT_boot[file] = "naos-q900-boot-payload.tar"
RAUC_SLOT_boot[rename] = "boot.tar"
RAUC_SLOT_boot[imagetype] = "tar"
RAUC_SLOT_boot[hooks] = "install"

python do_configure:prepend() {
    import tarfile
    from pathlib import Path

    deploy = Path(d.getVar("DEPLOY_DIR_IMAGE"))
    payload = Path(d.getVar("UNPACKDIR")) / d.getVarFlag("RAUC_SLOT_boot", "file")
    with tarfile.open(payload, "w", format=tarfile.USTAR_FORMAT) as archive:
        for slot in ("a", "b"):
            uki = deploy / ("naos-q900-%s.efi" % slot)
            if not uki.is_file() or not uki.stat().st_size:
                bb.fatal("Missing or empty Q900 UKI: %s" % uki)
            member = tarfile.TarInfo(uki.name)
            member.size = uki.stat().st_size
            with uki.open("rb") as source:
                archive.addfile(member, source)
}
