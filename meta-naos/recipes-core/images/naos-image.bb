SUMMARY = "NAOS factory image with RAUC boot/system A/B slots"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

inherit deploy nopackages python3native
require naos-ota-keys.inc
INHIBIT_DEFAULT_DEPS = "1"
DEPENDS = "u-boot-tools-native dtc-native squashfs-tools-native"
SRC_URI = "file://assemble-naos-image.py"
SRC_URI:append = "${@' file://naos_sbom_inventory.py' if d.getVar('NAOS_SBOM') == '1' else ''}"
S = "${UNPACKDIR}"
B = "${WORKDIR}/build"

NAOS_LAYOUT_FILE ?= ""
NAOS_ROOTFS_NAME ?= "naos-alpine-rootfs-aarch64"
NAOS_KERNEL_FILE ?= "Image"
NAOS_DTB_FILE ?= "${MACHINE}.dtb"
NAOS_INITRAMFS_FILE ?= "naos-initramfs-${MACHINE}.cpio.gz"
NAOS_MODULES_FILE ?= "modules-${MACHINE}.tgz"
NAOS_OTA_OVERLAY ?= ""
NAOS_RAUC_RUNTIME ?= "${DEPLOY_DIR_IMAGE}/naos-rauc"
NAOS_CORE_RUNTIME ?= "${DEPLOY_DIR_IMAGE}/naos-core"
NAOS_FIT_MKIMAGE ?= "${STAGING_BINDIR_NATIVE}/mkimage"
NAOS_FIT_BUILDER ?= ""
NAOS_FIT_CHECK_SIGN ?= ""
NAOS_FIT_KEY_DTB ?= ""
NAOS_RESOURCE_FILE ?= ""

do_configure[noexec] = "1"
do_compile[noexec] = "1"
do_install[noexec] = "1"

python do_assemble() {
    import subprocess
    if not d.getVar("NAOS_LAYOUT_FILE") or not d.getVar("NAOS_OTA_OVERLAY"):
        bb.fatal("The platform layer must supply a partition layout and an OTA configuration overlay")
    if not d.getVar("NAOS_OTA_KEYRING"):
        bb.fatal("Set NAOS_OTA_KEYRING to the trusted OTA signing certificate (independent of secure boot)")
    deploy = d.getVar("DEPLOY_DIR_IMAGE")
    args = [
        d.expand("${PYTHON}"), d.expand("${UNPACKDIR}/assemble-naos-image.py"),
        "--input-dir", deploy, "--output-dir", d.expand("${B}/images"),
        "--layout", d.getVar("NAOS_LAYOUT_FILE"),
        "--machine", d.getVar("MACHINE"), "--version", d.getVar("NAOS_VERSION"),
        "--build-id", d.getVar("NAOS_BUILD_ID"),
        "--rootfs", d.getVar("NAOS_ROOTFS_NAME") + ".tar.gz",
        "--kernel", d.getVar("NAOS_KERNEL_FILE"), "--dtb", d.getVar("NAOS_DTB_FILE"),
        "--initramfs", d.getVar("NAOS_INITRAMFS_FILE"),
        "--modules", d.getVar("NAOS_MODULES_FILE"),
        "--overlay", d.getVar("NAOS_OTA_OVERLAY"),
        "--overlay", d.getVar("NAOS_RAUC_RUNTIME"),
        "--overlay", d.expand("${DEPLOY_DIR_IMAGE}/naos-hawkbit-updater"),
        "--overlay", d.expand("${DEPLOY_DIR_IMAGE}/naos-container"),
        "--overlay", d.getVar("NAOS_CORE_RUNTIME"),
        "--keyring", d.getVar("NAOS_OTA_KEYRING"),
        "--mkimage", d.getVar("NAOS_FIT_MKIMAGE"),
        "--sqfstar", d.expand("${STAGING_BINDIR_NATIVE}/sqfstar"),
        "--epoch", d.getVar("SOURCE_DATE_EPOCH") or "0",
        "--processors", d.getVar("ALPINE_ROOTFS_PROCESSORS") or "4",
    ]
    if d.getVar("NAOS_RESOURCE_FILE"):
        args += ["--resource", d.getVar("NAOS_RESOURCE_FILE")]
    if d.getVar("NAOS_SBOM") == "1":
        args.append("--sbom-inventory")
    if d.getVar("NAOS_SECURE_BOOT") == "1":
        for variable in ("NAOS_FIT_BUILDER", "NAOS_FIT_CHECK_SIGN", "NAOS_FIT_KEY_DTB", "NAOS_SECURE_BOOT_KEY_DIR"):
            if not d.getVar(variable):
                bb.fatal("%s is required by the secure boot FIT builder" % variable)
        args += ["--secure-boot", "--fit-builder", d.getVar("NAOS_FIT_BUILDER"),
                 "--fit-key-dir", d.getVar("NAOS_SECURE_BOOT_KEY_DIR"),
                 "--fit-check-sign", d.getVar("NAOS_FIT_CHECK_SIGN"),
                 "--fit-key-dtb", d.getVar("NAOS_FIT_KEY_DTB")]
    subprocess.run(args, check=True)
}
do_assemble[cleandirs] = "${B}/images"
do_assemble[depends] += "virtual/kernel:do_deploy virtual/bootloader:do_deploy rauc:do_deploy rauc-hawkbit-updater:do_deploy naos-container:do_deploy naos-core:do_deploy naos-initramfs:do_image_complete naos-alpine-rootfs:do_deploy"
do_assemble[vardeps] += "NAOS_VERSION NAOS_BUILD_ID NAOS_LAYOUT_FILE NAOS_OTA_OVERLAY NAOS_OTA_KEYRING NAOS_SECURE_BOOT NAOS_FIT_BUILDER NAOS_FIT_MKIMAGE NAOS_FIT_CHECK_SIGN NAOS_FIT_KEY_DTB NAOS_SECURE_BOOT_KEY_DIR NAOS_RESOURCE_FILE NAOS_SBOM"
addtask assemble after do_unpack do_prepare_recipe_sysroot before do_deploy

python __anonymous() {
    import os
    inputs = []
    for variable in ("NAOS_LAYOUT_FILE", "NAOS_OTA_KEYRING"):
        path = d.getVar(variable)
        if path:
            inputs.append(path + ":True")
    overlay = d.getVar("NAOS_OTA_OVERLAY")
    if overlay:
        for directory, directories, files in os.walk(overlay):
            for filename in sorted(files):
                inputs.append(os.path.join(directory, filename) + ":True")
    d.appendVarFlag("do_assemble", "file-checksums", " " + " ".join(inputs))
}

do_deploy() {
    install -d ${DEPLOYDIR}
    for artifact in ${B}/images/*; do
        name=$(basename "$artifact")
        case "$name" in
            uboot.img|idblock.bin|MiniLoaderAll.bin|naos-uboot-env.bin)
                # The BSP deploy task owns these shared files. Verify the
                # assembler used the same bytes without claiming them twice.
                cmp -s "$artifact" "${DEPLOY_DIR_IMAGE}/$name" || \
                    bbfatal "BSP deploy input changed: $name"
                ;;
            *) cp --sparse=always "$artifact" ${DEPLOYDIR}/ ;;
        esac
    done
}
addtask deploy after do_assemble before do_build

# Keep passthrough BSP artifacts available even when naos-image:do_deploy is
# restored from sstate and the intermediate assembly task is skipped.
do_build[depends] += "virtual/bootloader:do_deploy"
