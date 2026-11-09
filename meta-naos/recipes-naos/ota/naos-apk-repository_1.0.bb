SUMMARY = "NAOS APK repository verification public key"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

inherit allarch deploy nopackages
INHIBIT_DEFAULT_DEPS = "1"
DEPENDS = "openssl-native"
SRC_URI = ""

do_configure[noexec] = "1"
do_compile[noexec] = "1"
do_install[noexec] = "1"

python do_deploy() {
    from pathlib import Path
    import re
    import subprocess

    source = Path(d.getVar("NAOS_APK_PUBLIC_KEY") or "")
    if (not source.is_absolute() or source.is_symlink() or not source.is_file()
            or source.stat().st_size > 16384):
        bb.fatal("NAOS_APK_PUBLIC_KEY must name an ordinary PEM public key file")
    content = source.read_bytes()
    pattern = rb"-----BEGIN PUBLIC KEY-----\r?\n(?:[A-Za-z0-9+/=]+\r?\n)+-----END PUBLIC KEY-----\r?\n?"
    if not re.fullmatch(pattern, content):
        bb.fatal("NAOS_APK_PUBLIC_KEY must contain only one PEM public key")
    openssl = d.expand("${STAGING_BINDIR_NATIVE}/openssl")
    for arguments in (("rsa", "-pubin", "-in", str(source), "-noout"),
                      ("pkey", "-pubin", "-in", str(source), "-pubcheck", "-noout")):
        result = subprocess.run([openssl, *arguments], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode:
            bb.fatal("NAOS_APK_PUBLIC_KEY is not a valid RSA public key")
    destination = Path(d.getVar("DEPLOYDIR")) / "naos-apk-repository/etc/apk/keys/naos-apk.rsa.pub"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)
    destination.chmod(0o644)
}
do_deploy[file-checksums] = "${NAOS_APK_PUBLIC_KEY}:True"
do_deploy[vardeps] += "NAOS_APK_PUBLIC_KEY"
addtask deploy after do_install before do_build
