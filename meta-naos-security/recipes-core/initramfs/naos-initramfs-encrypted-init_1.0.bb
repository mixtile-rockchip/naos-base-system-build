require ${NAOS_LAYER_DIR}/recipes-core/initramfs/naos-initramfs-init_1.0.bb

SUMMARY = "NAOS userdata-encrypted initramfs entrypoint"
COMPATIBLE_MACHINE = "${@'^mixtile-core3588e-v2$' if d.getVar('NAOS_DATA_ENCRYPTION') == '1' else '^$'}"
FILESEXTRAPATHS:prepend := "${THISDIR}/files:${THISDIR}/../images/files:${NAOS_LAYER_DIR}/recipes-core/initramfs/files:"
SRC_URI += "file://init-userdata-encrypted file://userdata-crypto.sh file://userdata-rootfs"
SRC_URI:append = "${@' file://system-crypto.sh file://system-rootfs' if d.getVar('NAOS_SYSTEM_ENCRYPTION') == '1' else ''}"
RDEPENDS:${PN} += "naos-data-key keyutils cryptsetup libdevmapper"
RDEPENDS:${PN}:append = "${@' rk-huk-keybox optee-client-naos' if d.getVar('NAOS_SYSTEM_ENCRYPTION') == '1' else ''}"
RCONFLICTS:${PN} = "naos-initramfs-init"

do_install:append() {
    install -m 0755 ${UNPACKDIR}/init-userdata-encrypted ${D}/init
    install -m 0644 ${UNPACKDIR}/userdata-crypto.sh ${D}${libdir}/naos/userdata-crypto.sh
    install -m 0644 ${UNPACKDIR}/userdata-rootfs/usr/lib/naos/storage.sh ${D}${libdir}/naos/storage.sh
    printf '1\n' > ${D}${libdir}/naos/userdata-encryption
    if [ "${NAOS_SYSTEM_ENCRYPTION}" = 1 ]; then
        install -m 0644 ${UNPACKDIR}/system-crypto.sh ${D}${libdir}/naos/system-crypto.sh
        install -m 0644 ${UNPACKDIR}/system-rootfs/usr/lib/naos/storage.sh ${D}${libdir}/naos/storage.sh
        printf '1\n' > ${D}${libdir}/naos/system-encryption
    fi
}

FILES:${PN} += "${libdir}/naos/userdata-crypto.sh ${libdir}/naos/userdata-encryption"
FILES:${PN} += "${libdir}/naos/system-crypto.sh ${libdir}/naos/system-encryption"
