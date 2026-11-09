SUMMARY = "NAOS slot selection and persistent overlay init"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

FILESEXTRAPATHS:prepend := "${THISDIR}/../../recipes-naos/ota/files/rootfs/usr/lib/naos:"
SRC_URI = "file://init file://storage.sh file://recovery.sh file://initramfs-debug"
S = "${UNPACKDIR}"

NAOS_INITRAMFS_DEBUG_SHELL ?= "${@'1' if d.getVar('NAOS_BUILD_MODE') == 'development' else '0'}"

inherit allarch

# This package only installs shell scripts; no target program is compiled.
do_configure[noexec] = "1"
do_compile[noexec] = "1"

RDEPENDS:${PN} = "busybox diffutils naos-growpart util-linux-blkid util-linux-sfdisk util-linux-partx util-linux-fallocate f2fs-tools"

do_install() {
    install -d ${D}${libdir}/naos
    install -m 0755 ${UNPACKDIR}/init ${D}/init
    install -m 0644 ${UNPACKDIR}/storage.sh ${D}${libdir}/naos/storage.sh
    install -m 0644 ${UNPACKDIR}/recovery.sh ${D}${libdir}/naos/recovery.sh
    if [ "${NAOS_BUILD_MODE}" = development ] && [ "${NAOS_INITRAMFS_DEBUG_SHELL}" = 1 ]; then
        install -m 0755 ${UNPACKDIR}/initramfs-debug ${D}${libdir}/naos/initramfs-debug
    fi
}

FILES:${PN} = "/init ${libdir}/naos/storage.sh ${libdir}/naos/recovery.sh ${libdir}/naos/initramfs-debug"
