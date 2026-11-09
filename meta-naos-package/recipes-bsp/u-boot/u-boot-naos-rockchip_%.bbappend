FILESEXTRAPATHS:prepend := "${THISDIR}/files:"
SRC_URI:append = "${@' file://export-bootloader.py' if d.getVar('NAOS_PACKAGE') == '1' else ''}"

# Final slot payloads require signed images, which the external generator owns.
python __anonymous() {
    if d.getVar('NAOS_PACKAGE') == '1':
        d.setVarFlag('do_bootloader_payloads', 'noexec', '1')
}

naos_uboot_compile_package() {
    bash ./make.sh CROSS_COMPILE=${TARGET_PREFIX} --raw-compile
    ${PYTHON} ${UNPACKDIR}/export-bootloader.py capture \
        --source ${S} --clean-dir ${WORKDIR}/naos-package-clean
    # This SDK subcommand prepares the selected BL31/BL32 and relative ITS.
    # It invokes unsigned mkimage only, without -k/-K or loader signing.
    bash ./make.sh CROSS_COMPILE=${TARGET_PREFIX} itb
}

naos_uboot_deploy_package() {
    ${PYTHON} ${UNPACKDIR}/export-bootloader.py export \
        --source ${S} --clean-dir ${WORKDIR}/naos-package-clean \
        --rkbin ${UNPACKDIR}/rkbin \
        --output ${DEPLOYDIR}/naos-package-inputs/bootloader \
        --machine ${MACHINE} --profile ${NAOS_BUILD_MODE} \
        --uboot-revision ${SRCREV_uboot} --rkbin-revision ${SRCREV_rkbin}
}

do_compile[vardeps] += "NAOS_PACKAGE"
do_deploy[vardeps] += "NAOS_PACKAGE NAOS_BUILD_MODE SRCREV_uboot SRCREV_rkbin"
