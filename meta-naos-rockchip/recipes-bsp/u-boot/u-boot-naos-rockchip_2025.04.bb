SUMMARY = "Rockchip v5 U-Boot with RAUC slot selection for NAOS"
LICENSE = "GPL-2.0-or-later & Rockchip-rkbin"
LIC_FILES_CHKSUM = "file://Licenses/README;md5=2ca5f2c35c8cc335f0a19756634782f1 \
                    file://../rkbin/LICENSE;md5=e1d83c54b4e0b5e550769dd04f092f6c"
PROVIDES = "virtual/bootloader"
CVE_PRODUCT = "u-boot:u-boot denx:u-boot"

inherit deploy python3native pkgconfig naos-shallow-git naos-rockchip-sbom

DEPENDS = "bc-native bison-native flex-native dtc-native openssl-native \
           python3-pyelftools-native python3-setuptools-native swig-native xxd-native"
SRC_URI = "${NAOS_RK_UBOOT_URI};name=uboot;destsuffix=u-boot \
           ${NAOS_RK_RKBIN_URI};name=rkbin;destsuffix=rkbin \
           file://mixtile-core3588e-v2_defconfig \
           file://${NAOS_UBOOT_CONFIG_FRAGMENT} \
           file://${NAOS_UBOOT_CONSOLE_FRAGMENT} \
           file://mixtile-core3588e-v2.dts \
           file://naos-default.env \
           file://naos_bootdev.c \
           file://0001-respect-build-parallelism-and-optional-git-identity.patch \
           file://0002-pass-compilers-to-vendor-make.patch"
SRCREV_uboot = "5b6584f18fc2430cecedbd6890b44be3d63c6879"
SRCREV_rkbin = "3e288fe814e059dd06833495f845cab04ac20a5c"
SRCREV_FORMAT = "uboot_rkbin"
S = "${UNPACKDIR}/u-boot"
B = "${S}"
PACKAGE_ARCH = "${MACHINE_ARCH}"
COMPATIBLE_MACHINE = "^mixtile-core3588e-v2$"
NAOS_UBOOT_JOBS ?= "${@oe.utils.parallel_make(d) or 1}"
NAOS_UBOOT_CC = "${TARGET_PREFIX}gcc ${TOOLCHAIN_OPTIONS} ${DEBUG_PREFIX_MAP}"
# Vendor host mkimage uses these layout constants even with target RSA=n.
# Keep them on HOSTCC, never on the target compiler or target configuration.
NAOS_UBOOT_HOSTCC = "${BUILD_CC} ${BUILD_CFLAGS} ${BUILD_LDFLAGS} -DCONFIG_RSA_N_SIZE=0x200 -DCONFIG_RSA_E_SIZE=0x10 -DCONFIG_RSA_C_SIZE=0x20"
NAOS_UBOOT_CONFIG_FRAGMENT = "development.cfg"
NAOS_UBOOT_CONSOLE_FRAGMENT = "${@'production-console.cfg' if d.getVar('NAOS_BUILD_MODE') == 'production' or d.getVar('NAOS_SECURE_BOOT') == '1' else 'development-console.cfg'}"
NAOS_UBOOT_SIGN_ARGS ?= "--no-sign"

python () {
    mode = d.getVar("NAOS_BUILD_MODE")
    if mode not in ("development", "production"):
        bb.fatal("Unsupported NAOS_BUILD_MODE for the Rockchip bootloader: %r" % mode)
    secure = d.getVar("NAOS_SECURE_BOOT")
    if secure not in ("0", "1"):
        bb.fatal("NAOS_SECURE_BOOT must be 0 or 1")
    if secure == "1" and d.getVar("NAOS_SECURE_BOOT_PROVIDER") != "rk3588-fit":
        bb.fatal("RK3588 secure boot requires the naos-security layer")
    for variable in ("NAOS_RK_UBOOT_URI", "NAOS_RK_RKBIN_URI"):
        if not d.getVar(variable):
            bb.fatal("Set %s to a Git source containing the pinned SRCREV; local overrides may be supplied through build/local.yml." % variable)
}

# Rockchip's packaging scripts require a sibling rkbin directory. Both source
# trees are fetched into UNPACKDIR, never built in the original local SDK.
do_configure() {
    install -m 0644 ${UNPACKDIR}/mixtile-core3588e-v2_defconfig ${S}/configs/
    install -m 0644 ${UNPACKDIR}/mixtile-core3588e-v2.dts ${S}/dts/upstream/src/arm64/rockchip/
    install -m 0644 ${UNPACKDIR}/naos-default.env ${S}/naos-default.env
    install -m 0644 ${UNPACKDIR}/naos_bootdev.c ${S}/cmd/naos_bootdev.c
    if ! grep -q 'naos_bootdev.o' ${S}/cmd/Makefile; then
        printf '\nobj-$(CONFIG_TARGET_EVB_RK3588) += naos_bootdev.o\n' >> ${S}/cmd/Makefile
    fi
    naos_uboot_prepare_security
    oe_runmake -C ${S} CROSS_COMPILE=${TARGET_PREFIX} \
        CC="${NAOS_UBOOT_CC}" \
        HOSTCC="${NAOS_UBOOT_HOSTCC}" ${UBOOT_MACHINE}
    (cd ${S} && sh scripts/kconfig/merge_config.sh -m .config \
        ${UNPACKDIR}/${NAOS_UBOOT_CONFIG_FRAGMENT} \
        ${UNPACKDIR}/${NAOS_UBOOT_CONSOLE_FRAGMENT})
    oe_runmake -C ${S} CROSS_COMPILE=${TARGET_PREFIX} \
        CC="${NAOS_UBOOT_CC}" \
        HOSTCC="${NAOS_UBOOT_HOSTCC}" olddefconfig
    naos_check_uboot_config
}

# Optional security-layer hooks. The development build keeps its original
# environment and unsigned image handling.
naos_uboot_prepare_security() {
    :
}

naos_uboot_sign_raw_loader() {
    :
}

# The optional packaging layer exports pre-signing inputs from this build.
# It replaces these hooks only when package mode is explicitly enabled.
naos_uboot_compile_package() {
    bbfatal "NAOS_PACKAGE requires the naos-package layer"
}

naos_uboot_deploy_package() {
    bbfatal "NAOS_PACKAGE requires the naos-package layer"
}

naos_check_uboot_config() {
    # Check resolved values after Kconfig select/default propagation. Disabled
    # dependency-hidden symbols may be absent, but must never resolve to y/m.
    for fragment in ${NAOS_UBOOT_CONFIG_FRAGMENT} ${NAOS_UBOOT_CONSOLE_FRAGMENT}; do
        for option in $(sed -n 's/^# CONFIG_\([A-Z0-9_]*\) is not set$/\1/p' \
            ${UNPACKDIR}/$fragment); do
            if grep -Eq "^CONFIG_$option=(y|m)$" ${S}/.config; then
                bbfatal "$option must be disabled by $fragment"
            fi
        done
        for setting in $(sed -n '/^CONFIG_[A-Z0-9_]*=y$/p' ${UNPACKDIR}/$fragment); do
            grep -qx "$setting" ${S}/.config || bbfatal "$setting is required by $fragment"
        done
    done
    if [ "${NAOS_BUILD_MODE}" = production ] || [ "${NAOS_SECURE_BOOT}" = 1 ]; then
        grep -qx 'CONFIG_BOOTDELAY=-2' ${S}/.config || bbfatal "Production or signed U-Boot requires non-interruptible autoboot"
    fi
    for option in SPL_ATF ARM_SMCCC \
                  SCMI_FIRMWARE SCMI_AGENT_SMCCC ROCKCHIP_OTP \
                  DM_RNG RNG_ROCKCHIP \
                  CRYPTO_MANAGER SPL_CRYPTO SPL_CRYPTO_MANAGER \
                  DM_HASH HASH_SOFTWARE ROCKCHIP_HMAC ROCKCHIP_CRYPTO_V2 \
                  SPL_ROCKCHIP_CRYPTO_V2 \
                  USB_GADGET_DOWNLOAD USB_FUNCTION_MASS_STORAGE \
                  USB_FUNCTION_ROCKUSB CMD_ROCKUSB \
                  ENV_IS_IN_MMC SYS_REDUNDAND_ENVIRONMENT USE_DEFAULT_ENV_FILE \
                  CMDLINE HUSH_PARSER HUSH_OLD_PARSER CMD_RUN CMD_ITEST \
                  CMD_SETEXPR CMD_PART CMD_MMC CMD_SAVEENV CMD_BOOTM BOOTM_LINUX \
                  OF_CONTROL OF_REAL OF_LIVE OF_SEPARATE OF_UPSTREAM DM_KERNEL_DTB; do
        grep -qx "CONFIG_$option=y" ${S}/.config || bbfatal "$option is required"
    done
    for option in ANDROID_BOOT_IMAGE ANDROID_AB SPL_AB AVB_VERIFY LIBAVB \
                  LIBAVB_AB LIBAVB_ATX LIBAVB_USER CMD_BOOT_ANDROID \
                  ANDROID_DTBO_SUPPORT ANDROID_BOOT_IMAGE_HASH \
                  ANDROID_WRITE_KEYBOX ANDROID_KEYMASTER_CA \
                  USB_FUNCTION_FASTBOOT UDP_FUNCTION_FASTBOOT TCP_FUNCTION_FASTBOOT \
                  CMD_FASTBOOT FASTBOOT CMD_AVB OPTEE_TA_AVB \
                  BOOTSTD_BOOTCOMMAND ENV_IS_NOWHERE \
                  HUSH_MODERN_PARSER OF_DTB_PROPS_REMOVE; do
        if grep -qx "CONFIG_$option=y" ${S}/.config; then
            bbfatal "$option conflicts with the NAOS RAUC boot protocol"
        fi
    done
    if grep -q '^CONFIG_OF_REMOVE_PROPS=' ${S}/.config && \
       ! grep -qx 'CONFIG_OF_REMOVE_PROPS=""' ${S}/.config; then
        bbfatal "The U-Boot control DTB must retain its pinctrl and clock properties"
    fi
}

do_compile() {
    unset CFLAGS CPPFLAGS LDFLAGS
    export CC="${NAOS_UBOOT_CC}"
    export HOSTCC="${NAOS_UBOOT_HOSTCC}"
    export HOSTCXX="${BUILD_CXX} ${BUILD_CXXFLAGS} ${BUILD_LDFLAGS}"
    export HOSTCFLAGS="${BUILD_CFLAGS}"
    export HOSTLDFLAGS="${BUILD_LDFLAGS}"
    # python3native exports target staging paths by default. pylibfdt is a
    # host extension, so Python's sysconfig must use the native sysroot.
    export STAGING_INCDIR="${STAGING_INCDIR_NATIVE}"
    export STAGING_LIBDIR="${STAGING_LIBDIR_NATIVE}"
    export PKG_CONFIG_LIBDIR="${STAGING_LIBDIR_NATIVE}/pkgconfig:${STAGING_DATADIR_NATIVE}/pkgconfig"
    export PKG_CONFIG_SYSROOT_DIR="${STAGING_DIR_NATIVE}"
    export NAOS_MAKE_JOBS="${NAOS_UBOOT_JOBS}"
    # The single board DTS includes the SDK support it needs explicitly.
    # Do not append a second, same-named legacy SoC include afterwards.
    export MAKEFLAGS="$MAKEFLAGS dtsi_include_list="
    # The SDK decoder still uses /usr/bin/env python. Resolve it to Yocto's
    # native Python 3 only inside this task, without patching vendor sources.
    install -d ${WORKDIR}/naos-host-tools
    ln -sf "${PYTHON}" ${WORKDIR}/naos-host-tools/python
    export PATH="${WORKDIR}/naos-host-tools:$PATH"
    cd ${S}
    if [ "${NAOS_PACKAGE}" = 1 ]; then
        naos_uboot_compile_package
        ./tools/mkenvimage -r -s 0x8000 -o naos-uboot-env.bin naos-default.env
        naos_check_uboot_config
        return
    fi
    bash ./make.sh CROSS_COMPILE=${TARGET_PREFIX} ${NAOS_UBOOT_SIGN_ARGS} --spl-new
    # A raw SD/eMMC ID block is distinct from the USB Rockchip loader package.
    bash ./make.sh CROSS_COMPILE=${TARGET_PREFIX} --idblock --spl
    naos_uboot_sign_raw_loader
    ./tools/mkenvimage -r -s 0x8000 -o naos-uboot-env.bin naos-default.env
    test -s uboot.img
    test -s idblock.bin
    naos_check_uboot_config
}

do_install[noexec] = "1"
PACKAGES = ""

python do_bootloader_payloads() {
    from pathlib import Path

    source = Path(d.getVar("S"))
    idblock = (source / "idblock.bin").read_bytes()
    uboot = (source / "uboot.img").read_bytes()
    idblock_size, uboot_size = 512 * 1024, 2 * 1024 * 1024
    if not idblock or len(idblock) > idblock_size:
        bb.fatal("IDBlock exceeds its 512 KiB redundant location")
    if len(uboot) != 2 * uboot_size or uboot[:uboot_size] != uboot[uboot_size:]:
        bb.fatal("uboot.img must contain two identical 2 MiB FIT copies")
    # RAUC boot-raw-fallback takes one copy and writes both locations itself.
    (source / "idblock-slot.img").write_bytes(idblock.ljust(idblock_size, b"\0"))
    (source / "uboot-slot.img").write_bytes(uboot[:uboot_size])
}
addtask bootloader_payloads after do_compile before do_deploy

do_deploy() {
    install -d ${DEPLOYDIR}
    if [ "${NAOS_PACKAGE}" = 1 ]; then
        naos_uboot_deploy_package
        install -m 0644 ${S}/naos-uboot-env.bin ${DEPLOYDIR}/naos-uboot-env.bin
        return
    fi
    install -m 0644 ${S}/uboot.img ${DEPLOYDIR}/uboot.img
    install -m 0644 ${S}/idblock.bin ${DEPLOYDIR}/idblock.bin
    install -m 0644 ${S}/idblock-slot.img ${DEPLOYDIR}/idblock-slot.img
    install -m 0644 ${S}/uboot-slot.img ${DEPLOYDIR}/uboot-slot.img
    install -m 0644 ${S}/naos-uboot-env.bin ${DEPLOYDIR}/naos-uboot-env.bin
    install -m 0644 ${S}/naos-default.env ${DEPLOYDIR}/naos-uboot-default.env
    install -m 0644 ${S}/.config ${DEPLOYDIR}/naos-uboot.config
    for loader in ${S}/*loader*.bin; do
        [ -f "$loader" ] || continue
        install -m 0644 "$loader" ${DEPLOYDIR}/MiniLoaderAll.bin
        break
    done
}
addtask deploy after do_compile before do_build
