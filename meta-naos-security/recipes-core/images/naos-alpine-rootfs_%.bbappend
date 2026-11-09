# The public CA is a normal build input. Keep password/console defaults in
# the development profile, and apply these restrictions only in production.
OVERRIDES:append = "${@':naos-production' if d.getVar('NAOS_BUILD_MODE') == 'production' else ''}"
ALPINE_DEFAULT_PASSWORD:naos-production = ""
ALPINE_CONSOLE_SHELL:naos-production = "0"
ALPINE_SSH_CA_KEY:naos-production = "${NAOS_SSH_CA_KEY}"

python __anonymous() {
    if d.getVar("NAOS_BUILD_MODE") != "production":
        return
    if d.getVar("ALPINE_DEFAULT_PASSWORD") or d.getVar("ALPINE_CONSOLE_SHELL") != "0":
        bb.fatal("Production login requires disabled password and serial login")
    if not d.getVar("ALPINE_SSH_CA_KEY"):
        bb.fatal("Production SSH requires NAOS_SSH_CA_KEY with a trusted user CA public key")
}
