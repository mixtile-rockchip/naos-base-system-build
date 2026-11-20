SUMMARY = "NAOS shared EFI system partition and recovery image for the Radxa AIRbox Q900"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"
COMPATIBLE_MACHINE = "^radxa-airbox-q900$"

inherit deploy nopackages python3native

DEPENDS = "dosfstools-native mtools-native python3-pefile-native systemd-boot-native virtual/cross-binutils"
do_compile[depends] += "systemd-boot:do_deploy virtual/kernel:do_deploy naos-initramfs:do_image_complete"
NAOS_Q900_UEFI_SIGNING_FILES = "db.key db.crt PK.auth KEK.auth db.auth"
do_compile[file-checksums] += "${@bb.utils.contains('NAOS_SECURE_BOOT', '1', ' '.join(d.getVar('NAOS_SECURE_BOOT_KEY_DIR') + '/' + name + ':True' for name in d.getVar('NAOS_Q900_UEFI_SIGNING_FILES').split()), '', d)}"

S = "${UNPACKDIR}"
B = "${WORKDIR}/boot"
do_compile[cleandirs] = "${B}"

# A 4 KiB FAT32 ESP needs at least 65,525 data clusters.
NAOS_Q900_BOOT_SIZE_KIB = "266240"

python () {
    import re
    from pathlib import Path

    enabled = d.getVar("NAOS_SECURE_BOOT")
    if enabled not in ("0", "1"):
        bb.fatal("NAOS_SECURE_BOOT must be 0 or 1")
    auto_enroll = d.getVar("NAOS_Q900_UEFI_AUTO_ENROLL")
    if auto_enroll not in ("0", "1"):
        bb.fatal("NAOS_Q900_UEFI_AUTO_ENROLL must be 0 or 1")
    if auto_enroll == "1" and enabled != "1":
        bb.fatal("NAOS_Q900_UEFI_AUTO_ENROLL requires NAOS_SECURE_BOOT=1")
    if enabled == "1":
        directory = d.getVar("NAOS_SECURE_BOOT_KEY_DIR") or ""
        if not re.fullmatch(r"/[A-Za-z0-9._+/-]+", directory):
            bb.fatal("NAOS_SECURE_BOOT_KEY_DIR must be an absolute signing directory without whitespace or shell metacharacters")
        for filename in d.getVar("NAOS_Q900_UEFI_SIGNING_FILES").split():
            if not (Path(directory) / filename).is_file():
                bb.fatal("Missing Q900 UEFI Secure Boot input: %s" % (Path(directory) / filename))
}

sign_efi() {
    input="$1"
    output="$2"
    keydir="${NAOS_SECURE_BOOT_KEY_DIR}"
    sbsign --key "$keydir/db.key" --cert "$keydir/db.crt" --output "$output" "$input"
    sbverify --cert "$keydir/db.crt" "$output"
}

build_uki() {
    slot="$1"
    system_uuid="$2"

    [ -n "$system_uuid" ] || bbfatal "Missing root filesystem PARTUUID for slot $slot"
    "${PYTHON}" "${STAGING_BINDIR_NATIVE}/ukify" build \
        --efi-arch aa64 \
        --stub "${DEPLOY_DIR_IMAGE}/linuxaa64.efi.stub" \
        --linux "${DEPLOY_DIR_IMAGE}/Image" \
        --initrd "${DEPLOY_DIR_IMAGE}/naos-initramfs-${MACHINE}.cpio.gz" \
        --devicetree "${DEPLOY_DIR_IMAGE}/${@os.path.basename(d.getVar('QCOM_UKI_DEVICETREE'))}" \
        --tools "${STAGING_DIR_NATIVE}${prefix_native}/lib/systemd/tools" \
        --cmdline "console=${KERNEL_CONSOLE} panic=3 naos.board=${MACHINE} naos.slot=$slot rauc.slot=$slot naos.system=PARTUUID=$system_uuid ${KERNEL_CMDLINE_EXTRA}" \
        --output "$3"
}

build_esp() {
    slot="$1"
    slot_name="$2"
    esp="${B}/naos-q900-boot-$slot_name.vfat"
    staging="${B}/$slot"

    install -Dm0644 "${B}/naos-q900-$slot_name.efi" "$staging/EFI/NAOS/naos-$slot_name.efi"
    install -Dm0644 "${B}/systemd-bootaa64.efi" "$staging/EFI/BOOT/BOOTAA64.EFI"
    if [ "$slot" = A ]; then
        install -m 0644 "${B}/naos-q900-b.efi" "$staging/EFI/NAOS/naos-b.efi"
        # Redirect the direct EFI entry created by the old slot management.
        install -m 0644 "$staging/EFI/BOOT/BOOTAA64.EFI" "$staging/EFI/NAOS/naos.efi"
    fi
    install -d "$staging/loader/entries"
    printf 'default naos-*.conf\ntimeout 0\neditor no\n' > "$staging/loader/loader.conf"
    if [ "${NAOS_SECURE_BOOT}" = 1 ] && [ "$slot" = A ]; then
        if [ "${NAOS_Q900_UEFI_AUTO_ENROLL}" = 1 ]; then
            enroll_dir=auto
            printf 'secure-boot-enroll force\n' >> "$staging/loader/loader.conf"
        else
            enroll_dir=NAOS
            printf 'default naos-*.conf\ntimeout 5\neditor no\nsecure-boot-enroll manual\n' > "$staging/loader/loader.conf"
        fi
        for auth in PK.auth KEK.auth db.auth; do
            install -Dm0644 "${NAOS_SECURE_BOOT_KEY_DIR}/$auth" "$staging/loader/keys/$enroll_dir/$auth"
        done
    fi
    printf 'title NAOS %s\nsort-key 10-naos-confirmed\nuki /EFI/NAOS/naos-%s.efi\n' \
        "$slot" "$slot_name" > "$staging/loader/entries/naos-confirmed.conf"

    mkfs.vfat -F 32 -S 4096 -s 1 -n "NAOS_BOOT_$slot" -C "$esp" "${NAOS_Q900_BOOT_SIZE_KIB}"
    mcopy -i "$esp" -s "$staging"/* ::/
}

do_compile() {
    build_uki A "${NAOS_Q900_SYSTEM_A_PARTUUID}" "${B}/naos-q900-a.efi"
    build_uki B "${NAOS_Q900_SYSTEM_B_PARTUUID}" "${B}/naos-q900-b.efi"
    install -m 0644 "${DEPLOY_DIR_IMAGE}/systemd-bootaa64.efi" "${B}/systemd-bootaa64.efi"
    if [ "${NAOS_SECURE_BOOT}" = 1 ]; then
        command -v sbsign >/dev/null && command -v sbverify >/dev/null ||
            bbfatal "Q900 UEFI Secure Boot signing requires the host sbsigntool package"
        for image in systemd-bootaa64.efi naos-q900-a.efi naos-q900-b.efi; do
            sign_efi "${B}/$image" "${B}/$image.signed"
            mv "${B}/$image.signed" "${B}/$image"
        done
    fi
    build_esp A a
    build_esp B b
}

do_deploy() {
    install -m 0644 "${B}/naos-q900-boot-a.vfat" "${B}/naos-q900-boot-b.vfat" \
        "${B}/naos-q900-a.efi" "${B}/naos-q900-b.efi" "${DEPLOYDIR}/"
}
addtask deploy after do_compile before do_build
