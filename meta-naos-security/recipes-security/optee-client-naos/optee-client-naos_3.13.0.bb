SUMMARY = "OP-TEE client and supplicant for the RK HUK system-key envelope"
LICENSE = "BSD-2-Clause"
LIC_FILES_CHKSUM = "file://LICENSE;md5=69663ab153298557a59c67a60a743e5b"

SRC_URI = "git://github.com/OP-TEE/optee_client.git;protocol=https;branch=master;destsuffix=optee-client"
SRCREV = "7c9c423d00e96bf51debd5fe10fd70dce83be5cc"
S = "${UNPACKDIR}/optee-client"

COMPATIBLE_MACHINE = "${@'^mixtile-core3588e-v2$' if d.getVar('NAOS_SYSTEM_ENCRYPTION') == '1' else '^$'}"

inherit cmake

EXTRA_OECMAKE = " \
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
    -DBUILD_SHARED_LIBS=ON -DCFG_WERROR=OFF \
    -DCFG_TEE_CLIENT_LOG_LEVEL=1 -DCFG_TEE_SUPP_LOG_LEVEL=1 \
    -DCFG_TEE_CLIENT_LOAD_PATH=/lib \
    -DCFG_TEE_FS_PARENT_PATH=/run/naos/tee-storage \
    -DCFG_TEE_CLIENT_LOG_FILE=/run/naos/tee-storage/teec.log \
    -DCFG_GP_SOCKETS=0 -DCFG_TA_TEST_PATH=OFF \
    -DRPMB_EMU=OFF -DCFG_TA_GPROF_SUPPORT=OFF -DCFG_FTRACE_SUPPORT=OFF \
    -DCMAKE_SKIP_RPATH=TRUE \
"
OECMAKE_TARGET_COMPILE = "teec tee-supplicant"

# Build only the client/supplicant needed by the HUK envelope, without PKCS#11 tools.
do_install() {
    install -d ${D}${libdir} ${D}${sbindir} ${D}${includedir}
    install -m 0644 ${B}/libteec/libteec.so.1.0.0 ${D}${libdir}/
    ln -s libteec.so.1.0.0 ${D}${libdir}/libteec.so.1
    ln -s libteec.so.1 ${D}${libdir}/libteec.so
    install -m 0755 ${B}/tee-supplicant/tee-supplicant ${D}${sbindir}/
    install -m 0644 ${S}/public/*.h ${D}${includedir}/
}

FILES:${PN} = "${libdir}/libteec.so.* ${sbindir}/tee-supplicant"
FILES:${PN}-dev += "${libdir}/libteec.so ${includedir}"
