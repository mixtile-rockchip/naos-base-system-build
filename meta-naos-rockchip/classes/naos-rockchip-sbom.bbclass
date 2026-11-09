# SPDX-License-Identifier: MIT
# Keep source configuration and selected firmware evidence in the same sstate
# payload as the BSP artifacts; later consumers never depend on WORKDIR.
NAOS_SBOM_PREEXPORTED = "1"

python naos_export_bsp_sbom() {
    import naos.sbom_metadata
    naos.sbom_metadata.emit_bsp_component(d)
}
do_deploy[postfuncs] += "${@'naos_export_bsp_sbom' if d.getVar('NAOS_SBOM') == '1' else ''}"
do_deploy[file-checksums] += "${@naos.sbom_metadata.dependencies(d) if d.getVar('NAOS_SBOM') == '1' else ''}"
do_deploy[vardeps] += "NAOS_SBOM PN BPN PV LICENSE HOMEPAGE SRC_URI CVE_PRODUCT NAOS_SBOM_COMPONENT_EXTENSION NAOS_SBOM_EXTENSION_DIR"
