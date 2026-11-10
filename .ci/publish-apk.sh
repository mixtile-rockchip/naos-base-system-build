#!/bin/sh
set -eu
[ "$#" -eq 2 ] && [ "$1" = --apk ] || { echo 'Usage: publish-apk.sh --apk PATH' >&2; exit 2; }
apk_path=$2
[ -f "$apk_path" ] || { echo 'APK file is missing' >&2; exit 2; }
filename=${apk_path##*/}
case "$filename" in ''|.*|*[!A-Za-z0-9_.+~-]*) echo 'Invalid APK filename' >&2; exit 2 ;; esac
: "${NAOS_CI_APK_SSH_PRIVATE_KEY:?Set the SSH private-key File variable}"
: "${NAOS_CI_APK_SSH_KNOWN_HOSTS:?Set the known-hosts File variable}"
upload_host=${NAOS_APK_SSH_HOST:-56.10.200.210}
upload_user=${NAOS_APK_SSH_USER:-ubuntu}
case "$upload_host" in ''|-*|*[!A-Za-z0-9.-]*) exit 2 ;; esac
case "$upload_user" in ''|-*|*[!A-Za-z0-9_-]*) exit 2 ;; esac
umask 077
upload_keys=$(mktemp -d)
trap 'rm -rf -- "$upload_keys"' 0
trap 'exit 130' INT
trap 'exit 143' TERM
cp -- "$NAOS_CI_APK_SSH_PRIVATE_KEY" "$upload_keys/key"
cp -- "$NAOS_CI_APK_SSH_KNOWN_HOSTS" "$upload_keys/known_hosts"
chmod 600 "$upload_keys/key" "$upload_keys/known_hosts"
ssh -T -i "$upload_keys/key" \
    -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes \
    -o "UserKnownHostsFile=$upload_keys/known_hosts" -o ConnectTimeout=15 \
    -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
    "$upload_user@$upload_host" "upload $filename" < "$apk_path"
