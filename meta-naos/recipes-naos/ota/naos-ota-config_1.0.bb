SUMMARY = "NAOS RAUC slot configuration and configurable boot confirmation"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"
SRC_URI = "file://rootfs"
S = "${UNPACKDIR}"
inherit allarch

do_install() {
    cp -R --no-preserve=ownership ${UNPACKDIR}/rootfs/. ${D}/
}

FILES:${PN} = "${sysconfdir}/fstab ${sysconfdir}/motd ${sysconfdir}/conf.d/localmount ${sysconfdir}/rauc ${sysconfdir}/fw_env.config ${sysconfdir}/init.d/rauc ${sysconfdir}/init.d/naos-boot-confirm ${libdir}/naos"
