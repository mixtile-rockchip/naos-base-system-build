FILESEXTRAPATHS:prepend := "${THISDIR}/files:"

SRC_URI:append:naos-qcs9075 = " \
    file://naos_qcs9075_defconfig;subdir=${BP}/arch/arm64/configs \
    file://0001-firmware-qcom-scm-Allow-QSEECOM-on-Radxa-AIRbox-Q900.patch \
"
SRC_URI:append:radxa-airbox-q900 = " \
    file://lemans-radxa-airbox-q900.dts;subdir=${BP}/arch/arm64/boot/dts/qcom \
"

KBUILD_DEFCONFIG:naos-qcs9075 = "naos_qcs9075_defconfig"
# The NAOS defconfig already incorporates Qualcomm's platform fragments.
KBUILD_CONFIG_EXTRA:remove:naos-qcs9075 = " \
    ${S}/arch/arm64/configs/prune.config \
    ${S}/arch/arm64/configs/qcom.config \
"
