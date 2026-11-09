SUMMARY = "Early NAOS rootfs, initramfs template and component evidence"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

inherit deploy nopackages
require ${NAOS_LAYER_DIR}/recipes-core/images/naos-ota-keys.inc
INHIBIT_DEFAULT_DEPS = "1"
EXCLUDE_FROM_WORLD = "1"
PACKAGES = ""
COMPATIBLE_MACHINE = "${@'.*' if d.getVar('NAOS_PACKAGE') == '1' else '^$'}"
PACKAGE_ARCH = "${MACHINE_ARCH}"

FILESEXTRAPATHS:prepend := "${NAOS_LAYER_DIR}/recipes-core/images/files:${NAOS_PROJECT_ROOT}/meta-naos-security/recipes-core/images/files:"
SRC_URI = "file://assemble-naos-image.py file://naos_sbom_inventory.py"
SRC_URI:append = "${@' file://userdata-rootfs' if d.getVar('NAOS_DATA_ENCRYPTION') == '1' else ''}"
SRC_URI:append = "${@' file://system-rootfs' if d.getVar('NAOS_SYSTEM_ENCRYPTION') == '1' else ''}"
S = "${UNPACKDIR}"
B = "${WORKDIR}/build"

NAOS_PACKAGE_ROOTFS_OUTPUT = "${WORKDIR}/naos-package-rootfs"
NAOS_PACKAGE_ROOTFS_NAME ?= "naos-alpine-rootfs-aarch64"
NAOS_PACKAGE_MODULES_FILE ?= "modules-${MACHINE}.tgz"
NAOS_PACKAGE_OTA_OVERLAY ?= "${NAOS_LAYER_DIR}/recipes-naos/ota/files/rootfs"
NAOS_PACKAGE_LAYOUT_FILE ?= "${NAOS_PROJECT_ROOT}/meta-naos-rockchip/recipes-core/images/files/${MACHINE}.json"
NAOS_PACKAGE_INITRAMFS_RECIPE = "${@'naos-initramfs-encrypted' if d.getVar('NAOS_DATA_ENCRYPTION') == '1' else 'naos-initramfs'}"
NAOS_PACKAGE_INITRAMFS_FILE ?= "${NAOS_PACKAGE_INITRAMFS_RECIPE}-${MACHINE}.cpio.gz"
NAOS_PACKAGE_COMPONENT_RECIPES = "${PREFERRED_PROVIDER_virtual/kernel} ${PREFERRED_PROVIDER_virtual/bootloader} rauc rauc-hawkbit-updater naos-core naos-container ${NAOS_PACKAGE_INITRAMFS_RECIPE}"

do_configure[noexec] = "1"
do_compile[noexec] = "1"
do_install[noexec] = "1"

python do_package_inputs() {
    from pathlib import Path
    import naos_package.rootfs
    naos_package.rootfs.produce(d, Path(d.getVar('NAOS_PACKAGE_ROOTFS_OUTPUT')))
}
do_package_inputs[cleandirs] = "${NAOS_PACKAGE_ROOTFS_OUTPUT}"
do_package_inputs[depends] = "naos-alpine-rootfs:do_deploy virtual/kernel:do_deploy rauc:do_deploy rauc-hawkbit-updater:do_deploy naos-core:do_deploy naos-container:do_deploy ${NAOS_PACKAGE_INITRAMFS_RECIPE}:do_image_complete"
do_package_inputs[depends] += "${@'kernel-devsrc:do_deploy' if d.getVar('NAOS_BUILD_MODE') == 'development' else ''}"
do_package_inputs[depends] += "${@' '.join(pn + ':do_naos_sbom_component' for pn in d.getVar('NAOS_PACKAGE_COMPONENT_RECIPES').split()) if d.getVar('NAOS_SBOM') == '1' else ''}"
do_package_inputs[recrdeptask] = "${@'do_naos_sbom_component' if d.getVar('NAOS_SBOM') == '1' else ''}"
do_package_inputs[file-checksums] = "${NAOS_PACKAGE_LAYER_DIR}/lib/naos_package/rootfs.py:True ${NAOS_PACKAGE_LAYER_DIR}/lib/naos_package/sbom.py:True ${NAOS_PACKAGE_LAYOUT_FILE}:True ${NAOS_OTA_KEYRING}:True"
do_package_inputs[vardeps] += "NAOS_PACKAGE NAOS_SBOM NAOS_BUILD_MODE MACHINE NAOS_VERSION NAOS_BUILD_ID SOURCE_DATE_EPOCH NAOS_SECURE_BOOT NAOS_SYSTEM_ENCRYPTION NAOS_DATA_ENCRYPTION NAOS_PACKAGE_ROOTFS_NAME NAOS_PACKAGE_MODULES_FILE NAOS_PACKAGE_OTA_OVERLAY NAOS_PACKAGE_LAYOUT_FILE NAOS_PACKAGE_INITRAMFS_RECIPE NAOS_PACKAGE_INITRAMFS_FILE NAOS_PACKAGE_COMPONENT_RECIPES NAOS_OTA_KEYRING"
addtask package_inputs after do_unpack before do_deploy

python do_deploy() {
    from pathlib import Path
    import shutil
    shutil.copytree(Path(d.getVar('NAOS_PACKAGE_ROOTFS_OUTPUT')),
                    Path(d.getVar('DEPLOYDIR')) / 'naos-package-inputs', dirs_exist_ok=True)
}
addtask deploy after do_package_inputs before do_build

python __anonymous() {
    import os
    overlay = d.getVar('NAOS_PACKAGE_OTA_OVERLAY')
    if overlay:
        for directory, directories, files in os.walk(overlay):
            for filename in sorted(files):
                d.appendVarFlag('do_package_inputs', 'file-checksums',
                                ' ' + os.path.join(directory, filename) + ':True')
}
