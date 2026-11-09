#!/usr/bin/env python3
"""Package a verified Yocto export as an opaque, signed Alpine data package."""

import argparse
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
KEY_NAME = "naos-apk.rsa"


def export_helper():
    filename = PROJECT_ROOT / "meta-naos-package/lib/naos_package/export.py"
    spec = importlib.util.spec_from_file_location("naos_apk_export", filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def digest(path, algorithm="sha256"):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, algorithm).hexdigest()


def inspect_export(root):
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Input must be an ordinary export directory")
    # The export verifier rejects unexpected regular files. Also reject special
    # entries, including directory symlinks which Path.rglob does not traverse.
    for path in root.rglob("*"):
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise ValueError("Export contains a non-ordinary entry: " + str(path.relative_to(root)))
    data = export_helper().verify(root)
    machine = data.get("machine")
    if not isinstance(machine, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._+-]*", machine):
        raise ValueError("Export has an invalid machine name")
    if data.get("profile") not in ("development", "production"):
        raise ValueError("Export has an invalid profile")
    if not isinstance(data.get("architecture"), str) or not re.fullmatch(
            r"[a-z0-9][a-z0-9_+-]*", data["architecture"]):
        raise ValueError("Export has an invalid target architecture")
    epoch = data.get("source_date_epoch", 0)
    if type(epoch) is not int or epoch < 0:
        raise ValueError("Export has an invalid source date epoch")
    return data


class CheckedReader:
    """Hash the bytes tarfile actually reads, not a separate pre-build copy."""

    def __init__(self, stream):
        self.stream = stream
        self.checksum = hashlib.sha256()
        self.size = 0

    def read(self, size=-1):
        data = self.stream.read(size)
        self.checksum.update(data)
        self.size += len(data)
        return data


def write_inputs_tar(root, data, destination):
    """Keep inner archives untouched and normalize only the outer tar metadata."""
    epoch = data.get("source_date_epoch", 0)
    records = dict(data["input_files"])
    records["export.json"] = {"sha256": digest(root / "export.json"),
                              "size": (root / "export.json").stat().st_size}
    with tarfile.open(destination, "w", format=tarfile.PAX_FORMAT) as archive:
        for name, record in sorted(records.items()):
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("Unsafe export-relative path")
            member = tarfile.TarInfo("inputs/" + path.as_posix())
            member.size = record["size"]
            member.mode = 0o644
            member.mtime = epoch
            member.uid = member.gid = 0
            member.uname = member.gname = "root"
            source = root / name
            if source.is_symlink() or not source.is_file():
                raise ValueError("Export input is no longer an ordinary file: " + name)
            with source.open("rb") as stream:
                checked = CheckedReader(stream)
                archive.addfile(member, checked)
                if checked.size != record["size"] or checked.checksum.hexdigest() != record["sha256"]:
                    raise ValueError("Export input changed during packaging: " + name)


def apkbuild_path(profile):
    if profile not in ("development", "production"):
        raise ValueError("Unsupported APK profile")
    filename = PROJECT_ROOT / "apk" / profile / "rk3588" / "APKBUILD"
    if filename.is_symlink() or not filename.is_file():
        raise ValueError("The checked-in APKBUILD is missing for " + profile)
    return filename


def public_identity(filename, private=False):
    command = ["openssl", "pkey", "-in", str(filename), "-outform", "DER"]
    if private:
        command += ["-pubout", "-passin", "pass:"]
    else:
        command.append("-pubin")
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise ValueError("Unable to read APK signing material")
    return result.stdout


def apk_metadata(filename):
    """Read the signed v2 control metadata produced by abuild.

    An APK concatenates independently compressed signature/control/data tar
    streams. gzip handles the gzip members; ignore_zeros handles tar padding.
    Stop at the control metadata without reading the firmware payload.
    """
    with gzip.open(filename, "rb") as stream, tarfile.open(
            fileobj=stream, mode="r|", ignore_zeros=True) as archive:
        for member in archive:
            if member.name != ".PKGINFO":
                continue
            if not member.isfile() or member.size > 1024 * 1024:
                raise ValueError("APK has invalid package metadata")
            info = {}
            content = archive.extractfile(member).read().decode("utf-8")
            for line in content.splitlines():
                key, separator, value = line.partition(" = ")
                if separator and key in ("arch", "pkgname", "pkgver"):
                    if key in info:
                        raise ValueError("APK has duplicate package metadata")
                    info[key] = value
            return info
    raise ValueError("APK package metadata is missing")


def build(input_dir, output_dir, signing_key, public_key, version):
    input_dir = Path(input_dir)
    data = inspect_export(input_dir)
    input_dir = input_dir.resolve()
    output_dir = Path(output_dir).resolve()
    if output_dir == input_dir or output_dir.is_relative_to(input_dir):
        raise ValueError("Output directory must be outside the input export")
    if output_dir.exists() and (not output_dir.is_dir() or any(output_dir.iterdir())):
        raise ValueError("Output directory must be empty")
    if not re.fullmatch(r"[0-9][A-Za-z0-9_.]*", version):
        raise ValueError("Version must be an Alpine pkgver without a package revision")
    for program in ("apk", "abuild", "openssl"):
        if shutil.which(program) is None:
            raise ValueError("Required build tool is missing: " + program)
    result = subprocess.run(["apk", "version", "--check", version + "-r0"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise ValueError("Version is not a valid Alpine package version")
    for filename in (Path(signing_key), Path(public_key)):
        if filename.is_symlink() or not filename.is_file():
            raise ValueError("APK signing material must be ordinary files")
    if public_identity(signing_key, private=True) != public_identity(public_key):
        raise ValueError("APK signing private and public keys do not match")

    pkgname = data["machine"] + "-alpine-" + ("dev" if data["profile"] == "development" else "prod")
    filename = pkgname + "-" + version + "-r0.apk"
    with tempfile.TemporaryDirectory(prefix="naos-apk-") as temporary:
        work = Path(temporary)
        signing = work / "signing"
        signing.mkdir(mode=0o700)
        for source, name, mode in ((signing_key, KEY_NAME, 0o600),
                                   (public_key, KEY_NAME + ".pub", 0o644)):
            target = signing / name
            target.write_bytes(Path(source).read_bytes())
            target.chmod(mode)
        userdir = work / "abuild-user"
        userdir.mkdir(mode=0o700)
        userconf = userdir / "abuild.conf"
        userconf.write_text("PACKAGER_PRIVKEY=" + shlex.quote(str(signing / KEY_NAME)) + "\n"
                            + "PACKAGER_PUBKEY=" + shlex.quote(str(signing / (KEY_NAME + ".pub"))) + "\n")
        userconf.chmod(0o600)
        start = work / "aports/naos" / pkgname
        start.mkdir(parents=True)
        source = start / "inputs.tar"
        write_inputs_tar(input_dir, data, source)
        shutil.copyfile(apkbuild_path(data["profile"]), start / "APKBUILD")
        env = os.environ.copy()
        env.update({"ABUILD_CONF": "/dev/null", "ABUILD_USERDIR": str(userdir),
                    "ABUILD_USERCONF": str(userconf), "REPODEST": str(work / "packages"),
                    "SRCDEST": str(start), "SOURCE_DATE_EPOCH": str(data.get("source_date_epoch", 0)),
                    "NAOS_APK_MACHINE": data["machine"], "NAOS_APK_VERSION": version,
                    "NAOS_APK_SOURCE_SHA512": digest(source, "sha512"),
                    "PACKAGER": "Wushuqi <shuqi.wu@qq.com>",
                    "PACKAGER_PRIVKEY": str(signing / KEY_NAME),
                    "PACKAGER_PUBKEY": str(signing / (KEY_NAME + ".pub"))})
        command = ["abuild"] + (["-F"] if os.geteuid() == 0 else [])
        subprocess.run(command + ["validate", "unpack", "rootpkg"], cwd=start, env=env, check=True)
        packages = list((work / "packages").rglob(filename))
        if len(packages) != 1 or not packages[0].is_file():
            raise ValueError("abuild did not produce exactly the expected APK")
        subprocess.run(["apk", "--keys-dir", str(signing), "verify", str(packages[0])],
                       check=True)
        info = apk_metadata(packages[0])
        architecture = info.get("arch", "")
        if (info.get("pkgname") != pkgname or info.get("pkgver") != version + "-r0"
                or architecture in ("noarch", "all")
                or not re.fullmatch(r"[a-z0-9][a-z0-9_+-]*", architecture)):
            raise ValueError("Built APK metadata does not match the selected package")
        # Revalidate before publishing so a changed CI artifact cannot silently
        # become a successful packaging result.
        if inspect_export(input_dir) != data:
            raise ValueError("Export metadata changed during packaging")
        output_dir.mkdir(parents=True, exist_ok=True)
        package = output_dir / filename
        shutil.copyfile(packages[0], package)
        package.chmod(0o644)
        report = {"schema_version": 1, "filename": filename, "pkgname": pkgname,
                  "version": version + "-r0", "pkgver": version, "pkgrel": 0,
                  "profile": data["profile"], "machine": data["machine"],
                  "architecture": architecture, "target_arch": data["architecture"],
                  "install_prefix": "/usr/share/naos/image-inputs/" + data["profile"] + "/" + data["machine"],
                  "sha256": digest(package), "size": package.stat().st_size,
                  "export_version": data.get("version"), "export_build_id": data.get("build_id")}
        metadata = output_dir / "package.json"
        metadata.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
        metadata.chmod(0o644)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--signing-key", required=True, type=Path)
    parser.add_argument("--public-key", required=True, type=Path)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    report = build(args.input_dir, args.output_dir, args.signing_key, args.public_key, args.version)
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print("APK packaging failed: " + str(error), file=sys.stderr)
        sys.exit(1)
