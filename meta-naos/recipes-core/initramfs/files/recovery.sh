#!/bin/sh
# Version 3 recovery control record: misc-relative 0x10000, exactly 512 bytes.
# Call after ordinary userdata mounting, before overlay/reserve preparation.
naos_recovery_record() (
    [ "$#" -eq 1 ] || exit 1
    recovery_payload="NAOS-RECOVERY
version=3
operation=clear-userdata"
    printf '%-256s' "$recovery_payload" >"$1" || exit 1
    [ "$(stat -c %s "$1")" -eq 256 ] || exit 1
    recovery_hash=$(sha256sum "$1") || exit 1
    recovery_hash=${recovery_hash%% *}
    [ "${#recovery_hash}" -eq 64 ] || exit 1
    printf '%s' "$recovery_hash" >>"$1" || exit 1
    dd if=/dev/zero bs=192 count=1 >>"$1" 2>/dev/null || exit 1
    [ "$(stat -c %s "$1")" -eq 512 ]
)

naos_recovery_apply() (
    [ "$#" -eq 3 ] || exit 1
    recovery_misc=$1
    recovery_userdata=$2
    recovery_data=$3
    recovery_work=$(mktemp -d /run/naos-recovery.XXXXXX) || exit 1
    trap 'rm -rf "$recovery_work"' EXIT
    recovery_fail() {
        printf 'NAOS: recovery %s failed; request not completed\n' "$1" >&2
        exit 1
    }
    dd if="$recovery_misc" of="$recovery_work/actual" bs=512 skip=128 count=1 2>/dev/null || exit 1
    [ "$(stat -c %s "$recovery_work/actual")" -eq 512 ] || exit 1
    dd if=/dev/zero of="$recovery_work/zero" bs=512 count=1 2>/dev/null || exit 1
    if cmp -s "$recovery_work/actual" "$recovery_work/zero"; then
        exit 0
    fi
    naos_recovery_record "$recovery_work/expected" || exit 1
    cmp -s "$recovery_work/actual" "$recovery_work/expected" || {
        printf 'NAOS: invalid or incompatible recovery record; refusing to erase userdata\n' >&2
        exit 1
    }
    # Require the already mounted, full userdata root and reject nested mounts
    # before walking its directory entries. Overlay is not mounted yet.
    recovery_dev=$(cat "/sys/class/block/${recovery_userdata##*/}/dev") || recovery_fail "userdata identity"
    recovery_mounts=$(cat /proc/self/mountinfo) || recovery_fail "mount identity"
    printf '%s\n' "$recovery_mounts" | awk -v dev="$recovery_dev" -v data="$recovery_data" '
        $5 == data { if ($3 != dev || $4 != "/" || $0 !~ / - f2fs /) bad=1; found++ }
        index($5, data "/") == 1 { bad=1 }
        $3 == dev && $5 != data { bad=1 }
        END { exit (bad || found != 1) }
    ' || recovery_fail "mounted userdata layout"
    recovery_type=$(blkid -p -D -s TYPE -o value "$recovery_userdata" 2>/dev/null) || recovery_fail "filesystem probe"
    [ "$recovery_type" = f2fs ] || recovery_fail "filesystem type (expected existing F2FS)"
    printf 'NAOS: recovery requested; clearing mounted userdata before overlay setup\n'
    # Include hidden entries while excluding dot and dot-dot. rm receives full
    # pathnames without a trailing slash and never follows a symlink entry.
    for recovery_entry in "$recovery_data"/* "$recovery_data"/.[!.]* "$recovery_data"/..?*; do
        [ -e "$recovery_entry" ] || [ -L "$recovery_entry" ] || continue
        rm -rf -- "$recovery_entry" || recovery_fail "userdata content deletion"
    done
    sync || recovery_fail "userdata sync"
    dd if="$recovery_work/zero" of="$recovery_misc" bs=512 seek=128 count=1 conv=notrunc 2>/dev/null || recovery_fail "request clear write"
    sync || recovery_fail "request clear sync"
    dd if="$recovery_misc" of="$recovery_work/cleared" bs=512 skip=128 count=1 2>/dev/null || recovery_fail "request clear readback"
    cmp -s "$recovery_work/cleared" "$recovery_work/zero" || recovery_fail "request clear verification"
    printf 'NAOS: userdata recovery completed\n'
)
