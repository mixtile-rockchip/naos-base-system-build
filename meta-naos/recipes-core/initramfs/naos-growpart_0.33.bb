SUMMARY = "Small cloud-utils growpart partition expansion script"
HOMEPAGE = "https://github.com/canonical/cloud-utils"
LICENSE = "GPL-3.0-only"
LIC_FILES_CHKSUM = "file://growpart;beginline=1;endline=18;md5=ed08374de53982e85264d294bb8948a2"

# Vendored from the installed Debian cloud-guest-utils 0.33-1 package. This is
# upstream growpart with the Debian packaging patches; do not silently replace
# it with a different host-installed version. See files/growpart.PROVENANCE.
SRC_URI = "file://growpart file://growpart.PROVENANCE"
S = "${UNPACKDIR}"

inherit allarch

# Modern util-linux sfdisk supports GPT, so the legacy sgdisk fallback is not
# needed. BusyBox supplies the shell/text/file utilities used by the script.
RDEPENDS:${PN} = "busybox util-linux-sfdisk util-linux-partx util-linux-flock util-linux-blkid"

do_install() {
    install -d ${D}${sbindir} ${D}${datadir}/doc/${PN}
    install -m 0755 ${UNPACKDIR}/growpart ${D}${sbindir}/growpart
    install -m 0644 ${UNPACKDIR}/growpart.PROVENANCE ${D}${datadir}/doc/${PN}/
}
