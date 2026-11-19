SUMMARY = "NAOS Alpine development root filesystem for Qualcomm QCS9075"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

inherit core-image image_types_qcom
require ${NAOS_LAYER_DIR}/recipes-core/images/naos-ota-keys.inc

COMPATIBLE_MACHINE = "^radxa-airbox-q900$"
IMAGE_FSTYPES = "squashfs"
IMAGE_LINK_NAME = "naos-q900-system"
EXTRA_IMAGECMD:squashfs = "-comp zstd -processors ${ALPINE_ROOTFS_PROCESSORS}"
IMAGE_FEATURES = ""
# Alpine supplies the deployed account policy.
EXTRA_IMAGE_FEATURES:remove = "allow-empty-password empty-root-password allow-root-login"
IMAGE_LINGUAS = ""
IMAGE_INSTALL = " \
    kernel-modules \
    linux-firmware-qcom-adreno-a660 \
    linux-firmware-qcom-adreno-a663 \
    linux-firmware-qcom-sa8775p-adreno \
    linux-firmware-qcom-sa8775p-audio \
    linux-firmware-qcom-sa8775p-compute \
    linux-firmware-qcom-sa8775p-generalpurpose \
    linux-firmware-qcom-sa8775p-qupv3fw \
    linux-firmware-qcom-vpu \
    linux-firmware-rtl8852 \
"

NAOS_QCOM_ALPINE_DEPLOY = "${TOPDIR}/tmp-alpine/deploy/images/${MACHINE}"

FILESEXTRAPATHS:prepend := "${NAOS_LAYER_DIR}/recipes-naos/ota/files:"
SRC_URI += "file://rootfs;subdir=ota file://q900-rootfs;subdir=ota"
S = "${UNPACKDIR}/ota"

python () {
    d.delVarFlag("do_fetch", "noexec")
    d.delVarFlag("do_unpack", "noexec")
}

addtask rootfs after do_unpack
# Keep the unpacked OTA overlays when the image class resets its staging directories.
do_rootfs[cleandirs] = "${IMAGE_ROOTFS} ${IMGDEPLOYDIR}"
do_rootfs[mcdepends] += " \
    mc::qcom-alpine:naos-alpine-rootfs:do_deploy \
    mc::qcom-alpine:rauc:do_deploy \
    mc::qcom-alpine:rauc-hawkbit-updater:do_deploy \
"
do_rootfs[depends] += "virtual/kernel:do_deploy naos-core:do_deploy naos-container:do_deploy"
do_rootfs[file-checksums] += "${NAOS_OTA_KEYRING}:True"

do_rootfs:append() {
    import json
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path

    root = Path(d.getVar("IMAGE_ROOTFS"))
    vendor_lib = root / d.getVar("nonarch_base_libdir").lstrip("/")
    alpine_deploy = Path(d.getVar("NAOS_QCOM_ALPINE_DEPLOY"))
    alpine = alpine_deploy / "naos-alpine-rootfs-aarch64"
    archive = alpine.with_suffix(".tar.gz")
    if not archive.is_file():
        bb.fatal("Missing Alpine rootfs archive: %s" % archive)
    packages = json.loads(alpine.with_suffix(".manifest.json").read_text())["packages"]
    manifest = Path(d.getVar("IMAGE_MANIFEST"))
    image_packages = [
        line for line in manifest.read_text().splitlines()
        if line.startswith(("kernel-module-", "linux-firmware-"))
    ]

    with tempfile.TemporaryDirectory(dir=d.getVar("WORKDIR")) as directory:
        for name in ("modules", "firmware"):
            source = vendor_lib / name
            if not source.is_dir() or not any(source.iterdir()):
                bb.fatal("Missing QCS9075 BSP payload: %s" % source)

        # Carry ownership through the archive and extract new inodes under
        # pseudo when the BSP paths change from the OE to the Alpine layout.
        bsp_archive = Path(directory) / "bsp.tar"
        subprocess.run(
            ["tar", "--create", "--file", str(bsp_archive), "--directory", str(vendor_lib),
             "--numeric-owner", "modules", "firmware"],
            check=True,
        )

        # The OE package manager stages the board files. Alpine supplies the
        # delivered userspace and its numeric ownership from the verified tar.
        shutil.rmtree(root)
        root.mkdir()
        subprocess.run(
            ["tar", "--extract", "--gzip", "--file", str(archive), "--directory", str(root),
             "--numeric-owner", "--same-owner", "--same-permissions"],
            check=True,
        )
        subprocess.run(
            ["tar", "--extract", "--file", str(bsp_archive), "--directory", str(root / "lib"),
             "--numeric-owner", "--same-owner", "--same-permissions"],
            check=True,
        )

    ota = Path(d.getVar("S"))
    deploy = Path(d.getVar("DEPLOY_DIR_IMAGE"))
    for overlay in (
        ota / "rootfs",
        ota / "q900-rootfs",
        alpine_deploy / "naos-rauc",
        alpine_deploy / "naos-hawkbit-updater",
        deploy / "naos-container",
        deploy / "naos-core",
    ):
        shutil.copytree(overlay, root, symlinks=True, dirs_exist_ok=True)

    (root / "etc/fw_env.config").unlink()
    core_config = root / "etc/naos-core/device.ini"
    core_config.write_text(core_config.read_text().replace("\nsoc=rk3588\n", "\nsoc=qcs9075\n"))
    for service in ("rauc", "naos-boot-confirm"):
        (root / "etc/runlevels/default" / service).symlink_to("/etc/init.d/" + service)

    keyring = root / "etc/rauc/keyring.pem"
    shutil.copyfile(d.getVar("NAOS_OTA_KEYRING"), keyring)
    keyring.chmod(0o644)
    version = {
        "machine": d.getVar("MACHINE"),
        "version": d.getVar("NAOS_VERSION"),
        "build_id": d.getVar("NAOS_BUILD_ID"),
        "layout_compatible": d.getVar("NAOS_LAYOUT_COMPATIBLE"),
        "secure_boot": False,
        "system_encryption": False,
        "data_encryption": False,
    }
    (root / "usr/lib/naos/version.json").write_text(json.dumps(version, sort_keys=True, indent=2) + "\n")

    image_packages.extend(
        "{name} {architecture} {version}".format_map(package) for package in packages
    )
    manifest.write_text("\n".join(sorted(image_packages)) + "\n")
}

check_naos_q900_system_size() {
    image="${IMGDEPLOYDIR}/${IMAGE_LINK_NAME}.squashfs"
    size=$(stat -L -c %s "$image") || bbfatal "Missing NAOS SquashFS image: $image"
    [ "$size" -le 209715200 ] || bbfatal "NAOS SquashFS exceeds the 200 MiB system slot"
}
IMAGE_POSTPROCESS_COMMAND += "check_naos_q900_system_size"
