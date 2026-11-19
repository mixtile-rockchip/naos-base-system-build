SUMMARY = "NAOS shared EFI system partition and recovery image for the Radxa AIRbox Q900"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"
COMPATIBLE_MACHINE = "^radxa-airbox-q900$"

inherit deploy nopackages python3native

DEPENDS = "dosfstools-native mtools-native python3-pefile-native systemd-boot-native virtual/cross-binutils"
do_compile[depends] += "systemd-boot:do_deploy virtual/kernel:do_deploy naos-initramfs:do_image_complete"

S = "${UNPACKDIR}"
B = "${WORKDIR}/boot"
do_compile[cleandirs] = "${B}"

# A 4 KiB FAT32 ESP needs at least 65,525 data clusters.
NAOS_Q900_BOOT_SIZE_KIB = "266240"

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
    install -Dm0644 "${DEPLOY_DIR_IMAGE}/systemd-bootaa64.efi" "$staging/EFI/BOOT/BOOTAA64.EFI"
    if [ "$slot" = A ]; then
        install -m 0644 "${B}/naos-q900-b.efi" "$staging/EFI/NAOS/naos-b.efi"
        # Redirect the direct EFI entry created by the old slot management.
        install -m 0644 "$staging/EFI/BOOT/BOOTAA64.EFI" "$staging/EFI/NAOS/naos.efi"
    fi
    install -d "$staging/loader/entries"
    printf 'default naos-*.conf\ntimeout 0\neditor no\n' > "$staging/loader/loader.conf"
    printf 'title NAOS %s\nsort-key 10-naos-confirmed\nuki /EFI/NAOS/naos-%s.efi\n' \
        "$slot" "$slot_name" > "$staging/loader/entries/naos-confirmed.conf"

    mkfs.vfat -F 32 -S 4096 -s 1 -n "NAOS_BOOT_$slot" -C "$esp" "${NAOS_Q900_BOOT_SIZE_KIB}"
    mcopy -i "$esp" -s "$staging"/* ::/
}

do_compile() {
    build_uki A "${NAOS_Q900_SYSTEM_A_PARTUUID}" "${B}/naos-q900-a.efi"
    build_uki B "${NAOS_Q900_SYSTEM_B_PARTUUID}" "${B}/naos-q900-b.efi"
    build_esp A a
    build_esp B b
}

do_deploy() {
    install -m 0644 "${B}/naos-q900-boot-a.vfat" "${B}/naos-q900-boot-b.vfat" \
        "${B}/naos-q900-a.efi" "${B}/naos-q900-b.efi" "${DEPLOYDIR}/"
}
addtask deploy after do_compile before do_build
