# Security features require an implementation supplied by the security layer.
inherit naos-disk-policy

python () {
    mode = d.getVar("NAOS_BUILD_MODE")
    if mode not in ("development", "production"):
        bb.fatal("NAOS_BUILD_MODE must be development or production")
    for name in ("NAOS_OTA", "NAOS_PACKAGE", "NAOS_SBOM"):
        if d.getVar(name) not in ("0", "1"):
            bb.fatal("%s must be 0 or 1" % name)
    if d.getVar("NAOS_PACKAGE") == "1" and d.getVar("NAOS_PACKAGE_PROVIDER") != "naos-generator-inputs-v1":
        bb.fatal("NAOS_PACKAGE requires the NAOS package layer")
    secure = d.getVar("NAOS_SECURE_BOOT")
    if secure not in ("0", "1"):
        bb.fatal("NAOS_SECURE_BOOT must be 0 or 1")
    provider = d.getVar("NAOS_SECURE_BOOT_PROVIDER")
    burn = d.getVar("NAOS_SECURE_BOOT_BURN_KEY_HASH") or "0"
    if burn not in ("0", "1"):
        bb.fatal("NAOS_SECURE_BOOT_BURN_KEY_HASH must be 0 or 1")
    if burn == "1" and (mode != "production" or secure != "1"):
        bb.fatal("Burning the secure-boot key hash requires an explicit signed production build")
    if secure == "1" and provider not in ("rk3588-fit", "q900-uefi"):
        bb.fatal("NAOS_SECURE_BOOT requires a secure-boot implementation for the selected machine")
    if burn == "1" and provider != "rk3588-fit":
        bb.fatal("NAOS_SECURE_BOOT_BURN_KEY_HASH is only supported for RK3588 FIT")
}
