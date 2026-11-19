IMAGE_QCOMFLASH_FS_TYPE = "squashfs"
QCOM_ESP_IMAGE = ""

NAOS_Q900_FLASH_INSTRUCTIONS = "${NAOS_QCOM_LAYER_DIR}/recipes-bsp/partition/files/flash-q900-ufs.txt"
do_image_qcomflash[depends] += "naos-q900-boot:do_deploy"
do_image_qcomflash[depends] += "${@bb.utils.contains('NAOS_Q900_SIGN_XBL_CONFIG', '1', 'naos-q900-xbl-config:do_deploy', '', d)}"
QCOM_XBL_CONFIG = "${@bb.utils.contains('NAOS_Q900_SIGN_XBL_CONFIG', '1', d.getVar('NAOS_Q900_SIGNED_XBL_CONFIG'), d.getVar('NAOS_Q900_XBL_CONFIG'), d)}"
do_image_qcomflash[file-checksums] += "${NAOS_Q900_FLASH_INSTRUCTIONS}:True"
do_image_qcomflash[prefuncs] += "naos_q900_check_xbl_config naos_q900_check_system_partuuids"
naos_q900_check_system_partuuids[vardeps] += "NAOS_Q900_SYSTEM_A_PARTUUID NAOS_Q900_SYSTEM_B_PARTUUID"

naos_q900_check_xbl_config() {
    if [ "${NAOS_Q900_SIGN_XBL_CONFIG}" = "1" ]; then
        local signed_xbl_config
        [ -z "${QCOM_CAPSULE_FIRMWARE}" ] ||
            bbfatal "Q900 signed XBL config must be the only XBL config override"
        [ "${QCOM_XBL_CONFIG}" = "${NAOS_Q900_SIGNED_XBL_CONFIG}" ] ||
            bbfatal "Q900 factory image must select the signed XBL config"
        signed_xbl_config="${DEPLOY_DIR_IMAGE}/${QCOM_BOOT_FILES_SUBDIR}/${QCOM_XBL_CONFIG}"
        [ -s "$signed_xbl_config" ] ||
            bbfatal "Q900 signed XBL config is missing: ${QCOM_XBL_CONFIG}"
        [ "$(stat -c %s "$signed_xbl_config")" -le 524288 ] ||
            bbfatal "Q900 signed XBL config exceeds its 512 KiB partitions"
    fi
}

python naos_q900_check_system_partuuids() {
    from pathlib import Path
    from struct import unpack_from
    from uuid import UUID

    layout_dir = Path(d.getVar("DEPLOY_DIR_IMAGE")) / d.getVar("QCOM_PARTITION_FILES_SUBDIR")
    gpt = (layout_dir / "gpt_main0.bin").read_bytes()
    sector_size = 4096
    if gpt[sector_size:sector_size + 8] != b"EFI PART":
        bb.fatal("Q900 factory GPT must use 4096-byte sectors")

    first_lba, count, entry_size = unpack_from("<QII", gpt, sector_size + 72)
    partition_ids = {}
    esp_type = UUID("c12a7328-f81f-11d2-ba4b-00a0c93ec93b").bytes_le
    esp_labels = []
    for index in range(count):
        offset = first_lba * sector_size + index * entry_size
        entry = gpt[offset:offset + entry_size]
        label = entry[56:128].decode("utf-16-le").rstrip("\0")
        if label:
            partition_ids[label] = UUID(bytes_le=entry[16:32])
        if entry[:16] == esp_type:
            esp_labels.append(label)

    if esp_labels != ["efi"]:
        bb.fatal("Q900 factory GPT must contain one EFI System Partition named efi")

    for slot in ("A", "B"):
        expected = UUID(d.getVar(f"NAOS_Q900_SYSTEM_{slot}_PARTUUID"))
        if partition_ids.get(f"system_{slot.lower()}") != expected:
            bb.fatal(f"Q900 system_{slot.lower()} GPT PARTUUID does not match its boot image")
}

deploy_partition_files:append() {
    if [ "$1" = "${DEPLOY_DIR_IMAGE}/${QCOM_PARTITION_FILES_SUBDIR}" ] && [ "$2" = "." ]; then
        rm -f vmlinux
        for slot in a b; do
            boot_image="naos-q900-boot-$slot.vfat"
            install -m 0644 "${DEPLOY_DIR_IMAGE}/$boot_image" "$boot_image"
            if [ "$(stat -c %s "$boot_image")" -ne 272629760 ]; then
                bbfatal "$boot_image must be 260 MiB"
            fi
        done

        mv rootfs.img naos-q900-system.squashfs
        if [ "$(stat -c %s naos-q900-system.squashfs)" -gt 209715200 ]; then
            bbfatal "Q900 SquashFS image exceeds the 200 MiB system partition"
        fi

        truncate -s 4M zeros_misc.bin
        truncate -s 32M zeros_userdata.bin
        chmod 0644 zeros_misc.bin zeros_userdata.bin

        install -m 0644 "${NAOS_Q900_FLASH_INSTRUCTIONS}" .
    fi
}
