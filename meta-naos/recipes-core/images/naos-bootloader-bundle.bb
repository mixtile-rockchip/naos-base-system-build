SUMMARY = "Signed RAUC bundle for the NAOS U-Boot FIT and IDBlock"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

inherit bundle
require naos-bundle-signing.inc

# Each payload is one copy. The target's boot-raw-fallback slots write both
# copies, syncing the content before publishing each copy's header. Keep this
# bundle separate from naos-bundle so ordinary OS updates never write loaders.
# Install U-Boot before IDBlock. Releases must keep the new FIT compatible with
# the existing SPL, including its signing key and FIT copy offsets.
RAUC_BUNDLE_SLOTS = "uboot idb"
RAUC_SLOT_uboot = "virtual/bootloader"
RAUC_SLOT_uboot[type] = "file"
RAUC_SLOT_uboot[file] = "uboot-slot.img"
RAUC_SLOT_uboot[rename] = "uboot-slot.img"
RAUC_SLOT_uboot[imagetype] = "raw"
RAUC_SLOT_idb = "virtual/bootloader"
RAUC_SLOT_idb[type] = "file"
RAUC_SLOT_idb[file] = "idblock-slot.img"
RAUC_SLOT_idb[rename] = "idblock-slot.img"
RAUC_SLOT_idb[imagetype] = "raw"

python do_configure:append() {
    import configparser
    from pathlib import Path

    bundle = Path(d.getVar("BUNDLE_DIR"))
    for name, size in (("uboot-slot.img", 2 * 1024 * 1024),
                       ("idblock-slot.img", 512 * 1024)):
        if (bundle / name).stat().st_size != size:
            bb.fatal("Bootloader bundle payload %s must contain exactly one %d-byte copy" % (name, size))

    manifest = configparser.ConfigParser(interpolation=None)
    manifest.read(bundle / "manifest.raucm")
    images = [section for section in manifest.sections() if section.startswith("image.")]
    if images != ["image.uboot", "image.idb"]:
        bb.fatal("Bootloader bundle must install only U-Boot followed by IDBlock")
}
