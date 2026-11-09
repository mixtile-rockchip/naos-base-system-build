require naos-ota-keys.inc

# Both system and bootloader bundles use the same OTA trust chain. Factory
# images need only the public certificate; bundle creation needs this key.
RAUC_KEY_FILE ?= "${NAOS_OTA_KEY_DIR}/signing.key.pem"
RAUC_CERT_FILE ?= "${NAOS_OTA_KEYRING}"
RAUC_KEYRING_FILE ?= "${NAOS_OTA_KEYRING}"

NAOS_LAYOUT_COMPATIBLE ?= "naos-${MACHINE}-v1"
RAUC_BUNDLE_COMPATIBLE = "${NAOS_LAYOUT_COMPATIBLE}"
RAUC_BUNDLE_VERSION = "${NAOS_VERSION}"
RAUC_BUNDLE_BUILD = "${NAOS_BUILD_ID}"
RAUC_BUNDLE_FORMAT = "verity"
BUNDLE_NAME = "${BPN}-${MACHINE}-${NAOS_VERSION}"

BUNDLE_ARGS += '--signing-keyring="${RAUC_KEYRING_FILE}" --mksquashfs-args="-comp zstd -processors ${ALPINE_ROOTFS_PROCESSORS}"'

do_bundle:append() {
    ${STAGING_BINDIR_NATIVE}/rauc info --keyring="${RAUC_KEYRING_FILE}" ${B}/bundle.raucb
}

# meta-rauc marks these optional inputs :False by default. Once the caller
# supplies local credentials, replacing them at the same path must rebuild
# either bundle as well as the factory image's public keyring.
python __anonymous() {
    paths = [d.getVar(name) for name in ("RAUC_KEY_FILE", "RAUC_CERT_FILE", "RAUC_KEYRING_FILE")]
    paths = [path for path in paths if path and not path.startswith("pkcs11:")]
    entries = (d.getVarFlag("do_bundle", "file-checksums") or "").split()
    entries = [entry for entry in entries if entry.rsplit(":", 1)[0] not in paths]
    entries += [path + ":True" for path in paths]
    d.setVarFlag("do_bundle", "file-checksums", " ".join(entries))
}
