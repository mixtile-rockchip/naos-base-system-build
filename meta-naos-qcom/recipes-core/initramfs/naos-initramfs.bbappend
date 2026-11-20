PACKAGE_INSTALL:remove:radxa-airbox-q900 = "cryptsetup keyutils ${@'libdevmapper' if d.getVar('NAOS_DATA_ENCRYPTION') != '1' else ''}"
PACKAGE_INSTALL:append:radxa-airbox-q900 = "${@' naos-initramfs-q900-crypto' if d.getVar('NAOS_DATA_ENCRYPTION') == '1' else ''}"
