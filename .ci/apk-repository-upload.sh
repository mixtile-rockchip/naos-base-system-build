#!/bin/sh
# Receive an APK and refresh the signed repository index.
set -eu
repository=/srv/naos/apk/repository/x86_64
signing_key=/var/lib/naos-apk/signing/naos-apk.rsa
apk_tool=/usr/local/bin/apk
signature=.SIGN.RSA256.naos-apk.rsa.pub
case "${SSH_ORIGINAL_COMMAND:-}" in
    'upload '*) filename=${SSH_ORIGINAL_COMMAND#upload } ;;
    *) echo 'Only upload FILE is allowed' >&2; exit 2 ;;
esac
case "$filename" in ''|.*|*[!A-Za-z0-9_.+~-]*) echo 'Invalid APK filename' >&2; exit 2 ;; esac
printf '%s\n' "$filename" | grep -Eq '^((core3588e-nano|core3588e-tx|core3588e-v2-1-nano|core3588e-v2-nano|vision1|vision1-v2|vision1-v3|vision1-v3-1)-(uboot|kernel|alpine-rootfs)|[a-z0-9][a-z0-9._+-]*-alpine-(dev|prod))-[0-9][A-Za-z0-9_.+~]*-r[0-9]+[.]apk$' || exit 2
umask 077
work=$(mktemp -d "$repository/.upload.XXXXXXXX")
trap 'rm -rf -- "$work"' 0
trap 'exit 130' INT
trap 'exit 143' TERM
cat > "$work/$filename"
exec 9> "$repository/.publish.lock"
flock -x 9
chmod 644 "$work/$filename"
mv -- "$work/$filename" "$repository/$filename"
set -- "$repository"/*.apk
"$apk_tool" --allow-untrusted index --no-warnings --rewrite-arch x86_64 \
    --description 'NAOS APK repository' --output "$work/unsigned.tar.gz" "$@"
openssl dgst -sha256 -sign "$signing_key" -out "$work/$signature" "$work/unsigned.tar.gz"
tar --format=ustar --owner=0 --group=0 --numeric-owner \
    -C "$work" -cf "$work/signature.tar" "$signature"
signature_size=$(wc -c < "$work/$signature")
signature_blocks=$((1 + (signature_size + 511) / 512))
dd if="$work/signature.tar" bs=512 count="$signature_blocks" status=none | gzip -n -9 > "$work/signature.tar.gz"
cat "$work/signature.tar.gz" "$work/unsigned.tar.gz" > "$work/APKINDEX.tar.gz"
chmod 644 "$work/APKINDEX.tar.gz"
mv -- "$work/APKINDEX.tar.gz" "$repository/APKINDEX.tar.gz"
printf 'Uploaded %s and updated APKINDEX.tar.gz\n' "$filename"
