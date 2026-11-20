python () {
    for name in ("NAOS_DISK_ENCRYPTION", "NAOS_SYSTEM_ENCRYPTION", "NAOS_DATA_ENCRYPTION"):
        if d.getVar(name) not in ("0", "1"):
            bb.fatal("%s must be 0 or 1" % name)

    system = d.getVar("NAOS_SYSTEM_ENCRYPTION") == "1"
    data = d.getVar("NAOS_DATA_ENCRYPTION") == "1"
    if system and not data:
        bb.fatal("System encryption requires userdata encryption")

    q900 = d.getVar("MACHINE") == "radxa-airbox-q900"
    system_provider = "qcs9075-hwkm" if q900 else "rk3588-huk"
    data_provider = "qcs9075-hwkm" if q900 else "rk3588-tee-trusted"
    if system and d.getVar("NAOS_SYSTEM_ENCRYPTION_PROVIDER") != system_provider:
        bb.fatal("System encryption requires a device-bound system key provider")
    if data and d.getVar("NAOS_DATA_ENCRYPTION_PROVIDER") != data_provider:
        bb.fatal("Userdata encryption requires a device-bound trusted key provider")
}
