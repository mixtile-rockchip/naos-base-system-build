SUMMARY = "NAOS Rockchip vendor Linux kernel for Mixtile Core3588E v2 Nano"
LICENSE = "GPL-2.0-only"
LIC_FILES_CHKSUM = "file://COPYING;md5=6bc538ed5bd9a7fc9398086aedcd7e46"

inherit kernel naos-shallow-git naos-rockchip-sbom naos-cve-status

NAOS_CVE_STATUS_FILE := "${THISDIR}/linux-naos-rockchip-cve.ini"

DEPENDS += "bc-native bison-native flex-native openssl-native elfutils-native"
# Common board artwork is independent of the optional secure-boot layer.
NAOS_KERNEL_RESOURCE_DIR ?= "${NAOS_PROJECT_ROOT}/resource/picture"
FILESEXTRAPATHS:prepend := "${NAOS_KERNEL_RESOURCE_DIR}:"
SRC_URI = "${NAOS_RK_KERNEL_URI};destsuffix=kernel \
           file://defconfig \
           file://mixtile-core3588e-v2.dts \
           file://logo.bmp \
           file://logo_kernel.bmp \
           file://0001-bcmdhd-use-kbuild-source-directory-for-headers.patch \
           file://0002-bcmdhd-use-relative-build-tag.patch \
           file://0003-rockchip-system-monitor-include-extra-thermal-zones.patch \
           file://0004-dt-bindings-serial-describe-paired-rs485-gpios.patch \
           file://0005-serial-8250-dw-support-rs485-direction-gpios.patch"
SRCREV = "37a8041e0edc9bb53e60994a875ffee7936f4074"
S = "${UNPACKDIR}/kernel"
B = "${WORKDIR}/build"
COMPATIBLE_MACHINE = "^mixtile-core3588e-v2$"
KERNEL_VERSION_SANITY_SKIP = "1"
# Keep the upstream release for uname and the matching module directory.
KERNEL_LOCALVERSION = ""
KERNEL_DTBVENDORED = "0"

# Indexes generated below are delivered in the module tarball and must also
# have an owner when OE splits kernel packages for the initramfs feed.
FILES:${KERNEL_PACKAGE_NAME}-base += "${nonarch_base_libdir}/modules/${KERNEL_VERSION}/modules.*"

python () {
    if not d.getVar("NAOS_RK_KERNEL_URI"):
        bb.fatal("Set NAOS_RK_KERNEL_URI to a Git source containing the pinned SRCREV; local overrides may be supplied through build/local.yml.")
}

do_configure:prepend() {
    install -m 0644 ${UNPACKDIR}/mixtile-core3588e-v2.dts \
        ${S}/arch/arm64/boot/dts/rockchip/mixtile-core3588e-v2.dts
}

# The vendor %.img target also builds Android boot images. NAOS only needs its
# resource container: use the same Kbuild-built host tool with the finished DTB
# and artwork in B, without changing the original SDK or generating another FIT.
do_resource() {
    set -- ${KERNEL_DEVICETREE}
    [ "$#" -eq 1 ] || bbfatal "NAOS resource.img requires exactly one board DTB"
    resource_dtb="${B}/arch/${ARCH}/boot/dts/$1"
    test -s "$resource_dtb" || bbfatal "The kernel DTB is missing for resource.img"
    test -x ${B}/scripts/resource_tool || bbfatal "The kernel resource_tool was not built"
    install -m 0644 ${UNPACKDIR}/logo.bmp ${B}/logo.bmp
    install -m 0644 ${UNPACKDIR}/logo_kernel.bmp ${B}/logo_kernel.bmp
    ${B}/scripts/resource_tool --pack --image=${B}/resource.img --root=${B} \
        "$resource_dtb" ${B}/logo.bmp ${B}/logo_kernel.bmp
    test -s ${B}/resource.img
}
do_resource[dirs] = "${B}"
addtask resource after do_compile before do_deploy

do_deploy:append() {
    install -m 0644 ${B}/resource.img ${DEPLOYDIR}/resource.img
}

# NAOS consumes the module tarball directly rather than installing kernel
# packages into an OE rootfs. Generate the indexes normally produced by the
# kernel package postinstall before kernel_do_deploy archives the modules.
# kernel.bbclass already stages kmod-native for the native depmod executable.
do_install:append() {
    if [ -d ${D}${nonarch_base_libdir}/modules/${KERNEL_VERSION} ]; then
        depmod -a -b ${D} -F ${B}/System.map ${KERNEL_VERSION}
        test -f ${D}${nonarch_base_libdir}/modules/${KERNEL_VERSION}/modules.dep
        test -f ${D}${nonarch_base_libdir}/modules/${KERNEL_VERSION}/modules.alias
    fi
}

# The BSP uses an external FIT initramfs; it is packaged by naos-image.
INITRAMFS_IMAGE_BUNDLE = "0"
