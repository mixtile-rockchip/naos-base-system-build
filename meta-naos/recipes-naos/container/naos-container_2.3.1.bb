SUMMARY = "Prebuilt NAOS Container runtime and clients"
DESCRIPTION = "Prebuilt static AArch64 binaries, preinstalled for later management by NAOS Core."
LICENSE = "CLOSED"

inherit deploy nopackages

# Override the URI and checksum together to select another reviewed release.
NAOS_CONTAINER_URI ?= "https://github.com/mixtile-rockchip/naos-container/releases/download/2026.11.16/naos-alpine-arm64.tar.gz"
NAOS_CONTAINER_SHA256 ?= "02a7544a1e330258be897fb6b2647814fc975f7e71ac872e62662263360871a3"
SRC_URI = "${NAOS_CONTAINER_URI};name=container"
S = "${UNPACKDIR}/naos-alpine-arm64"
COMPATIBLE_HOST = "aarch64.*-linux.*"

python __anonymous() {
    import re

    uri = d.getVar("NAOS_CONTAINER_URI")
    expected = (d.getVar("NAOS_CONTAINER_SHA256") or "").lower()
    if not uri or any(char.isspace() for char in uri):
        bb.fatal("NAOS_CONTAINER_URI must select exactly one binary archive")
    if expected and not re.fullmatch(r"[0-9a-f]{64}", expected):
        bb.fatal("NAOS_CONTAINER_SHA256 must be a complete SHA256 digest")
    if not uri.startswith("file:") and not expected:
        bb.fatal("Remote NAOS_CONTAINER_URI requires a fixed NAOS_CONTAINER_SHA256")
    if expected:
        d.setVarFlag("SRC_URI", "container.sha256sum", expected)
}

do_configure[noexec] = "1"
do_compile[noexec] = "1"

python do_unpack:prepend() {
    # file:// inputs have no fetcher done stamp, so enforce the pinned digest
    # here as well as declaring the normal checksum for HTTPS release fetching.
    import bb.fetch2

    expected = d.getVarFlag("SRC_URI", "container.sha256sum")
    if expected:
        uri = d.getVar("SRC_URI").split()[0]
        archive = bb.fetch2.Fetch([uri], d).localpath(uri)
        if bb.utils.sha256_file(archive) != expected:
            bb.fatal("NAOS Container archive differs from NAOS_CONTAINER_SHA256")
}

do_install() {
    install -d ${D}${bindir}
    for program in naos naosd naos-exec; do
        binary="${S}/bin/$program"
        header=$(${READELF} --file-header "$binary") || bbfatal "Invalid ELF: $program"
        printf '%s\n' "$header" | grep -q 'Class:.*ELF64' || bbfatal "$program must be ELF64"
        printf '%s\n' "$header" | grep -q 'Machine:.*AArch64' || bbfatal "$program must be AArch64"
        if ${READELF} --program-headers "$binary" | grep -q ' INTERP '; then
            bbfatal "$program must be statically linked"
        fi
        if ${READELF} --dynamic "$binary" | grep -q '(NEEDED)'; then
            bbfatal "$program must not require shared libraries"
        fi
        install -m0755 "$binary" ${D}${bindir}/$program
    done
    install -Dm0644 ${S}/README.md ${D}${docdir}/${BPN}/README.md
    install -Dm0644 ${S}/sbom.spdx.json ${D}${datadir}/naos-container/sbom.spdx.json
}

do_deploy() {
    install -d ${DEPLOYDIR}/naos-container
    cp -R --no-preserve=ownership ${D}/. ${DEPLOYDIR}/naos-container/
}
addtask deploy after do_install before do_build
