FILESEXTRAPATHS:prepend := "${THISDIR}/files:"
SRC_URI:append = " file://naos-initramfs.cfg"
# The initramfs runs our /init and never uses BusyBox's inittab/getty setup.
RDEPENDS:${PN}:remove = "busybox-inittab"
