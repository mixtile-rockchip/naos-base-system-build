# Security features require an implementation supplied by the security layer.
python () {
    mode = d.getVar("NAOS_BUILD_MODE")
    if mode not in ("development", "production"):
        bb.fatal("NAOS_BUILD_MODE must be development or production")
    for name in ("NAOS_OTA", "NAOS_PACKAGE", "NAOS_SBOM", "NAOS_DISK_ENCRYPTION"):
        if d.getVar(name) not in ("0", "1"):
            bb.fatal("%s must be 0 or 1" % name)
    if d.getVar("NAOS_PACKAGE") == "1" and d.getVar("NAOS_PACKAGE_PROVIDER") != "naos-generator-inputs-v1":
        bb.fatal("NAOS_PACKAGE requires the NAOS package layer")
    secure = d.getVar("NAOS_SECURE_BOOT")
    if secure not in ("0", "1"):
        bb.fatal("NAOS_SECURE_BOOT must be 0 or 1")
    burn = d.getVar("NAOS_SECURE_BOOT_BURN_KEY_HASH") or "0"
    if burn not in ("0", "1"):
        bb.fatal("NAOS_SECURE_BOOT_BURN_KEY_HASH must be 0 or 1")
    if burn == "1" and (mode != "production" or secure != "1"):
        bb.fatal("Burning the secure-boot key hash requires an explicit signed production build")
    if secure == "1" and d.getVar("NAOS_SECURE_BOOT_PROVIDER") != "rk3588-fit":
        bb.fatal("NAOS_SECURE_BOOT requires the NAOS security layer")
    system = d.getVar("NAOS_SYSTEM_ENCRYPTION")
    if system not in ("0", "1"):
        bb.fatal("NAOS_SYSTEM_ENCRYPTION must be 0 or 1")
    if system == "1" and d.getVar("NAOS_SYSTEM_ENCRYPTION_PROVIDER") != "rk3588-huk":
        bb.fatal("System encryption requires the Rockchip HUK system-key implementation")
    data = d.getVar("NAOS_DATA_ENCRYPTION")
    if data not in ("0", "1"):
        bb.fatal("NAOS_DATA_ENCRYPTION must be 0 or 1")
    if data == "1" and d.getVar("NAOS_DATA_ENCRYPTION_PROVIDER") != "rk3588-tee-trusted":
        bb.fatal("Userdata encryption requires the NAOS TEE Trusted Keys implementation")
    if system == "1" and data != "1":
        bb.fatal("System encryption requires the encrypted initramfs and userdata implementation")
}
