# Production keeps the UART log output without a serial debugger or SysRq input.
FILESEXTRAPATHS:prepend := "${THISDIR}/files:"
SRC_URI:append = "${@' file://production-console.cfg file://production-console.dtsi' if d.getVar('NAOS_BUILD_MODE') == 'production' else ''}"

do_configure:prepend() {
    # A profile switch must not reuse the other profile's resolved .config.
    # Use the recipe's fetched defconfig, including any higher-priority override.
    install -m 0644 ${UNPACKDIR}/defconfig ${B}/.config
    export KCONFIG_CONFIG="${B}/.config"
}

do_configure:append() {
    if [ "${NAOS_BUILD_MODE}" = production ]; then
        install -m 0644 ${UNPACKDIR}/production-console.dtsi \
            ${S}/arch/arm64/boot/dts/rockchip/naos-production-console.dtsi
        # The base recipe installs the unmodified board DTS on each configure.
        printf '\n#include "naos-production-console.dtsi"\n' >> \
            ${S}/arch/arm64/boot/dts/rockchip/mixtile-core3588e-v2.dts
        (cd ${S} && sh scripts/kconfig/merge_config.sh -m -O ${B} ${B}/.config \
            ${UNPACKDIR}/production-console.cfg)
        ${KERNEL_CONFIG_COMMAND}
        for option in CONFIG_FIQ_DEBUGGER CONFIG_ROCKCHIP_FIQ_DEBUGGER CONFIG_MAGIC_SYSRQ_SERIAL; do
            if grep -Eq "^$option=[ym]$" ${B}/.config; then
                bbfatal "Production UART input requires $option to be disabled"
            fi
        done
        for option in CONFIG_SERIAL_8250 CONFIG_SERIAL_8250_CONSOLE CONFIG_SERIAL_8250_DW; do
            grep -qx "$option=y" ${B}/.config || bbfatal "Production UART logging requires $option=y"
        done
    fi
}
