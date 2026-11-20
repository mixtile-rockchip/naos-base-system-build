FILESEXTRAPATHS:prepend := "${THISDIR}/files:"
SRC_URI:append:radxa-airbox-q900 = "${@' file://0001-validate-Q900-system-key-records.patch' if d.getVar('NAOS_SYSTEM_ENCRYPTION') == '1' else ''}"
CPPFLAGS:append:radxa-airbox-q900 = " -I${UNPACKDIR}/security-tool/qcs9075-hwkm-keybox"
