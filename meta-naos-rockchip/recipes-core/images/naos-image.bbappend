NAOS_LAYOUT_FILE := "${THISDIR}/files/mixtile-core3588e-v2.json"
NAOS_OTA_OVERLAY := "${THISDIR}/../../../meta-naos/recipes-naos/ota/files/rootfs"
NAOS_RESOURCE_FILE = "resource.img"

# naos-image tracks the layout, certificate and every overlay file in its
# assembly task signature, in addition to the producer task dependencies.
