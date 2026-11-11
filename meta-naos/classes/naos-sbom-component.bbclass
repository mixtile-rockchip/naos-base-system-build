# SPDX-License-Identifier: MIT
# Native SPDX remains the build provenance. These sidecars expose only target
# recipe facts; the product renderer selects components from shipped content.
NAOS_SBOM_COMPONENT_OUTPUT = "${WORKDIR}/naos-sbom-component"
NAOS_SBOM_COMPONENT_EXTENSION ?= ""
NAOS_SBOM_EXTENSION_DIR ?= ""
# sstate registers its pre/post functions before this class's anonymous Python
# runs. Declare the selected tasks as metadata, rather than appending too late.
SSTATETASKS += "${@'do_naos_sbom_component' if naos.sbom_metadata.component_enabled(d) else ''}"

python do_naos_sbom_component() {
    import naos.sbom_metadata
    naos.sbom_metadata.emit_component(d)
}
do_naos_sbom_component[cleandirs] = "${NAOS_SBOM_COMPONENT_OUTPUT}"
do_naos_sbom_component[stamp-extra-info] = "${MACHINE}"
do_naos_sbom_component[sstate-inputdirs] = "${NAOS_SBOM_COMPONENT_OUTPUT}"
do_naos_sbom_component[sstate-outputdirs] = "${DEPLOY_DIR_IMAGE}/sbom-inputs"
do_naos_sbom_component[file-checksums] = "${@naos.sbom_metadata.dependencies(d)}"
do_naos_sbom_component[vardeps] += "PN BPN PV LICENSE HOMEPAGE SRC_URI CVE_PRODUCT CVE_STATUS CVE_CHECK_STATUSMAP SPDX_INCLUDE_VEX IMAGE_LINK_NAME NAOS_SBOM NAOS_SBOM_PREEXPORTED NAOS_SBOM_COMPONENT_EXTENSION NAOS_SBOM_EXTENSION_DIR DEPLOY_DIR_IMAGE PKGDATA_DIR"

python do_naos_sbom_component_setscene() {
    sstate_setscene(d)
}

python __anonymous() {
    import bb
    import naos.sbom_metadata
    pn = d.getVar('PN')
    if not naos.sbom_metadata.component_enabled(d):
        bb.build.deltask('do_naos_sbom_component', d)
        bb.build.deltask('do_naos_sbom_component_setscene', d)
        return
    if pn in ('naos-initramfs', 'naos-initramfs-encrypted'):
        after = 'do_image_complete'
    elif d.getVarFlag('do_deploy', 'task'):
        after = 'do_deploy'
    else:
        after = 'do_packagedata'
    bb.build.addtask('do_naos_sbom_component', 'do_build', after, d)
    bb.build.addtask('do_naos_sbom_component_setscene', '', '', d)
}
