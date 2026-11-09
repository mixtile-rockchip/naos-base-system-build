#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir"

: "${MACHINE:=mixtile-core3588e-v2}"
: "${NAOS_PROFILE:=development}"
: "${NAOS_PACKAGE:?NAOS_PACKAGE=1 is required to package image-generator inputs}"
: "${CI_COMMIT_TAG:?A MACHINE-X.Y.Z release tag is required}"
if [ "$NAOS_PACKAGE" != 1 ]; then
    printf '%s\n' 'NAOS_PACKAGE must be 1 for APK publication' >&2
    exit 1
fi
case "$NAOS_PROFILE" in
    development|production) ;;
    *) printf '%s\n' 'NAOS_PROFILE must be development or production' >&2; exit 1 ;;
esac

apk_version=$(python3 .ci/release-version.py --machine "$MACHINE" --tag "$CI_COMMIT_TAG")

: "${NAOS_CI_APK_SIGNING_KEY:?Set the APK signing private key File variable}"
: "${NAOS_CI_APK_SIGNING_PUBLIC_KEY:?Set the APK signing public key File variable}"
: "${NAOS_CI_APK_SSH_PRIVATE_KEY:?Set the repository SSH private key File variable}"
: "${NAOS_CI_APK_SSH_KNOWN_HOSTS:?Set the repository SSH known-hosts File variable}"

python3 .ci/build-apk.py \
    --input-dir "package/$NAOS_PROFILE/$MACHINE" \
    --output-dir "apk-output/$NAOS_PROFILE" \
    --signing-key "$NAOS_CI_APK_SIGNING_KEY" \
    --public-key "$NAOS_CI_APK_SIGNING_PUBLIC_KEY" \
    --version "$apk_version"

python3 .ci/publish-apk.py \
    --output-dir "apk-output/$NAOS_PROFILE" \
    --repository-url "${NAOS_APK_REPOSITORY_URL:-https://packages.naos.ai/repository}" \
    --ssh-host "${NAOS_APK_SSH_HOST:-56.10.200.210}" \
    --ssh-user "${NAOS_APK_SSH_USER:-ubuntu}" \
    --ssh-key "$NAOS_CI_APK_SSH_PRIVATE_KEY" \
    --known-hosts "$NAOS_CI_APK_SSH_KNOWN_HOSTS" \
    --signing-key "$NAOS_CI_APK_SIGNING_KEY" \
    --public-key "$NAOS_CI_APK_SIGNING_PUBLIC_KEY" \
    --arch x86_64
