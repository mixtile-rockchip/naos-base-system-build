#!/bin/sh
# Sourced by /init only when userdata encryption is explicitly enabled.
# Userspace persists the sealed blob. dm-crypt obtains the 64-byte AES-XTS
# key directly from the platform's kernel keyring.

naos_userdata_crypto_sectors() {
    naos_crypto_sectors=$(cat "/sys/class/block/${naos_crypto_raw##*/}/size") ||
        fatal "cannot read raw userdata capacity"
    case "$naos_crypto_sectors" in
        ''|*[!0-9]*) fatal "invalid raw userdata capacity" ;;
    esac
    [ "$naos_crypto_sectors" -gt 0 ] || fatal "raw userdata partition is empty"
}

naos_userdata_crypto_capacity() {
    naos_crypto_table_sectors=$(partx --raw --noheadings --output SECTORS \
        --nr "$naos_crypto_partno" "$naos_crypto_disk") ||
        fatal "cannot read encrypted userdata partition size"
    case "$naos_crypto_table_sectors" in
        ''|*[!0-9]*) fatal "invalid encrypted userdata partition size" ;;
    esac
    naos_userdata_crypto_sectors
    [ "$naos_crypto_sectors" -eq "$naos_crypto_table_sectors" ] ||
        fatal "kernel userdata capacity has not caught up with GPT"
    [ "$((naos_crypto_sectors * 512))" -ge 67108864 ] ||
        fatal "encrypted userdata requires at least 64 MiB"
}

naos_userdata_crypto_check_q900() {
    naos_crypto_parts=$(naos_q900_partitions "$naos_crypto_disk") ||
        fatal "invalid Q900 GPT for encrypted userdata"
    [ "$(printf '%s\n' "$naos_crypto_parts" | awk '$1 == "userdata" {print $2}')" = \
        "$naos_crypto_raw" ] || fatal "encrypted userdata does not match the Q900 GPT"
    [ "$(printf '%s\n' "$naos_crypto_parts" | awk '$1 == "misc" {print $2}')" = \
        "$naos_userdata_crypto_misc" ] || fatal "userdata key metadata does not match the Q900 GPT"
}

naos_userdata_crypto_key() {
    if [ "$naos_crypto_key_type" = logon ]; then
        /usr/bin/keybox_app "userdata-$1" "$naos_crypto_desc" "$naos_crypto_blob"
    elif [ "$1" = generate ]; then
        keyctl add trusted "$naos_crypto_desc" 'new 64' @u
    else
        naos_crypto_load=$(cat "$naos_crypto_blob") || return 1
        keyctl add trusted "$naos_crypto_desc" "load $naos_crypto_load" @u
    fi
}

naos_prepare_encrypted_userdata() {
    naos_crypto_raw=$1
    naos_crypto_disk=$2
    naos_crypto_partno=$3
    naos_crypto_board=${4:-}
    naos_userdata_crypto_pending=0
    [ -b "$naos_crypto_raw" ] || fatal "raw userdata is not a block device"
    naos_userdata_crypto_misc=$(readlink -f /dev/naos/misc) ||
        fatal "cannot identify userdata key metadata partition"
    [ -b "$naos_userdata_crypto_misc" ] || fatal "misc is not a block device"
    if [ "$naos_crypto_board" = radxa-airbox-q900 ]; then
        naos_userdata_crypto_check_q900
    fi
    ln -snf "$naos_crypto_raw" /dev/naos/userdata-raw

    naos_crypto_state=$(naos-data-key status --device "$naos_userdata_crypto_misc") ||
        fatal "userdata key metadata is invalid or unreadable"
    case "$naos_crypto_state" in
        empty)
            # A missing blob never authorizes replacing existing data. Prove
            # the entire original physical placeholder zero before growth.
            if userdata_type=$(blkid -p -D -s TYPE -o value "$naos_crypto_raw" 2>/dev/null); then
                fatal "userdata key metadata is empty but raw userdata has a signature; refusing initialization"
            else
                probe_result=$?
                [ "$probe_result" -eq 2 ] && [ -z "$userdata_type" ] ||
                    fatal "raw userdata probe failed or is ambiguous"
            fi
            naos_userdata_crypto_sectors
            cmp -n "$((naos_crypto_sectors * 512))" "$naos_crypto_raw" /dev/zero >/dev/null ||
                fatal "userdata has no key metadata and is not entirely zero; refusing initialization"
            ;;
        initializing|ready) ;;
        *) fatal "unknown userdata key metadata state" ;;
    esac
    naos_crypto_uuid=$(cat /proc/sys/kernel/random/uuid) ||
        fatal "cannot create a unique userdata trusted-key description"
    case "$naos_crypto_uuid" in
        ''|*[!a-fA-F0-9-]*) fatal "invalid userdata trusted-key description" ;;
    esac
    naos_crypto_key_type=trusted
    naos_crypto_desc=naos-userdata-$naos_crypto_uuid
    if [ "$naos_crypto_board" = radxa-airbox-q900 ]; then
        naos_crypto_key_type=logon
        naos_crypto_desc=naos:userdata-$naos_crypto_uuid
    fi
    naos_crypto_tmp=$(umask 077; mktemp -d /run/naos-userdata-key.XXXXXX) ||
        fatal "cannot create private userdata blob staging directory"
    naos_crypto_blob=$naos_crypto_tmp/sealed.hex

    if [ "$naos_crypto_state" = empty ]; then
        naos_crypto_key=$(naos_userdata_crypto_key generate) ||
            fatal "userdata key generation failed"
    else
        naos-data-key export --device "$naos_userdata_crypto_misc" --output "$naos_crypto_blob" ||
            fatal "cannot export the sealed userdata key blob"
        [ -s "$naos_crypto_blob" ] || fatal "userdata key blob is empty"
        naos_crypto_key=$(naos_userdata_crypto_key load) ||
            fatal "userdata key unsealing failed"
    fi
    case "$naos_crypto_key" in
        ''|*[!0-9]*) fatal "userdata key creation returned an invalid key identifier" ;;
    esac
    [ "$naos_crypto_key" -gt 0 ] || fatal "userdata key creation returned an invalid key identifier"
    if [ "$naos_crypto_state" = empty ]; then
        if [ "$naos_crypto_key_type" = trusted ]; then
            (umask 077; keyctl pipe "$naos_crypto_key" >"$naos_crypto_blob") ||
                fatal "cannot export the newly sealed userdata key blob"
        fi
        [ -s "$naos_crypto_blob" ] || fatal "new userdata key blob is empty"
        # Persist the only key before changing the GPT or writing ciphertext.
        # Otherwise a power loss after growth could expose old nonzero free
        # space and make the next empty-state whole-zero proof impossible.
        naos-data-key init --device "$naos_userdata_crypto_misc" --blob "$naos_crypto_blob" ||
            fatal "cannot persist initial userdata key metadata"
        naos_crypto_state=initializing
    fi
    rm -f "$naos_crypto_blob"
    rmdir "$naos_crypto_tmp"

    if [ "$naos_crypto_state" = initializing ]; then
        # The durable record authorizes completing the initial growth/format.
        # Retry with the saved key after a power loss; an unchanged GPT is OK.
        if growpart --update=force "$naos_crypto_disk" "$naos_crypto_partno" \
            >/run/naos-growpart.log 2>&1; then
            cat /run/naos-growpart.log
        else
            naos_crypto_result=$?
            if [ "$naos_crypto_result" -ne 1 ] ||
                ! grep -q '^NOCHANGE:' /run/naos-growpart.log; then
                cat /run/naos-growpart.log
                fatal "encrypted userdata partition growth failed"
            fi
        fi
        if [ "$naos_crypto_board" = radxa-airbox-q900 ]; then
            naos_userdata_crypto_check_q900
        fi
    fi
    naos_userdata_crypto_capacity

    # Do not discard: the cipher mapping must not expose raw key bytes or
    # forward trim to the underlying userdata partition.
    dmsetup --noudevsync create naos-userdata --table \
        "0 $naos_crypto_sectors crypt capi:xts(aes)-plain64 :64:$naos_crypto_key_type:$naos_crypto_desc 0 $naos_crypto_raw 0" ||
        fatal "cannot create encrypted userdata mapping"
    dmsetup --noudevsync mknodes naos-userdata || fatal "cannot create userdata mapper device node"
    # mknodes may create a block-special node rather than a symlink. Resolve
    # its device number through sysfs so recovery always receives /dev/dm-N.
    naos_crypto_rdev=$(stat -Lc '%t:%T' /dev/mapper/naos-userdata) ||
        fatal "cannot identify encrypted userdata device number"
    naos_crypto_major=${naos_crypto_rdev%:*}
    naos_crypto_minor=${naos_crypto_rdev#*:}
    case "$naos_crypto_major" in
        ''|*[!a-fA-F0-9]*) fatal "invalid encrypted userdata major device number" ;;
    esac
    case "$naos_crypto_minor" in
        ''|*[!a-fA-F0-9]*) fatal "invalid encrypted userdata minor device number" ;;
    esac
    naos_crypto_sysdev=$(readlink -f "/sys/dev/block/$((0x$naos_crypto_major)):$((0x$naos_crypto_minor))") ||
        fatal "cannot resolve encrypted userdata sysfs device"
    [ "$(cat "$naos_crypto_sysdev/dm/name")" = naos-userdata ] ||
        fatal "userdata mapper node belongs to an unexpected device"
    userdata=/dev/${naos_crypto_sysdev##*/}
    [ -b "$userdata" ] || fatal "encrypted userdata mapping did not appear"
    [ "$(stat -Lc '%t:%T' "$userdata")" = "$naos_crypto_rdev" ] ||
        fatal "encrypted userdata device numbers disagree"
    if [ "$naos_crypto_board" = radxa-airbox-q900 ]; then
        [ "$userdata" = "$(naos_q900_mapper_for_raw "$naos_crypto_raw" naos-userdata 0)" ] ||
            fatal "userdata mapper does not match the selected Q900 partition"
    fi
    ln -snf "$userdata" /dev/naos/userdata

    naos_crypto_format=0
    if userdata_type=$(blkid -p -D -s TYPE -o value "$userdata" 2>/dev/null); then
        probe_result=0
        [ "$userdata_type" = f2fs ] ||
            fatal "decrypted userdata is not F2FS; refusing to format"
    else
        probe_result=$?
        if [ "$naos_crypto_state" = initializing ] &&
           [ "$probe_result" -eq 2 ] && [ -z "$userdata_type" ]; then
            # The saved initializing record explicitly authorizes completing
            # the first format, including an interrupted earlier mkfs.
            naos_crypto_format=1
        else
            fatal "decrypted userdata probe failed; refusing to format"
        fi
    fi
    if [ "$naos_crypto_format" -eq 1 ]; then
        mkfs.f2fs -f -t 0 -l userdata "$userdata" </dev/null ||
            fatal "initial encrypted userdata F2FS creation failed"
        sync
        userdata_type=$(blkid -p -D -s TYPE -o value "$userdata" 2>/dev/null) ||
            fatal "new encrypted userdata filesystem probe failed"
        [ "$userdata_type" = f2fs ] ||
            fatal "new encrypted userdata filesystem was not recognized"
        probe_result=0
    fi
    if [ "$naos_crypto_state" = initializing ]; then
        naos_userdata_crypto_pending=1
    fi
}

naos_userdata_crypto_ready() {
    # /init calls this only after the decrypted F2FS mount succeeds. Flush the
    # first filesystem before committing ready. On later boots the helper is
    # idempotent for healthy ready copies and repairs a degraded redundant copy.
    if [ "$naos_userdata_crypto_pending" -eq 1 ]; then
        sync
    fi
    naos-data-key ready --device "$naos_userdata_crypto_misc" ||
        fatal "cannot commit or repair ready userdata key metadata"
    naos_userdata_crypto_pending=0
}
