# Reuse meta-rauc's checksummed 1.4 release and NAOS's internal musl toolchain.
# Alpine supplies the shared libraries; Core starts the updater explicitly.
inherit deploy

FILESEXTRAPATHS:prepend := "${THISDIR}/files:"
SRC_URI += "file://hawkbit-config.conf.example"

PACKAGECONFIG = ""
EXTRA_OEMESON += "-Ddoc=disabled -Dapidoc=disabled"
SYSTEMD_SERVICE:${PN} = ""
SYSTEMD_AUTO_ENABLE = "disable"

python () {
    if d.getVar("TCLIBC") != "musl":
        bb.fatal("NAOS hawkBit updater must be built with TCLIBC=musl for the Alpine rootfs")
    if d.getVar("TCMODE") != "default" or d.getVar("TOOLCHAIN") != "gcc":
        bb.fatal("NAOS hawkBit updater requires the internal GCC toolchain from naos-toolchain.inc")
}

do_install:append() {
    # Upstream installs an active example with a server and token. Core will
    # supply its own configuration, so ship only an unconfigured doc sample.
    rm -f ${D}${sysconfdir}/${BPN}/config.conf
    install -Dm0644 ${UNPACKDIR}/hawkbit-config.conf.example \
        ${D}${docdir}/${BPN}/config.conf.example
}

do_deploy() {
    ${READELF} --program-headers ${D}${bindir}/${BPN} | grep -q '/lib/ld-musl-' || \
        bbfatal "hawkBit updater does not use the musl ELF interpreter"
    bbnote "hawkBit updater shared library requirements for the Alpine runtime:"
    ${READELF} --dynamic ${D}${bindir}/${BPN} | sed -n '/NEEDED/p'

    install -Dm0755 ${D}${bindir}/${BPN} \
        ${DEPLOYDIR}/naos-hawkbit-updater${bindir}/${BPN}
    install -Dm0644 ${D}${docdir}/${BPN}/config.conf.example \
        ${DEPLOYDIR}/naos-hawkbit-updater${docdir}/${BPN}/config.conf.example
    install -Dm0644 ${S}/LICENSE \
        ${DEPLOYDIR}/naos-hawkbit-updater${datadir}/licenses/${BPN}/LICENSE
}
addtask deploy after do_install before do_build
