SUMMARY = "Qualcomm SecTools v2 with the LeMans security profile"
HOMEPAGE = "https://softwarecenter.qualcomm.com/catalog/item/Qualcomm_Security_Tools"
LICENSE = "Proprietary"
LIC_FILES_CHKSUM = "file://License.pdf;md5=b04bc87bdc900cc24ab2ce3447a53bb2"

SRC_URI = " \
    https://softwarecenter.qualcomm.com/api/download/software/tools/Qualcomm_Security_Tools/All/${PV}/1.50.zip;downloadfilename=qcom-sectools-${PV}.zip;name=sectools \
    https://raw.githubusercontent.com/qualcomm/security-profiles/bb3e8c735d6b7fee25227395249f9ec674867464/lemans_security_profile.xml;name=profile \
"
SRC_URI[sectools.sha256sum] = "8d6694f1d184e7c204d1e1ea50447a946d7f21838cfddb48b06f0c733d700cbe"
SRC_URI[profile.sha256sum] = "cc99bbf9275285e9f38db512d6220ee48e869c9f5ce05e9aa3fe4fd6c3636f7a"
S = "${UNPACKDIR}/1.50"

inherit native

COMPATIBLE_HOST = "^(x86_64|aarch64)-linux$"
SECTOOLS_PLATFORM_DIR = "${@'Linux_aarch64' if d.getVar('BUILD_ARCH') == 'aarch64' else 'Linux'}"
INHIBIT_DEFAULT_DEPS = "1"
INHIBIT_SYSROOT_STRIP = "1"
do_configure[noexec] = "1"
do_compile[noexec] = "1"

do_install() {
    install -Dm 0755 "${S}/${SECTOOLS_PLATFORM_DIR}/sectools" "${D}${bindir}/sectools"
    install -Dm 0644 "${UNPACKDIR}/lemans_security_profile.xml" \
        "${D}${datadir}/qcom-security-profiles/lemans_security_profile.xml"
}
