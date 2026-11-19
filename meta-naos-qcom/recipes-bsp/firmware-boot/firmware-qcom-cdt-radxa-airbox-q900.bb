DESCRIPTION = "Configuration Data Table for Radxa AIRbox Q900"

require recipes-bsp/firmware-boot/firmware-qcom-cdt-common.inc

LICENSE = "CLOSED"
LIC_FILES_CHKSUM = ""

SRC_URI = "https://dl.radxa.com/users/dev/radxa-dragon/qli-2.1.1/radxa-airbox-q900/cdt-radxa-airbox-q900.zip"
SRC_URI[sha256sum] = "d36ee2a5410733e2235a92d21ec9a9688678a1d4413720caa76dd402ee335875"
QCOM_CDT_SUBDIR = "qcs9100"
