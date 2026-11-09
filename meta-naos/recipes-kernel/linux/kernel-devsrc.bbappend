# Reuse OE's prepared, relocatable module development tree. Its scripts are
# shipped as source so build-host executables never enter the target rootfs.
inherit deploy

do_deploy() {
    install -d ${DEPLOYDIR}/naos-kernel-devsrc
    cp -a ${D}/. ${DEPLOYDIR}/naos-kernel-devsrc/
}
addtask deploy after do_install before do_build
