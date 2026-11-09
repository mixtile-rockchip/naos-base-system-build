SUMMARY = "Signed RAUC bundle for paired NAOS FIT and SquashFS slots"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

inherit bundle
require naos-bundle-signing.inc
RAUC_BUNDLE_SLOTS = "rootfs boot"
# Also preserve the factory recipe's BSP restoration dependencies on sstate hits.
RAUC_BUNDLE_EXTRA_DEPENDS = "naos-image:do_build"
RAUC_SLOT_rootfs = "naos-image"
RAUC_SLOT_rootfs[type] = "file"
RAUC_SLOT_rootfs[file] = "system.squashfs"
RAUC_SLOT_rootfs[rename] = "system.squashfs"
RAUC_SLOT_rootfs[imagetype] = "raw"
RAUC_SLOT_boot = "naos-image"
RAUC_SLOT_boot[type] = "file"
RAUC_SLOT_boot[file] = "boot.img"
RAUC_SLOT_boot[rename] = "boot.img"
RAUC_SLOT_boot[imagetype] = "raw"
