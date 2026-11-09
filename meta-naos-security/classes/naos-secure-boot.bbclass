# Signed boot firmware and signed kernel/DTB/initramfs only. This class neither
# encrypts filesystems nor writes device OTP/eFuses during the host build.
# The opt-in burn flag makes the resulting SPL provision OTP on device boot.
python () {
    if d.getVar("NAOS_SECURE_BOOT") != "1":
        return
    if d.getVar("MACHINE") != "mixtile-core3588e-v2":
        bb.fatal("NAOS secure boot is currently implemented only for mixtile-core3588e-v2")
    directory = d.getVar("NAOS_SECURE_BOOT_KEY_DIR") or ""
    import os
    if not os.path.isabs(directory) or any(c.isspace() or c in '\"$`\\\\' for c in directory):
        bb.fatal("NAOS_SECURE_BOOT_KEY_DIR must be an absolute signing-material directory without shell metacharacters")
    # A signed initramfs must not expose the development failure/break shell.
    d.setVar("NAOS_INITRAMFS_DEBUG_SHELL", "0")
}
