SUMMARY = "Q900 hardware-backed client for importing the NAOS system key"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://LICENSES/MIT.txt;md5=7ae2be7fb1637141840314b51970a9f7"

require conf/distro/include/naos-security-tool.inc
inherit naos-shallow-git

COMPATIBLE_MACHINE = "${@'^radxa-airbox-q900$' if d.getVar('NAOS_DATA_ENCRYPTION') == '1' else '^$'}"
SRC_URI = "${NAOS_SECURITY_TOOL_URI};destsuffix=security-tool"
SRCREV = "${NAOS_SECURITY_TOOL_SRCREV}"
S = "${UNPACKDIR}/security-tool/qcs9075-hwkm-keybox"
B = "${WORKDIR}/build"

do_configure[noexec] = "1"

do_compile() {
    ${CC} ${CPPFLAGS} ${CFLAGS} -Wall -Wextra -Wformat=2 \
        ${S}/keybox-app.c -o ${B}/keybox_app ${LDFLAGS}
}

do_install() {
    install -Dm0755 ${B}/keybox_app ${D}${bindir}/keybox_app
}
