# Target-side RAUC writes the already encrypted payload to the raw A/B slot.
python __anonymous() {
    if d.getVar("NAOS_SYSTEM_ENCRYPTION") == "1":
        d.setVarFlag("RAUC_SLOT_rootfs", "file", "system.img")
        d.setVarFlag("RAUC_SLOT_rootfs", "rename", "system.img")
}
