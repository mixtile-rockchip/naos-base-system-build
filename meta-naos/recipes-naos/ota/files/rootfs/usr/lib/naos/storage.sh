#!/bin/sh
# Shared by initramfs and the Alpine RAUC service. No partition writes here.

naos_partlabel() {
    naos_device=$(readlink -f "$1") || return 1
    [ -b "$naos_device" ] || return 1
    naos_name=${naos_device##*/}
    [ -f "/sys/class/block/$naos_name/partition" ] || return 1
    while IFS= read -r naos_line; do
        case "$naos_line" in PARTNAME=?*) printf '%s\n' "${naos_line#PARTNAME=}"; return 0 ;; esac
    done <"/sys/class/block/$naos_name/uevent"

    # BLKPG delete/add can discard the kernel's GPT name after growpart.
    # Query only this partition's parent and require identical disk extents.
    naos_table_disk=$(naos_parent_disk "$naos_device") || return 1
    naos_partno=$(cat "/sys/class/block/$naos_name/partition") || return 1
    case "$naos_partno" in ''|*[!0-9]*) return 1 ;; esac
    [ "$naos_partno" -gt 0 ] || return 1
    naos_kernel_start=$(cat "/sys/class/block/$naos_name/start") || return 1
    naos_kernel_size=$(cat "/sys/class/block/$naos_name/size") || return 1
    case "$naos_kernel_start:$naos_kernel_size" in *[!0-9:]*|:*|*:) return 1 ;; esac
    [ "$naos_kernel_size" -gt 0 ] || return 1
    naos_table_row=$(
        partx --raw --noheadings --output START,SECTORS,NAME \
            --nr "$naos_partno" "$naos_table_disk" || exit 1
        printf x
    ) || return 1
    naos_table_row=${naos_table_row%x}
    naos_table_row=$(printf '%s' "$naos_table_row" | awk '
        NR == 1 && NF == 3 && $1 ~ /^[0-9]+$/ && $2 ~ /^[0-9]+$/ {
            start = $1; size = $2; name = $3; valid = 1
        }
        END {
            if (NR != 1 || !valid) exit 1
            print start, size, name
        }') || return 1
    IFS=' ' read -r naos_table_start naos_table_size naos_table_name <<EOF || return 1
$naos_table_row
EOF
    [ "$naos_table_start" -eq "$naos_kernel_start" ] || return 1
    [ "$naos_table_size" -eq "$naos_kernel_size" ] || return 1
    printf '%s\n' "$naos_table_name"
}

naos_parent_disk() {
    naos_path=$(readlink -f "/sys/class/block/${1##*/}") || return 1
    [ -f "$naos_path/partition" ] || return 1
    naos_parent=${naos_path%/*}
    [ -b "/dev/${naos_parent##*/}" ] || return 1
    printf '/dev/%s\n' "${naos_parent##*/}"
}

naos_find_partition() {
    naos_disk_path=$(readlink -f "/sys/class/block/${1##*/}") || return 1
    naos_wanted=$2
    naos_found=
    for naos_partition in "$naos_disk_path"/*; do
        [ -f "$naos_partition/partition" ] || continue
        naos_dev="/dev/${naos_partition##*/}"
        naos_label=$(naos_partlabel "$naos_dev") || continue
        [ "$naos_label" = "$naos_wanted" ] || continue
        [ -z "$naos_found" ] || return 1
        [ -b "$naos_dev" ] || return 1
        naos_found=$naos_dev
    done
    [ -n "$naos_found" ] || return 1
    printf '%s\n' "$naos_found"
}

naos_bind_disk_parts() {
    naos_disk=$(readlink -f "$1") || return 1
    [ -b "$naos_disk" ] || return 1
    [ ! -f "/sys/class/block/${naos_disk##*/}/partition" ] || return 1
    mkdir -p /dev/naos || return 1
    for naos_target in uboot boot_a boot_b system_a system_b misc userdata; do
        naos_target_dev=$(naos_find_partition "$naos_disk" "$naos_target") || return 1
        ln -sfn "$naos_target_dev" "/dev/naos/$naos_target" || return 1
    done
    # fw_setenv writes partition-relative offsets in misc. Verify its physical
    # start agrees with U-Boot's absolute offsets (13 MiB and 13 MiB + 64 KiB).
    naos_misc=$(readlink -f /dev/naos/misc) || return 1
    naos_misc_start=$(cat "/sys/class/block/${naos_misc##*/}/start") || return 1
    naos_misc_size=$(cat "/sys/class/block/${naos_misc##*/}/size") || return 1
    [ "$((naos_misc_start * 512))" -eq 12582912 ] || return 1
    [ "$((naos_misc_size * 512))" -ge 1146880 ] || return 1
    ln -sfn "$naos_disk" /dev/naos/disk || return 1
}

naos_require_last_partition() {
    naos_disk_path=$(readlink -f "/sys/class/block/${1##*/}") || return 1
    naos_data_path=$(readlink -f "/sys/class/block/${2##*/}") || return 1
    [ "${naos_data_path%/*}" = "$naos_disk_path" ] || return 1
    naos_data_start=$(cat "$naos_data_path/start") || return 1
    for naos_partition in "$naos_disk_path"/*; do
        [ -f "$naos_partition/partition" ] || continue
        [ "$naos_partition" != "$naos_data_path" ] || continue
        naos_start=$(cat "$naos_partition/start") || return 1
        naos_size=$(cat "$naos_partition/size") || return 1
        [ "$((naos_start + naos_size))" -le "$naos_data_start" ] || return 1
    done
}
