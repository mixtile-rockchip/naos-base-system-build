# Assemble an Alpine userspace from official APK inputs managed by BitBake.
# This is a separate rootfs implementation, not an OE package_apk backend.
inherit deploy nopackages

INHIBIT_DEFAULT_DEPS = "1"
DEPENDS += "squashfs-tools-native naos-rootfs-ownership-helper"
PACKAGE_ARCH = "${MACHINE_ARCH}"

ALPINE_ARCH ?= "aarch64"
ALPINE_PACKAGES ??= "alpine-base ca-certificates"
ALPINE_SERVICES ??= ""
ALPINE_DEFAULT_USER ??= "naos"
ALPINE_DEFAULT_PASSWORD ??= ""
ALPINE_SSH_CA_KEY ??= ""
ALPINE_ROOTFS_OVERLAY_DIRS ??= ""
ALPINE_EXTRA_APK_LOCK ??= ""
ALPINE_EXTRA_PACKAGES ??= ""
ALPINE_EXTRA_APK_KEYS ??= ""
ALPINE_CONSOLE_SHELL ??= "0"
SERIAL_CONSOLES ??= ""
ALPINE_ROOTFS_NAME ?= "${PN}-${ALPINE_ARCH}"
ALPINE_ROOTFS_OUTPUT = "${B}/rootfs-artifacts"
ALPINE_IMAGE_OUTPUT = "${B}/images"
ALPINE_ROOTFS_PROCESSORS ?= "4"
ALPINE_ROOTFS_EPOCH ?= "1748563200"
# Common containers retain a protected proc mount which cannot be recreated
# inside an unprivileged user namespace. Other runtimes can select bind too.
ALPINE_ROOTFS_PROC_MODE ?= "auto"
# Override OE's global tar.zst default; this implementation supports only
# these formats. A recipe inheriting the class can still select either one.
IMAGE_FSTYPES = "tar.gz squashfs"

S = "${UNPACKDIR}"
B = "${WORKDIR}/build"

do_configure[noexec] = "1"
do_compile[noexec] = "1"
do_install[noexec] = "1"
do_populate_sysroot[noexec] = "1"
do_patch[noexec] = "1"

python do_rootfs() {
    import os
    import shlex
    import shutil
    import subprocess
    import sys
    import bb.fetch2

    if d.getVar("ALPINE_ARCH") != "aarch64":
        bb.fatal("This initial Alpine rootfs implementation supports aarch64")
    if sorted(shlex.split(d.getVar("ALPINE_PACKAGES"))) != sorted(shlex.split(d.getVar("ALPINE_LOCKED_REQUESTED_PACKAGES") or "")):
        bb.fatal("ALPINE_PACKAGES differs from its lock; regenerate it with tools/update-alpine-packages.py")

    proc_mode = d.getVar("ALPINE_ROOTFS_PROC_MODE")
    if proc_mode == "auto":
        in_container = any(os.path.exists(path) for path in
                           ("/.dockerenv", "/run/.containerenv", "/run/systemd/container"))
        proc_mode = "bind" if in_container else "private"
    if proc_mode not in {"private", "bind"}:
        bb.fatal("ALPINE_ROOTFS_PROC_MODE must be auto, private or bind")

    unshare = bb.utils.which(d.getVar("PATH"), "unshare")
    if not unshare:
        bb.fatal("Alpine rootfs assembly requires host unshare; install util-linux and include unshare in HOSTTOOLS")
    namespace_args = [unshare, "--user", "--map-root-user", "--mount", "--ipc",
                      "--propagation", "private"]
    if proc_mode == "private":
        namespace_args += ["--pid", "--fork", "--mount-proc"]
    bb.note("Alpine rootfs proc mode: " + proc_mode)

    # Stage only the current lock's APKs, excluding inputs left by an older
    # package selection in WORKDIR.
    apk_dir = d.expand("${B}/locked-apks")
    bb.utils.mkdirhier(apk_dir)
    fetcher = bb.fetch2.Fetch(d.getVar("SRC_URI").split(), d)
    for uri in fetcher.urls:
        path = bb.fetch.decodeurl(uri)[2]
        if path.endswith(".apk") and os.path.basename(path) != d.getVar("ALPINE_FAKEROOT_FILENAME"):
            filename = os.path.basename(path)
            source = fetcher.localpath(uri)
            urldata = fetcher.ud[uri]
            if not urldata.sha256_expected:
                bb.fatal("Every runtime APK needs a locked SHA256: " + uri)
            # Local file:// inputs participate in BitBake's file-checksums;
            # additionally enforce their explicit lock before staging them.
            bb.fetch2.verify_checksum(urldata, d, localpath=source)
            shutil.copyfile(source, os.path.join(apk_dir, filename))

    args = namespace_args + [
        sys.executable, d.expand("${UNPACKDIR}/assemble-alpine-rootfs.py"),
        "--proc-mode", proc_mode,
        "--seed", d.expand("${UNPACKDIR}/${ALPINE_SEED_FILENAME}"),
        "--apk-dir", apk_dir,
        "--output-dir", d.getVar("ALPINE_ROOTFS_OUTPUT"),
        "--name", d.getVar("ALPINE_ROOTFS_NAME"),
        "--packages", " ".join((d.getVar("ALPINE_PACKAGES"), d.getVar("ALPINE_EXTRA_PACKAGES"))),
        "--services", d.getVar("ALPINE_SERVICES"),
        "--default-user", d.getVar("ALPINE_DEFAULT_USER"),
        "--default-password", d.getVar("ALPINE_DEFAULT_PASSWORD"),
        "--console-shell", d.getVar("ALPINE_CONSOLE_SHELL"),
        "--serial-consoles", d.getVar("SERIAL_CONSOLES"),
        "--repositories", d.expand("${UNPACKDIR}/repositories"),
        "--owner", "0:0",
        "--epoch", d.getVar("ALPINE_ROOTFS_EPOCH"),
        "--processors", d.getVar("ALPINE_ROOTFS_PROCESSORS"),
        "--fakeroot-apk", d.expand("${UNPACKDIR}/${ALPINE_FAKEROOT_FILENAME}"),
        "--ownership-library", d.expand("${RECIPE_SYSROOT}${libdir}/naos-build/libnaos-chown-compat.so"),
        "--tar-only",
    ]
    for overlay in d.getVar("ALPINE_ROOTFS_OVERLAY_DIRS").split():
        args += ["--overlay", overlay]
    for key in d.getVar("ALPINE_EXTRA_APK_KEYS").split():
        args += ["--apk-key", key]
    if d.getVar("ALPINE_SSH_CA_KEY"):
        args += ["--ssh-ca-key", d.getVar("ALPINE_SSH_CA_KEY")]
    subprocess.run(args, check=True)
}

do_rootfs[cleandirs] = "${B}"
do_rootfs[depends] += "squashfs-tools-native:do_populate_sysroot"
# The namespace helper needs mount/chroot capability in a nested user namespace.
# The helper installs only fetched local APKs with apk --no-network.
do_rootfs[network] = "1"
do_rootfs[vardeps] += "ALPINE_ARCH ALPINE_PACKAGES ALPINE_EXTRA_PACKAGES ALPINE_SERVICES ALPINE_DEFAULT_USER ALPINE_DEFAULT_PASSWORD ALPINE_SSH_CA_KEY ALPINE_ROOTFS_OVERLAY_DIRS ALPINE_EXTRA_APK_KEYS ALPINE_CONSOLE_SHELL SERIAL_CONSOLES ALPINE_ROOTFS_NAME ALPINE_ROOTFS_EPOCH ALPINE_ROOTFS_PROCESSORS ALPINE_ROOTFS_PROC_MODE"

addtask rootfs after do_unpack do_prepare_recipe_sysroot before do_image

python do_image() {
    import gzip
    import hashlib
    import os
    import pathlib
    import shutil
    import subprocess

    formats = d.getVar("IMAGE_FSTYPES").split()
    if not formats or set(formats) - {"tar.gz", "squashfs"}:
        bb.fatal("alpine-rootfs currently supports IMAGE_FSTYPES = 'tar.gz squashfs'")

    source = pathlib.Path(d.getVar("ALPINE_ROOTFS_OUTPUT"))
    output = pathlib.Path(d.getVar("ALPINE_IMAGE_OUTPUT"))
    name = d.getVar("ALPINE_ROOTFS_NAME")
    if "tar.gz" in formats:
        shutil.copyfile(source / (name + ".tar.gz"), output / (name + ".tar.gz"))

    if "squashfs" in formats:
        args = [
            d.expand("${STAGING_BINDIR_NATIVE}/sqfstar"),
            "-comp", "zstd", "-processors", d.getVar("ALPINE_ROOTFS_PROCESSORS"),
            "-mkfs-time", d.getVar("ALPINE_ROOTFS_EPOCH"),
            "-all-time", d.getVar("ALPINE_ROOTFS_EPOCH"),
            "-root-uid", "0", "-root-gid", "0", "-root-mode", "0755",
            "-no-progress", "-exit-on-error", str(output / (name + ".squashfs")),
        ]
        env = os.environ.copy()
        env.pop("SOURCE_DATE_EPOCH", None)
        with gzip.open(source / (name + ".tar.gz"), "rb") as archive:
            process = subprocess.Popen(args, stdin=subprocess.PIPE, env=env)
            try:
                shutil.copyfileobj(archive, process.stdin)
            finally:
                process.stdin.close()
                status = process.wait()
            if status:
                bb.fatal("sqfstar failed with exit status %d" % status)

    shutil.copyfile(source / (name + ".manifest.json"), output / (name + ".manifest.json"))
    checksums = []
    for path in sorted(output.iterdir()):
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        checksums.append("%s  %s" % (digest.hexdigest(), path.name))
    (output / (name + ".sha256")).write_text("\n".join(checksums) + "\n")
}
do_image[cleandirs] = "${ALPINE_IMAGE_OUTPUT}"
do_image[depends] += "squashfs-tools-native:do_populate_sysroot"
addtask image after do_rootfs before do_deploy

do_deploy() {
    install -d ${DEPLOYDIR}
    install -m 0644 ${ALPINE_IMAGE_OUTPUT}/${ALPINE_ROOTFS_NAME}.* ${DEPLOYDIR}/
}
addtask deploy after do_image before do_build


# Recipe-local files named in the overlay and key variables participate in task
# signatures as well as their path names. Keep these inputs in source control.
python __anonymous() {
    import os
    paths = []
    for directory in (d.getVar("ALPINE_ROOTFS_OVERLAY_DIRS") or "").split():
        for base, directories, files in os.walk(directory):
            for name in sorted(files):
                paths.append(os.path.join(base, name) + ":True")
    paths += [path + ":True" for path in (d.getVar("ALPINE_EXTRA_APK_KEYS") or "").split()]
    if d.getVar("ALPINE_SSH_CA_KEY"):
        paths.append(d.getVar("ALPINE_SSH_CA_KEY") + ":True")
    d.appendVarFlag("do_rootfs", "file-checksums", " " + " ".join(paths))
}
