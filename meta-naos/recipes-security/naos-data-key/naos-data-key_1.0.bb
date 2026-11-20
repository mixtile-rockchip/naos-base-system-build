SUMMARY = "Manage bounded misc records for userdata and system encryption"
DESCRIPTION = "Preserve userdata Trusted Key records and add system-key record I/O with snapshot comparison in fixed misc windows."
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://LICENSE;md5=7ae2be7fb1637141840314b51970a9f7"

DEPENDS = "openssl"
require conf/distro/include/naos-security-tool.inc
SRC_URI = "${NAOS_SECURITY_TOOL_URI};destsuffix=security-tool"
SRCREV = "${NAOS_SECURITY_TOOL_SRCREV}"
S = "${UNPACKDIR}/security-tool/naos-data-key"
B = "${WORKDIR}/build"

inherit naos-shallow-git

do_configure[noexec] = "1"

do_compile() {
    install -d ${B}
    ${CC} ${CPPFLAGS} ${CFLAGS} -Wall -Wextra -Wformat=2 -UNAOS_SYSTEM_KEY_TEST_FILE \
        ${S}/src/naos-data-key.c ${S}/src/naos-system-key-record.c -o ${B}/naos-data-key ${LDFLAGS} -lcrypto
}

do_install() {
    install -Dm0755 ${B}/naos-data-key ${D}${sbindir}/naos-data-key
}
