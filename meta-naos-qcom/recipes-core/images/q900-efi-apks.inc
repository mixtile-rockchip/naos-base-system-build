# EFI boot entry management in the Q900 Alpine rootfs.
SRC_URI += " \
    ${ALPINE_MIRROR}/${ALPINE_BRANCH}/main/${ALPINE_ARCH}/efibootmgr-18-r2.apk;name=q900efibootmgr;unpack=0 \
    ${ALPINE_MIRROR}/${ALPINE_BRANCH}/main/${ALPINE_ARCH}/efivar-libs-39-r1.apk;name=q900efivar;unpack=0 \
    ${ALPINE_MIRROR}/${ALPINE_BRANCH}/main/${ALPINE_ARCH}/popt-1.19-r4.apk;name=q900popt;unpack=0 \
"
SRC_URI[q900efibootmgr.sha256sum] = "36fb6b48930cf9e411df7c303973fc4620b70abbff6766ae824ed3a3d802f4b1"
SRC_URI[q900efivar.sha256sum] = "ee9f3633d3cc99c92faf9c832efda4aaf7c63f5899bea3d3c2f962a89852fbc6"
SRC_URI[q900popt.sha256sum] = "5eb2037c453c870f31a1db4f1235f8ac2a27f8d401421cb5662f2ff6f1bea94b"
