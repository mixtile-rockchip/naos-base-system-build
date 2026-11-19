SUMMARY = "Qualcomm firmware capsule and XBL configuration tools"
HOMEPAGE = "https://github.com/qualcomm/cbsp-boot-utilities"
LICENSE = "BSD-3-Clause-Clear"
LIC_FILES_CHKSUM = "file://LICENSE;md5=8e1eb38e3de3966193d29f31f5d7e684"

SRC_URI = " \
    git://github.com/qualcomm/cbsp-boot-utilities.git;protocol=https;branch=main;destsuffix=git \
    file://0001-handle-missing-property-with-yocto-libfdt.patch \
"
SRCREV = "41f74f121dfb95e55df1c3ecab0a55edc7a501bf"
S = "${UNPACKDIR}/git/uefi_capsule_generation"

inherit python_poetry_core native

DEPENDS += "python3-dtc-native python3-pyelftools-native python3-defusedxml-native"
