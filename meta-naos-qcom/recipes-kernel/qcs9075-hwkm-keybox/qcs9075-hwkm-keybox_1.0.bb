SUMMARY = "NAOS disk key sealing using Qualcomm QCS9075 HWKM"
LICENSE = "GPL-2.0-only"
LIC_FILES_CHKSUM = "file://LICENSES/GPL-2.0-only.txt;md5=b234ee4d69f5fce4486a80fdaf4a4263"

require conf/distro/include/naos-security-tool.inc
inherit module naos-shallow-git

COMPATIBLE_MACHINE = "^radxa-airbox-q900$"
SRC_URI = "${NAOS_SECURITY_TOOL_URI};destsuffix=security-tool"
SRCREV = "${NAOS_SECURITY_TOOL_SRCREV}"
S = "${UNPACKDIR}/security-tool/qcs9075-hwkm-keybox"
