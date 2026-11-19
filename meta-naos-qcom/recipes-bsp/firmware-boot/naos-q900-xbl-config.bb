SUMMARY = "Q900 development XBL configuration with NAOS Capsule trust"
LICENSE = "CLOSED"
COMPATIBLE_MACHINE = "^radxa-airbox-q900$"
PACKAGE_ARCH = "${MACHINE_ARCH}"

SRC_URI = "file://verify-xbl-config.py"
S = "${UNPACKDIR}"
B = "${WORKDIR}/build"

inherit deploy nopackages python3native

DEPENDS = "naos-qcom-capsule-tool-native naos-qcom-sectools-native openssl-native"
NAOS_Q900_CAPSULE_ROOT ?= "${NAOS_PROJECT_ROOT}/key/qcom-capsule/QcFMPRoot.cer"
# SHA-384 of the Qualcomm SecTools test root already in the Q900 BSP XBL config.
NAOS_Q900_XBL_TEST_ROOT_HASH = "0xf953644308944bb811ca0ec2a736a17fe38509941ce7f55860130857813c8378e93359b70dfd874c270dca08a53bd99f"

do_configure[noexec] = "1"
do_install[noexec] = "1"
do_compile[depends] += "${QCOM_BOOT_FIRMWARE}:do_deploy"
do_compile[file-checksums] += "${NAOS_Q900_CAPSULE_ROOT}:${@os.path.isfile(d.getVar('NAOS_Q900_CAPSULE_ROOT'))}"
do_compile[cleandirs] = "${B}"

do_compile() {
    [ "${NAOS_BUILD_MODE}" = "development" ] ||
        bbfatal "Q900 SecTools TEST signing requires a development build"
    [ -s "${NAOS_Q900_CAPSULE_ROOT}" ] ||
        bbfatal "Missing Q900 Capsule root certificate: ${NAOS_Q900_CAPSULE_ROOT}"

    profile="${STAGING_DATADIR_NATIVE}/qcom-security-profiles/lemans_security_profile.xml"
    source="${DEPLOY_DIR_IMAGE}/${QCOM_BOOT_FILES_SUBDIR}/${NAOS_Q900_XBL_CONFIG}"
    signed="${B}/${NAOS_Q900_SIGNED_XBL_CONFIG}"
    openssl x509 -inform DER -in "${NAOS_Q900_CAPSULE_ROOT}" -noout
    sectools secure-image --verify-root "${NAOS_Q900_XBL_TEST_ROOT_HASH}" "$source"
    qcom-capsule-tool patch-capsule-cert "$source" "${NAOS_Q900_CAPSULE_ROOT}" "${B}/xbl-patched.elf"
    sectools secure-image --sign "${B}/xbl-patched.elf" --image-id XBL-CONFIG \
        --security-profile "$profile" --signing-mode TEST --outfile "$signed"
    sectools secure-image --verify-root "${NAOS_Q900_XBL_TEST_ROOT_HASH}" "$signed"
    "${PYTHON}" "${UNPACKDIR}/verify-xbl-config.py" "$signed" "${NAOS_Q900_CAPSULE_ROOT}"
}

do_deploy() {
    install -Dm 0644 "${B}/${NAOS_Q900_SIGNED_XBL_CONFIG}" \
        "${DEPLOYDIR}/${QCOM_BOOT_FILES_SUBDIR}/${NAOS_Q900_SIGNED_XBL_CONFIG}"
}
addtask deploy after do_compile before do_build
