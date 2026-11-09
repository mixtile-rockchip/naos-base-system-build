SUMMARY = "Hardware-bound stateless envelope for the NAOS system key"
LICENSE = "GPL-2.0-only & BSD-2-Clause"
LIC_FILES_CHKSUM = " \
    file://${UNPACKDIR}/security-tool/rk-huk-keybox/LICENSES/GPL-2.0-only.txt;md5=b234ee4d69f5fce4486a80fdaf4a4263 \
    file://${UNPACKDIR}/security-tool/rk-huk-keybox/LICENSES/BSD-2-Clause.txt;md5=1cf499653e47debdbb383d79b999dc5b \
"

# Keep the SDK devkit and our CA/TA implementation in separate pinned sources.
NAOS_RK_TEE_USER_URI ?= ""
SRC_URI = "${@d.getVar('NAOS_RK_TEE_USER_URI') + ';name=tee;destsuffix=rk-tee-user' if d.getVar('NAOS_RK_TEE_USER_URI') else ''} \
    ${NAOS_SECURITY_TOOL_URI};name=tools;destsuffix=security-tool \
"
SRCREV_tee = "957bd3aa88589a2c78bc12867ef082675f464e19"
SRCREV_tools = "${NAOS_SECURITY_TOOL_SRCREV}"
SRCREV_FORMAT = "tee_tools"
S = "${UNPACKDIR}/rk-tee-user"
B = "${WORKDIR}/build"

COMPATIBLE_MACHINE = "${@'^mixtile-core3588e-v2$' if d.getVar('NAOS_SYSTEM_ENCRYPTION') == '1' else '^$'}"
NAOS_SYSTEM_KEY_DIR ?= "${NAOS_PROJECT_ROOT}/key/system-encryption"
NAOS_PACKAGE ?= "0"
RK_HUK_KEYBOX_UUID = "6bada6f2-74f7-49cc-8d0f-4195a3c62fb0"
DEPENDS = "optee-client-naos python3-pycryptodomex-native python3-pyelftools-native"
RDEPENDS:${PN} = "optee-client-naos"

inherit python3native naos-shallow-git

python __anonymous() {
    if d.getVar("NAOS_SYSTEM_ENCRYPTION") != "1":
        return
    if not d.getVar("NAOS_RK_TEE_USER_URI"):
        bb.fatal("Set NAOS_RK_TEE_USER_URI to the pinned SDK rk_tee_user Git source")
    if d.getVar("NAOS_PACKAGE") == "1":
        return
    directory = d.getVar("NAOS_SYSTEM_KEY_DIR") or ""
    if not directory:
        bb.fatal("NAOS_SYSTEM_KEY_DIR is required for system encryption")
    for task in ("do_configure", "do_compile", "do_install"):
        d.appendVarFlag(task, "file-checksums", " " + directory + "/system.key:True")
}

do_configure[cleandirs] = "${B}"
do_configure[vardeps] += "NAOS_PACKAGE"
do_compile[vardeps] += "NAOS_PACKAGE"
do_install[vardeps] += "NAOS_PACKAGE"
python do_configure() {
    import hashlib
    import shutil
    import stat
    from pathlib import Path
    import re
    import uuid

    key = None
    if d.getVar("NAOS_PACKAGE") != "1":
        key_path = Path(d.getVar("NAOS_SYSTEM_KEY_DIR")) / "system.key"
        try:
            if key_path.is_symlink():
                raise ValueError("system.key must be an ordinary file")
            info = key_path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise ValueError("system.key must be a private ordinary file")
            key = key_path.read_bytes()
            if not re.fullmatch(rb"[a-f0-9]{64}", key):
                raise ValueError("system.key must be exactly 64 lowercase hex bytes without newline")
        except (OSError, ValueError):
            bb.fatal("Invalid private 64-character hexadecimal system.key file")

    source = Path(d.getVar("S"))
    build = Path(d.getVar("B"))
    tools = Path(d.getVar("UNPACKDIR")) / "security-tool/rk-huk-keybox"
    identity = uuid.UUID(d.getVar("RK_HUK_KEYBOX_UUID"))
    fields = identity.fields
    definition = "{ 0x%08x, 0x%04x, 0x%04x, { %s } }" % (
        fields[0], fields[1], fields[2], ", ".join("0x%02x" % value for value in identity.bytes[8:]))
    # Keep the pinned external SDK derivation API, TA devkit and its native
    # default TA signing; the CA/TA implementation comes only from our project.
    shutil.copyfile(tools / "ca/main.c", source / "v2/host/keybox_app/main.c")
    ta = source / "v2/ta/keybox_app"
    shutil.copyfile(tools / "ta/keybox.c", ta / "keybox.c")
    header = (tools / "include/ta_keybox.h.in").read_text()
    header = header.replace("@TA_KEYBOX_UUID@", definition)
    header = header.replace("@NAOS_UUID_BYTES@", ", ".join("0x%02x" % value for value in identity.bytes))
    (ta / "include/ta_keybox.h").write_text(header)
    shutil.copyfile(tools / "ta/include/user_ta_header_defines.h", ta / "include/user_ta_header_defines.h")
    shutil.copyfile(tools / "ta/Makefile", ta / "Makefile")
    shutil.copyfile(tools / "ta/sub.mk", ta / "sub.mk")
    if key is not None:
        (build / "system-key.sha256").write_text(hashlib.sha256(key).hexdigest() + "\n")
}

do_compile() {
    install -d ${B}/host ${B}/ta
    ${CC} ${CPPFLAGS} ${CFLAGS} -UCA_DEBUG -UDEBUG_DUMP -UCA_TEST \
        -I${STAGING_INCDIR} \
        -I${S}/v2/export-ta_arm64/host_include \
        -I${S}/v2/ta/keybox_app/include \
        ${S}/v2/host/keybox_app/main.c -o ${B}/host/keybox_app \
        ${LDFLAGS} -lteec -lpthread
    oe_runmake -C ${S}/v2/ta/keybox_app \
        O=${B}/ta BINARY=${RK_HUK_KEYBOX_UUID} \
        TA_DEV_KIT_DIR=${S}/v2/export-ta_arm64 \
        CROSS_COMPILE=${TARGET_PREFIX} \
        CROSS_COMPILE64=${TARGET_PREFIX} \
        CROSS_COMPILE_ta_arm64=${TARGET_PREFIX} \
        HOSTCC="${BUILD_CC}" PYTHON3="${PYTHON}" \
        libgccta_arm64="$(${CC} -print-libgcc-file-name)" \
        CFLAGS= CPPFLAGS= LDFLAGS=
}

do_install() {
    install -d ${D}${bindir} ${D}/lib/optee_armtz
    install -m 0755 ${B}/host/keybox_app ${D}${bindir}/keybox_app
    install -m 0644 ${B}/ta/${RK_HUK_KEYBOX_UUID}.ta ${D}/lib/optee_armtz/${RK_HUK_KEYBOX_UUID}.ta
    if [ "${NAOS_PACKAGE}" != 1 ]; then
        install -d ${D}${libdir}/naos
        install -m 0644 ${B}/system-key.sha256 ${D}${libdir}/naos/system-key.sha256
    fi
}

FILES:${PN} += "/lib/optee_armtz/*.ta ${libdir}/naos/system-key.sha256"
