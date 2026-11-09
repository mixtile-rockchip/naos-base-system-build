FILESEXTRAPATHS:prepend := "${THISDIR}/files:"

NAOS_UBOOT_CONFIG_FRAGMENT = "${@'secure-boot.cfg' if d.getVar('NAOS_SECURE_BOOT') == '1' else 'development.cfg'}"
NAOS_UBOOT_SIGN_ARGS = "${@('--burn-key-hash' if d.getVar('NAOS_SECURE_BOOT_BURN_KEY_HASH') == '1' else '') if d.getVar('NAOS_SECURE_BOOT') == '1' else '--no-sign'}"
SRC_URI:append = "${@' file://naos-secure-env.h' if d.getVar('NAOS_BUILD_MODE') == 'production' or d.getVar('NAOS_SECURE_BOOT') == '1' else ''}"
SRC_URI:append = "${@' file://0001-rsa-sign-match-rockchip-pss-salt-length.patch' if d.getVar('NAOS_SECURE_BOOT') == '1' else ''}"

python __anonymous() {
    if d.getVar("NAOS_SECURE_BOOT") != "1" or d.getVar("NAOS_PACKAGE") == "1":
        return
    if d.getVar("MACHINE") != "mixtile-core3588e-v2":
        bb.fatal("RK3588 secure boot is only implemented for mixtile-core3588e-v2")
    directory = d.getVar("NAOS_SECURE_BOOT_KEY_DIR")
    inputs = " ".join(directory + "/" + name + ":True" for name in ("dev.key", "dev.crt", "dev.pubkey"))
    d.appendVarFlag("do_configure", "file-checksums", " " + inputs)
}

do_configure[vardeps] += "NAOS_SECURE_BOOT_BURN_KEY_HASH"

naos_uboot_prepare_security() {
    # Always reset the generated key DTBs and our optional include. A change
    # from signed to unsigned firmware must not retain the previous policy.
    rm -f ${S}/u-boot.dtb ${S}/spl/u-boot-spl.dtb
    sed -i '/naos-secure-env[.]h/d' ${S}/include/configs/evb_rk3588.h
    rm -f ${S}/include/configs/naos-secure-env.h
    if [ "${NAOS_BUILD_MODE}" = production ] || [ "${NAOS_SECURE_BOOT}" = 1 ]; then
        install -m 0644 ${UNPACKDIR}/naos-secure-env.h ${S}/include/configs/naos-secure-env.h
        printf '\n#include <configs/naos-secure-env.h>\n' >> ${S}/include/configs/evb_rk3588.h
        # Only RAUC state may replace the built-in defaults from MMC env.
        sed -i '/^bootdelay=/c\bootdelay=-2' ${S}/naos-default.env
        sed -i '/^preboot=/d' ${S}/naos-default.env
    fi
    if [ "${NAOS_BUILD_MODE}" = production ]; then
        sed -i '/^naos_console=/s/console=ttyFIQ0/console=ttyS2/' ${S}/naos-default.env
    fi
    if [ "${NAOS_SECURE_BOOT}" != 1 ]; then
        rm -f ${S}/keys/dev.key ${S}/keys/dev.crt ${S}/keys/dev.pubkey
        return
    fi
    if [ "${NAOS_PACKAGE}" = 1 ]; then
        # Package inputs are captured before public-key injection. They must
        # remain independent of this build host's signing credentials.
        rm -f ${S}/keys/dev.key ${S}/keys/dev.crt ${S}/keys/dev.pubkey
    else
        install -d -m 0700 ${S}/keys
        install -m 0600 "${NAOS_SECURE_BOOT_KEY_DIR}/dev.key" ${S}/keys/dev.key
        install -m 0644 "${NAOS_SECURE_BOOT_KEY_DIR}/dev.crt" ${S}/keys/dev.crt
        install -m 0644 "${NAOS_SECURE_BOOT_KEY_DIR}/dev.pubkey" ${S}/keys/dev.pubkey
    fi
    # Signature verification remains distinct from the production CLI policy.
    sed -i '/^verify=/d' ${S}/naos-default.env
    printf '\nverify=yes\n' >> ${S}/naos-default.env
}

naos_uboot_sign_raw_loader() {
    if [ "${NAOS_SECURE_BOOT}" != "1" ]; then
        return
    fi
    # fit.sh signs the USB loader, but --idblock above creates a separate raw
    # SD/eMMC ID block. Sign and verify that actual factory-image input too.
    ${UNPACKDIR}/rkbin/tools/rk_sign_tool cc --chip 3588
    ${UNPACKDIR}/rkbin/tools/rk_sign_tool lk --key ${S}/keys/dev.key --pubkey ${S}/keys/dev.pubkey
    ${UNPACKDIR}/rkbin/tools/rk_sign_tool sb --idb ${S}/idblock.bin
    ${UNPACKDIR}/rkbin/tools/rk_sign_tool vb --idb ${S}/idblock.bin
    for loader in ${S}/*loader*.bin; do
        [ -f "$loader" ] || continue
        ${UNPACKDIR}/rkbin/tools/rk_sign_tool vl --loader "$loader"
    done
    # fit.sh checks against the full SPL DTB before clearing the software-only
    # Montgomery r-squared value. The final SPL DTB uses Rockchip hardware RSA;
    # repeat host verification with the identical, complete U-Boot public key.
    for property in rsa,modulus rsa,exponent rsa,num-bits; do
        main_value=$(fdtget -tx ${S}/u-boot.dtb /signature/key-dev "$property")
        spl_value=$(fdtget -tx ${S}/spl/u-boot-spl.dtb /signature/key-dev "$property")
        test -n "$main_value" && test "$main_value" = "$spl_value" || \
            bbfatal "The U-Boot and SPL public keys differ at $property"
    done
    ${S}/tools/fit_check_sign -f ${S}/fit/uboot.itb -k ${S}/u-boot.dtb -s
    test "$(fdtget -ts ${S}/u-boot.dtb /signature/key-dev required)" = "conf" || \
        bbfatal "The U-Boot control DTB must require the signed FIT configuration"
    test "$(fdtget -ts ${S}/spl/u-boot-spl.dtb /signature/key-dev required)" = "conf" || \
        bbfatal "The SPL control DTB must require the signed U-Boot configuration"
    if [ "${NAOS_SECURE_BOOT_BURN_KEY_HASH}" = "1" ]; then
        test "$(fdtget -ti ${S}/spl/u-boot-spl.dtb /signature/key-dev burn-key-hash)" = "1" || \
            bbfatal "The SPL burn-key-hash flag was not enabled as requested"
    elif fdtget -tx ${S}/spl/u-boot-spl.dtb /signature/key-dev burn-key-hash >/dev/null 2>&1; then
        bbfatal "Automatic OTP programming must remain disabled by default"
    fi
    if fdtget -tx ${S}/u-boot.dtb /signature/key-dev burn-key-hash >/dev/null 2>&1; then
        bbfatal "Only SPL may contain the explicitly requested OTP burn flag"
    fi
    grep -qx 'CONFIG_BOOTDELAY=-2' ${S}/.config || bbfatal "Secure boot requires non-interruptible autoboot"
    grep -qx 'CONFIG_ENV_WRITEABLE_LIST=y' ${S}/.config || bbfatal "Secure boot requires the RAUC environment writeable list"
}

do_deploy:append() {
    if [ "${NAOS_SECURE_BOOT}" = "1" ] && [ "${NAOS_PACKAGE}" != 1 ]; then
        install -d ${DEPLOYDIR}/naos-secure-boot-tools
        install -m 0755 ${S}/tools/mkimage ${DEPLOYDIR}/naos-secure-boot-tools/mkimage
        install -m 0755 ${S}/tools/fit_check_sign ${DEPLOYDIR}/naos-secure-boot-tools/fit_check_sign
        install -m 0644 ${S}/u-boot.dtb ${DEPLOYDIR}/naos-secure-boot-tools/u-boot.dtb
        install -m 0644 ${S}/spl/u-boot-spl.dtb ${DEPLOYDIR}/naos-secure-boot-tools/u-boot-spl.dtb
    fi
}
