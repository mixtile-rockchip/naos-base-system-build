SUMMARY = "NAOS early userspace for an unencrypted A/B Alpine system"
DESCRIPTION = "Mounts the selected SquashFS system with a persistent shared overlay. Security tools are available but the normal boot path does not invoke them."
LICENSE = "MIT"

inherit core-image

PACKAGE_INSTALL = " \
    base-files base-passwd busybox diffutils naos-initramfs-init naos-growpart \
    util-linux-blkid util-linux-sfdisk util-linux-partx util-linux-flock \
    f2fs-tools \
    cryptsetup libdevmapper keyutils \
    ${ROOTFS_BOOTSTRAP_INSTALL} \
"
IMAGE_FEATURES = ""
IMAGE_LINGUAS = ""
PACKAGE_EXCLUDE = "kernel-image-*"
PACKAGE_EXCLUDE:append = " busybox-inittab ttyrun"
IMAGE_FSTYPES = "cpio.gz"
IMAGE_NAME_SUFFIX = ""
IMAGE_ROOTFS_SIZE = "8192"
IMAGE_ROOTFS_EXTRA_SPACE = "0"
IMAGE_ROOTFS_MAXSIZE = "131072"
