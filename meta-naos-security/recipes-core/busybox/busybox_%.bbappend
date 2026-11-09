# Bound HUK-envelope RPC operations only in the explicit system-encrypted profile.
FILESEXTRAPATHS:prepend := "${THISDIR}/files:"
SRC_URI:append = "${@' file://system-crypto.cfg' if d.getVar('NAOS_SYSTEM_ENCRYPTION') == '1' else ''}"

do_configure:append() {
    if [ "${NAOS_SYSTEM_ENCRYPTION}" = 1 ]; then
        grep -qx 'CONFIG_TIMEOUT=y' ${B}/.config || bbfatal "Encrypted system init requires BusyBox timeout"
    fi
}
