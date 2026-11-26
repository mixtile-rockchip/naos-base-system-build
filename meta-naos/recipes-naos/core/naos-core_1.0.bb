SUMMARY = "Prebuilt NAOS Core local control service"
DESCRIPTION = "Import an independently built static Core archive and install the image's service scripts and configuration."
# Core has no declared project license. The release distributes the dependency
# notices and their individual license declarations in its SPDX inventory.
LICENSE = "CLOSED"

inherit deploy nopackages

DEPENDS = "binutils-cross-${TARGET_ARCH}"
COMPATIBLE_HOST = "aarch64.*-linux.*"

# The release supplies the binary, dependency notices and service configuration.
# The release workflow builds Core for both Alpine platforms.
NAOS_CORE_URI ?= "https://github.com/mixtile-rockchip/naos-device-manager/releases/download/2026.11.26.2/naos-core-linux-arm64.tar.gz"
NAOS_CORE_SHA256 ?= "c6c6bc88bef4f895d9936522da4c3504264fcc016d650b25bd865dca5d77fb9a"
SRC_URI = "${NAOS_CORE_URI};name=core;subdir=prebuilt"
S = "${UNPACKDIR}/prebuilt"

python __anonymous() {
    import re

    uri = d.getVar("NAOS_CORE_URI")
    expected = (d.getVar("NAOS_CORE_SHA256") or "").lower()
    if not uri or any(char.isspace() for char in uri):
        bb.fatal("NAOS_CORE_URI must select exactly one binary archive")
    if expected and not re.fullmatch(r"[0-9a-f]{64}", expected):
        bb.fatal("NAOS_CORE_SHA256 must be a complete SHA256 digest")
    if not uri.startswith("file:") and not expected:
        bb.fatal("Remote NAOS_CORE_URI requires a fixed NAOS_CORE_SHA256")
    if expected:
        d.setVarFlag("SRC_URI", "core.sha256sum", expected)
        if uri.startswith(("http://", "https://")):
            # Release assets share a filename across versions. Keep each verified archive in DL_DIR.
            filename = bb.fetch2.URI(uri).path.rsplit("/", 1)[-1]
            d.appendVar("SRC_URI", ";downloadfilename=" + expected + "-" + filename)
}

do_configure[noexec] = "1"
do_compile[noexec] = "1"

python do_unpack:prepend() {
    # BitBake tracks local file contents. If an explicit digest was also
    # supplied, enforce it for file:// as well as release downloads.
    import bb.fetch2

    expected = d.getVarFlag("SRC_URI", "core.sha256sum")
    if expected:
        uri = d.getVar("SRC_URI").split()[0]
        archive = bb.fetch2.Fetch([uri], d).localpath(uri)
        if bb.utils.sha256_file(archive) != expected:
            bb.fatal("NAOS Core archive differs from NAOS_CORE_SHA256")
}

python do_verify_core() {
    import hashlib
    import json
    import os

    source = d.getVar("S")
    try:
        with open(os.path.join(source, "build-info.json"), "rb") as stream:
            data = stream.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024:
            bb.fatal("NAOS Core build metadata exceeds the size limit")
        info = json.loads(data)
    except (OSError, ValueError) as error:
        bb.fatal("NAOS Core archive must contain valid build-info.json: %s" % error)
    if not isinstance(info, dict):
        bb.fatal("Invalid Core build metadata")
    if info.get("schema_version") == 2:
        import re
        if (info.get("target") != "aarch64-unknown-linux-musl"
                or not re.fullmatch("[0-9a-f]{40}", info.get("source_revision", ""))
                or not info.get("rustc", "").startswith("rustc ")):
            bb.fatal("Expected a Rust ARM64 musl release and source revision")
    elif (info.get("schema_version") != 1 or info.get("goos") != "linux"
          or info.get("goarch") != "arm64" or info.get("cgo_enabled") != 0):
        bb.fatal("Unsupported Core build metadata")
    binary = os.path.join(source, "usr/bin/naos-core")
    with open(binary, "rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != info.get("binary_sha256"):
        bb.fatal("NAOS Core binary differs from its independent build metadata")
    sbom_path = os.path.join(source, "sbom.spdx.json")
    try:
        with open(sbom_path, "r") as stream:
            sbom = json.load(stream)
        if sbom.get("spdxVersion") != "SPDX-2.3":
            raise ValueError("unsupported SPDX document")
        digests = [checksum.get("checksumValue")
                   for entry in sbom.get("files", [])
                   if entry.get("fileName", "").removeprefix("./") == "usr/bin/naos-core"
                   for checksum in entry.get("checksums", [])
                   if checksum.get("algorithm") == "SHA256"]
        if digests != [actual]:
            raise ValueError("Core checksum differs from release SBOM")
    except (OSError, ValueError) as error:
        bb.fatal("NAOS Core archive requires a matching sbom.spdx.json: %s" % error)
}
addtask verify_core after do_unpack before do_install

do_install() {
    binary="${S}/usr/bin/naos-core"
    header=$(${READELF} --file-header "$binary") || bbfatal "Invalid Core ELF"
    printf '%s\n' "$header" | grep -q 'Class:.*ELF64' || bbfatal "NAOS Core must be ELF64"
    printf '%s\n' "$header" | grep -q 'Machine:.*AArch64' || bbfatal "NAOS Core must be AArch64"
    headers=$(${READELF} --program-headers "$binary") || bbfatal "Invalid Core program headers"
    if printf '%s\n' "$headers" | grep -q ' INTERP '; then
        bbfatal "NAOS Core must be statically linked"
    fi
    dynamic=$(${READELF} --dynamic "$binary") || bbfatal "Invalid Core dynamic metadata"
    if printf '%s\n' "$dynamic" | grep -q '(NEEDED)'; then
        bbfatal "NAOS Core must not require shared libraries"
    fi
    install -Dm0755 "$binary" ${D}${bindir}/naos-core
    install -Dm0755 ${S}/etc/init.d/naos-core \
        ${D}${sysconfdir}/init.d/naos-core
    install -Dm0600 ${S}/etc/naos-core/device.ini \
        ${D}${sysconfdir}/naos-core/device.ini
    install -Dm0755 ${S}/etc/init.d/naosd \
        ${D}${sysconfdir}/init.d/naosd
    install -Dm0644 ${S}/etc/naosd/config.yml \
        ${D}${sysconfdir}/naosd/config.yml
    install -d ${D}${sysconfdir}/runlevels/default
    ln -s ${sysconfdir}/init.d/naos-core ${D}${sysconfdir}/runlevels/default/naos-core
    ln -s ${sysconfdir}/init.d/naosd ${D}${sysconfdir}/runlevels/default/naosd
    install -d ${D}${datadir}/licenses/naos-core
    cp -R --no-preserve=ownership ${S}/usr/share/licenses/naos-core/. ${D}${datadir}/licenses/naos-core/
    install -Dm0644 ${S}/build-info.json ${D}${datadir}/naos-core/build-info.json
    install -Dm0644 ${S}/sbom.spdx.json ${D}${datadir}/naos-core/sbom.spdx.json
}

do_deploy() {
    install -d ${DEPLOYDIR}/naos-core
    cp -R --no-preserve=ownership ${D}/. ${DEPLOYDIR}/naos-core/
}
addtask deploy after do_install do_populate_lic before do_build
