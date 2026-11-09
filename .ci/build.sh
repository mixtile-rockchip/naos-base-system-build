#!/bin/sh
set -eu

if [ "$(id -u)" = 0 ]; then
    printf '%s\n' 'The Yocto runner must run the build as a non-root user' >&2
    exit 1
fi

: "${NAOS_PROFILE:=development}"
case "$NAOS_PROFILE" in
    development|production) ;;
    *) printf '%s\n' 'NAOS_PROFILE must be development or production' >&2; exit 1 ;;
esac

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir"

: "${CI_PROJECT_ID:?CI_PROJECT_ID is required for the shared cache namespace}"
export DL_DIR="${DL_DIR:-/cache/yocto/${CI_PROJECT_ID}/downloads}"
export SSTATE_DIR="${SSTATE_DIR:-/cache/yocto/${CI_PROJECT_ID}/sstate-cache}"
if ! mkdir -p -- "$DL_DIR" "$SSTATE_DIR"; then
    printf '%s\n' "Cannot create Yocto cache directories as UID $(id -u): $DL_DIR, $SSTATE_DIR" >&2
    exit 1
fi
if [ ! -w "$DL_DIR" ] || [ ! -w "$SSTATE_DIR" ]; then
    printf '%s\n' "Yocto cache directories must be writable by UID $(id -u): $DL_DIR, $SSTATE_DIR" >&2
    exit 1
fi
export MACHINE="${MACHINE:-mixtile-core3588e-v2}"
export NAOS_OTA="${NAOS_OTA:-0}"
export NAOS_PACKAGE="${NAOS_PACKAGE:-0}"

if [ -n "${CI_COMMIT_TAG:-}" ]; then
    NAOS_VERSION=$(python3 .ci/release-version.py --machine "$MACHINE" --tag "$CI_COMMIT_TAG")
    export NAOS_VERSION
    export NAOS_BUILD_ID="${NAOS_BUILD_ID:-${CI_COMMIT_SHA:-$CI_COMMIT_TAG}}"
fi

umask 077
if [ -d /workdir ] && [ -w /workdir ]; then
    secret_parent=/workdir
else
    secret_parent=${TMPDIR:-/tmp}
fi
secret_dir=$(mktemp -d "$secret_parent/naos-ci-keys.XXXXXXXX")
cleanup() {
    rm -rf -- "$secret_dir"
}
trap cleanup 0
trap 'exit 130' INT
trap 'exit 143' TERM

python3 .ci/prepare-keys.py "$secret_dir"
export NAOS_OTA_KEY_DIR="$secret_dir/ota"
export NAOS_SECURE_BOOT_KEY_DIR="$secret_dir/secure-boot"
export NAOS_SYSTEM_KEY_DIR="$secret_dir/system-encryption"
export NAOS_SSH_CA_KEY="$secret_dir/ssh-ca/ssh_ca_key.pub"
export NAOS_APK_PUBLIC_KEY="$secret_dir/apk-repository/naos-apk.rsa.pub"

kas build "kas/${NAOS_PROFILE}.yml"

if [ "$NAOS_PACKAGE" = 0 ]; then
    image="build/tmp/deploy/images/$MACHINE/naos-$MACHINE.img"
    zstd -T1 --long=28 -3 -q -f "$image" -o "$image.zst"
fi
