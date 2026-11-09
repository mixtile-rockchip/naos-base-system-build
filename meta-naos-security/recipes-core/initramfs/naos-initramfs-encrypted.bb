require ${NAOS_LAYER_DIR}/recipes-core/initramfs/naos-initramfs.bb

SUMMARY = "NAOS early userspace with TEE Trusted Keys userdata encryption"
DESCRIPTION = "Extends the base initramfs with a separate encrypted-userdata init package."
COMPATIBLE_MACHINE = "${@'^mixtile-core3588e-v2$' if d.getVar('NAOS_DATA_ENCRYPTION') == '1' else '^$'}"

PACKAGE_INSTALL:remove = "naos-initramfs-init"
PACKAGE_INSTALL:append = " naos-initramfs-encrypted-init naos-data-key"
PACKAGE_INSTALL:append = "${@' rk-huk-keybox optee-client-naos' if d.getVar('NAOS_SYSTEM_ENCRYPTION') == '1' else ''}"
IMAGE_BASENAME = "naos-initramfs-encrypted"
