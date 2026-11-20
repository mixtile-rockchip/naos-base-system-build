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

# Check the on-disk GPT against the kernel's view before binding a Q900 disk.
# Both partx and sysfs report extents in 512-byte units on 4Kn media.
naos_q900_partitions() (
    q900_disk=$(readlink -f "$1") || exit 1
    [ -b "$q900_disk" ] || exit 1
    q900_path="/sys/class/block/${q900_disk##*/}"
    [ ! -f "$q900_path/partition" ] || exit 1
    [ "$(cat "$q900_path/queue/logical_block_size" 2>/dev/null)" = 4096 ] || exit 1
    q900_device=$(readlink -f "$q900_path/device") || exit 1
    q900_address=${q900_device##*/}
    case "$q900_address" in *:0:0:0) ;; *) exit 1 ;; esac
    q900_host=${q900_address%%:*}
    case "$q900_host" in ''|*[!0-9]*) exit 1 ;; esac
    [ "$(cat "/sys/class/scsi_host/host$q900_host/proc_name" 2>/dev/null)" = ufshcd ] || exit 1
    q900_controller=${q900_device%/*/*/*}
    q900_driver=$(readlink -f "$q900_controller/driver") || exit 1
    [ "${q900_driver##*/}" = ufshcd-qcom ] || exit 1

    q900_table=$(partx --raw --noheadings --output NR,START,SECTORS,NAME,SCHEME "$q900_disk") || exit 1
    q900_rows=$(printf '%s\n' "$q900_table" | awk '
        BEGIN {
            split("efi boot-b system_a system_b misc userdata", names)
            split("260 260 200 200 4 32", mib)
        }
        NF != 5 || $1 != NR || $2 !~ /^[0-9]+$/ || $3 !~ /^[0-9]+$/ ||
            $4 != names[NR] || $5 != "gpt" { exit 1 }
        $2 <= 0 || $2 < previous_end || $2 % 8 || $3 % 8 { exit 1 }
        NR < 6 && $3 != mib[NR] * 2048 { exit 1 }
        NR == 6 && $3 < mib[NR] * 2048 { exit 1 }
        { previous_end = $2 + $3; print $1, $2, $3, $4 }
        END { if (NR != 6) exit 1 }
    ') || exit 1
    while IFS=' ' read -r q900_number q900_start q900_size q900_label; do
        q900_partition=$(naos_find_partition "$q900_disk" "$q900_label") || exit 1
        q900_part_path="/sys/class/block/${q900_partition##*/}"
        [ "$(cat "$q900_part_path/partition")" = "$q900_number" ] || exit 1
        [ "$(cat "$q900_part_path/start")" = "$q900_start" ] || exit 1
        [ "$(cat "$q900_part_path/size")" = "$q900_size" ] || exit 1
        case "$q900_label" in
            efi) q900_label=boot_a ;;
            boot-b) q900_label=boot_b ;;
        esac
        printf '%s %s\n' "$q900_label" "$q900_partition"
    done <<EOF
$q900_rows
EOF
    naos_require_last_partition "$q900_disk" "$q900_partition"
)

naos_q900_layout_compatible() (
    layout_path=/run/naos/layout-compatible
    if [ ! -e "$layout_path" ] && [ ! -L "$layout_path" ]; then
        printf '%s\n' naos-radxa-airbox-q900-ufs-v3
        exit 0
    fi
    [ -f "$layout_path" ] && [ ! -L "$layout_path" ] || exit 1
    layout=$(cat "$layout_path") || exit 1
    case "$layout" in
        naos-radxa-airbox-q900-ufs-v3|naos-radxa-airbox-q900-ufs-v3-data-crypt|naos-radxa-airbox-q900-ufs-v3-disk-crypt)
            printf '%s\n' "$layout" ;;
        *) exit 1 ;;
    esac
)

naos_q900_system_matches_layout() {
    grep -q "\"layout_compatible\"[[:space:]]*:[[:space:]]*\"$2\"" "$1/usr/lib/naos/version.json"
}

naos_sys_block_for_node() (
    node=$(readlink -f "$1") || exit 1
    [ -b "$node" ] || exit 1
    number=$(stat -Lc '%t:%T' "$node") || exit 1
    case "$number" in ''|:*|*:|*[!0-9a-fA-F:]*) exit 1 ;; esac
    major=${number%%:*}
    minor=${number#*:}
    case "$minor" in *:*) exit 1 ;; esac
    sys=$(readlink -f "/sys/dev/block/$((0x$major)):$((0x$minor))") || exit 1
    [ -d "$sys" ] && [ -r "$sys/dev" ] || exit 1
    printf '%s\n' "$sys"
)

# Q900 keeps the raw system A/B aliases for RAUC. A mounted mapper must
# cover exactly its selected partition with the UFS logical block size.
naos_q900_mapper_for_raw() (
    raw=$(naos_sys_block_for_node "$1") || exit 1
    [ -r "$raw/partition" ] || exit 1
    mapper=$(naos_sys_block_for_node "/dev/mapper/$2") || exit 1
    canonical=/dev/${mapper##*/}
    [ "$(naos_sys_block_for_node "$canonical")" = "$mapper" ] || exit 1
    [ "$(cat "$mapper/dm/name")" = "$2" ] || exit 1
    [ "$(cat "$mapper/ro")" = "$3" ] || exit 1
    [ "$(cat "$mapper/queue/logical_block_size")" = 4096 ] || exit 1
    raw_size=$(cat "$raw/size") || exit 1
    case "$raw_size" in ''|*[!0-9]*) exit 1 ;; esac
    [ "$raw_size" -gt 0 ] && [ "$((raw_size % 8))" -eq 0 ] || exit 1
    [ "$(cat "$mapper/size")" = "$raw_size" ] || exit 1
    set -- "$mapper"/slaves/*
    [ "$#" -eq 1 ] && [ -L "$1" ] || exit 1
    [ "$(readlink -f "$1")" = "$raw" ] || exit 1
    printf '%s\n' "$canonical"
)

naos_system_mapper_for_raw() {
    naos_q900_mapper_for_raw "$1" naos-system 1
}

naos_require_block_mount() (
    sys=$(naos_sys_block_for_node "$4") || exit 1
    dev=$(cat "$sys/dev") || exit 1
    awk -v point="$1" -v fs="$2" -v mode="$3" -v dev="$dev" '
        $5 == point {
            seen++
            if ($3 != dev || $4 != "/" || $6 !~ ("(^|,)" mode "(,|$)")) next
            for (i = 7; i <= NF; i++) {
                if ($i != "-") continue
                if ($(i + 1) == fs) matched++
                break
            }
        }
        END { exit !(seen == 1 && matched == 1) }
    ' /proc/self/mountinfo
)

naos_ensure_device_link() {
    [ "$(readlink "$2" 2>/dev/null)" = "$1" ] || ln -sfn "$1" "$2"
}

naos_bind_disk_parts() {
    naos_disk=$(readlink -f "$1") || return 1
    [ -b "$naos_disk" ] || return 1
    [ ! -f "/sys/class/block/${naos_disk##*/}/partition" ] || return 1
    case "${2:-}" in
        radxa-airbox-q900)
            naos_q900_parts=$(naos_q900_partitions "$naos_disk") || return 1
            if [ "${3:-0}" = 1 ]; then
                naos_raw_userdata=$(printf '%s\n' "$naos_q900_parts" | awk '$1 == "userdata" {print $2}')
                naos_mapped_userdata=$(naos_q900_mapper_for_raw "$naos_raw_userdata" naos-userdata 0) ||
                    return 1
            fi
            mkdir -p /dev/naos || return 1
            while IFS=' ' read -r naos_target naos_target_dev; do
                if [ "$naos_target" = userdata ] && [ "${3:-0}" = 1 ]; then
                    naos_ensure_device_link "$naos_target_dev" /dev/naos/userdata-raw || return 1
                    naos_target_dev=$naos_mapped_userdata
                fi
                naos_ensure_device_link "$naos_target_dev" "/dev/naos/$naos_target" || return 1
            done <<EOF
$naos_q900_parts
EOF
            naos_ensure_device_link "$naos_disk" /dev/naos/disk
            return
            ;;
        '') ;;
        *) return 1 ;;
    esac
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
