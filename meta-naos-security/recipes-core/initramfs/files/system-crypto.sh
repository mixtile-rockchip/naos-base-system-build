#!/bin/sh
# The platform keybox authenticates the system-key envelope. Only the sealed
# envelope persists in misc; all plaintext stays in private tmpfs.

naos_system_crypto_stop_supplicant() {
    if [ -n "${naos_system_crypto_pid:-}" ]; then
        kill "$naos_system_crypto_pid" 2>/dev/null || true
        wait "$naos_system_crypto_pid" 2>/dev/null || true
        naos_system_crypto_pid=
    fi
}

naos_system_crypto_cleanup() {
    naos_system_crypto_stop_supplicant
    if [ -n "${naos_system_crypto_tmp:-}" ]; then
        rm -rf "$naos_system_crypto_tmp" || return 1
        naos_system_crypto_tmp=
    fi
    unset naos_system_crypto_key naos_system_import_key
}

naos_system_crypto_private_file() {
    [ -f "$1" ] && [ ! -L "$1" ] || return 1
    [ "$(stat -c %a "$1")" = 600 ] || return 1
    [ "$(stat -c %h "$1")" = 1 ] || return 1
    [ "$(stat -c %s "$1")" = "$2" ]
}

naos_system_crypto_hash() {
    naos_system_hash=$(sha256sum "$1") || return 1
    naos_system_hash=${naos_system_hash%% *}
    [ "${#naos_system_hash}" -eq 64 ] || return 1
    case "$naos_system_hash" in *[!0-9a-f]*) return 1 ;; esac
    printf '%s\n' "$naos_system_hash"
}

naos_system_crypto_key_hash() {
    naos_system_crypto_private_file "$1" 64 || return 1
    naos_system_check_key=$(cat "$1") || return 1
    [ "${#naos_system_check_key}" -eq 64 ] || return 1
    case "$naos_system_check_key" in *[!0-9a-f]*) return 1 ;; esac
    unset naos_system_check_key
    naos_system_crypto_hash "$1"
}

naos_system_crypto_read_region() {
    # Only these fixed 64 KiB windows belong to the system-key protocol.
    case "$2" in
        256) naos_system_region=import ;;
        640) naos_system_region=blob-a ;;
        768) naos_system_region=blob-b ;;
        *) return 1 ;;
    esac
    [ ! -e "$1" ] && [ ! -L "$1" ] || return 1
    naos_system_crypto_misc_io read "$naos_system_crypto_misc" \
        "$naos_system_region" "$1" || return 1
    naos_system_crypto_private_file "$1" 65536
}

naos_system_crypto_misc_io() {
    timeout -k 5 60 /usr/sbin/naos-data-key system "$@" \
        >"$naos_system_crypto_tmp/misc.log" 2>&1
}

naos_system_crypto_keybox() {
    [ ! -e "$3" ] && [ ! -L "$3" ] || return 2
    timeout -k 5 60 /usr/bin/keybox_app "$1" "$2" "$3" \
        >"$naos_system_crypto_tmp/keybox.log" 2>&1
}

naos_system_crypto_import_record() {
    # Version 2 has no caller-supplied TA UUID. The independent HUK tool owns
    # its fixed UUID; rebuild the exact header without evaluating shell data.
    (umask 077; dd if="$naos_system_crypto_tmp/region" \
        of="$naos_system_crypto_tmp/header" bs=256 count=1 2>/dev/null) || return 1
    naos_system_import_key=$(sed -n 's/^key=//p' "$naos_system_crypto_tmp/header") || return 1
    [ "${#naos_system_import_key}" -eq 64 ] || return 1
    case "$naos_system_import_key" in *[!0-9a-f]*) return 1 ;; esac
    (umask 077; printf '%s' "$naos_system_import_key" >"$naos_system_crypto_tmp/import.key") || return 1
    [ "$(naos_system_crypto_key_hash "$naos_system_crypto_tmp/import.key")" = "$naos_system_crypto_expected_hash" ] || return 1
    (umask 077; printf 'NAOS-SYSTEM-KEY\nversion=2\nkey=%s\n' \
        "$naos_system_import_key" >"$naos_system_crypto_tmp/padded-header") || return 1
    unset naos_system_import_key
    printf '%256s' '' >>"$naos_system_crypto_tmp/padded-header" || return 1
    (umask 077; dd if="$naos_system_crypto_tmp/padded-header" \
        of="$naos_system_crypto_tmp/canonical-header" bs=256 count=1 2>/dev/null) || return 1
    cmp -s "$naos_system_crypto_tmp/header" "$naos_system_crypto_tmp/canonical-header" || return 1
    (umask 077; dd if="$naos_system_crypto_tmp/region" \
        of="$naos_system_crypto_tmp/stored-hash" bs=64 skip=4 count=1 2>/dev/null) || return 1
    naos_system_header_hash=$(naos_system_crypto_hash "$naos_system_crypto_tmp/header") || return 1
    (umask 077; printf '%s' "$naos_system_header_hash" >"$naos_system_crypto_tmp/computed-hash") || return 1
    cmp -s "$naos_system_crypto_tmp/stored-hash" "$naos_system_crypto_tmp/computed-hash" || return 1
    (umask 077; dd if="$naos_system_crypto_tmp/region" \
        of="$naos_system_crypto_tmp/padding" bs=64 skip=5 count=1019 2>/dev/null) || return 1
    cmp -n 65216 "$naos_system_crypto_tmp/padding" /dev/zero >/dev/null
}

naos_system_crypto_misc_check() {
    [ -b "$naos_system_crypto_misc" ] || return 1
    naos_system_misc_name=${naos_system_crypto_misc##*/}
    [ "$(naos_partlabel "$naos_system_crypto_misc")" = misc ] || return 1
    [ "$(naos_parent_disk "$naos_system_crypto_misc")" = "$naos_system_crypto_disk" ] || return 1
    if [ "$naos_system_crypto_board" = radxa-airbox-q900 ]; then
        naos_system_crypto_parts=$(naos_q900_partitions "$naos_system_crypto_disk") || return 1
        [ "$(printf '%s\n' "$naos_system_crypto_parts" | awk '$1 == "misc" {print $2}')" = \
            "$naos_system_crypto_misc" ] || return 1
    else
        [ "$(cat "/sys/class/block/$naos_system_misc_name/start")" = 24576 ] || return 1
    fi
    [ "$(cat "/sys/class/block/$naos_system_misc_name/size")" = 8192 ]
}

naos_system_crypto_probe_slot() {
    naos_system_slot_valid=0
    naos_system_slot_hash=
    naos_system_crypto_read_region "$naos_system_crypto_tmp/slot-$1" "$2" ||
        fatal "cannot read a bounded system blob slot"
    if cmp -n 65536 "$naos_system_crypto_tmp/slot-$1" /dev/zero >/dev/null; then
        return 0
    else
        naos_system_slot_status=$?
        [ "$naos_system_slot_status" -eq 1 ] || fatal "cannot inspect the system blob slot"
    fi
    # A slot is exactly the platform's authenticated envelope and a zero tail.
    (umask 077; dd if="$naos_system_crypto_tmp/slot-$1" \
        of="$naos_system_crypto_tmp/tail-$1" bs="$naos_system_crypto_blob_bytes" skip=1 2>/dev/null) ||
        fatal "cannot inspect system blob padding"
    if ! cmp -n "$((65536 - naos_system_crypto_blob_bytes))" \
        "$naos_system_crypto_tmp/tail-$1" /dev/zero >/dev/null; then
        return 0
    fi
    (umask 077; dd if="$naos_system_crypto_tmp/slot-$1" \
        of="$naos_system_crypto_tmp/blob-$1" bs="$naos_system_crypto_blob_bytes" count=1 2>/dev/null) ||
        fatal "cannot extract the system blob"
    naos_system_crypto_private_file "$naos_system_crypto_tmp/blob-$1" "$naos_system_crypto_blob_bytes" ||
        fatal "invalid system blob staging file"
    if naos_system_crypto_keybox unseal "$naos_system_crypto_tmp/blob-$1" "$naos_system_crypto_tmp/key-$1"; then
        naos_system_slot_hash=$(naos_system_crypto_key_hash "$naos_system_crypto_tmp/key-$1") ||
            fatal "system blob returned an invalid private key file"
        naos_system_slot_valid=1
    else
        rm -f "$naos_system_crypto_tmp/key-$1" || fatal "cannot discard a failed system unseal output"
    fi
}

naos_system_crypto_write_slot() {
    case "$2" in 640|768) ;; *) return 1 ;; esac
    naos_system_crypto_private_file "$naos_system_crypto_selected_blob" "$naos_system_crypto_blob_bytes" || return 1
    naos_system_crypto_misc_check || return 1
    (umask 077; dd if=/dev/zero of="$naos_system_crypto_tmp/output-$1" \
        bs=512 count=128 2>/dev/null) || return 1
    dd if="$naos_system_crypto_selected_blob" of="$naos_system_crypto_tmp/output-$1" \
        bs="$naos_system_crypto_blob_bytes" count=1 conv=notrunc 2>/dev/null || return 1
    naos_system_crypto_private_file "$naos_system_crypto_tmp/output-$1" 65536 || return 1
    naos_system_crypto_misc_io write "$naos_system_crypto_misc" "blob-$1" \
        "$naos_system_crypto_tmp/output-$1" "$naos_system_crypto_tmp/slot-$1" || return 1
    naos_system_crypto_read_region "$naos_system_crypto_tmp/readback-$1" "$2" || return 1
    cmp -s "$naos_system_crypto_tmp/output-$1" "$naos_system_crypto_tmp/readback-$1" || return 1
    (umask 077; dd if="$naos_system_crypto_tmp/readback-$1" \
        of="$naos_system_crypto_tmp/blob-readback-$1" \
        bs="$naos_system_crypto_blob_bytes" count=1 2>/dev/null) || return 1
    naos_system_crypto_keybox unseal "$naos_system_crypto_tmp/blob-readback-$1" \
        "$naos_system_crypto_tmp/key-readback-$1" || return 1
    [ "$(naos_system_crypto_key_hash "$naos_system_crypto_tmp/key-readback-$1")" = "$naos_system_crypto_expected_hash" ] || return 1
    cmp -s "$naos_system_crypto_selected_key" "$naos_system_crypto_tmp/key-readback-$1" || return 1
    # The mount-time recheck compares against the record actually persisted.
    cat "$naos_system_crypto_tmp/readback-$1" >"$naos_system_crypto_tmp/slot-$1" || return 1
}

naos_prepare_encrypted_system() {
    naos_system_crypto_raw=$1
    naos_system_crypto_disk=$2
    naos_system_crypto_board=${3:-}
    case "$naos_system_crypto_board" in
        '') naos_system_crypto_blob_bytes=100 ;;
        radxa-airbox-q900) naos_system_crypto_blob_bytes=136 ;;
        *) fatal "unsupported system key provider" ;;
    esac
    naos_system_crypto_pid=
    naos_system_crypto_tmp=
    naos_system_crypto_repair_a=0
    naos_system_crypto_repair_b=0
    [ -b "$naos_system_crypto_raw" ] || fatal "raw system is not a block device"
    naos_system_crypto_misc=$(readlink -f /dev/naos/misc) || fatal "cannot identify the system key partition"
    naos_system_crypto_misc_check || fatal "unsafe system key partition"
    [ -f /usr/lib/naos/system-key.sha256 ] && [ ! -L /usr/lib/naos/system-key.sha256 ] ||
        fatal "system key fingerprint is missing or invalid"
    naos_system_crypto_expected_hash=$(cat /usr/lib/naos/system-key.sha256) || fatal "cannot read the expected system key fingerprint"
    [ "${#naos_system_crypto_expected_hash}" -eq 64 ] || fatal "invalid system key fingerprint"
    case "$naos_system_crypto_expected_hash" in *[!0-9a-f]*) fatal "invalid system key fingerprint" ;; esac
    awk '$2 == "/run" && $3 == "tmpfs" { found++ } END { exit(found != 1) }' /proc/mounts || fatal "system key staging must use tmpfs"
    naos_system_crypto_tmp=$(umask 077; mktemp -d /run/naos-system-key.XXXXXX) || fatal "cannot create private system key staging"
    (umask 077; : >"$naos_system_crypto_tmp/keybox.log") || fatal "cannot create private HUK client log"
    naos_system_crypto_read_region "$naos_system_crypto_tmp/region" 256 || fatal "cannot read the bounded system key import region"
    if cmp -n 65536 "$naos_system_crypto_tmp/region" /dev/zero >/dev/null; then
        naos_system_crypto_import_pending=0
    else
        naos_system_cmp_status=$?
        [ "$naos_system_cmp_status" -eq 1 ] || fatal "cannot inspect the system key import region"
        naos_system_crypto_import_pending=1
    fi
    if [ -z "$naos_system_crypto_board" ]; then
        mkdir -p /run/naos/tee-storage
        chmod 0700 /run/naos/tee-storage
        (cd /run/naos/tee-storage && exec /usr/sbin/tee-supplicant /dev/teepriv0) \
            >"$naos_system_crypto_tmp/supplicant.log" 2>&1 &
        naos_system_crypto_pid=$!
    fi
    naos_system_crypto_probe_slot a 640
    naos_system_valid_a=$naos_system_slot_valid
    naos_system_hash_a=$naos_system_slot_hash
    naos_system_crypto_probe_slot b 768
    naos_system_valid_b=$naos_system_slot_valid
    naos_system_hash_b=$naos_system_slot_hash
    if [ "$naos_system_valid_a" -eq 1 ] || [ "$naos_system_valid_b" -eq 1 ]; then
        if [ "$naos_system_valid_a" -eq 1 ] && [ "$naos_system_valid_b" -eq 1 ]; then
            cmp -s "$naos_system_crypto_tmp/key-a" "$naos_system_crypto_tmp/key-b" ||
                fatal "authenticated system blob copies contain different keys"
        fi
        if [ "$naos_system_valid_a" -eq 1 ]; then
            [ "$naos_system_hash_a" = "$naos_system_crypto_expected_hash" ] || fatal "stored system key does not match this signed image"
            naos_system_crypto_selected_blob=$naos_system_crypto_tmp/blob-a
            naos_system_crypto_selected_key=$naos_system_crypto_tmp/key-a
        else
            [ "$naos_system_hash_b" = "$naos_system_crypto_expected_hash" ] || fatal "stored system key does not match this signed image"
            naos_system_crypto_selected_blob=$naos_system_crypto_tmp/blob-b
            naos_system_crypto_selected_key=$naos_system_crypto_tmp/key-b
        fi
        [ "$naos_system_valid_a" -eq 1 ] || naos_system_crypto_repair_a=1
        [ "$naos_system_valid_b" -eq 1 ] || naos_system_crypto_repair_b=1
    else
        [ "$naos_system_crypto_import_pending" -eq 1 ] || fatal "no authenticated system blob or factory import exists"
        naos_system_crypto_import_record || fatal "system key import is invalid or does not match this signed image"
        naos_system_crypto_keybox seal "$naos_system_crypto_tmp/import.key" "$naos_system_crypto_tmp/new.blob" || fatal "hardware-backed system key sealing failed"
        naos_system_crypto_private_file "$naos_system_crypto_tmp/new.blob" "$naos_system_crypto_blob_bytes" ||
            fatal "invalid sealed system blob output"
        naos_system_crypto_keybox unseal "$naos_system_crypto_tmp/new.blob" "$naos_system_crypto_tmp/new.key" || fatal "new system blob authentication failed"
        [ "$(naos_system_crypto_key_hash "$naos_system_crypto_tmp/new.key")" = "$naos_system_crypto_expected_hash" ] || fatal "new system blob restored a different key"
        cmp -s "$naos_system_crypto_tmp/import.key" "$naos_system_crypto_tmp/new.key" || fatal "new system blob did not preserve the imported key"
        naos_system_crypto_selected_blob=$naos_system_crypto_tmp/new.blob
        naos_system_crypto_selected_key=$naos_system_crypto_tmp/new.key
        naos_system_crypto_write_slot a 640 || fatal "cannot persist and verify system blob A"
        naos_system_crypto_write_slot b 768 || fatal "cannot persist and verify system blob B"
        naos_system_crypto_selected_blob=$naos_system_crypto_tmp/blob-readback-a
        naos_system_crypto_selected_key=$naos_system_crypto_tmp/key-readback-a
    fi
    naos_system_crypto_sectors=$(cat "/sys/class/block/${naos_system_crypto_raw##*/}/size") || fatal "cannot read the selected system partition capacity"
    case "$naos_system_crypto_sectors" in ''|*[!0-9]*) fatal "invalid selected system capacity" ;; esac
    [ "$naos_system_crypto_sectors" -gt 0 ] || fatal "selected system partition is empty"
    naos_system_crypto_key=$(cat "$naos_system_crypto_selected_key") || fatal "cannot read the verified system key"
    dmsetup --noudevsync --readonly create naos-system --table \
        "0 $naos_system_crypto_sectors crypt aes-cbc-plain $naos_system_crypto_key 0 $naos_system_crypto_raw 0" || fatal "cannot create the read-only encrypted system mapping"
    unset naos_system_crypto_key
    dmsetup --noudevsync mknodes naos-system || fatal "cannot create the system mapper node"
    system_mount=$(naos_system_mapper_for_raw "$naos_system_crypto_raw") || fatal "system mapper does not match the selected partition"
}

naos_system_crypto_commit() {
    naos_system_mounted_source=$(awk '$5 == "/lower" && $4 == "/" && $6 ~ /(^|,)ro(,|$)/ {
        for (i=6; i<=NF; i++) if ($i == "-" && $(i+1) == "squashfs") print $(i+2)
    }' /proc/self/mountinfo) || fatal "cannot identify the encrypted system mount"
    [ -n "$naos_system_mounted_source" ] || fatal "encrypted system is not mounted read-only as SquashFS"
    naos_system_mounted_sys=$(naos_sys_block_for_node "$naos_system_mounted_source") || fatal "cannot identify the mounted system mapper"
    naos_system_expected_mapper=$(naos_system_mapper_for_raw "$naos_system_crypto_raw") || fatal "system mapper changed before key cleanup"
    naos_system_expected_sys=$(naos_sys_block_for_node "$naos_system_expected_mapper") || fatal "cannot identify the validated system mapper"
    [ "$naos_system_mounted_sys" = "$naos_system_expected_sys" ] || fatal "mounted system does not belong to the selected encrypted partition"
    naos_system_crypto_misc_check || fatal "system key partition changed before cleanup"
    naos_system_crypto_read_region "$naos_system_crypto_tmp/recheck-a" 640 || fatal "cannot recheck system blob A"
    naos_system_crypto_read_region "$naos_system_crypto_tmp/recheck-b" 768 || fatal "cannot recheck system blob B"
    cmp -s "$naos_system_crypto_tmp/slot-a" "$naos_system_crypto_tmp/recheck-a" || fatal "system blob A changed before cleanup"
    cmp -s "$naos_system_crypto_tmp/slot-b" "$naos_system_crypto_tmp/recheck-b" || fatal "system blob B changed before cleanup"
    if [ "$naos_system_crypto_repair_a" -eq 1 ]; then
        if ! naos_system_crypto_write_slot a 640; then
            [ "$naos_system_crypto_import_pending" -eq 0 ] || fatal "cannot repair system blob A before clearing the key import"
            printf 'NAOS: system blob A repair deferred; booting the verified remaining copy\n' >&2
        fi
    fi
    if [ "$naos_system_crypto_repair_b" -eq 1 ]; then
        if ! naos_system_crypto_write_slot b 768; then
            [ "$naos_system_crypto_import_pending" -eq 0 ] || fatal "cannot repair system blob B before clearing the key import"
            printf 'NAOS: system blob B repair deferred; booting the verified remaining copy\n' >&2
        fi
    fi
    if [ "$naos_system_crypto_import_pending" -eq 1 ]; then
        naos_system_crypto_read_region "$naos_system_crypto_tmp/recheck-import" 256 || fatal "cannot recheck the system key import region"
        cmp -s "$naos_system_crypto_tmp/region" "$naos_system_crypto_tmp/recheck-import" || fatal "system key import changed before cleanup"
        naos_system_crypto_misc_io erase "$naos_system_crypto_misc" import \
            "$naos_system_crypto_tmp/region" || fatal "cannot clear the bounded system key import region"
        naos_system_crypto_read_region "$naos_system_crypto_tmp/cleared" 256 || fatal "cannot read back system key import cleanup"
        cmp -n 65536 "$naos_system_crypto_tmp/cleared" /dev/zero >/dev/null || fatal "system key import cleanup verification failed"
        naos_system_crypto_import_pending=0
    fi
    naos_system_crypto_cleanup || fatal "cannot remove temporary system key state"
}
