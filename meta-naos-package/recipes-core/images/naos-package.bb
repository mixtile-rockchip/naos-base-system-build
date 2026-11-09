SUMMARY = "Portable NAOS image-generator inputs"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

inherit deploy nopackages
INHIBIT_DEFAULT_DEPS = "1"
EXCLUDE_FROM_WORLD = "1"
PACKAGES = ""

NAOS_PACKAGE_INPUT_DIR = "${DEPLOY_DIR_IMAGE}/naos-package-inputs"
NAOS_PACKAGE_OUTPUT = "${WORKDIR}/generator-inputs"
NAOS_PACKAGE_DESTINATION = "${NAOS_PROJECT_ROOT}/package/${NAOS_BUILD_MODE}/${MACHINE}"
NAOS_LAYOUT_FILE ?= ""

do_configure[noexec] = "1"
do_compile[noexec] = "1"
do_install[noexec] = "1"

python do_collect() {
    from pathlib import Path
    import naos_package.export

    if d.getVar('NAOS_PACKAGE') != '1':
        bb.fatal('Enable NAOS_PACKAGE=1 to export image-generator inputs')
    settings = {
        'machine': d.getVar('MACHINE'), 'profile': d.getVar('NAOS_BUILD_MODE'),
        'version': d.getVar('NAOS_VERSION'), 'build_id': d.getVar('NAOS_BUILD_ID'),
        'architecture': d.getVar('TARGET_ARCH'),
        'sbom': d.getVar('NAOS_SBOM') == '1',
        'secure_boot': d.getVar('NAOS_SECURE_BOOT') == '1',
        'system_encryption': d.getVar('NAOS_SYSTEM_ENCRYPTION') == '1',
        'data_encryption': d.getVar('NAOS_DATA_ENCRYPTION') == '1',
        'ota': d.getVar('NAOS_OTA') == '1',
        'dtb': d.getVar('MACHINE') + '.dtb',
    }
    naos_package.export.collect(
        Path(d.getVar('NAOS_PACKAGE_INPUT_DIR')), Path(d.getVar('DEPLOY_DIR_IMAGE')),
        Path(d.getVar('NAOS_LAYOUT_FILE')), Path(d.getVar('NAOS_PACKAGE_OUTPUT')), settings)
}
do_collect[cleandirs] = "${NAOS_PACKAGE_OUTPUT}"
do_collect[depends] = "naos-package-rootfs:do_deploy virtual/kernel:do_deploy virtual/bootloader:do_deploy"
do_collect[file-checksums] = "${NAOS_LAYOUT_FILE}:True ${NAOS_PACKAGE_LAYER_DIR}/lib/naos_package/export.py:True"
do_collect[vardeps] += "NAOS_PACKAGE MACHINE NAOS_BUILD_MODE NAOS_VERSION NAOS_BUILD_ID TARGET_ARCH NAOS_SBOM NAOS_SECURE_BOOT NAOS_SYSTEM_ENCRYPTION NAOS_DATA_ENCRYPTION NAOS_OTA NAOS_LAYOUT_FILE"
addtask collect after do_unpack before do_deploy

do_deploy() {
    install -d ${DEPLOYDIR}/naos-package/${NAOS_BUILD_MODE}
    cp -R ${NAOS_PACKAGE_OUTPUT}/. ${DEPLOYDIR}/naos-package/${NAOS_BUILD_MODE}/
}
addtask deploy after do_collect before do_publish

# Restore a removed top-level export even if do_deploy was an sstate hit.
python do_publish() {
    from pathlib import Path
    import naos_package.export

    source = Path(d.getVar('DEPLOY_DIR_IMAGE')) / 'naos-package' / d.getVar('NAOS_BUILD_MODE')
    naos_package.export.publish(source, Path(d.getVar('NAOS_PACKAGE_DESTINATION')),
        machine=d.getVar('MACHINE'), profile=d.getVar('NAOS_BUILD_MODE'),
        version=d.getVar('NAOS_VERSION'), build_id=d.getVar('NAOS_BUILD_ID'))
    bb.note('Image-generator inputs: ' + d.getVar('NAOS_PACKAGE_DESTINATION'))
}
do_publish[nostamp] = "1"
do_publish[file-checksums] = "${NAOS_PACKAGE_LAYER_DIR}/lib/naos_package/export.py:True"
addtask publish after do_deploy before do_build
