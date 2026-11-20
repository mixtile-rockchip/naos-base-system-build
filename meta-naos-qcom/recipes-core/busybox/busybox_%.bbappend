FILESEXTRAPATHS:prepend := "${NAOS_LAYER_DIR}/../meta-naos-security/recipes-core/busybox/files:"
SRC_URI:append:radxa-airbox-q900 = "${@' file://system-crypto.cfg' if d.getVar('NAOS_SYSTEM_ENCRYPTION') == '1' else ''}"

do_configure:append:radxa-airbox-q900() {
    if [ "${NAOS_SYSTEM_ENCRYPTION}" = 1 ]; then
        grep -qx 'CONFIG_TIMEOUT=y' ${B}/.config ||
            bbfatal "Encrypted system init requires BusyBox timeout"
    fi
}
