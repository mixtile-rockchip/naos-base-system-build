# Use meta-rauc's pinned source recipe with the NAOS musl toolchain. Alpine
# supplies shared libraries; do not copy the Yocto sysroot into its rootfs.
inherit deploy

# The updater downloads bundles first; RAUC installs their local paths.
# Keep one download implementation instead of mapping another network stack.
PACKAGECONFIG = "service json gpt"

python () {
    if d.getVar("TCLIBC") != "musl":
        bb.fatal("NAOS target RAUC must be built with TCLIBC=musl to run in the Alpine rootfs")
    if d.getVar("TCMODE") != "default" or d.getVar("TOOLCHAIN") != "gcc":
        bb.fatal("NAOS target RAUC requires the pinned internal GCC toolchain from naos-toolchain.inc")
}

do_deploy() {
    # Fail before publishing if a configuration accidentally selects glibc.
    ${READELF} --program-headers ${D}${bindir}/rauc | grep -q '/lib/ld-musl-' || \
        bbfatal "RAUC does not use the musl ELF interpreter"
    bbnote "RAUC shared library requirements for the Alpine runtime:"
    ${READELF} --dynamic ${D}${bindir}/rauc | sed -n '/NEEDED/p'

    install -Dm0755 ${D}${bindir}/rauc ${DEPLOYDIR}/naos-rauc/usr/bin/rauc
    install -Dm0644 ${D}${datadir}/dbus-1/system.d/de.pengutronix.rauc.conf \
        ${DEPLOYDIR}/naos-rauc/usr/share/dbus-1/system.d/de.pengutronix.rauc.conf
}
addtask deploy after do_install before do_build
