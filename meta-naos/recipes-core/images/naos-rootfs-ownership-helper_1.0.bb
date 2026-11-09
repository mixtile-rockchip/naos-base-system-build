SUMMARY = "Build-only ownership compatibility library for rootless Alpine assembly"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

SRC_URI = "file://rootfs-chown-compat.c"
S = "${UNPACKDIR}"
B = "${WORKDIR}/build"

# The target library is used through QEMU only while assembling rootfs. It is
# staged to the recipe sysroot and never installed in a shipped image.
inherit nopackages

do_compile() {
    ${CC} ${CFLAGS} ${LDFLAGS} -shared -fPIC ${S}/rootfs-chown-compat.c \
        -Wl,-soname,libnaos-chown-compat.so -o libnaos-chown-compat.so -ldl
}

do_install() {
    install -d ${D}${libdir}/naos-build
    install -m 0755 ${B}/libnaos-chown-compat.so ${D}${libdir}/naos-build/
}
SYSROOT_DIRS += "${libdir}/naos-build"
