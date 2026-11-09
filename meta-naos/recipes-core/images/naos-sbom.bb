SUMMARY = "SPDX 3.0 product SBOM for the assembled NAOS firmware"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

inherit deploy nopackages
INHIBIT_DEFAULT_DEPS = "1"
EXCLUDE_FROM_WORLD = "1"

NAOS_SBOM_OUTPUT = "${WORKDIR}/product-sbom"
NAOS_SBOM_KERNEL_RECIPE = "${PREFERRED_PROVIDER_virtual/kernel}"
NAOS_SBOM_BOOTLOADER_RECIPE = "${PREFERRED_PROVIDER_virtual/bootloader}"
NAOS_SBOM_INITRAMFS_RECIPE = "${@'naos-initramfs-encrypted' if d.getVar('NAOS_DATA_ENCRYPTION') == '1' else 'naos-initramfs'}"
NAOS_SBOM_COMPONENT_RECIPES = "${NAOS_SBOM_KERNEL_RECIPE} ${NAOS_SBOM_BOOTLOADER_RECIPE} rauc rauc-hawkbit-updater naos-core naos-container ${NAOS_SBOM_INITRAMFS_RECIPE}"

do_configure[noexec] = "1"
do_compile[noexec] = "1"
do_install[noexec] = "1"

python do_generate() {
    import naos.sbom_product
    if d.getVar('NAOS_SBOM') != '1':
        bb.fatal('naos-sbom requires NAOS_SBOM=1')
    summary = naos.sbom_product.generate(d)
    bb.note('Generated product SBOM: ' + summary['spdx_file'])
}
do_generate[cleandirs] = "${NAOS_SBOM_OUTPUT}"
do_generate[depends] = "naos-image:do_deploy virtual/kernel:do_deploy virtual/bootloader:do_deploy"
do_generate[depends] += "${@'naos-bundle:do_deploy naos-bootloader-bundle:do_deploy' if d.getVar('NAOS_OTA') == '1' else ''}"
do_generate[depends] += "${@' '.join(name + ':do_naos_sbom_component' for name in d.getVar('NAOS_SBOM_COMPONENT_RECIPES').split()) if d.getVar('NAOS_SBOM') == '1' else ''}"
do_generate[recrdeptask] += "do_naos_sbom_component"
do_generate[file-checksums] = "${@naos.sbom_metadata.dependencies(d)}"
do_generate[vardeps] += "NAOS_SBOM NAOS_OTA NAOS_SBOM_COMPONENT_RECIPES NAOS_BUILD_ID NAOS_VERSION NAOS_SECURE_BOOT NAOS_SYSTEM_ENCRYPTION NAOS_DATA_ENCRYPTION SOURCE_DATE_EPOCH"
addtask generate after do_unpack before do_deploy

do_deploy() {
    install -d ${DEPLOYDIR}
    cp ${NAOS_SBOM_OUTPUT}/* ${DEPLOYDIR}/
}
addtask deploy after do_generate before do_build
