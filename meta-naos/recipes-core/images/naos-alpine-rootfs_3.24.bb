SUMMARY = "NAOS Alpine ARM64 base root filesystem"
DESCRIPTION = "Assemble Alpine's official minirootfs and pinned APK packages, without a kernel or kernel modules."
# This license covers the integration recipe. Bundled packages retain the
# licenses recorded in the generated installed-package manifest.
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

inherit alpine-rootfs
require conf/distro/include/naos-alpine-base.inc

ALPINE_BRANCH = "v3.24"
ALPINE_SEED_VERSION = "3.24.2"
ALPINE_SEED_FILENAME = "alpine-minirootfs-${ALPINE_SEED_VERSION}-${ALPINE_ARCH}.tar.gz"
ALPINE_SEED_SHA256 = "9bf70a7f18ea44094cbb5f70c58f9af129c8214745743db0e68e5502cc2ce773"
ALPINE_MIRROR ?= "https://dl-cdn.alpinelinux.org/alpine"
# Retain checksum-pinned APKs after the rolling upstream repositories replace them.
MIRRORS:prepend = "https://dl-cdn[.]alpinelinux[.]org/alpine/(.*) https://packages.naos.ai/sources/alpine/\1 \n"

SRC_URI = " \
    ${ALPINE_MIRROR}/${ALPINE_BRANCH}/releases/${ALPINE_ARCH}/${ALPINE_SEED_FILENAME};name=seed;unpack=0 \
    file://assemble-alpine-rootfs.py \
    file://repositories \
"
SRC_URI[seed.sha256sum] = "${ALPINE_SEED_SHA256}"

# Build helper only: its official signature is verified before extraction. It
# does not become a package or library in the delivered rootfs.
ALPINE_FAKEROOT_FILENAME = "fakeroot-1.37.2-r0.apk"
SRC_URI += "${ALPINE_MIRROR}/${ALPINE_BRANCH}/main/${ALPINE_ARCH}/${ALPINE_FAKEROOT_FILENAME};name=buildfakeroot;unpack=0"
SRC_URI[buildfakeroot.sha256sum] = "025a98bdef543fa59218d9761b0928c64edd74d75b27b3881a6ced4817bf9dc6"

ALPINE_APK_LOCKFILE := "${THISDIR}/alpine-3.24-aarch64-packages.inc"
require alpine-3.24-aarch64-packages.inc


# Empty expands to zero filenames in BitBake. A configured product APK lock
# must exist; its absence must not silently omit an updater or its trust key.
require ${ALPINE_EXTRA_APK_LOCK}
